"""Daily support-card training (migration batch B5): pure rules and per-run state. No Maa import, no input.

Flow (assets/resource/base/pipeline/training.json): Home -> アイドル -> トレーニング -> training list (the
configured card) -> detail with no ticket (Zero) -> + Bronze -> detail with one Bronze (One) -> トレーニング ->
トレーニング確認 (proves the sole material Bronze x1) -> grant -> トレーニング開始 (the only consuming click) ->
result dialog (トレーニング成功 / 大成功 / 超成功, title by OCR) -> 閉じる -> detail again (After: inventory -1,
level up or EXP closer) -> home.

The numeric fields, crops and preprocessing are those of the verified legacy runner (tools/run_daily_training.py,
2026-10-01 live: Bronze 22614 -> 22613, Lv2 -> 3). Inventory is hidden by the dialogs; the dialog stages are bound
to the baseline read on the detail page of the same run.
"""
from __future__ import annotations

import dataclasses
import re

from agent.core import task_common as tc

CARD_ID = "【くっつく、くっつかない】芹沢あさひ"
# 2026-10-10: the task option 「训练卡」 carries a wiki card id of any S card the user confirmed on the resources page
# (``card_id``, ``^2\d{9}$``). Empty = default mode: the first trainable S card of the list in its current verified sort
# order (agent/core/training_select.py ``locate_default``); it is never "no training".
NO_CARD = ""  # no explicit card (default mode); also the legacy 「未选择」 option value
CARD_IDS = {CARD_ID: "2050170010", NO_CARD: ""}  # legacy option name -> ShinyColorsDB/wiki id (ui/game-assets manifest "option")
CARD_ID_PATTERN = r"2\d{9}"
CARD_NAMES = {v: k for k, v in CARD_IDS.items() if v}
# Card name line of the detail page, the One preview and the (dimmed) トレーニング確認 background: identity re-check by
# OCR after the list tap (2026-10-07: the template guards are card-specific; the OCR names the card that is open).
CARD_NAME_BOX = [159, 96, 451, 31]
# 2026-10-12 live: 「【HoP PoP らびっつ】三峰結華」 read exactly at 0.9497 (Latin + spaces score lower), so the name line
# accepts 0.90; the text must still equal the bound/DB card name exactly, so a misread can only stop, never select.
CARD_NAME_THRESHOLD = 0.90
MAX_SCROLL_STEPS = 12
# Default wheel-step budget of the list search (PI option 「训练列表滚动」 offers 0-4: Training's live click cap of 14 holds
# 10 navigation/spend taps + 4 wheel steps). Scrolling still needs the reviewed, enabled training-list wheel actions.
DEFAULT_SCROLL_STEPS = 10   # Search longer for the chosen card (±120 wheel steps)
TICKET = "basic_bronze"
QUANTITY = 1
SPEND_ACTION = "training-start"
APPROVER = "SC.Training.Check.Confirm"
MAX_UNREADABLE_FRAMES = 3
# 2026-10-12 live: the list was read while it was still loading/scrolling (first tile art 0.0; offsets 296 -> 312 after a
# scroll-up). A list reading must repeat on the next frame before it may scroll or tap; after this many holds it proceeds.
MAX_SETTLE_HOLDS = 6
# Result dialog title (B6c). 2026-10-03 16:31 live: 「トレーニング大成功」 (big 「大成功!!」 banner); 「超成功」 is advertised on the
# トレーニング button (「超成功発生中!」) and accepted by the same rule. Any other title is not a verified result.
RESULT_TITLE_BOX = [404, 186, 300, 44]
RESULT_TITLE_PATTERN = r"トレーニング(大|超)?成功"
RESULT_KINDS = {"": "success", "大": "great_success", "超": "super_success"}
INTENT_FIELDS = ("card", "card_id", "ticket", "quantity", "inventory", "level", "remaining_exp", "ticket_layout")

