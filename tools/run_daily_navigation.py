#!/usr/bin/env python3
"""One fixed daily navigation through Maa; no spending and no automatic retry."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent.core.browser_bridge import BrowserBridge
from agent.core.daily_config import load_json
from agent.core.daily_navigation import PREFIX, GROUPS, ACTION_GROUPS, POST_GROUPS, complete_group, any_group, actionable, postcondition, load_contract, fixed_pipeline
from agent.core import navigation_interrupt as interrupt
from agent.core.foreground_guard import interpret_foreground
from tools.navigation_protocol import ALL_DAILY_ACTION_POINTS as DAILY_ACTION_POINTS, validate_daily_session, daily_navigation_request, validate_navigation_response
from tools import repeatable_navigation_protocol as repeatable_protocol
from tools.replay_foreground import load_configuration
from tools.replay_screenshots import MaaRecognizer, read_png, require, template_path
from tools.run_supervised_navigation import private_output

def implementation_stamp():
    files=(Path(__file__),ROOT/'agent/core/daily_navigation.py',ROOT/'agent/core/navigation_interrupt.py',ROOT/'tools/navigation_protocol.py',ROOT/'tools/replay_screenshots.py',ROOT/'tests/fixtures/daily_navigation/pipeline.json')
    return hashlib.sha256(b''.join(p.read_bytes() for p in files)).hexdigest()

def validate_offline_proof(proof,action,digest,foreground_digest,sample_ids,stamp):
    if 'offline_producer' in proof or 'offline_producer_sha256' in proof:
        producer=ROOT/'tools/replay_daily_navigation_batch.py'
        require(proof.get('offline_producer')=='SC.DailyNavigation.Batch' and producer.is_file()
                and proof.get('offline_producer_sha256')==hashlib.sha256(producer.read_bytes()).hexdigest(),
                '批量离线证明生成器变化')
    require(proof.get('passed') is True and proof.get('offline_only') is True and proof.get('input_enabled') is False
        and type(proof.get('input_attempts')) is int and proof['input_attempts']==0,'离线证明不是无真实输入的成功回放')
    require(proof.get('action')==action and proof.get('configuration_sha256')==digest
        and proof.get('foreground_sha256')==foreground_digest and proof.get('implementation_sha256')==stamp,'离线证明与当前动作/代码/模板不匹配')
    samples=proof.get('offline')
    require(type(samples) is list and len(samples)==len(sample_ids) and {s['id'] for s in samples}==set(sample_ids)
        and all(s.get('passed') is True for s in samples),'离线证明样本不完整')
    return samples

def guarded_send(report,execute,action,point,guard,send,validate=validate_navigation_response):
    if execute is not True or action not in ACTION_GROUPS or point != DAILY_ACTION_POINTS[action] or report['input_attempts']:
        return False
    try:
        request=guard()
        report['input_attempts']+=1
        if validate is not validate_navigation_response:
            report.update(nonce=request['nonce'],pre_frame_sha256=request['frame_sha256'])
        response=validate(send(request))
        report['response']=response
        return response['ok']
    except Exception as exc:
        report['input_error']=str(exc)
        return False

def validate_repeatable_navigate_response(data):
    # Native success is only "pending"; the postcondition proof decides success.
    response=repeatable_protocol.validate_response(data)
    require(not response['ok'] or response['attempted'] and response['state']=='pending','可重复导航响应状态不符')
    return response

def confirm_repeatable(report,args,session,bridge,entry,hits,state,digest,fore_digest,producer_digest,send,interruption=None):
    """Write the private postcondition (or interruption) proof once ('x'), then register it; never resends input."""
    response=report.get('response',{})
    require(response.get('ok') is True and response.get('attempted') is True and response.get('state')=='pending'
            and repeatable_protocol.nonce_valid(report.get('nonce')),'原生手势未确认发送；保持pending，不登记')
    proof_path=args.report.resolve().with_name(args.report.stem+'-postcondition.json')
    private_output(proof_path)
    require(hashlib.sha256(Path(__file__).read_bytes()).hexdigest()==producer_digest,'生产者代码变化，拒绝登记')
    fields=(args.action,report['nonce'],bridge.session['pid'],report['pre_frame_sha256'],
        {'image':str(Path(entry['image']).resolve()),'sha256':entry['sha256']},digest,fore_digest,hits,state,producer_digest)
    proof=(repeatable_protocol.postcondition_proof(*fields) if interruption is None
           else repeatable_protocol.interrupted_proof(*fields,interruption))
    with proof_path.open('x',encoding='utf-8') as stream:json.dump(proof,stream,ensure_ascii=False,indent=2)
    proof_sha=hashlib.sha256(proof_path.read_bytes()).hexdigest()
    report['postcondition_proof']={'path':str(proof_path),'sha256':proof_sha}
    response=repeatable_protocol.validate_response(send(session,repeatable_protocol.confirm_request(
        session,bridge.status(),entry['sha256'],args.action,report['nonce'],proof_path,proof_sha)))
    report['confirmation_response']=response
    expected='confirmed' if interruption is None else 'interrupted'
    require(response['ok'] and not response['attempted'] and response['state']==expected,
            '可重复导航后置登记未确认；保持pending，不重发输入')
    return response

def session_pid_now(args):
    """Re-read the host session file: a restarted host has a different PID."""
    data=load_json(args.session)
    return data.get('pid') if type(data) is dict else None

def interruption_check(action,hits,state,pre_sha,post_sha,pre_pid,post_pid):
    """Read-only: the fixed Home tap hit the inert backdrop of a whitelisted popup (no expected page)."""
    expected=any(complete_group(g,hits) for g in POST_GROUPS.get(action,())) or postcondition(action,hits,state)
    return interrupt.decide_interrupted(action,DAILY_ACTION_POINTS.get(action),hits,state,pre_sha,post_sha,pre_pid,post_pid,expected)

def validate_pending_report(prior,action,pid):
    """A repeatable gesture was sent once and its post state was not recognised at the time."""
    response=prior.get('response') if type(prior) is dict else None
    require(type(prior) is dict and prior.get('repeatable') is True and prior.get('action')==action
            and prior.get('input_enabled') is True and prior.get('input_attempts')==1
            and prior.get('passed') is False and prior.get('reason')=='postcondition_unverified_no_retry'
            and prior.get('resource_consumption') is False and prior.get('session_pid')==pid
            and type(response) is dict and response.get('ok') is True and response.get('attempted') is True
            and response.get('state')=='pending' and repeatable_protocol.nonce_valid(prior.get('nonce'))
            and type(prior.get('pre_frame_sha256')) is str and len(prior['pre_frame_sha256'])==64,
            '只接受本宿主已发送一次、后置未识别的可重复导航报告')
    return {'nonce':prior['nonce'],'pre_frame_sha256':prior['pre_frame_sha256'],'response':dict(response)}

# Consecutive login-bonus popups share the Reward anchors; a chained Reward only counts as
# progress when its reward item area differs from the frame the OK was pressed on.
REWARD_ITEM_ROI=(393,312,500,112)

def chain_progressed(action,hits,image,frames,pre_sha):
    from agent.core.daily_navigation import complete_group
    if action!='reward-ack' or not complete_group('Reward',hits):return True
    match=[f for f in frames if f.get('sha256')==pre_sha]
    if not match:return False
    import numpy as np
    pre=read_png(Path(match[0]['image']))
    x,y,w,h=REWARD_ITEM_ROI
    # Share of clearly changed pixels in the item icon/name: 0.152 for a new reward, 0.0 for the same popup.
    changed=np.abs(pre[y:y+h,x:x+w].astype(int)-image[y:y+h,x:x+w].astype(int)).max(axis=2)>48
    return float(changed.mean())>.05

def resolve_pending(args,report,session,bridge,initial,controller,classify,digest,fore_digest,producer_digest,evidence):
    """Re-observe the live screen with the current profile; never sends input."""
    require(initial.get('state')=='pending','宿主账本不是pending，无需或不能解决')
    blob=args.resolve_pending_report.read_bytes()
    pending=validate_pending_report(json.loads(blob.decode('utf-8-sig')),args.action,bridge.session['pid'])
    report.update(pending,resolved_from={'path':str(args.resolve_pending_report.resolve()),
                  'sha256':hashlib.sha256(blob).hexdigest()},postcondition_checks=[])
    evidence.mkdir(parents=True)
    controller.offline=False
    image=controller.screencap()
    require(image.shape==(720,1280,3),'重新观察截图失败')
    hits,state=classify(image)
    prior=json.loads(blob.decode('utf-8-sig'))
    verified=postcondition(args.action,hits,state) and chain_progressed(args.action,hits,image,prior.get('frames',[]),pending['pre_frame_sha256'])
    report['postcondition_checks'].append({'recognitions':hits,'foreground':state,'verified':verified})
    if verified:
        confirm_repeatable(report,args,session,bridge,report['frames'][-1],hits,state,digest,fore_digest,producer_digest,send_request)
        report.update(passed=True,reason='repeatable_pending_resolved_by_reobservation')
        return
    require(args.action in interrupt.INTERRUPTIBLE_ACTIONS,'当前画面不是该动作的唯一后置状态；保持pending')
    decisions=[]
    for index in range(2):
        if index:
            time.sleep(interrupt.STABLE_GUARD_INTERVAL_S)
            image=controller.screencap()
            require(image.shape==(720,1280,3),'重新观察截图失败')
            hits,state=classify(image)
        decision=interruption_check(args.action,hits,state,pending['pre_frame_sha256'],report['frames'][-1]['sha256'],
                                    prior.get('session_pid'),session_pid_now(args))
        decisions.append(decision)
        report.setdefault('interruption_checks',[]).append({'frame_sha256':report['frames'][-1]['sha256'],**decision})
    require(all(d['interrupted'] for d in decisions) and decisions[0]['interruption']==decisions[1]['interruption'],
            '既不是后置状态，也不是稳定的已知弹窗中断；保持pending')
    confirm_repeatable(report,args,session,bridge,report['frames'][-1],hits,state,digest,fore_digest,producer_digest,send_request,
                       decisions[-1]['interruption'])
    report.update(passed=False,interrupted=True,reason='repeatable_pending_recorded_as_interrupted_no_retry')

def record_live_interruption(report,args,session,bridge,hits,state,digest,fore_digest,producer_digest):
    """After the bounded observation: the last INTERRUPT_STABLE_FRAMES checks must show one identical interruption."""
    checks=report.get('postcondition_checks',[])[-interrupt.INTERRUPT_STABLE_FRAMES:]
    decisions=[c.get('interruption') for c in checks]
    if not (args.repeatable and len(decisions)==interrupt.INTERRUPT_STABLE_FRAMES
            and all(type(d) is dict and d.get('interrupted') is True for d in decisions)
            and all(d['interruption']==decisions[-1]['interruption'] for d in decisions)):
        return False
    confirm_repeatable(report,args,session,bridge,report['frames'][-1],hits,state,digest,fore_digest,producer_digest,send_request,
                       decisions[-1]['interruption'])
    report.update(passed=False,interrupted=True,reason='repeatable_interrupted_by_known_popup_no_retry')
    return True

def guard_signature(hits,state):
    fore=(state.get('state'),state.get('popup'),tuple(state.get('complete_contracts') or ()),tuple(state.get('partial_contracts') or ()))
    return (interrupt.recognition_signature(hits,interrupt.home_signature_names()),fore)

def send_request(session,request):
    import socket
    with socket.create_connection((session['host'],session['port']),timeout=5) as connection:
        connection.settimeout(10)
        connection.sendall((json.dumps(request)+'\n').encode())
        with connection.makefile('rb') as stream: line=stream.readline(4097)
        require(len(line)<=4096 and line.endswith(b'\n'),'输入响应过长或中断，不重试')
        return json.loads(line)

def run(args,report):
    _,evidence=private_output(args.report)
    data,truth,digest=load_contract(args.manifest,args.sources)
    fore_data,fore_rules,fore_digest,_=load_configuration(args.foreground_manifest,args.foreground_contracts)
    base=args.manifest.resolve().parent
    fore_base=args.foreground_manifest.resolve().parent
    reco=MaaRecognizer()
    def classify(image):
        hits=[reco.recognize(s,image,base) for s in data['scenes']]
        fore_hits=[reco.recognize(s,image,fore_base) for s in fore_data['scenes']]
        return hits,interpret_foreground(fore_hits,(1280,720),fore_rules)
    pipeline=fixed_pipeline(args.action,args.execute)
    from maa.controller import CustomController
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.define import LoggingLevelEnum
    import numpy as np
    Tasker.set_stdout_level(LoggingLevelEnum.Off)
    stamp=implementation_stamp()
    producer_digest=hashlib.sha256(Path(__file__).read_bytes()).hexdigest() if args.repeatable else None
    report.update(configuration_sha256=digest,foreground_sha256=fore_digest,implementation_sha256=stamp,framework_version=reco.version,offline=[])
    bridge=None
    session=None
    class Controller(CustomController):
        offline=True
        image=None
        clicks=[]
        def get_features(self):return 0
        def connect(self):return True
        def request_uuid(self):return 'SC.DailyNavigation.FixedSingleStep'
        def screencap(self):
            if self.offline:return self.image
            if report.get('capture_error'):return np.empty((0,0,3),dtype=np.uint8)
            try:
                raw=bridge.screencap()
                path=evidence/f"frame-{len(report['frames'])+1:03d}.png"
                path.write_bytes(raw)
                report['frames'].append({'image':str(path),'sha256':hashlib.sha256(raw).hexdigest()})
                return read_png(path)
            except Exception as exc:
                report['capture_error']=str(exc)
                return np.empty((0,0,3),dtype=np.uint8)
        def click(self,x,y):
            if self.offline:
                self.clicks.append([x,y])
                return True
            def guard_frame():
                image=self.screencap()
                require(image.shape==(720,1280,3),'新截图失败')
                hits,state=classify(image)
                report['fresh_guard']={'recognitions':hits,'foreground':state}
                return {'passed':actionable(args.action,hits,state),'frame_sha256':report['frames'][-1]['sha256'],
                        'signature':guard_signature(hits,state)}
            def fresh_guard():
                require(load_contract(args.manifest,args.sources)[2]==digest,'守卫配置变化')
                require(load_configuration(args.foreground_manifest,args.foreground_contracts)[2]==fore_digest,'前景配置变化')
                first=guard_frame()
                require(first['passed'],'前景或目标守卫拒绝')
                if ACTION_GROUPS[args.action]=='Home':
                    # A popup may still be opening over Home: require two identical full guard passes
                    # at least STABLE_GUARD_INTERVAL_S apart and bind the request to the second frame.
                    started=time.monotonic()
                    time.sleep(interrupt.STABLE_GUARD_INTERVAL_S)
                    second=guard_frame()
                    elapsed=time.monotonic()-started
                    report['stable_guard']={'first_frame_sha256':first['frame_sha256'],'second_frame_sha256':second['frame_sha256'],
                                            'elapsed_s':elapsed,'passed':[first['passed'],second['passed']],
                                            'identical':first['signature']==second['signature']}
                    require(interrupt.stable_pair(first,second,elapsed),'主页两帧守卫不稳定或未通过；不发送')
                stamp=report['frames'][-1]['sha256']
                if args.repeatable:
                    return repeatable_protocol.navigate_request(session,bridge.status(),stamp,args.action)
                return daily_navigation_request(session,bridge.status(),stamp,args.action)
            if args.repeatable:
                return guarded_send(report,args.execute,args.action,(x,y),fresh_guard,lambda request:send_request(session,request),
                                    validate_repeatable_navigate_response)
            return guarded_send(report,args.execute,args.action,(x,y),fresh_guard,lambda request:send_request(session,request))
        def deny(self,*args):return False
        start_app=stop_app=swipe=touch_down=touch_move=touch_up=deny
        click_key=input_text=key_down=key_up=scroll=relative_move=deny
        def shell(self,*args):return None
    controller=Controller()
    require(controller.set_screenshot_use_raw_size(True) and controller.post_connection().wait().succeeded,'Maa控制器初始化失败')
    resource,tasker=Resource(),Tasker()
    with tempfile.TemporaryDirectory(prefix='sc-daily-navigation-') as temporary:
        bundle=Path(temporary)
        (bundle/'pipeline').mkdir()
        path=bundle/'pipeline/daily.json'
        # Offline simulations may reach Click, but have no browser/input session.
        path.write_text(json.dumps(fixed_pipeline(args.action,True)),encoding='utf-8')
        require(resource.post_bundle(bundle).wait().succeeded,'Pipeline加载失败')
        for scene in data['scenes']:
            require(resource.override_image(scene['name']+'.png',read_png(template_path(scene,base),expected_size=None)),'模板加载失败')
        require(tasker.bind(resource,controller) and tasker.inited,'Maa任务初始化失败')
        head=PREFIX+args.action+'.'
        def task():
            job=tasker.post_task(head+'Observe')
            deadline=time.monotonic()+20
            while not job.done:
                if time.monotonic()>deadline:
                    tasker.post_stop().wait()
                    raise RuntimeError('任务超时，不重试')
                time.sleep(.02)
            detail=job.get()
            trace=[{'name':n.name,'completed':n.completed,'hit':n.recognition.hit if n.recognition else None,
                    'action_success':n.action.success if n.action else None} for n in detail.nodes] if detail else []
            return job.succeeded,trace
        samples=data['samples']
        if args.offline_proof:
            require(args.offline_proof.resolve().is_relative_to((ROOT/'dev_assets/private').resolve()),'离线证明须在private')
            report['offline']=validate_offline_proof(load_json(args.offline_proof),args.action,digest,fore_digest,[s['id'] for s in samples],stamp)
            report['offline_reused']=True
            samples=[]
        for sample in samples:
            controller.image=read_png(base/sample['image'])
            controller.clicks=[]
            hits,state=classify(controller.image)
            expected=args.action in truth[sample['image']]['actions']
            permit=actionable(args.action,hits,state)
            succeeded,trace=task()
            wanted=[list(DAILY_ACTION_POINTS[args.action])] if expected else []
            # Pipeline positives AND the separate foreground rule must agree.
            passed=succeeded and permit==expected and controller.clicks==wanted
            report['offline'].append({'id':sample['id'],'passed':passed,'expected':expected,'allowed':permit,'clicks':controller.clicks,'trace':trace})
        require(all(s['passed'] for s in report['offline']) and reco.input_attempts==0,'离线识别/完整Pipeline/前景预期不符')
        if args.offline_only:
            report.update(passed=True,reason='offline_pipeline_verified')
            return
        if args.offline_proof:
            # Decode and bind the hash-checked templates before acquiring any
            # live frame, so the first guard fits the five-second frame limit.
            classify(read_png(base/data['samples'][-1]['image']))
        bridge=BrowserBridge.from_file(args.session)
        status=bridge.status()
        require(status['ready'] and status.get('minimized') is False,'宿主未就绪或最小化')
        report['session_pid']=bridge.session['pid']
        if args.repeatable:
            session=repeatable_protocol.validate_session(load_json(Path(str(args.session)+repeatable_protocol.SESSION_SUFFIX)),bridge.session['pid'])
            initial=repeatable_protocol.validate_response(send_request(session,repeatable_protocol.status_request(session,args.action)))
            report['initial_repeatable_status']=initial
            require(not args.execute or repeatable_protocol.status_allows_attempt(initial),'可重复导航pending或已达上限；禁止发送')
            if args.resolve_pending_report:
                resolve_pending(args,report,session,bridge,initial,controller,classify,digest,fore_digest,producer_digest,evidence)
                return
        elif args.execute:session=validate_daily_session(load_json(Path(str(args.session)+'.daily-input.json')),bridge.session['pid'])
        evidence.mkdir(parents=True)
        controller.offline=False
        require(resource.override_pipeline(pipeline),'实时Pipeline配置失败')
        succeeded,trace=task()
        report['node_trace']=trace
        end=head+('Click' if args.execute else 'Guard')
        expected_trace=[head+'Observe',end]
        if args.execute and ACTION_GROUPS[args.action]=='Home':expected_trace=[head+'Observe',head+'Guard',PREFIX+'HomeUnobstructed',end]
        positive=succeeded and [n['name'] for n in trace]==expected_trace and all(n['completed'] and (n['hit'] or n['name']==PREFIX+'HomeUnobstructed') and n['action_success'] for n in trace)
        report.update(passed=bool(positive and not args.execute),reason='readonly_positive_guard' if positive else 'unknown_or_refused')
        if args.execute:
            report.update(passed=False,reason='postcondition_unverified_no_retry',postcondition_checks=[])
            if positive and report.get('response',{}).get('attempted') is True:
                for _ in range(20):
                    image=controller.screencap()
                    require(image.shape==(720,1280,3),'后置截图失败')
                    hits,state=classify(image)
                    verified=postcondition(args.action,hits,state) and chain_progressed(args.action,hits,image,report['frames'],report.get('pre_frame_sha256'))
                    report['postcondition_checks'].append({'recognitions':hits,'foreground':state,'verified':verified})
                    if verified and args.repeatable:
                        # Leave the host entry pending unless the proof is registered.
                        confirm_repeatable(report,args,session,bridge,report['frames'][-1],hits,state,digest,fore_digest,producer_digest,send_request)
                        report.update(passed=True,reason='repeatable_postcondition_confirmed')
                        break
                    if verified:
                        report.update(passed=True,reason='navigation_postcondition_verified')
                        break
                    if args.repeatable and args.action in interrupt.INTERRUPTIBLE_ACTIONS:
                        decision=interruption_check(args.action,hits,state,report.get('pre_frame_sha256'),report['frames'][-1]['sha256'],
                                                    report.get('session_pid'),session_pid_now(args))
                        report['postcondition_checks'][-1]['interruption']=decision
                    # Button effects can precede navigation without a loading
                    # overlay. Only read again; never resend the gesture.
                    time.sleep(.5)
                else:
                    record_live_interruption(report,args,session,bridge,hits,state,digest,fore_digest,producer_digest)

def main():
    parser=argparse.ArgumentParser(description='真实日常固定导航实验：默认只读，不消耗资源。')
    for name in ('manifest','sources','foreground-manifest','foreground-contracts','report'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--session',type=Path)
    parser.add_argument('--action',choices=ACTION_GROUPS,required=True)
    parser.add_argument('--offline-only',action='store_true')
    parser.add_argument('--offline-proof',type=Path)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--repeatable',action='store_true',help='使用独立的每日有界非消费导航账本（需宿主显式启用）')
    parser.add_argument('--resolve-pending-report',type=Path,
                        help='可重复动作后置未识别时：用当前档案只读重新观察实时画面，确认后登记；不发送输入')
    args=parser.parse_args()
    if args.resolve_pending_report and (not args.repeatable or args.execute or args.offline_only):
        parser.error('解决pending须与--repeatable同用，且不能执行或离线')
    if args.resolve_pending_report and not args.resolve_pending_report.resolve().is_relative_to((ROOT/'dev_assets/private').resolve()):
        parser.error('pending报告须在private')
    if args.repeatable and (args.offline_only or args.action not in repeatable_protocol.ACTIONS):parser.error('可重复模式仅限实机白名单非消费动作')
    if args.execute and args.offline_only or not args.offline_only and args.session is None:parser.error('离线禁止执行；实机需要会话')
    if args.offline_only and args.offline_proof:parser.error('生成离线证明时不能复用旧证明')
    private_output(args.report)
    report={'passed':False,'input_attempts':0,'input_enabled':args.execute,'offline_only':args.offline_only,'action':args.action,'resource_consumption':False,'frames':[],'node_trace':[]}
    if args.repeatable:report['repeatable']=True
    try:run(args,report)
    except Exception as exc:report.update(passed=False,reason='refused_or_uncertain_no_retry',error=str(exc))
    with args.report.open('x',encoding='utf-8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps({k:report[k] for k in ('passed','reason','input_attempts')}))
    return 0 if report['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
