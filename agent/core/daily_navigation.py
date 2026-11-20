"""Fixed daily navigation contracts; no resource spending or arbitrary coordinates."""
from pathlib import Path
import hashlib

from agent.core.daily_config import load_json
from tools.replay_screenshots import require, validate_manifest, template_path

PREFIX = 'SC.DailyNavigation.'
GROUPS = {
    'Home': ('SC.Research.HomePostcondition.Mission','SC.Research.HomePostcondition.MissionNative', 'SC.Research.HomePostcondition.FesText'),
    'Announcement': tuple('SC.Research.Announcement.' + n for n in ('Title', 'ListHeading', 'TopicsSelected', 'ReturnTarget')),
    'Reward': tuple('SC.Research.StartupPopup.Reward.' + n for n in ('Title', 'Body', 'OK')),
    'Loading': tuple('SC.Research.StartupPopup.Loading.' + n for n in ('Text', 'Feather')),
    'Mission': tuple('SC.Research.DailyMission.' + n for n in ('Title', 'DailyTab', 'Return')),
    'IdolPortal':tuple('SC.Research.DailyIdol.'+n for n in ('Title','TrainingEntry','TrainingEntryNative','Return')),
    'TrainingList':tuple('SC.Research.DailyTrainingList.'+n for n in ('Title','SupportTab','SupportTabNative','AsahiPortrait')),
    'TrainingSelected':tuple('SC.Training.Zero.'+n for n in ('Title','CardName','Portrait')),
    'MissionCompleted':tuple('SC.Research.MissionCompleted.'+n for n in ('Title','Body','LoginObjective','MissionTarget')),
    'MissionHelp':tuple('SC.Research.MissionHelp.'+n for n in ('Title','Page','Close')),
    'FesMenu':tuple('SC.Research.FesMenu.'+n for n in ('MatchTarget','GradeTarget','Border')),
    'MatchMain':tuple('SC.Research.MatchMain.'+n for n in ('Title','AreaPrompt','SkipTarget')),
    'MatchHelp':tuple('SC.Research.MatchHelp.'+n for n in ('Title','Page','Close')),
    'MatchSkip':tuple('SC.Research.MatchSkip.'+n for n in ('Title','Filter','Cancel','Clear','CostLabel')),
    'Pickup':tuple('SC.Research.Pickup.'+n for n in ('Title','Close','Details')),
    'Story':tuple('SC.Research.DailyStory.'+n for n in ('Skip','Log','Hide')),
    'TrainingHelp':tuple('SC.Research.TrainingHelp.'+n for n in ('Title','Page','Close')),
    # Title screen; anchors avoid the animated character art (button text, logo, copyright line).
    'StartScreen':tuple('SC.Research.StartScreen.'+n for n in ('TouchToStart','Logo','Copyright')),
}
# FesText stops above the Fes participation gauge, whose fill length changes with the rights count.
ROIS = dict(zip(GROUPS['Home'], ([25,394,76,70],[25,394,76,70], [1150,645,80,25])))
ROIS.update(zip(GROUPS['Announcement'], ([45,18,125,45],[620,108,280,43],[15,87,176,86],[17,620,84,83])))
ROIS.update(zip(GROUPS['Reward'], ([408,151,184,37],[491,241,301,31],[556,481,169,81])))
ROIS.update(zip(GROUPS['Loading'], ([826,660,306,47],[1150,613,67,62])))
ROIS.update(zip(GROUPS['Mission'], ([38,18,161,42],[42,94,115,33],[17,620,84,83])))
ROIS.update(zip(GROUPS['IdolPortal'],([40,18,141,44],[409,285,218,50],[409,285,218,50],[17,620,84,83])))
ROIS.update(zip(GROUPS['TrainingList'],([43,17,328,48],[39,98,121,38],[39,98,121,38],[409,189,101,87])))
ROIS.update(zip(GROUPS['TrainingSelected'],([48,24,177,31],[159,96,451,31],[40,102,99,75])))
ROIS.update(zip(GROUPS['MissionCompleted'],([405,90,253,40],[379,153,533,61],[391,252,264,34],[658,540,171,81])))
ROIS.update(zip(GROUPS['MissionHelp'],([191,79,438,39],[1125,78,86,41],[558,562,167,78])))
ROIS.update(zip(GROUPS['FesMenu'],([976,504,136,74],[1128,549,126,26],[1171,590,35,15])))
ROIS.update(zip(GROUPS['MatchMain'],([49,23,170,34],[97,157,236,37],[1126,518,130,33])))
ROIS.update(zip(GROUPS['MatchHelp'],([187,77,339,42],[1107,78,107,43],[558,562,167,78])))
ROIS.update(zip(GROUPS['MatchSkip'],([91,51,130,40],[1020,131,166,44],[380,582,166,80],[560,582,166,80],[1044,588,172,25])))
ROIS.update(zip(GROUPS['Pickup'],([465,52,343,39],[1091,48,42,38],[552,558,101,33])))
ROIS.update(zip(GROUPS['Story'],([1180,17,82,80],[1173,423,82,81],[1173,514,82,79])))
ROIS.update(zip(GROUPS['TrainingHelp'],([190,80,342,37],[1125,78,86,41],[558,562,167,78])))
# Measured on the native 1280x720 frame manual-capture-kept-20261002/touch-to-start-native-20261002.png.
ROIS.update(zip(GROUPS['StartScreen'],([474,611,332,37],[440,497,402,52],[586,684,672,27])))
ACTION_GROUPS = {'reward-ack':'Reward', 'announcement-back':'Announcement', 'home-mission':'Home', 'mission-back':'Mission',
    'home-idol':'Home','idol-training':'IdolPortal','training-select-asahi':'TrainingList',
    'mission-complete-to-mission':'MissionCompleted',
    'mission-help-close':'MissionHelp','home-fes':'Home','fes-match-entry':'FesMenu',
    'match-help-close':'MatchHelp','match-skip-open':'MatchMain','match-skip-cancel':'MatchSkip',
    'match-back':'MatchMain','training-back':'TrainingSelected',
    'pickup-close':'Pickup','story-skip-open':'Story','training-help-close':'TrainingHelp',
    'training-list-back':'TrainingList','idol-back':'IdolPortal','game-start':'StartScreen'}