# name -> (crop, preprocessing kind, threshold cutoff, full-match pattern, unanimous level variants)
NUMERIC_FIELDS = {
    "inventory": ([739, 357, 59, 21], "light", 128, r"[xX×]?[0-9]{1,8}", False),
    # 2026-10-12 live (Lv.66, あと 630): the 1-digit-era crops clipped wider numbers. Widened; the level crop stops
    # before the One-preview ▶▶ arrow (x≈293). Same reads on native frames 22-27 and the b5 Lv50 frames.
    "level": ([252, 138, 38, 25], "dark", 128, r"[0-9]{1,3}", True),
    "remaining_exp": ([476, 149, 100, 25], "dark", 80, r"[0-9]{1,8}", False),
}
TOP_INVENTORY_BOX = [739, 226, 59, 21]  # no Lv.50 row: native 2026-10-11 甜花 frames 22/23/28
# Which fields each stage reads (the dialogs hide the inventory and EXP).
STAGE_FIELDS = {"Zero": ("inventory", "level", "remaining_exp"), "One": ("inventory", "level"),
                "Confirm": ("level",), "After": ("inventory", "level", "remaining_exp")}

SUCCESS_OUTCOMES = frozenset({"trained", "nothing_to_do"})
# 2026-10-07 list adaptation (agent/core/training_select.py): stops before any list tap / before + on the detail page.
LIST_OUTCOMES = frozenset({"card_not_selected", "list_sort_unknown", "list_filter_active", "target_not_visible",
                           "target_ambiguous", "target_slot_unbound", "identity_reference_missing", "identity_mismatch",
                           "target_uncalibrated", "default_card_not_trainable"})
FAILURE_OUTCOMES = LIST_OUTCOMES | frozenset({"config_invalid", "insufficient_tickets", "ocr_unreadable", "baseline_mismatch",
                              "confirm_mismatch", "spend_not_enabled", "unexpected_confirm", "unexpected_result_dialog",
                              "result_unverified", "spend_uncertain", "spend_refused", "log_write_failed", "timeout"})


def card_name(meta):
    """In-game card name line (「【title】idol」) from a ``CardIndex.meta`` entry; None when the title or idol is missing."""
    title, idol = (meta or {}).get("title"), (meta or {}).get("idol")
    return f"【{title}】{idol}" if title and idol else None


@dataclasses.dataclass(frozen=True)
class TrainingConfig:
    card: str = NO_CARD          # display name; legacy option value, or the calibrated name of ``card_id``
    ticket: str = TICKET
    quantity: int = QUANTITY
    skip_if_done_today: bool = False   # A re-run trains again (the UI warns 「今天已完成」)
    max_scroll_steps: int = DEFAULT_SCROLL_STEPS   # list wheel-step budget
    card_id: str = ""            # wiki id of the chosen S card; "" = default mode (first trainable of the current sort)

    def __post_init__(self):
        if not self.card_id and self.card:
            object.__setattr__(self, "card_id", CARD_IDS.get(self.card, ""))
        if self.card_id and not self.card:
            object.__setattr__(self, "card", CARD_NAMES.get(self.card_id, ""))

    @classmethod
    def from_params(cls, params):
        tc.require(isinstance(params, dict), "训练配置必须为对象")
        allowed = {"card", "ticket", "quantity", "skip_if_done_today", "card_id", "max_scroll_steps"}
        tc.require(set(params) <= allowed, "训练配置含未知字段: " + ",".join(sorted(set(params) - allowed)))
        card = params.get("card", NO_CARD)
        tc.require(isinstance(card, str) and card in CARD_IDS, "训练卡名无效（旧选项值只认已校准卡或空）：" + CARD_ID)
        card_id = params.get("card_id", CARD_IDS[card])
        tc.require(isinstance(card_id, str) and (card_id == "" or re.fullmatch(CARD_ID_PATTERN, card_id)),
                   "card_id 须为空（默认：当前排序最新可训练）或以 2 开头的 10 位 S 卡 ID")
        tc.require(not card or card_id == CARD_IDS[card], "card_id 与训练卡不一致")
        steps = params.get("max_scroll_steps", DEFAULT_SCROLL_STEPS)
        tc.require(type(steps) is int and 0 <= steps <= MAX_SCROLL_STEPS, f"max_scroll_steps 须为 0–{MAX_SCROLL_STEPS}")
        tc.require(params.get("ticket", TICKET) == TICKET, "只允许基础铜券（basic_bronze）")
        tc.require(params.get("quantity", QUANTITY) == QUANTITY and type(params.get("quantity", QUANTITY)) is int,
                   "每次只用 1 张")
        skip = params.get("skip_if_done_today", False)
        tc.require(type(skip) is bool, "skip_if_done_today 必须为布尔值")
        return cls(card, TICKET, QUANTITY, skip, steps, card_id)

    def to_dict(self):
        return dataclasses.asdict(self)

    @property
    def selected(self):
        """True when the user chose a card; False = default mode (first trainable card of the current list sort)."""
        return bool(self.card_id)

    @property
    def default_mode(self):
        return not self.card_id


