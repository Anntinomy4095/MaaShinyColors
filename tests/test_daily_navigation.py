import copy
import unittest
from unittest.mock import Mock,patch
from agent.core.daily_navigation import GROUPS,PREFIX,ROIS,complete_group,any_group,actionable,postcondition,fixed_pipeline
from tools.run_daily_navigation import guarded_send,validate_offline_proof

class DailyNavigationTests(unittest.TestCase):
    def test_cached_offline_proof_rejects_changed_code_action_or_samples(self):
        proof={'passed':True,'offline_only':True,'input_enabled':False,'input_attempts':0,'action':'home-mission',
            'configuration_sha256':'a','foreground_sha256':'b','implementation_sha256':'c','offline':[{'id':'x','passed':True}]}
        self.assertEqual(validate_offline_proof(proof,'home-mission','a','b',['x'],'c'),proof['offline'])
        for key,value in [('action','reward-ack'),('implementation_sha256','d'),('input_attempts',True),('offline',[])]:
            with self.assertRaises(ValueError):validate_offline_proof({**proof,key:value},'home-mission','a','b',['x'],'c')
    def hits(self,group):
        return [{'scene':n,'hit':n in GROUPS[group],'score':.99 if n in GROUPS[group] else .1,
                 'raw_detail':{'filtered':[{'box':[0,0,1,1]}] if n in GROUPS[group] else []}} for n in ROIS]
    def test_all_anchors_and_unique_target_required(self):
        hits=self.hits('Reward')
        self.assertTrue(actionable('reward-ack',hits,{'state':'no_known_popup'}))
        hits[-1]['hit']=False
        reward=next(r for r in hits if r['scene']==GROUPS['Reward'][-1])
        reward['raw_detail']['filtered']=[{},{}]
        self.assertFalse(complete_group('Reward',hits))
    def test_game_start_requires_all_start_anchors_and_unique_landing(self):
        clear={'state':'no_known_popup'}
        hits=self.hits('StartScreen')
        self.assertTrue(actionable('game-start',hits,clear))
        for name in GROUPS['StartScreen']:
            partial=copy.deepcopy(hits);next(r for r in partial if r['scene']==name)['hit']=False
            next(r for r in partial if r['scene']==name)['score']=.5
            self.assertFalse(actionable('game-start',partial,clear))
        self.assertFalse(actionable('game-start',hits,{'state':'known_popup'}))
        # A Loading text over the copyright, or any other foreign anchor, refuses the click.
        blocked=copy.deepcopy(hits);next(r for r in blocked if r['scene']==GROUPS['Loading'][0])['score']=.85
        self.assertFalse(actionable('game-start',blocked,clear))
        for group in ('Loading','Reward','Announcement','Home','MissionCompleted','Pickup'):
            landing=self.hits(group)
            if group=='Announcement':
                topics=next(r for r in landing if r['scene']==GROUPS[group][2]);topics.update(hit=False,score=.1)
            self.assertTrue(postcondition('game-start',landing,clear),group)
        self.assertFalse(postcondition('game-start',hits,clear))
        self.assertFalse(postcondition('game-start',self.hits('Mission'),clear))
        # Still on the title screen under a landing page: refused.
        mixed=self.hits('Home');next(r for r in mixed if r['scene']==GROUPS['StartScreen'][1])['score']=.9
        self.assertFalse(postcondition('game-start',mixed,clear))
        self.assertTrue(any_group('StartScreen',hits))
    def test_other_foreground_partial_and_conflicting_scene_blocks(self):
        for state in ('known_popup','partial','conflict','invalid_dimensions'):
            self.assertFalse(actionable('home-mission',self.hits('Home'),{'state':state}))
        hits=self.hits('Home')
        next(r for r in hits if r['scene']==GROUPS['Loading'][0])['hit']=True
        self.assertFalse(actionable('home-mission',hits,{'state':'no_known_popup'}))
    def test_shared_return_is_not_unrelated_notice_identity(self):
        hits=self.hits('Mission')
        next(r for r in hits if r['scene']==GROUPS['Announcement'][-1])['hit']=True
        self.assertTrue(actionable('mission-back',hits,{'state':'no_known_popup'}))
    def test_mission_completed_requires_own_target_and_blocks_home(self):
        hits=self.hits('MissionCompleted')
        self.assertTrue(actionable('mission-complete-to-mission',hits,{'state':'no_known_popup'}))
        next(r for r in hits if r['scene']==GROUPS['Home'][-1])['hit']=True
        self.assertTrue(actionable('mission-complete-to-mission',hits,{'state':'no_known_popup'}))
        target=next(r for r in hits if r['scene']==GROUPS['MissionCompleted'][-1])
        target['hit']=False
        self.assertFalse(actionable('mission-complete-to-mission',hits,{'state':'no_known_popup'}))
        hits=self.hits('Home')
        next(r for r in hits if r['scene']==GROUPS['MissionCompleted'][0])['hit']=True
        self.assertFalse(actionable('home-mission',hits,{'state':'no_known_popup'}))
    def test_near_match_overlay_anchor_blocks_home_without_lowering_positive_threshold(self):
        # Observed live: ミッション達成 modal title 0.869 over a Home whose anchors still matched.
        hits=self.hits('Home')
        title=next(r for r in hits if r['scene']==GROUPS['MissionCompleted'][0])
        title['score']=.869
        self.assertFalse(postcondition('announcement-back',hits,{'state':'no_known_popup'}))
        self.assertFalse(actionable('home-mission',hits,{'state':'no_known_popup'}))
        title['score']=.79
        self.assertTrue(postcondition('announcement-back',hits,{'state':'no_known_popup'}))
        own=next(r for r in hits if r['scene']==GROUPS['Home'][-1])
        own.update(hit=False,score=.94)
        self.assertFalse(actionable('home-mission',hits,{'state':'no_known_popup'}))
    def test_complete_popup_over_its_declared_page_is_the_popup_state(self):
        hits=self.hits('MissionCompleted')
        for r in hits:
            if r['scene'] in GROUPS['Home']:
                r.update(hit=True,score=.99,raw_detail={'filtered':[{'box':[0,0,1,1]}]})
        self.assertTrue(postcondition('game-start',hits,{'state':'no_known_popup'}))
        # An undeclared second complete page still refuses.
        for r in hits:
            if r['scene'] in GROUPS['Loading']:
                r.update(hit=True,score=.99,raw_detail={'filtered':[{'box':[0,0,1,1]}]})
        self.assertFalse(postcondition('game-start',hits,{'state':'no_known_popup'}))
    def test_post_requires_expected_unique_state_and_no_foreground(self):
        self.assertTrue(postcondition('home-mission',self.hits('Mission'),{'state':'no_known_popup'}))
        self.assertFalse(postcondition('home-mission',self.hits('Home'),{'state':'no_known_popup'}))
        self.assertFalse(postcondition('home-mission',self.hits('Mission'),{'state':'partial'}))
    def test_default_disabled_and_disk_mutations_rejected(self):
        data=fixed_pipeline('home-mission')
        self.assertFalse(data[PREFIX+'home-mission.Click']['enabled'])
        for key,value in [('enabled',True),('target',[640,520,1,1]),('all_of',[])]:
            edited=copy.deepcopy(data)
            edited[PREFIX+'home-mission.Click'][key]=value
            with patch('agent.core.daily_navigation.load_json',return_value=edited),self.assertRaises(ValueError):fixed_pipeline('home-mission',True)
    def test_unimplemented_action_and_truthy_flag_refused(self):
        for action,enabled in [('login-board-skip',True),('home-mission',1)]:
            with self.assertRaises(ValueError):fixed_pipeline(action,enabled)
    def test_portal_returns_bind_own_group_variant_and_post_group(self):
        cases={'training-list-back':('TrainingList','IdolPortal','TrainingSupportVariant',
                'SC.Research.DailyTrainingList.SupportTab','SC.Research.DailyTrainingList.SupportTabNative'),
               'idol-back':('IdolPortal','Home','IdolTrainingVariant',
                'SC.Research.DailyIdol.TrainingEntry','SC.Research.DailyIdol.TrainingEntryNative')}
        for action,(group,post,variant,primary,native) in cases.items():
            with self.subTest(action=action):
                data=fixed_pipeline(action)
                click=data[PREFIX+action+'.Click']
                self.assertFalse(click['enabled'])
                self.assertEqual(click['target'],[57,662,1,1])
                self.assertIn(PREFIX+variant,click['all_of'])
                for drop in (primary,native):
                    hits=self.hits(group)
                    next(r for r in hits if r['scene']==drop)['hit']=False
                    self.assertTrue(actionable(action,hits,{'state':'no_known_popup'}))
                hits=self.hits(group)
                for drop in (primary,native):next(r for r in hits if r['scene']==drop)['hit']=False
                self.assertFalse(actionable(action,hits,{'state':'no_known_popup'}))
                self.assertFalse(actionable(action,self.hits(post),{'state':'no_known_popup'}))
                self.assertTrue(postcondition(action,self.hits(post),{'state':'no_known_popup'}))
                self.assertFalse(postcondition(action,self.hits(group),{'state':'no_known_popup'}))
                self.assertTrue(fixed_pipeline(action,True)[PREFIX+action+'.Click']['enabled'])
    def test_lost_response_never_repeats(self):
        report={'input_attempts':0}
        send=Mock(side_effect=TimeoutError('lost'))
        guard=Mock(return_value={})
        self.assertFalse(guarded_send(report,True,'reward-ack',(640,520),guard,send))
        self.assertFalse(guarded_send(report,True,'reward-ack',(640,520),guard,send))
        self.assertEqual(report['input_attempts'],1)
        self.assertEqual(send.call_count,1)
    def test_guard_failure_disabled_and_arbitrary_point_send_nothing(self):
        send=Mock()
        for execute,point in [(False,(640,520)),(1,(640,520)),(True,(12,34))]:
            self.assertFalse(guarded_send({'input_attempts':0},execute,'reward-ack',point,Mock(),send))
        self.assertFalse(guarded_send({'input_attempts':0},True,'reward-ack',(640,520),Mock(side_effect=ValueError('stale')),send))
        send.assert_not_called()

class RewardChainTests(unittest.TestCase):
    def test_chained_reward_needs_changed_item_area(self):
        import numpy as np, tempfile
        from pathlib import Path
        from PIL import Image
        from tools.run_daily_navigation import chain_progressed, REWARD_ITEM_ROI
        hits=[{'scene':n,'hit':n in GROUPS['Reward'],'score':.99 if n in GROUPS['Reward'] else .1,
               'raw_detail':{'filtered':[{'box':[0,0,1,1]}] if n in GROUPS['Reward'] else []}} for n in ROIS]
        pre=np.full((720,1280,3),200,dtype=np.uint8)
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'pre.png'; Image.fromarray(pre).save(p)
            frames=[{'image':str(p),'sha256':'a'*64}]
            self.assertFalse(chain_progressed('reward-ack',hits,pre.copy(),frames,'a'*64))
            post=pre.copy(); x,y,w,h=REWARD_ITEM_ROI; post[y:y+h//2,x:x+w]=0
            self.assertTrue(chain_progressed('reward-ack',hits,post,frames,'a'*64))
            self.assertFalse(chain_progressed('reward-ack',hits,post,frames,'b'*64))
            self.assertTrue(chain_progressed('home-mission',hits,pre,frames,'a'*64))