POST_GROUPS = {'reward-ack':('Home','Announcement','Reward','MissionCompleted'), 'announcement-back':('Home',), 'home-mission':('Mission',), 'mission-back':('Home',),
    'home-idol':('IdolPortal',),'idol-training':('TrainingList','TrainingHelp'),'training-select-asahi':('TrainingSelected',),
    'mission-complete-to-mission':('Mission','MissionHelp'),
    'mission-help-close':('Mission',),'home-fes':('FesMenu',),'fes-match-entry':('MatchMain','MatchHelp'),
    'match-help-close':('MatchMain',),'match-skip-open':('MatchSkip',),'match-skip-cancel':('MatchMain',),
    'match-back':('Home',),'training-back':('TrainingList',),
    'pickup-close':('Home',),'story-skip-open':('Home','Mission','MissionHelp','MatchMain','MatchHelp'),
    'training-help-close':('TrainingList',),'training-list-back':('IdolPortal',),'idol-back':('Home',),
    'game-start':('Loading','Reward','Announcement','Home','MissionCompleted','Pickup')}
FIXTURE = Path(__file__).resolve().parents[2] / 'tests/fixtures/daily_navigation/pipeline.json'
# Daily notices (e.g. ピックアップコンテンツ) can open over Home on any arrival.
for _action,_groups in list(POST_GROUPS.items()):
    if 'Home' in _groups and 'Pickup' not in _groups:POST_GROUPS[_action]=_groups+('Pickup',)
UNDERLYING_GROUPS={'MissionCompleted':('Home',),'Reward':('Home','Story'),'MissionHelp':('Mission',),'Pickup':('Home',),
    'FesMenu':('Home',),'MatchHelp':('MatchMain',),'MatchSkip':('MatchMain',),'TrainingHelp':('TrainingList',)}