def parse_fields(texts):
    """{"inventory": "x22614" | "22614", "level": "2", ...} -> ints (None when unreadable)."""
    return {name: tc.to_int(texts.get(name)) for name in texts}


def baseline_reasons(fields):
    """Reasons the Zero detail page cannot start a training (empty list = ok)."""
    reasons = []
    if any(fields.get(n) is None for n in STAGE_FIELDS["Zero"]):
        return ["ocr_unreadable"]
    if fields["inventory"] < QUANTITY:
        reasons.append("insufficient_tickets")
    if fields["level"] <= 0:
        reasons.append("level_unknown")
    if fields["remaining_exp"] <= 0:
        reasons.append("no_remaining_exp")  # max level or unknown: nothing to train
    return reasons


def one_reasons(baseline, fields):
    """One-Bronze preview must show the same card state as the baseline (EXP50 is proved by its template)."""
    if any(fields.get(n) is None for n in STAGE_FIELDS["One"]):
        return ["ocr_unreadable"]
    reasons = []
    if fields["inventory"] != baseline["inventory"]:
        reasons.append("inventory_changed")
    if fields["level"] != baseline["level"]:
        reasons.append("level_changed")
    return reasons


def confirm_reasons(config, baseline, fields):
    """Reasons the トレーニング確認 dialog must NOT be confirmed. The anchors (OnlyBronzeOne, BronzeTicket,
    QuantityOne, Prompt, Start) already proved the sole material Bronze x1; OCR binds the level."""
    if baseline is None:
        return ["no_baseline"]
    reasons = []
    if config.ticket != TICKET or config.quantity != QUANTITY:
        reasons.append("config_not_one_bronze")
    if fields.get("level") is None:
        reasons.append("level_unreadable")
    elif fields["level"] != baseline["level"]:
        reasons.append("level_changed")
    if baseline["inventory"] < QUANTITY:
        reasons.append("insufficient_tickets")
    return reasons


def after_reasons(baseline, fields):
    """Reasons the detail page after the result does not prove exactly one Bronze was used (empty = verified)."""
    if any(fields.get(n) is None for n in STAGE_FIELDS["After"]):
        return ["ocr_unreadable"]
    reasons = []
    if baseline["inventory"] - fields["inventory"] != QUANTITY:
        reasons.append("inventory_not_minus_1")
    gained = fields["level"] > baseline["level"] or (
        fields["level"] == baseline["level"] and 0 < fields["remaining_exp"] < baseline["remaining_exp"])
    if not gained:
        reasons.append("no_level_or_exp_gain")
    return reasons


CARD_NAME_LINE = re.compile(r"^【[^【】]+】[^【】]+$")
# Live 2026-10-31: OCR read the katakana middle dots of 【煌・煌・星・娘】幽谷霧子 as U+00B7 (77 S titles have dots).
MIDDLE_DOTS = str.maketrans({**dict.fromkeys("·‧∙•", "・"),
                             # 2026-11-22: 【──過去】 vs 【――過去】 (card DBs differ in the long dash)
                             **dict.fromkeys("─━—", "―")})


def name_key(text):
    """Card name compared across OCR and the DB: ``normalize_text`` with every middle-dot variant as 「・」."""
    return tc.normalize_text(text).translate(MIDDLE_DOTS)


