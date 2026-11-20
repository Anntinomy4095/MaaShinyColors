#!/usr/bin/env python3
"""Maa固定选卡研究循环：默认只读，单次输入，独立后置确认。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core.browser_bridge import BrowserBridge
from agent.core.daily_config import load_json
from agent.core.daily_navigation import PREFIX, actionable, fixed_pipeline, load_contract
from agent.core.foreground_guard import interpret_foreground
from agent.core.training_flow import load_guard, stage_matches
from tools.replay_foreground import load_configuration
from tools.replay_screenshots import MaaRecognizer, read_png, require, template_path
from tools.run_daily_navigation import implementation_stamp as navigation_stamp, send_request, validate_offline_proof
from tools.run_daily_training import validate_offline_proof as validate_training_proof
from tools.run_supervised_navigation import private_output
from tools.selection_loop_protocol import (ACTION, POINT, RESOLUTION, confirm_request, select_request,
                                           status_request, validate_response, validate_session)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def guarded_select(report, execute, point, guard, send, checkpoint):
    if execute is not True or point != POINT or report['input_attempts']:
        return False
    try:
        request = guard()
        report.update(input_attempts=1, reason='selection_pending_no_retry',
                      nonce=request['nonce'], pre_frame_sha256=request['frame_sha256'])
        checkpoint()
        response = validate_response(send(request))
        report['response'] = response
        return response['ok'] and response['attempted'] and response['state'] == 'pending'
    except Exception as exc:
        report['input_error'] = str(exc)
        return False


def post_verified(hits, foreground):
    return (stage_matches('Zero', hits) and foreground.get('state') == 'no_known_popup'
            and foreground.get('complete_contracts') == [] and foreground.get('partial_contracts') == [])


def run(args, report, checkpoint):
    _, frames = private_output(args.report)
    data, _, digest = load_contract(args.manifest, args.sources)
    native, truth, native_digest = load_guard(args.native_manifest, args.native_sources)
    fore_data, rules, fore_digest, _ = load_configuration(args.foreground_manifest, args.foreground_contracts)
    validate_offline_proof(load_json(args.offline_proof), ACTION, digest, fore_digest,
                           [s['id'] for s in data['samples']], navigation_stamp())
    validate_training_proof(load_json(args.native_offline_proof), native, truth, native_digest)
    producer_digest = sha(__file__)
    protocol_digest = sha(ROOT / 'tools/selection_loop_protocol.py')
    report.update(configuration_sha256=digest, native_configuration_sha256=native_digest,
                  foreground_sha256=fore_digest, producer_sha256=producer_digest,
                  protocol_sha256=protocol_digest, offline_verified=True)
    reco = MaaRecognizer()
    def foreground(image):
        hits = [reco.recognize(s, image, args.foreground_manifest.parent) for s in fore_data['scenes']]
        return interpret_foreground(hits, (1280, 720), rules)
    def selection(image):
        hits = [reco.recognize(s, image, args.manifest.parent) for s in data['scenes']]
        state = foreground(image)
        return hits, state, actionable(ACTION, hits, state)
    def selected(image):
        hits = [reco.recognize(s, image, args.native_manifest.parent) for s in native['scenes']
                if s['name'].startswith('SC.Training.Zero.')]
        state = foreground(image)
        return hits, state, post_verified(hits, state)
    # Load and hash-check all recognition resources before a five-second live frame.
    warm = read_png(args.manifest.parent / data['samples'][-1]['image'])
    selection(warm)
    selected(warm)
    bridge = BrowserBridge.from_file(args.session)
    report['session_pid'] = bridge.session['pid']
    session = None
    if args.execute:
        resolution = load_json(RESOLUTION)
        require(resolution['configuration_sha256'] == native_digest
                and resolution['foreground_sha256'] == fore_digest, '选卡后置配置与已审查历史证明不符')
        session = validate_session(load_json(Path(str(args.session) + '.selection-loop-input.json')),
                                   bridge.session['pid'], sha(RESOLUTION))
        state = validate_response(send_request(session, status_request(session)))
        report['initial_loop_status'] = state
        require(state['ok'] and state['state'] in ('ready', 'confirmed')
                and state['attempts'] < state['max_attempts'], '选卡循环待核验或已达上限；禁止重发')
    frames.mkdir(parents=True)
    def capture():
        raw = bridge.screencap()
        path = frames / f"frame-{len(report['frames'])+1:03d}.png"
        path.write_bytes(raw)
        entry = {'image': str(path.resolve()), 'sha256': hashlib.sha256(raw).hexdigest()}
        report['frames'].append(entry)
        return read_png(path), entry
    from maa.controller import CustomController
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.define import LoggingLevelEnum
    Tasker.set_stdout_level(LoggingLevelEnum.Off)
    class Controller(CustomController):
        def connect(self): return True
        def get_features(self): return 0
        def request_uuid(self): return 'SC.TrainingSelection.FixedLoop'
        def screencap(self): return capture()[0]
        def click(self, x, y):
            def guard():
                require(load_contract(args.manifest, args.sources)[2] == digest
                        and load_guard(args.native_manifest, args.native_sources)[2] == native_digest
                        and load_configuration(args.foreground_manifest, args.foreground_contracts)[2] == fore_digest
                        and sha(__file__) == producer_digest
                        and sha(ROOT / 'tools/selection_loop_protocol.py') == protocol_digest,
                        '选卡配置或代码变化')
                image, entry = capture()
                hits, state, allowed = selection(image)
                report['fresh_guard'] = {'recognitions': hits, 'foreground': state, 'verified': allowed,
                                         'frame_sha256': entry['sha256']}
                require(allowed, '完整训练列表/指定卡/前景守卫拒绝')
                return select_request(session, bridge.status(), entry['sha256'])
            return guarded_select(report, args.execute, (x, y), guard,
                                  lambda request: send_request(session, request), checkpoint)
        def deny(self, *args): return False
        start_app = stop_app = swipe = touch_down = touch_move = touch_up = deny
        click_key = input_text = key_down = key_up = scroll = relative_move = deny
        def shell(self, *args): return None
    controller = Controller()
    require(controller.set_screenshot_use_raw_size(True) and controller.post_connection().wait().succeeded,
            'Maa只读截图控制器连接失败')
    resource, tasker = Resource(), Tasker()
    with tempfile.TemporaryDirectory(prefix='sc-selection-loop-') as directory:
        bundle = Path(directory)
        (bundle / 'pipeline').mkdir()
        (bundle / 'pipeline/selection.json').write_text(json.dumps(fixed_pipeline(ACTION, args.execute)), encoding='utf-8')
        require(resource.post_bundle(bundle).wait().succeeded, '固定选卡Pipeline加载失败')
        for scene in data['scenes']:
            require(resource.override_image(scene['name'] + '.png', read_png(template_path(scene, args.manifest.parent),
                    expected_size=None)), '选卡模板加载失败')
        require(tasker.bind(resource, controller) and tasker.inited, 'Maa Tasker连接失败')
        head = PREFIX + ACTION + '.'
        job = tasker.post_task(head + 'Observe')
        deadline = time.monotonic() + 20
        while not job.done:
            if time.monotonic() > deadline:
                tasker.post_stop().wait()
                raise RuntimeError('选卡任务超时；禁止重发')
            time.sleep(.02)
        detail = job.get()
        report['node_trace'] = [{'name': n.name, 'completed': n.completed,
            'action_success': n.action.success if n.action else None} for n in detail.nodes] if detail else []
        positive = (job.succeeded and [n['name'] for n in report['node_trace']]
                    == [head + 'Observe', head + ('Click' if args.execute else 'Guard')]
                    and all(n['completed'] and n['action_success'] for n in report['node_trace']))
    if not args.execute:
        report.update(passed=positive, reason='readonly_list_verified' if positive else 'list_guard_refused')
        return
    require(positive and report.get('response', {}).get('attempted') is True,
            '选卡输入未确认，停止且不重发')
    report['reason'] = 'selection_postcondition_unverified_no_retry'
    for _ in range(20):
        image, entry = capture()
        hits, state, verified = selected(image)
        report.setdefault('postcondition_checks', []).append({'frame_sha256': entry['sha256'],
                'recognitions': hits, 'foreground': state, 'verified': verified})
        if verified:
            proof_path = args.report.with_name(args.report.stem + '-postcondition.json')
            private_output(proof_path)
            proof = {'version': 1, 'kind': 'SC.TrainingSelection.Postcondition', 'action': ACTION,
                'passed': True, 'resource_consumption': False, 'input_attempts': 1, 'nonce': report['nonce'],
                'session_pid': bridge.session['pid'], 'pre_frame_sha256': report['pre_frame_sha256'],
                'post_frame': entry, 'configuration_sha256': native_digest, 'foreground_sha256': fore_digest,
                'post_recognitions': hits, 'foreground': state,
                'producer_path': str(Path(__file__).resolve()), 'producer_sha256': producer_digest}
            with proof_path.open('x', encoding='utf-8') as stream:
                json.dump(proof, stream, ensure_ascii=False, indent=2)
            report['postcondition_proof'] = {'path': str(proof_path.resolve()), 'sha256': sha(proof_path)}
            checkpoint()
            response = validate_response(send_request(session, confirm_request(session, bridge.status(),
                                  entry['sha256'], report['nonce'], proof_path, sha(proof_path))))
            report['confirmation_response'] = response
            require(response['ok'] and not response['attempted'] and response['state'] == 'confirmed',
                    '选卡后置登记未确认；仅允许只读查询，不重发输入')
            report.update(passed=True, reason='selection_postcondition_confirmed')
            break
        time.sleep(.5)
    require(reco.input_attempts == 0, '识别器出现意外输入')


def main():
    parser = argparse.ArgumentParser(description='固定S卡的有界研究选卡，默认只读，不消费材料。')
    for name in ('session', 'manifest', 'sources', 'offline-proof', 'native-manifest', 'native-sources',
                 'native-offline-proof', 'foreground-manifest', 'foreground-contracts', 'report'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    private_output(args.report)
    report = {'runner': 'SC.TrainingSelection', 'passed': False, 'input_enabled': args.execute,
              'input_attempts': 0, 'resource_consumption': False, 'frames': []}
    def checkpoint():
        args.report.parent.mkdir(parents=True, exist_ok=True)
        mode = 'w' if report.get('checkpoint_written') else 'x'
        with args.report.open(mode, encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
        report['checkpoint_written'] = True
    try:
        run(args, report, checkpoint)
    except Exception as exc:
        report.update(passed=False, reason='refused_or_uncertain_no_retry', error=str(exc))
    checkpoint()
    print(json.dumps({k: report[k] for k in ('passed', 'reason', 'input_attempts')}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