def home_blocking_anchors():
    return [PREFIX+'Anchor.'+n for group in GROUPS if group!='Home' for n in GROUPS[group]
            if not n.endswith(('.ReturnTarget','.Return','.OK','.Close','.Page'))]

def complete_group(group, hits):
    if group=='Home':
        return _complete_names((GROUPS[group][-1],),hits) and any(_complete_names((n,),hits) for n in GROUPS[group][:-1])
    if group in ('IdolPortal','TrainingList'):
        return _complete_names((GROUPS[group][0],GROUPS[group][-1]),hits) and any(_complete_names((n,),hits) for n in GROUPS[group][1:-1])
    if group == 'MissionCompleted':
        # The objective row is per-mission text (login, training, ...), not an identity anchor.
        return _complete_names(tuple(n for n in GROUPS[group] if not n.endswith('.LoginObjective')),hits)
    if group == 'Announcement':
        title,heading,topics,target=GROUPS[group]
        return _complete_names((title,target),hits) and sum(_complete_names((n,),hits) for n in (heading,topics)) == 1
    return _complete_names(GROUPS[group],hits)

def _complete_names(names, hits):
    selected = [r for r in hits if r.get('scene') in names]
    return len(selected) == len(names) and {r['scene'] for r in selected} == set(names) and all(
        r.get('hit') is True and type(r.get('score')) in (float,int) and .95 <= r['score'] <= 1
        and type(r.get('raw_detail', {}).get('filtered')) is list and len(r['raw_detail']['filtered']) == 1 for r in selected)

# Exclusion only: a foreign anchor this close to a match may be a dimmed or differently rendered
# overlay (observed 0.869 for a modal title over a Home that still matched at 0.95). Positive
# guards keep the 0.95 threshold; this can only refuse more.
PRESENCE_SCORE = .80

def any_group(group, hits):
    # Shared Return / OK buttons do not identify an unrelated foreground.
    names = [n for n in GROUPS[group] if not n.endswith(('.ReturnTarget','.Return','.OK','.Close','.Page'))]
    if group=='TrainingSelected':names=[n for n in names if not n.endswith('.Title')]
    return any(r.get('hit') is True or type(r.get('score')) in (float, int) and r['score'] >= PRESENCE_SCORE
               for r in hits if r.get('scene') in names)

def actionable(action, hits, foreground):
    if action not in ACTION_GROUPS:return False
    selected = ACTION_GROUPS[action]
    if not foreground_allowed(selected,foreground):return False
    if not complete_group(selected,hits): return False
    # This identified modal is drawn over Home; dimmed Fes text can still match.
    # Its four own anchors must all match before allowing that underlying page.
    underneath=UNDERLYING_GROUPS.get(selected,())
    return not any(any_group(g,hits) for g in GROUPS if g != selected and g not in underneath)

def postcondition(action,hits,foreground):
    if action not in POST_GROUPS:return False
    complete = [g for g in GROUPS if complete_group(g,hits)]
    candidates=[g for g in complete if g in POST_GROUPS[action]]
    # A complete popup over its declared underlying page is the popup state, not two states.
    candidates=[g for g in candidates if not any(g in UNDERLYING_GROUPS.get(o,()) for o in candidates if o!=g)]
    if len(candidates)!=1:return False
    selected=candidates[0];underneath=UNDERLYING_GROUPS.get(selected,())
    if not foreground_allowed(selected,foreground):return False
    return not any(any_group(g,hits) for g in GROUPS if g != selected and g not in underneath)