def swapped_names(manifest_names, library_names):
    """Ids whose two card-DB names disagree in a way that names another card: this id's name in one source is another
    id's name in the other (2026-11-22: 8 crossed titles, e.g. 1030060030/40 雪染めロマンティカ ⇄ 真紅一輪). A typo or
    width variant (【当冬の隙間】 vs 【冬の隙間】) is not a swap. Either name may be the wrong one, so neither is trusted."""
    m_ids, l_ids = {}, {}
    for card_id, name in manifest_names.items():
        m_ids.setdefault(name_key(name), set()).add(card_id)
    for card_id, name in library_names.items():
        l_ids.setdefault(name_key(name), set()).add(card_id)
    out = set()
    for card_id in set(manifest_names) & set(library_names):
        m, lib = name_key(manifest_names[card_id]), name_key(library_names[card_id])
        if m != lib and (m_ids.get(lib, set()) - {card_id} or l_ids.get(m, set()) - {card_id}):
            out.add(card_id)
    return out


def default_name_reasons(name_text):
    """Default mode: reasons a detail-page name line cannot bind the target (empty = a 「【title】name」 line)."""
    if not name_text:
        return ["card_name_unreadable"]
    if not CARD_NAME_LINE.match(tc.normalize_text(name_text)):
        return ["card_name_malformed"]
    return []


def identity_reasons(config, name_text, expected=None):
    """Reasons the card name read on a detail/confirm frame is not the target card (empty = same card). ``expected``
    is the name the list step bound (the located card's name; default mode has no name before the list); without it
    the configured card name is used. No known name at all -> ``card_name_unbound`` (never confirmed)."""
    expected = expected or config.card
    if not expected:
        return ["card_name_unbound"]
    if not name_text:
        return ["card_name_unreadable"]
    if name_key(name_text) != name_key(expected):
        return ["identity_mismatch"]
    return []


def result_kind(title):
    """'success' | 'great_success' | 'super_success' for an exact result title (OCR text, spaces/punctuation
    stripped), else None."""
    text = re.sub(r"[\s!！]", "", title or "")
    match = re.fullmatch(RESULT_TITLE_PATTERN, text)
    return RESULT_KINDS[match.group(1) or ""] if match else None


def open_intent(events):
    """The one intent of the day still without a verified/refused conclusion (its consumption is uncertain), or
    None when there is none or more than one (then nothing is resolved)."""
    if log_state(events).uncertain != 1:
        return None
    pending = None
    for event in events:
        kind = event.get("event")
        if kind == "intent":
            pending = event
        elif kind in ("verified", "refused"):
            pending = None
    if pending is None or any(type(pending.get(n)) is not int for n in ("inventory", "level", "remaining_exp")):
        return None
    return {n: pending[n] for n in INTENT_FIELDS if n in pending}


@dataclasses.dataclass(frozen=True)
class LogState:
    verified: int = 0
    uncertain: int = 0     # intents without a verified/refused conclusion


def log_state(events):
    intents = verified = refused = 0
    for event in events:
        kind = event.get("event")
        intents += kind == "intent"
        verified += kind == "verified"
        refused += kind == "refused"
    return LogState(verified, max(0, intents - verified - refused))


class TrainingRun(tc.TaskRun):
    """Phases: active -> plus_tapped -> submitted -> awaiting_result -> result_closing -> finished.

    ``resolving`` (B6c): the day's log holds one uncertain intent; only a read-only look at the result dialog that
    is still open may resolve it (-> result_closing, then the After check); any other screen -> nothing_to_do."""
    SUCCESS = SUCCESS_OUTCOMES
    HALT = tc.TaskRun.HALT

    def __init__(self, config, log):
        super().__init__(config, log)
        self.baseline = None   # {"inventory", "level", "remaining_exp"} from the Zero page of this run
        self.after = None
        self.result_kind = None
        self.list_search = None      # training_select.ListSearch, created on the first list frame
        self.list_frame = None       # (image digest, Decision, action) of the last evaluated list frame
        self.identity_frame = None   # (image digest, reasons) of the last card-name check
        self.list_unreadable = 0
        self.list_settle = None      # (offset, grid digest, status) of the previous list reading (settle check)
        self.list_settle_holds = 0
        self.target_id = None        # card id / name bound by the list step (explicit choice or default first tile)
        self.target_name = None
        self.target_mode = None      # explicit | default
        self.default_bind_pending = False   # default mode: the opened detail page binds the card name
