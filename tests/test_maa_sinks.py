"""Event sinks are released when a run ends (offline; no game input)."""
import unittest

from agent.core import maa_sinks
from agent.core.run_control import RunControl


class Owner:
    def __init__(self, fail=False):
        self.removed, self.fail = [], fail

    def remove_sink(self, sink_id):
        self.removed.append(("task", sink_id))
        if self.fail:
            raise RuntimeError("gone")

    def remove_context_sink(self, sink_id):
        self.removed.append(("context", sink_id))


class MaaSinkTests(unittest.TestCase):
    def test_release_removes_every_tracked_sink_once(self):
        owner = Owner()
        maa_sinks.track(owner, "remove_sink", 3)
        maa_sinks.track(owner, "remove_context_sink", 4)
        maa_sinks.release(owner)
        maa_sinks.release(owner)
        self.assertEqual(owner.removed, [("task", 3), ("context", 4)])

    def test_a_failed_remove_does_not_stop_the_others(self):
        owner = Owner(fail=True)
        maa_sinks.track(owner, "remove_sink", 1)
        maa_sinks.track(owner, "remove_context_sink", 2)
        maa_sinks.release(owner)
        self.assertEqual(owner.removed, [("task", 1), ("context", 2)])
        self.assertEqual(owner._sc_sink_ids, [])

    def test_run_event_sink_leaves_the_bindings_class_level_holder(self):
        # 2026-10-27: Tasker._sink_holder is shared by every Tasker and only remove_*_sink empties it; a sink left there
        # kept each run's tasker, Resource and OCR model alive (+0.33 GB per replay test, then a native abort).
        try:
            from maa.tasker import Tasker
            tasker = Tasker()
        except Exception as error:   # no MaaFramework library in this environment
            self.skipTest(f"MaaFramework unavailable: {error}")
        before = len(Tasker._sink_holder)
        RunControl().attach(tasker)
        self.assertEqual(len(Tasker._sink_holder), before + 1)
        maa_sinks.release(tasker)
        self.assertEqual(len(Tasker._sink_holder), before)


if __name__ == "__main__":
    unittest.main()