def foreground_allowed(group,foreground):
    if foreground.get('state')=='no_known_popup':return True
    # The daily login board under the reward popup has a SKIP identical to the story Skip;
    # only that lone partial Story match is tolerated, and only for the Reward popup itself.
    if (group=='Reward' and foreground.get('state')=='partial' and foreground.get('complete_contracts')==[]
            and foreground.get('partial_contracts')==['SC.Foreground.Story']):return True
    expected={'MissionHelp':'SC.Foreground.MissionHelp','Story':'SC.Foreground.Story'}.get(group)
    return (expected is not None and foreground.get('state')=='known_popup' and foreground.get('popup')==expected
            and foreground.get('complete_contracts')==[expected] and foreground.get('partial_contracts')==[])

def load_contract(manifest,sources,missing_scenes=frozenset(),rebased_scenes=frozenset()):
    # missing_scenes is only for the offline profile builder reading an older base that predates
    # newly registered anchors; every runtime caller keeps the exact whitelist.
    manifest,sources=Path(manifest).resolve(),Path(sources).resolve()
    # rebased_scenes: builder-only, base anchors whose code ROI changed and are re-cropped now.
    missing,rebased=set(missing_scenes),set(rebased_scenes)
    require(missing<=set(ROIS) and rebased<=set(ROIS),'缺失或重定锚点不在白名单')
    data=validate_manifest(load_json(manifest),manifest.parent)
    origin=load_json(sources)
    require({s['name'] for s in data['scenes']} == set(ROIS)-missing,'日常导航锚点白名单不符')
    templates={r['template']:r['sha256'] for r in origin['templates']}
    samples={r['image']:r for r in origin['samples']}
    for scene in data['scenes']:
        require(scene['recognition']=='TemplateMatch' and (scene['roi']==ROIS[scene['name']] or scene['name'] in rebased) and scene['threshold']==.95,'禁止变更导航守卫')
        require(hashlib.sha256(template_path(scene,manifest.parent).read_bytes()).hexdigest()==templates.get(scene['template']),'模板哈希不符')
    require(set(samples)=={s['image'] for s in data['samples']},'样本来源不完整')
    for sample in data['samples']:
        proof=samples[sample['image']]
        require(hashlib.sha256((manifest.parent/sample['image']).read_bytes()).hexdigest()==proof['sha256'],'样本哈希不符')
        require(type(proof['actions']) is list and set(proof['actions'])<=set(ACTION_GROUPS),'样本动作真值无效')
    return data,samples,hashlib.sha256(manifest.read_bytes()+sources.read_bytes()).hexdigest()

