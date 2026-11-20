#!/usr/bin/env python3
"""Shared screenshot classification, isolated full Maa daily navigation replays."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core.daily_navigation import ACTION_GROUPS, PREFIX, actionable, fixed_pipeline, load_contract
from agent.core.foreground_guard import interpret_foreground
from tools.navigation_protocol import ALL_DAILY_ACTION_POINTS
from tools.replay_foreground import load_configuration
from tools.replay_screenshots import MaaRecognizer, read_png, require, template_path
from tools.run_daily_navigation import implementation_stamp
from tools.run_supervised_navigation import private_output


@dataclass(frozen=True)
class CachedSample:
    """Process-local offline evidence; JSON strings and pixels cannot be mutated."""
    identifier: str
    path: Path
    sha256: str
    image: object
    hits_json: str
    foreground_json: str
    actions: tuple[str, ...]


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_files(hashes):
    for path, digest in hashes.items():
        require(file_hash(path) == digest, f'回放期间文件发生变化：{path.name}')


def cache_samples(args, recognizer):
    data, truth, digest = load_contract(args.manifest, args.sources)
    foreground, rules, foreground_digest, _ = load_configuration(
        args.foreground_manifest, args.foreground_contracts)
    base, foreground_base = args.manifest.resolve().parent, args.foreground_manifest.resolve().parent
    paths = {args.manifest.resolve(), args.sources.resolve(), args.foreground_manifest.resolve(),
             args.foreground_contracts.resolve()}
    paths.update((base / sample['image']).resolve() for sample in data['samples'])
    paths.update(template_path(scene, base) for scene in data['scenes'])
    paths.update(template_path(scene, foreground_base) for scene in foreground['scenes']
                 if scene['recognition'] == 'TemplateMatch')
    hashes = {path: file_hash(path) for path in paths}
    # Bind the cached image to the original, trusted source hash, not just the
    # current bytes. The final checks also bind configuration and templates.
    for sample in data['samples']:
        require(hashes[(base / sample['image']).resolve()] == truth[sample['image']]['sha256'],
                '样本原始哈希不符')
    require(load_contract(args.manifest, args.sources)[2] == digest, '加载期间导航配置变化')
    require(load_configuration(args.foreground_manifest, args.foreground_contracts)[2] == foreground_digest,
            '加载期间前景配置变化')
    decoded = {}

    def image_at(path, expected_size=None):
        path = path.resolve()
        if path not in decoded:
            image = read_png(path, expected_size=expected_size)
            # bytes-backed NumPy views cannot have writeability re-enabled.
            import numpy as np
            decoded[path] = np.frombuffer(image.tobytes(), dtype=image.dtype).reshape(image.shape)
        image = decoded[path]
        require(expected_size is None or image.shape == (expected_size[1], expected_size[0], 3),
                '缓存图片尺寸不符')
        return image

    templates = {scene['name'] + '.png': image_at(template_path(scene, base)) for scene in data['scenes']}
    cached = []
    for sample in data['samples']:
        path = (base / sample['image']).resolve()
        image = image_at(path, (1280, 720))
        hits = [recognizer.recognize(scene, image, base) for scene in data['scenes']]
        foreground_hits = [recognizer.recognize(scene, image, foreground_base) for scene in foreground['scenes']]
        state = interpret_foreground(foreground_hits, (1280, 720), rules)
        require(file_hash(path) == hashes[path], '识别期间样本发生变化')
        cached.append(CachedSample(sample['id'], path, hashes[path], image,
                                   json.dumps(hits), json.dumps(state), tuple(truth[sample['image']]['actions'])))
    check_files(hashes)
    require(recognizer.input_attempts == 0, '仅识别控制器出现输入请求')
    return tuple(cached), templates, hashes, digest, foreground_digest


def replay_action(action, samples, templates, framework_version, digest, foreground_digest, stamp):
    from maa.controller import CustomController
    from maa.resource import Resource
    from maa.tasker import Tasker

    report = {'passed': False, 'input_attempts': 0, 'input_enabled': False, 'offline_only': True,
              'action': action, 'resource_consumption': False, 'frames': [], 'node_trace': [],
              'configuration_sha256': digest, 'foreground_sha256': foreground_digest,
              'implementation_sha256': stamp, 'framework_version': framework_version, 'offline': []}
    report.update(offline_producer='SC.DailyNavigation.Batch',
                  offline_producer_sha256=file_hash(Path(__file__)))

    class Controller(CustomController):
        def __init__(self):
            super().__init__()
            self.image = None
            self.clicks = []
            self.other_input_attempts = 0
        def get_features(self): return 0
        def connect(self): return True
        def request_uuid(self): return 'SC.DailyNavigation.OfflineBatch.' + action
        def screencap(self): return self.image
        def click(self, x, y):
            self.clicks.append([x, y])
            return True
        def deny(self, *args):
            self.other_input_attempts += 1
            return False
        start_app = stop_app = swipe = touch_down = touch_move = touch_up = deny
        click_key = input_text = key_down = key_up = scroll = relative_move = deny
        def shell(self, *args):
            self.other_input_attempts += 1
            return None

    controller = Controller()
    require(controller.set_screenshot_use_raw_size(True) and controller.post_connection().wait().succeeded,
            '离线Maa控制器初始化失败')
    resource, tasker = Resource(), Tasker()
    with tempfile.TemporaryDirectory(prefix='sc-daily-batch-') as temporary:
        bundle = Path(temporary)
        (bundle / 'pipeline').mkdir()
        # A new resource per action prevents merged overrides retaining another
        # action's HomeUnobstructed.next or enabled Click node.
        (bundle / 'pipeline/daily.json').write_text(json.dumps(fixed_pipeline(action, True)), encoding='utf-8')
        require(resource.post_bundle(bundle).wait().succeeded, '完整Pipeline加载失败')
        for name, image in templates.items():
            require(resource.override_image(name, image), '离线模板加载失败')
        require(tasker.bind(resource, controller) and tasker.inited, '离线Maa任务绑定失败')
        for sample in samples:
            require(file_hash(sample.path) == sample.sha256, '动作回放前样本发生变化')
            controller.image, controller.clicks = sample.image, []
            expected = action in sample.actions
            permit = actionable(action, json.loads(sample.hits_json), json.loads(sample.foreground_json))
            job = tasker.post_task(PREFIX + action + '.Observe')
            deadline = time.monotonic() + 20
            while not job.done:
                if time.monotonic() > deadline:
                    tasker.post_stop().wait()
                    raise RuntimeError('离线任务超时，不重试')
                time.sleep(.02)
            detail = job.get()
            trace = [{'name': node.name, 'completed': node.completed,
                      'hit': node.recognition.hit if node.recognition else None,
                      'action_success': node.action.success if node.action else None}
                     for node in detail.nodes] if detail else []
            wanted = [list(ALL_DAILY_ACTION_POINTS[action])] if expected else []
            passed = bool(job.succeeded and permit == expected and controller.clicks == wanted
                          and controller.other_input_attempts == 0)
            report['offline'].append({'id': sample.identifier, 'passed': passed, 'expected': expected,
                                      'allowed': permit, 'clicks': list(controller.clicks), 'trace': trace})
            require(file_hash(sample.path) == sample.sha256, '动作回放后样本发生变化')
    report['passed'] = all(sample['passed'] for sample in report['offline'])
    report['reason'] = 'offline_pipeline_verified' if report['passed'] else 'refused_or_uncertain_no_retry'
    report['other_input_attempts'] = controller.other_input_attempts
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description='日常导航批量离线回放：共享截图识别，每动作运行独立完整Maa Pipeline，不连接浏览器。')
    for name in ('manifest', 'sources', 'foreground-manifest', 'foreground-contracts'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--actions', nargs='+', choices=ACTION_GROUPS, required=True)
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument('--report-prefix', type=Path, help='输出 <prefix>-<action>.json；必须位于private且不得覆盖')
    output.add_argument('--report-dir', type=Path, help='输出目录；各动作保存为 daily-navigation-<action>.json')
    args = parser.parse_args(argv)
    require(len(set(args.actions)) == len(args.actions), '动作不得重复')
    prefix = args.report_prefix if args.report_prefix else args.report_dir / 'daily-navigation'
    outputs = {action: Path(str(prefix) + '-' + action + '.json') for action in args.actions}
    # Validate every destination before any expensive replay or report write.
    for path in outputs.values():
        private_output(path)
    reports = {}
    try:
        from maa.define import LoggingLevelEnum
        from maa.tasker import Tasker
        Tasker.set_stdout_level(LoggingLevelEnum.Off)
        stamp = implementation_stamp()
        producer_stamp = file_hash(Path(__file__))
        recognizer = MaaRecognizer()
        samples, templates, hashes, digest, foreground_digest = cache_samples(args, recognizer)
        for action in args.actions:
            check_files(hashes)
            reports[action] = replay_action(action, samples, templates, recognizer.version,
                                            digest, foreground_digest, stamp)
        check_files(hashes)
        require(implementation_stamp() == stamp, '回放期间实现发生变化')
        require(file_hash(Path(__file__)) == producer_stamp, '回放期间批量回放实现发生变化')
        require(load_contract(args.manifest, args.sources)[2] == digest, '回放期间导航合同变化')
        require(load_configuration(args.foreground_manifest, args.foreground_contracts)[2] == foreground_digest,
                '回放期间前景合同变化')
        require(recognizer.input_attempts == 0, '仅识别控制器出现输入请求')
    except Exception as exc:
        for action in args.actions:
            report = reports.setdefault(action, {'action': action, 'offline_only': True, 'input_enabled': False,
                                                 'input_attempts': 0, 'offline': []})
            report.update(passed=False, reason='refused_or_uncertain_no_retry', error=str(exc))
    for action, path in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as stream:
            json.dump(reports[action], stream, ensure_ascii=False, indent=2, allow_nan=False)
        print(json.dumps({'action': action, 'passed': reports[action]['passed'], 'report': str(path),
                          'error': reports[action].get('error')}, ensure_ascii=False))
    return 0 if all(report['passed'] for report in reports.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
