#!/usr/bin/env python3
"""Index say "Halo" sample frames by screen.

Reads the explore logs (``dev_assets/private/wing_samples/<day>/explore-steps.jsonl``) and files the before-frame of
every sent click under a screen name: the record's ``label`` when the agent gave one (``explore_step.py --label``),
else a rule on its ``--note``. Writes ``dev_assets/private/halo-samples/manifest.json`` ({screen: [frame paths]});
frames are not copied. Read-only on the game: no capture, no input.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PRIVATE = ROOT / "dev_assets/private"
DEFAULT_LOGS = (PRIVATE / "wing_samples/2026-11-01/explore-steps.jsonl",)
DEFAULT_OUT = PRIVATE / "halo-samples/manifest.json"
DEFAULT_OCR = PRIVATE / "halo-samples/ocr.jsonl"
NOTE_PREFIX = re.compile(r"^H[0-9]+[：:]\s*")

# First match wins; the note says what was clicked, so the rule names the screen the click was made on.
NOTE_RULES = (
    (r"について|Halo tutorial", "HaloTutorial"),
    (r"勧誘確認", "HaloRecruitConfirm"),
    (r"チームメンバー上限", "HaloTeamFull"),
    (r"recruit", "HaloRecruit"),
    (r"\bhub\b", "HaloHub"),
    (r"成長確認", "HaloGrowthConfirm"),
    (r"^FesBirth|^フェスアイドル誕生$", "FesBirth"),
    (r"^誕生確認 キャンセル", "FesBirthConfirm"),         # H1: the キャンセル was pressed on the SP-loss confirm
    (r"^(フェスアイドル)?誕生確認", "FesBirthSkillDialog"),   # 「スキルを変更せずに…誕生させますか」
    (r"^フェスアイドル確認", "FesBirthConfirm"),
    (r"スキル一括変更", "SkillBulkChange"),
    (r"成長|^ability", "HaloGrowth"),
    (r"back to lesson tab|^camp W2 audition tab", "HaloPractice"),   # tab taps made from the practice tab
    (r"(audition|team practice|チーム合同練習|オーディション&ライブ) tab", "HaloLesson"),
    (r"REPORT", "HaloReport"),
    (r"audition", "HaloAudition"),
    (r"carousel < \([b-d]\d\)", "HaloAudition"),          # H1 11:37–11:40: the Final Live week's audition page
    (r"carousel|team practice|施設効率相談", "HaloPractice"),
    (r"^(S\d+W\d+|S\d+end) (select|vocal|Vocal|Visual|Dance|radio|magazine)", "HaloLesson"),
    (r"はづき OK", "HaloSeasonEnd"),
    (r"inheritance", "HaloInheritance"),
    (r"プロデュース目標", "HaloGoals"),
    (r"挑戦確認", "HaloLiveConfirm"),
    (r"^(First|Final) Live", "HaloLiveSelect"),
    (r"社長からの評価", "PresidentEval"),
    (r"promise", "PromiseChoice"),
    (r"commu menu|commu Auto|commu 早送り|commu tap", "Commu"),
    (r"プロデュースクリア報酬", "ClearReward"),
    (r"獲得報酬", "RewardDetail"),
    (r"フェスアイドル詳細", "FesDetail"),
    (r"ProduceEndCommu", "ProduceEndCommu"),
    (r"team info", "HaloTeamInfo"),
    (r"^home ", "Home"),
    (r"Halo selected", "ProduceSelect"),
    (r"ユニット", "UnitEdit"),
    (r"^book ", "BookSelect"),
    (r"no items", "ItemPrepare"),
    (r"使用確認", "UseConfirm"),
    (r"SKIP", "CommuSkip"),
)


# Book-step pages H1 never opened, borrowed from the weekly Auto setup sampling (2026-10-29, the same page widgets):
# the newest 設定履歴 set and the プリセット dialog.
BORROWED = {
    "BookHistory": ("wing_samples/2026-10-29/20261029T132542403417Z-r04c26-before.png",),
    "BookPreset": ("wing_samples/2026-10-29/20261029T162652403177Z-r16c16-before.png",),
    # H2d live: week 2 勧誘 with the first candidate already joined (勧誘済, チームに勧誘 greyed)
    "HaloRecruit": ("live-20261105/halo-recruit-joined.png",),
    # H2d live: ノウハウ継承成功！ after the S1-end 勧誘 (its native Stats anchor merged the row)
    "HaloInheritance": ("live-20261106/halo-inheritance-success.png",),
    # H2d live: その他 with 親愛度+10 already 習得済み (greyed, no +)
    "HaloGrowth": ("live-20261106/halo-growth-affection-learned.png",),
    # H2d live: S2 ライブ card (no fan bubble) and S3 two locked 出場条件 cards in a row; fans 14,093 / 114,204
    "HaloAudition": ("live-20261106/halo-audition-s2-live-card.png", "live-20261106/halo-audition-s3-locked.png"),
}


# Labels name the Halo flow; where the page is a W.I.N.G. one the classifier reports the W.I.N.G. screen.
SHARED = {
    "PresidentEval": ("Evaluation",),             # 社長からの評価 is W.I.N.G.'s プロデュース評価 page
    "HaloSeasonEnd": ("HaloSeasonEnd", "SeasonEndCommu", "PassCommu"),   # camp ends use W.I.N.G.'s はづき lines + OK
    "HaloTutorial": ("Tutorial", "SetupTransition"),     # H0 ユニット編成について: 閉じる unread, the setup page waits
    "Commu": ("Commu", "CommuSkip", "CommuFastForwardOff", "Choice"),
    "PromiseChoice": ("Choice",),
    "RewardDetail": ("Reward", "RewardDetail"),
    "UseConfirm": ("StartConfirm",),
    "ItemPrepare": ("ItemSelect",),
    "UnitEdit": ("UnitSelect",),
    "ProduceSelect": ("ProduceSelectOther",),    # say "Halo" sits on the other プロデュース選択 tab
}


def screen_of(record):
    """Screen name for one explore record, or None (unlabelled, not a sent click, or no rule)."""
    if record.get("label"):
        return record["label"]
    note = NOTE_PREFIX.sub("", str(record.get("note") or ""))
    if not note or not NOTE_PREFIX.match(str(record.get("note") or "")):
        return None
    for pattern, screen in NOTE_RULES:
        if re.search(pattern, note):
            return screen
    return None


def build_manifest(log_paths):
    manifest: dict[str, list[str]] = {}
    unlabelled = []
    for log in log_paths:
        for line in Path(log).read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            frame = record.get("before") if record.get("mode") == "click" else record.get("frame")
            if record.get("result") not in ("sent", "captured", "waited") or not frame:
                continue
            screen = screen_of(record)
            if screen is None:
                if record.get("mode") == "click" and str(record.get("note") or "").startswith("H"):
                    unlabelled.append(record.get("note"))
                continue
            frames = manifest.setdefault(screen, [])
            if Path(frame).is_file() and frame not in frames:
                frames.append(frame)
    for screen, names in BORROWED.items():
        found = [str(PRIVATE / name) for name in names if (PRIVATE / name).is_file()]
        if found:
            manifest.setdefault(screen, []).extend(f for f in found if f not in manifest.get(screen, []))
    return dict(sorted(manifest.items())), unlabelled


def write_ocr(manifest, out=DEFAULT_OCR):
    """Whole-frame Maa OCR boxes of every manifest frame -> ocr.jsonl ({frame, texts: [[text, [x, y, w, h]]]}).
    Frames already in the file are kept; only new ones are read. Offline: no capture, no input."""
    from tools.wing_demo_replay import load_rgb
    from tools.wing_sample_ocr import WingRecognizer
    out = Path(out)
    done = set(load_ocr(out))
    todo = sorted({f for frames in manifest.values() for f in frames} - done)
    if not todo:
        return 0
    rec = WingRecognizer()
    with out.open("ab") as fh:
        for path in todo:
            image = load_rgb(Path(path))[:, :, ::-1].copy()     # Maa expects BGR
            result = rec.run(image, {"recognition": "OCR", "roi": [0, 0, 1280, 720], "expected": [], "threshold": 0.3})
            row = {"frame": path, "texts": [[t["text"], list(t["box"])] for t in result["texts"]]}
            fh.write((json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
    if rec.input_attempts:
        raise RuntimeError(f"OCR controller attempted input: {rec.input_attempts}")
    return len(todo)


def load_ocr(path=DEFAULT_OCR) -> dict:
    path = Path(path)
    if not path.is_file():
        return {}
    rows = (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return {r["frame"]: [(t, tuple(b)) for t, b in r["texts"]] for r in rows}


def accepted(label):
    """Screens the classifier may report for a manifest label (its own name, or the W.I.N.G. screen it shares)."""
    return SHARED.get(label, (label,))


def classify(manifest, backend_name="boxes", ocr=None):
    """{label: Counter(screen)} over the manifest frames, plus the misses [(label, screen, frame)]."""
    import collections

    from agent.core import wing_screen as wsc
    from tools.wing_demo_replay import MaaBackend, load_rgb
    ocr = load_ocr() if ocr is None else ocr
    backend = MaaBackend(DEFAULT_OUT.parent / "maa_roi_cache.json") if backend_name == "maa" else wsc.BoxBackend()
    classifier = wsc.ScreenClassifier(wsc.load_nodes(), backend, {"SC.Wing.State": lambda p, f: True})
    table, misses = {}, []
    for label, frames in manifest.items():
        got = table.setdefault(label, collections.Counter())
        for path in frames:
            frame = wsc.Frame(ocr.get(path, ()), lambda p=Path(path): load_rgb(p), key=path)
            screen = classifier.classify(frame).screen
            got[screen] += 1
            if screen not in accepted(label):
                misses.append((label, screen, path))
    if backend_name == "maa":
        backend.save()
    return table, misses


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="按画面整理 Halo 样本帧（只写清单，不截图、不输入）")
    parser.add_argument("--log", type=Path, action="append", help="explore-steps.jsonl（可多次；默认 2026-11-01）")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--ocr", action="store_true", help="再给清单帧做整帧 OCR（ocr.jsonl，只补新帧）")
    parser.add_argument("--classify", choices=("boxes", "maa"), help="用当前 Pipeline 判定清单帧并列出不符")
    args = parser.parse_args(argv)
    manifest, unlabelled = build_manifest(args.log or DEFAULT_LOGS)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes((json.dumps(manifest, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    for screen, frames in manifest.items():
        print(f"{screen}: {len(frames)}")
    if unlabelled:
        print(f"unlabelled: {len(unlabelled)} e.g. {unlabelled[:3]}")
    if args.ocr:
        print(f"ocr: {write_ocr(manifest)} new frames")
    if args.classify:
        table, misses = classify(manifest, args.classify)
        for label, got in table.items():
            print(f"{label}: {dict(got.most_common())}")
        for label, screen, path in misses:
            print(f"miss {label} -> {screen}: {Path(path).name}")
        print(f"misses: {len(misses)} / {sum(len(f) for f in manifest.values())}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
