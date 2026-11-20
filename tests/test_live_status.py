"""Live status feed for the UI 「运行中」 view (agent/core/live_status.py, wing_live_feed.py)."""
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from agent.core import live_status as ls  # noqa: E402
from agent.core import run_control as rc  # noqa: E402
from agent.core import wing_live_feed as wlf  # noqa: E402
import live_status_replay  # noqa: E402

# Text a user should never see in the live view: node names, intent ids, reason codes, paths.
JARGON = re.compile(r"SC\.|schedule_|hub_|choice_(left|center|right)|sp_learn|_unconfirmed|act_refused|dev_assets|[A-Z]:\\\\")


class FakeTimer:
    def __init__(self, sched, delay, fn):
        self.sched, self.delay, self.fn, self.cancelled = sched, delay, fn, False

    def cancel(self):
        self.cancelled = True


class Clock:
    def __init__(self):
        self.t = 100.0
        self.timers = []

    def __call__(self):
        return self.t

    def schedule(self, delay, fn):
        timer = FakeTimer(self, delay, fn)
        self.timers.append(timer)
        return timer

    def fire(self):
        live = [t for t in self.timers if not t.cancelled]
        self.timers = []
        for t in live:
            self.t += t.delay
            t.fn()


class LiveStatusTests(unittest.TestCase):
    def setUp(self):
        rc.PRIVATE.mkdir(parents=True, exist_ok=True)
        self.dir = tempfile.TemporaryDirectory(prefix="_live_status_", dir=rc.PRIVATE)
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "current.json"
        self.clock = Clock()

    def status(self):
        s = ls.LiveStatus(self.path, clock=self.clock, wall=lambda: "2026-10-10T00:00:00+00:00",
                          schedule=self.clock.schedule, labels=dict(ls.TASK_LABELS))
        self.addCleanup(s.close)
        return s

    def read(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_default_path_is_private_and_gitignored(self):
        self.assertTrue(ls.DEFAULT_PATH.is_relative_to(ROOT / "dev_assets" / "private"))
        self.assertIn("dev_assets/private/", (ROOT / ".gitignore").read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            ls.LiveStatus(ROOT / "ui" / "current.json")

    def test_queue_states_and_plain_texts(self):
        s = self.status()
        s.start("run1", ["Startup", "Shop", "Training"], 120)
        self.assertEqual(self.read()["state"], "running")
        s.observe("task.started", {"task": "Startup"})
        s.observe("task.finished", {"task": "Startup", "status": "succeeded"})
        s.observe("task.started", {"task": "Shop"})
        s.observe("click.result", {"task": "Shop", "result": "sent"})
        s.observe("click.result", {"task": "Shop", "result": "refused"})
        s.observe("task.finished", {"task": "Shop", "status": "timeout"})
        s.observe("run.finished", {"status": "failed"})
        self.clock.fire()                                 # the final state lands within the 0.5 s budget
        snap = self.read()
        self.assertEqual([t["label"] for t in snap["tasks"]], ["启动", "商店", "训练"])
        self.assertEqual([t["state"] for t in snap["tasks"]], ["done", "failed", "skipped"])
        self.assertEqual(snap["tasks"][1]["text"], "超时")
        self.assertEqual(snap["clicks"], {"used": 1, "cap": 120})
        self.assertEqual(snap["progress"], {"done": 3, "total": 3})
        self.assertEqual((snap["state"], snap["outcome"], snap["outcome_text"]), ("finished", "failed", "有未完成"))
        self.assertFalse(list(self.path.parent.glob("*.tmp")))   # atomic replace leaves no temp file

    def test_node_events_become_plain_phases_without_node_names(self):
        s = self.status()
        s.start("run1", ["Shop"])
        s.observe("task.started", {"task": "Shop"})
        self.clock.t += 1
        s.observe("node", {"task": "Shop", "node": "SC.Common.GoHome.Entry", "status": "starting"})
        snap = self.read()
        self.assertEqual(snap["step"], "商店 · 回主页")
        self.assertIsNotNone(snap["last_frame_at"])
        self.assertIsNone(JARGON.search(json.dumps(snap, ensure_ascii=False)))

    def test_a_held_step_survives_node_events_until_cleared_or_the_task_ends(self):
        s = self.status()
        s.start("run1", ["CollaboFes"])
        s.observe("task.started", {"task": "CollaboFes"})
        self.clock.t += 1
        s.hold("Collabo Fes · Vocal 满员，等重开 · 20:47 再看 · 已打 5/6 局")
        self.clock.t += 1
        s.observe("node", {"task": "CollaboFes", "node": "SC.Collabo.Wait", "status": "starting"})
        self.clock.fire()
        self.assertEqual(self.read()["step"], "Collabo Fes · Vocal 满员，等重开 · 20:47 再看 · 已打 5/6 局")
        s.hold(None)
        self.clock.t += 1
        s.observe("node", {"task": "CollaboFes", "node": "SC.Collabo.Wait", "status": "starting"})
        self.clock.fire()
        self.assertEqual(self.read()["step"], "Collabo Fes · 等待")
        s.hold("x")
        s.observe("task.finished", {"task": "CollaboFes", "status": "succeeded"})
        self.clock.fire()
        self.assertIsNone(self.read()["hold"])

    def test_writes_are_throttled_to_two_per_second_and_flushed_later(self):
        s = self.status()
        s.start("run1", ["Shop"])
        writes = s.writes
        for i in range(10):
            s.step(f"第 {i} 步")
        self.assertEqual(s.writes, writes)               # within 0.5 s: no extra write
        self.assertEqual(self.read()["step"], None)
        self.clock.fire()                                 # deferred flush carries the last change
        self.assertEqual(s.writes, writes + 1)
        self.assertEqual(self.read()["step"], "第 9 步")
        # forced updates (task boundaries) also respect the budget
        s.observe("task.started", {"task": "Shop"})
        self.assertEqual(s.writes, writes + 1)
        self.clock.fire()
        self.assertEqual(self.read()["tasks"][0]["state"], "running")

    def test_heartbeat_keeps_a_running_snapshot_fresh_and_close_cancels(self):
        stamps = iter(f"2026-10-10T00:00:{i:02d}+00:00" for i in range(60))
        s = ls.LiveStatus(self.path, clock=self.clock, wall=lambda: next(stamps), schedule=self.clock.schedule, labels={})
        s.start("run1", ["Shop"])
        first = self.read()["updated_at"]
        self.assertTrue(any(abs(t.delay - ls.HEARTBEAT_S) < 1e-6 for t in self.clock.timers))
        self.clock.fire()
        self.assertNotEqual(self.read()["updated_at"], first)
        s.close()
        self.assertTrue(all(t.cancelled for t in self.clock.timers))
        s.step("after close")
        self.assertNotEqual(self.read()["step"], "after close")

    def test_hooks_never_raise(self):
        s = self.status()
        s.observe("task.finished", {})
        s.observe("node", {"node": None})
        s.wing(lambda w: 1 / 0)
        s.update(lambda snap: snap["missing"]["x"])

    def test_run_control_forwards_events_without_an_event_file(self):
        control = rc.RunControl()
        s = self.status()
        control.live = s
        s.start(control.run_id, ["Shop"])
        control.task = "Shop"
        control.emit("task.started", task="Shop")
        control.input_event({"kind": "click", "result": "sent", "task": "Shop", "action": "shop-open"})
        self.clock.fire()
        snap = self.read()
        self.assertEqual(snap["run_id"], control.run_id)
        self.assertEqual(snap["tasks"][0]["state"], "running")
        self.assertEqual(snap["clicks"]["used"], 1)

        class Broken:
            def observe(self, *a):
                raise RuntimeError("boom")
        control.live = Broken()
        self.assertTrue(control.emit("task.finished", task="Shop", status="succeeded"))

    def test_runner_hook_is_off_unless_requested(self):
        import run_tasks
        control = rc.RunControl()
        self.assertIsNone(run_tasks.start_live_status(None, control, {}))
        self.assertIsNone(run_tasks.start_live_status(False, control, {}))
        live = run_tasks.start_live_status(self.path, control, {"tasks": ["Shop"], "max_clicks": 7})
        try:
            self.assertIs(control.live, live)
            self.assertIs(ls.current(), live)
            self.assertEqual(self.read()["clicks"]["cap"], 7)
            control.emit("run.finished", status="succeeded")
        finally:
            run_tasks.stop_live_status(live, control)
        self.assertIsNone(ls.current())
        self.assertEqual(self.read()["outcome_text"], "全部完成")


class WingFeedTests(unittest.TestCase):
    def setUp(self):
        rc.PRIVATE.mkdir(parents=True, exist_ok=True)
        self.dir = tempfile.TemporaryDirectory(prefix="_live_wing_", dir=rc.PRIVATE)
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "current.json"
        self.status = ls.LiveStatus(self.path, schedule=lambda d, f: FakeTimer(None, d, f), labels=dict(ls.TASK_LABELS))
        self.addCleanup(self.status.close)
        self.status.start("run1", ["Wing"])

    def feed_all(self, log):
        feed = wlf.WingFeed()
        self.assertEqual(feed.feed(log, status=self.status), len(log))
        self.assertEqual(feed.feed(log, status=self.status), 0)   # cursor: entries are folded once
        self.status.flush()
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_week_decision_card_header_and_timeline_are_plain(self):
        snap = self.feed_all(live_status_replay.synthetic_log())
        w = snap["wing"]
        self.assertEqual((w["season"], w["weeks_left"], w["sp"], w["stamina"]), (1, 6, 34, 95))
        self.assertEqual(w["stats"], {"Vo": 150, "Da": 100, "Vi": 100, "Me": 130})
        d = w["decision"]
        self.assertEqual(d["chosen"], "Vo课 3人")
        self.assertEqual(d["chosen_score"], 40.0)
        self.assertLessEqual(len(d["options"]), 4)
        self.assertEqual(sum(o["chosen"] for o in d["options"]), 1)
        self.assertEqual(d["options"][0]["label"], "Vo课 3人")
        self.assertIn("主 +24.0", d["why"])
        self.assertEqual(d["excluded"], [{"label": "试镜 +1000", "reason": "合格率未知"}])
        self.assertEqual(d["more"], 1)
        texts = [e["text"] for e in w["events"]]
        self.assertEqual(texts.count("剩6周 · Vo课 3人"), 1)   # select + confirm = one week event
        self.assertIn("（示例技能） −30SP", texts)
        self.assertIn("（示例选项）", texts)
        self.assertIn("第1季 合格", texts)
        self.assertEqual(w["live"], {"text": "Live · 出牌"})
        self.assertIsNone(JARGON.search(json.dumps(snap, ensure_ascii=False)))

    def test_step_sentences(self):
        week = live_status_replay.synthetic_log()[2]["decision"]["detail"]["decision"]
        step = lambda screen, intent, **detail: wlf.step_for(  # noqa: E731
            {"screen": screen, "decision": {"action": "click", "intent": intent, "detail": detail}}, {})
        self.assertEqual(step("Hub", "hub_schedule"), "日程")
        self.assertEqual(step("Schedule", "schedule_room_1", decision=week), "选 Vo课 3人")
        self.assertEqual(step("Schedule", "schedule_decide", decision=week), "确定 Vo课 3人")
        self.assertEqual(step("AuditionTab", "schedule_decide", verdict={"reward": 3000}), "试镜 +3000")
        self.assertEqual(step("ForgetSelect", "forget_select_2", choice={"name": "メンタルキュアI"}), "忘记 · メンタルキュアI")
        self.assertEqual(step("Hub", "some_new_intent"), "主页")
        self.assertEqual(wlf.stop_text("sp_learn_unconfirmed"), "学习结果未确认")
        self.assertEqual(wlf.stop_text("act_refused:target_not_verified"), "安全检查拒绝")
        self.assertEqual(wlf.stop_text("totally_new_code"), "已停止·请查看")

    def test_lesson_failure_event(self):
        snap = self.feed_all([{"event": "lesson_failure", "kind": "failure", "drop": {"Vo": -8, "Me": -2}},
                              {"event": "lesson_failure", "kind": "catastrophe", "drop": {"Da": -20}}])
        self.assertEqual([(e["kind"], e["text"]) for e in snap["wing"]["events"]],
                         [("fail", "失败 Vo−8 Me−2"), ("fail", "大失败 Da−20")])

    def test_quiet_stop_after_a_real_stop_is_not_repeated(self):
        snap = self.feed_all([{"event": "stop", "screen": "Review", "reason": "sp_info_unread"},
                              {"event": "stop", "screen": "Review", "reason": "no_decision"}])
        self.assertEqual([e["text"] for e in snap["wing"]["events"]], ["技能信息未读到"])
        self.assertEqual(snap["step"], "技能信息未读到")

    def test_events_are_bounded_and_snapshot_small(self):
        log = [{"event": "sp_learned", "name": f"技能{i}", "cost": 10, "sp": 100} for i in range(40)]
        snap = self.feed_all(log)
        self.assertEqual(len(snap["wing"]["events"]), ls.MAX_EVENTS)
        self.assertLess(len(self.path.read_bytes()), ls.MAX_BYTES)

    def test_feed_is_a_no_op_without_an_active_status(self):
        ls.deactivate()
        self.assertEqual(wlf.WingFeed().feed([{"event": "begin"}]), 0)

    def test_wing_session_reports_through_the_active_status(self):
        from agent.core.wing_custom import WingSession
        session = WingSession()
        ls.activate(self.status)
        self.addCleanup(ls.deactivate, self.status)
        session.runtime.log.append({"event": "sp_learned", "name": "テスト", "cost": 5, "sp": 1})
        session._live()
        self.status.flush()
        snap = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(snap["wing"]["events"][-1]["text"], "テスト −5SP")


class ProtocolTests(unittest.TestCase):
    def test_status_update_whitelists_run_live_and_mirror_matches(self):
        proto = json.loads((ROOT / "ui" / "protocol.json").read_text(encoding="utf-8"))
        props = proto["events"]["status.update"]["data"]["properties"]
        self.assertEqual(props["run_live"]["type"], ["object", "null"])
        mirror = (ROOT / "ui" / "protocol.js").read_text(encoding="utf-8")
        self.assertIn('"run_live"', mirror)
        self.assertLess(ls.MAX_BYTES, proto["limits"]["max_message_bytes"])


if __name__ == "__main__":
    unittest.main()
