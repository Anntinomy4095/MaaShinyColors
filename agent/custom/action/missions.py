"""Default-closed AgentServer registrations; Pipeline owns every daily claim click."""
from maa.agent.agent_server import AgentServer
from agent.core import mission_claim_actions as actions


@AgentServer.custom_action("SC.Missions.Begin")
class MissionBegin(actions.MissionBeginAction):
    pass
@AgentServer.custom_action("SC.Missions.Read")
class MissionRead(actions.MissionReadAction):
    pass
@AgentServer.custom_action("SC.Missions.Intent")
class MissionIntent(actions.MissionIntentAction):
    pass
@AgentServer.custom_action("SC.Missions.Receipt")
class MissionReceipt(actions.MissionReceiptAction):
    pass
@AgentServer.custom_action("SC.Missions.Confirm")
class MissionConfirm(actions.MissionConfirmAction):
    pass
@AgentServer.custom_action("SC.Missions.Done")
class MissionDone(actions.MissionDoneAction):
    pass
@AgentServer.custom_action("SC.Missions.Stop")
class MissionStop(actions.MissionStopAction):
    pass
@AgentServer.custom_recognition("SC.Missions.Ready")
class MissionReady(actions.MissionReadyRecognition):
    pass
@AgentServer.custom_action("SC.Missions.BulkRead")
class MissionBulkRead(actions.MissionBulkReadAction):
    pass
@AgentServer.custom_action("SC.Missions.BulkIntent")
class MissionBulkIntent(actions.MissionBulkIntentAction):
    pass
@AgentServer.custom_action("SC.Missions.BulkResult")
class MissionBulkResult(actions.MissionBulkResultAction):
    pass
@AgentServer.custom_action("SC.Missions.BulkConfirm")
class MissionBulkConfirm(actions.MissionBulkConfirmAction):
    pass