def fixed_pipeline(action,execute=False):
    from tools.navigation_protocol import ALL_DAILY_ACTION_POINTS as DAILY_ACTION_POINTS
    require(type(execute) is bool and action in ACTION_GROUPS,'只允许已采样的固定导航动作')
    data=load_json(FIXTURE)
    presence=[PREFIX+'Presence.'+a[len(PREFIX+'Anchor.'):] for a in home_blocking_anchors()]
    required={PREFIX+'Anchor.'+n for n in ROIS} | set(presence) | {PREFIX+action+'.'+n for action in ACTION_GROUPS for n in ('Observe','Guard','Click','Unknown','Error')} | {PREFIX+'AnnouncementVariant',PREFIX+'HomeVariant',PREFIX+'IdolTrainingVariant',PREFIX+'TrainingSupportVariant',PREFIX+'HomeUnobstructed',PREFIX+'HomeBlocked'}
    require(set(data)==required,'导航Pipeline节点不符')
    for name,roi in ROIS.items():
        require(data[PREFIX+'Anchor.'+name]=={'recognition':'TemplateMatch','template':name+'.png','roi':roi,'threshold':.95,'method':5,'action':'DoNothing','pre_delay':0,'post_delay':0},'导航锚点被改变')
    for key,group in ACTION_GROUPS.items():
        head=PREFIX+key+'.'
        anchors=[PREFIX+'Anchor.'+n for n in GROUPS[group]]
        if group=='Announcement':
            anchors=[PREFIX+'Anchor.'+GROUPS[group][0],PREFIX+'Anchor.'+GROUPS[group][-1],PREFIX+'AnnouncementVariant']
        if group=='Home':anchors=[PREFIX+'Anchor.'+GROUPS[group][-1],PREFIX+'HomeVariant']
        if group=='MissionCompleted':anchors=[a for a in anchors if not a.endswith('.LoginObjective')]
        if group=='IdolPortal':anchors=[PREFIX+'Anchor.'+GROUPS[group][0],PREFIX+'Anchor.'+GROUPS[group][-1],PREFIX+'IdolTrainingVariant']
        if group=='TrainingList':anchors=[PREFIX+'Anchor.'+GROUPS[group][0],PREFIX+'Anchor.'+GROUPS[group][-1],PREFIX+'TrainingSupportVariant']
        base={'pre_delay':0,'post_delay':0}
        require(data[head+'Guard']==dict(base,recognition='And',all_of=anchors,action='DoNothing'),'导航需全锚点')
        require(data[head+'Click']==dict(base,enabled=False,recognition='And',all_of=anchors,action='Click',target=[*DAILY_ACTION_POINTS[key],1,1],on_error=[head+'Error']),'固定动作必须默认关闭')
        require(data[head+'Observe']==dict(base,recognition='DirectHit',action='DoNothing',next=[head+'Guard',head+'Unknown'],on_error=[head+'Error'],timeout=1000,rate_limit=0),'入口不符')
        require(data[head+'Unknown']==dict(base,recognition='Or',any_of=[head+'Guard'],inverse=True,action='StopTask'),'未知必须停止')
        require(data[head+'Error']==dict(base,recognition='DirectHit',action='StopTask'),'失败必须停止')
    require(data[PREFIX+'AnnouncementVariant']=={'recognition':'Or','any_of':[PREFIX+'Anchor.'+n for n in GROUPS['Announcement'][1:3]],'action':'DoNothing','pre_delay':0,'post_delay':0},'公告分类变体白名单不符')
    require(data[PREFIX+'HomeVariant']=={'recognition':'Or','any_of':[PREFIX+'Anchor.'+n for n in GROUPS['Home'][:-1]],'action':'DoNothing','pre_delay':0,'post_delay':0},'主页固定字体变体白名单不符')
    require(data[PREFIX+'IdolTrainingVariant']=={'recognition':'Or','any_of':[PREFIX+'Anchor.'+n for n in GROUPS['IdolPortal'][1:-1]],'action':'DoNothing','pre_delay':0,'post_delay':0},'训练入口固定字体变体白名单不符')
    require(data[PREFIX+'TrainingSupportVariant']=={'recognition':'Or','any_of':[PREFIX+'Anchor.'+n for n in GROUPS['TrainingList'][1:-1]],'action':'DoNothing','pre_delay':0,'post_delay':0},'S卡页签固定字体变体白名单不符')
    # Mirrors any_group: overlays are excluded at the lower presence score.
    for node,anchor in zip(presence,home_blocking_anchors()):
        require(data[node]==dict(data[anchor],threshold=PRESENCE_SCORE) and data[anchor]['threshold']==.95,'主页弹层存在性锚点不符')
    require(data[PREFIX+'HomeUnobstructed']=={'recognition':'Or','any_of':presence,'inverse':True,'action':'DoNothing','pre_delay':0,'post_delay':0},'主页弹层必须先排除')
    require(data[PREFIX+'HomeBlocked']=={'recognition':'Or','any_of':presence,'action':'StopTask','pre_delay':0,'post_delay':0},'主页弹层必须停止')
    if execute:
        data[PREFIX+action+'.Click']['enabled']=True
        if ACTION_GROUPS[action]=='Home':
            data[PREFIX+action+'.Guard']['next']=[PREFIX+'HomeUnobstructed',PREFIX+'HomeBlocked']
            data[PREFIX+'HomeUnobstructed']['next']=[PREFIX+action+'.Click']
            data[PREFIX+'HomeUnobstructed']['on_error']=[PREFIX+action+'.Error']
        else:data[PREFIX+action+'.Observe']['next'][0]=PREFIX+action+'.Click'
    return data
