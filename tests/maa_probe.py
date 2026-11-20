"""Offline Maa node probe for native-frame replay tests (no input: the replay controller refuses every click).

``NodeProbe().hits(frame_path, node)`` loads the real resource bundles (base + private native templates) and the
OCR model once, then runs a tiny Pipeline on one frame: ``SC.Test.Start`` -> [node-under-test, SC.Test.Miss].
Maa evaluates the ``next`` list on the same screenshot in order, so the node ran iff its recognition hit.
``ocr_items(frame_path)`` is the full-frame OCR (the same recogniser Maa uses for ``SC.*.PageOCR`` nodes).
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "dev_assets" / "private" / "models" / "ppocr_v6_small"


def maa_ready(*frames) -> bool:
    try:
        from tests.test_task_launcher import private_ready
    except Exception:
        return False
    return private_ready() and all((ROOT / f).is_file() for f in frames)


class NodeProbe:
    _resource = None

    @classmethod
    def resource(cls):
        if cls._resource is None:
            from maa.resource import Resource

            from agent.core.resource_bundles import bundle_paths, ocr_model_dir

            resource = Resource()
            for bundle in bundle_paths():
                assert resource.post_bundle(bundle).wait().succeeded
            model = ocr_model_dir()
            if model is not None:
                assert resource.post_ocr_model(model).wait().succeeded
            cls._resource = resource
        return cls._resource

    def run(self, frame, override):
        from maa.tasker import Tasker

        from tools import run_tasks as launcher

        images, advances = launcher.load_frames([(ROOT / frame, 1)])
        scenario = launcher.ReplayScenario(images, advances, allow_input=False)
        controller = launcher.create_replay_controller(scenario)
        assert controller.set_screenshot_use_raw_size(True) and controller.post_connection().wait().succeeded
        tasker = Tasker()
        assert tasker.bind(self.resource(), controller)
        job = tasker.post_task("SC.Test.Start", override).wait()
        detail = tasker.get_task_detail(job.job_id)
        names = [n.name for n in detail.nodes if n is not None]
        assert scenario.clicks == [] and scenario.other_calls == 0
        return names

    def hits(self, frame, node: str) -> bool:
        """True when ``node``'s own recognition (including ``inverse``) hits on ``frame``: the node is put directly in
        the next list with its action and successors neutralised for this probe only."""
        override = {"SC.Test.Start": {"recognition": "DirectHit", "next": [node, "SC.Test.Miss"]},
                    node: {"enabled": True, "action": "DoNothing", "next": [], "on_error": [], "pre_delay": 0,
                           "post_delay": 0},
                    "SC.Test.Miss": {"recognition": "DirectHit"}}
        names = self.run(frame, override)
        return node in names


_OCR = None


def ocr_items(frame, roi=(0, 0, 1280, 720)):
    """Full-frame (or ``roi``) Maa OCR of a private PNG as [{"text", "box", "score"}]."""
    global _OCR
    import numpy as np
    from PIL import Image

    from tools.replay_ocr import MaaOcrRecognizer

    if _OCR is None:
        _OCR = MaaOcrRecognizer(MODEL)
    path = frame if Path(frame).is_absolute() else ROOT / frame
    with Image.open(path) as image:
        bgr = np.ascontiguousarray(np.asarray(image.convert("RGB"))[:, :, ::-1])
    result = _OCR.recognize({"name": "SC.Test.OCR", "roi": list(roi), "expected": "^.*$", "threshold": 0.0}, bgr)
    assert _OCR.input_attempts == 0
    return [{"text": i["text"], "box": i["box"], "score": i["score"]} for i in result["results"]]


def rgb(frame):
    import numpy as np
    from PIL import Image

    path = frame if Path(frame).is_absolute() else ROOT / frame
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB")).copy()
