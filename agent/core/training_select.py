"""Training list (トレーニングアイドル選択): sort/filter reading and identity-based target location. No Maa, no input.

2026-10-07 supervised run: the user had sorted the list Lv順 / ▼降順, the configured あさひ left the old fixed slot 2 and
the task timed out. The user may change the sort at any time, so the task no longer assumes a position:

1. **Order** (``parse_order``): the sort button text (``デフォルト`` / ``Lv順``, native samples), the direction button
   (``降順`` / ``昇順``) and the filter button (``絞り込み`` = no filter) are read by OCR on every list frame. Anything
   unreadable, an unknown sort key, an unknown direction or an active filter stops the task; nothing is guessed.
2. **Identity** (``locate``): every fully visible grid tile is checked against the target. A tile is the target only
   when (a) its art matches the calibrated native tile of the configured card (normalised cross-correlation of the
   art region, which excludes the Lv band and the rank stars, so level-ups and 特訓 do not break it), (b) its rarity
   frame colour is that of the card's rarity (``card_inventory.frame_tier``: the game reuses art across rarities),
   (c) the ShinyColorsDB thumbnail matcher's best coarse candidate is the same idol, and (d) the DB matcher did not
   accept the tile as a *different* card. A tile resembling the target art that fails any of these is a
   *look-alike*; with a look-alike in view the task stops instead of scrolling past an unidentified possible target.
3. **Click binding** (``click_action``): a table action is used only when its inner (sampled) rect lies inside the
   located tile; a target in a slot without such an action is reported, never clicked elsewhere.
4. **Search** (``ListSearch``): bounded scroll planning (down to the end, then up to the top, at most
   ``max_steps`` wheel steps). A changed order or list content that the task did not cause invalidates the search;
   located boxes are never cached across frames (every click decision is made on the frame it is sent from).
   Scrolling stays disabled until the action table enables the reviewed training-list wheel actions.
5. **Any confirmed S card**: the task option carries a wiki card id; ``spec_for`` builds the identity spec from the
   DB thumbnail index (``CardIndex.meta``: rarity -> frame tier, idol, title -> the in-game name line). Without a calibrated
   native template (only the calibrated card has one) a tile is the target only when the DB matcher *accepted* it as exactly
   that card id and its frame tier is the card's rarity tier; an unaccepted tile of the same idol is a look-alike (stop).
   Detail/One/Confirm now share sampled material layouts and re-check the bound card's complete name by OCR; no
   card-specific portrait is required. Result closing is non-consuming and the After page rechecks identity.
6. **Default mode** (``locate_default``, 2026-10-12): in every sort order the game draws untrainable cards (max level
   etc.) as dimmed, untappable tiles. From the proven list top the tiles are classified in reading order by pixels only
   (``tile_state``: the S badge and the art under the game's grey overlay; no Lv OCR, no DB identity): dimmed tiles are
   skipped, the first positively bright tile is the target, any unclassifiable tile before it stops. The DB identity
   is recorded when the matcher accepted the tile; otherwise the detail page binds the card name (and, when unique in
   the DB, the card id). The detail page independently requires the S badge, Bronze >= 1, Lv > 0 and remaining EXP > 0.
7. **Pipeline binding** (``binding_refusal``): every list Click node names itself in its recognition parameters; the
   runtime answers only when the action table binds that node to exactly the action it asks for, so a mis-wired node
   (copied parameters, swapped slot) stops the run instead of tapping another tile.
"""
from __future__ import annotations

import dataclasses
import hashlib
import re
import unicodedata
from pathlib import Path

from agent.core import card_inventory as ci
from agent.core import training_task as tt

# ---------------------------------------------------------------------------------------------------- order
# OCR boxes on the 1280x720 list (2026-10-01 default frame and 2026-10-07 after_training_r1: text boxes
# 絞り込み [794,112,98,29], デフォルト [998,113,104,29] / Lv順 [1022,113,58,30], ▼降順 [1153,112,77,30]).
FILTER_BOX = (760, 108, 170, 44)
SORT_BOX = (968, 108, 168, 44)
DIRECTION_BOX = (1144, 108, 100, 44)
# Sort keys with a native sample. Another key (e.g. a stat or 特訓 order) needs its own sample first: stop.
# 2026-10-11 live (sample-live-20261011/14): the user's list opened as 加入順/▼降順 (OCR [1014,113,74,29]).
SORT_KEYS = {"デフォルト": "default", "Lv順": "level", "加入順": "joined",
             # 2026-10-12 live: the list opened as Vocal順. Every key of the 並び替え popup (native 3x3 sample above) is a
             # known order; any key other than Lv順 only leads to the planned sort change to Lv順 (never a slot read).
             "特訓回数順": "special_training", "Vocal順": "vocal", "Dance順": "dance", "Visual順": "visual",
             "メンタル順": "mental", "リリース順": "release"}
DIRECTIONS = {"降順": "desc", "昇順": "asc"}   # 降順 native; 昇順 is the game's opposite label (no native sample yet)
FILTER_OFF = "絞り込み"
ARROWS = "▼▲▽△↓↑"


@dataclasses.dataclass(frozen=True)
class ListOrder:
    key: str          # default | level | joined
    direction: str    # desc | asc

    def to_dict(self):
        return dataclasses.asdict(self)


def _clean(text):
    if not isinstance(text, str):
        return None
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))
    text = text.strip(ARROWS)
    return re.sub(r"(?i)^lv", "Lv", text) or None


def parse_order(sort_text, direction_text, filter_text):
    """(ListOrder | None, reasons). ``reasons`` empty only when sort, direction and filter are all known."""
    reasons = []
    sort, direction, flt = _clean(sort_text), _clean(direction_text), _clean(filter_text)
    if flt is None:
        reasons.append("filter_unreadable")
    elif flt != FILTER_OFF:
        reasons.append("filter_active:" + flt)
    if sort is None:
        reasons.append("sort_unreadable")
    elif sort not in SORT_KEYS:
        reasons.append("sort_unknown:" + sort)
    if direction is None:
        reasons.append("direction_unreadable")
    elif direction not in DIRECTIONS:
        reasons.append("direction_unknown:" + direction)
    if reasons:
        return None, reasons
    return ListOrder(SORT_KEYS[sort], DIRECTIONS[direction]), []


def order_outcome(reasons):
    """Task outcome for parse_order reasons (only called when not all of them are 'unreadable')."""
    if any(r.startswith("filter_active") for r in reasons):
        return "list_filter_active"
    return "list_sort_unknown"


def only_unreadable(reasons):
    return bool(reasons) and all(r.endswith("_unreadable") for r in reasons)


# ---------------------------------------------------------------------------------------------------- identity
@dataclasses.dataclass(frozen=True)
class TargetSpec:
    name: str              # training option / config card name
    card_id: str           # ShinyColorsDB / wiki id (ui/game-assets/manifest.json "option" binding)
    rarity: str            # rarity of card_id -> expected frame tier
    template: str          # native list tile template inside the private bundle's image dir
    template_offset: tuple  # (dx, dy) of the template inside the tile box (x0 + 138c, offset + 154r, 108, 108)
    art: tuple             # (x0, y0, x1, y1) inside the template: art only (no stars at the left, no Lv band below)
    calibrated: bool = True   # False: no native template, the DB matcher + frame tier alone identify the tile

    @property
    def idol(self):
        return ci.idol_of(self.card_id)

    @property
    def tier(self):
        return ci.FRAME_TIERS.get(self.rarity)


# The calibrated card (tools/ui_game_assets.py CALIBRATED_WIKI). The template is the native tile of the 2026-10-01
# default-order list ([409,189,101,87], tile [405,184,108,108] -> offset (4, 5)); the art crop drops the left star
# column (x < 14) and the Lv band (y >= 70: tile y >= 75, the band starts at tile y 84 minus margin).
TARGETS = {
    tt.CARD_ID: TargetSpec(tt.CARD_ID, "2050170010", "UR", "native/training_list/asahi_portrait.png", (4, 5),
                           (14, 0, 101, 70)),
}
TARGETS_BY_ID = {spec.card_id: spec for spec in TARGETS.values()}
OPEN_UNCALIBRATED = True   # detail/preview/confirm now bind the list-selected identity by OCR, not one card's art
FIRST_TILE_SLOT = "r1c1"
FIRST_ROW_Y = 184           # grid y of the first row of the unscrolled training list (2026-10-01 default frame)
FIRST_ROW_TOLERANCE = 4
ART_NCC = 0.90          # target: measured 1.000 on the native tiles (Lv2 and Lv3 frames); other tiles <= 0.56
LOOKALIKE_NCC = 0.75    # anything this close that is not proven the target stops the search
SEARCH = 4              # +-px around the grid position (grid offset estimate and sub-pixel scroll positions)
CLICK_INSET = 6         # an action's inner rect must lie this far inside the located tile
SELECT_ACTIONS = ("training-select-card",)       # table actions that open a list tile (slot r1c2 today)
SELECT_ACTION_PREFIX = "training-list-slot-"     # per-slot actions
SCROLL_ACTIONS = {"down": "training-list-scroll-down", "up": "training-list-scroll-up"}  # reserved, disabled in the table
MAX_SCROLL_STEPS = tt.MAX_SCROLL_STEPS
# N support cards cannot be limit-broken and cap at Lv10. Evidence: the actual card's Wiki status table
# https://wikiwiki.jp/shinycolors/【アイドルのたまご】大崎甜花 (Lv10(MAX)); the native Lv1/2 samples bind its identity.
N_LEVEL_CAP = 10
# Pipeline Click nodes of the list (training.json): the legacy slot-2 node plus 20 grid slots and two wheel nodes.
LEGACY_SELECT_NODE = "SC.Training.List.Select"
SLOTS = tuple((r, c) for r in range(1, 4) for c in range(1, 8))
SELECT_NODES = {"training-select-card": LEGACY_SELECT_NODE,
                **{f"{SELECT_ACTION_PREFIX}r{r}c{c}": f"{LEGACY_SELECT_NODE}.R{r}C{c}" for r, c in SLOTS if (r, c) != (1, 2)}}
# 2026-11-22: grids shifted +51 / +103 px (the list can rest between offset-184 rows: offset 286 -> target_slot_unbound)
SHIFTED_ROWS = {"b": 3, "c": 2}
SELECT_NODES.update({f"{SELECT_ACTION_PREFIX}{ph}-r{r}c{c}": f"{LEGACY_SELECT_NODE}.{ph.upper()}.R{r}C{c}"
                     for ph, rows in SHIFTED_ROWS.items() for r in range(1, rows + 1) for c in range(1, 8)})
SCROLL_NODES = {"down": "SC.Training.List.ScrollDown", "up": "SC.Training.List.ScrollUp"}


def spec_for(card_id, meta=None):
    """Identity spec of ``card_id``: the calibrated one, or one built from the DB index ``meta`` (idol/title/rarity).
    None when the id is not an S card with a known rarity frame (the task then stops: identity_reference_missing)."""
    if not isinstance(card_id, str) or not re.fullmatch(tt.CARD_ID_PATTERN, card_id):
        return None
    if card_id in TARGETS_BY_ID:
        return TARGETS_BY_ID[card_id]
    rarity = (meta or {}).get("rarity")
    name = tt.card_name(meta)
    if rarity not in ci.FRAME_TIERS or name is None:
        return None
    return TargetSpec(name, card_id, rarity, None, (0, 0), (0, 0, 0, 0), calibrated=False)


def load_reference(spec, bundle=None):
    """Art crop (float32 HxWx3 RGB) of the calibrated native tile; raises FileNotFoundError when the private bundle
    lacks it (then the target cannot be identified and the task stops)."""
    import numpy as np
    from PIL import Image

    from agent.core import resource_bundles

    if not spec.calibrated:
        return None
    path = Path(bundle or resource_bundles.PRIVATE_NATIVE_BUNDLE) / "image" / spec.template
    if not path.is_file():
        raise FileNotFoundError(f"缺少训练列表身份模板 {spec.template}（运行 tools/build_native_resource.py）")
    with Image.open(path) as image:
        rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    x0, y0, x1, y1 = spec.art
    return rgb[y0:y1, x0:x1]


def _ncc(patch, ref):
    import numpy as np

    a = patch - patch.mean()
    b = ref - ref.mean()
    den = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float((a * b).sum() / den) if den > 1e-6 else 0.0


def art_score(frame_rgb, box, spec, reference):
    """Best NCC of the reference art around its expected place in ``box`` (+-SEARCH px)."""
    x, y = box[0] + spec.template_offset[0] + spec.art[0], box[1] + spec.template_offset[1] + spec.art[1]
    h, w = reference.shape[:2]
    best = -1.0
    for dy in range(-SEARCH, SEARCH + 1):
        for dx in range(-SEARCH, SEARCH + 1):
            X, Y = x + dx, y + dy
            if X < 0 or Y < 0 or Y + h > frame_rgb.shape[0] or X + w > frame_rgb.shape[1]:
                continue
            best = max(best, _ncc(frame_rgb[Y:Y + h, X:X + w], reference))
    return best


@dataclasses.dataclass(frozen=True)
class TileCheck:
    slot: str
    box: tuple
    art: float
    tier: str | None
    db_idol: str | None
    db_card: str | None      # card the DB matcher accepted (None: rejected)
    verdict: str             # target | lookalike | other
    reasons: tuple = ()

    def to_dict(self):
        return {"slot": self.slot, "box": list(self.box), "art": round(self.art, 4), "tier": self.tier,
                "db_idol": self.db_idol, "db_card": self.db_card, "verdict": self.verdict, "reasons": list(self.reasons)}


@dataclasses.dataclass(frozen=True)
class ListView:
    status: str              # found | ambiguous | absent
    offset: int | None
    targets: tuple           # TileCheck verdict == target
    lookalikes: tuple        # TileCheck verdict == lookalike
    tiles: int               # fully visible tiles checked
    digest: str              # content digest of the visible grid (scroll movement / content change)
    reasons: tuple = ()      # why a default-mode view is not a clean first tile

    @property
    def target(self):
        return self.targets[0] if self.status == "found" else None

    def to_dict(self):
        return {"status": self.status, "offset": self.offset, "tiles": self.tiles, "reasons": list(self.reasons),
                "targets": [t.to_dict() for t in self.targets], "lookalikes": [t.to_dict() for t in self.lookalikes]}


def grid_digest(frame):
    """Short digest of the list viewport (coarse grey, quantised): equal when the list did not move."""
    import numpy as np

    top, bottom = ci.LAYOUTS["idol_list"]["viewport"]
    region = frame.convert("L").crop((260, top, 1215, bottom)).resize((96, 48))
    data = (np.asarray(region, dtype=np.uint8) // 32).tobytes()
    return hashlib.sha256(data).hexdigest()[:16]


def check_tile(frame_rgb, frame, tile, spec, reference, db_idol):
    box = tuple(tile["box"])
    tier = ci.frame_tier(frame, box)
    db_card = tile["card_id"] if tile["status"] == "accepted" else None
    reasons = []
    if spec.calibrated:
        art = art_score(frame_rgb, box, spec, reference)
        if art < ART_NCC:
            reasons.append("art")
    else:
        art = 1.0 if db_card == spec.card_id else 0.0   # no native art reference: the DB matcher is the art check
        if db_card is None:
            reasons.append("db_not_accepted")
            # the matcher's own reason and numbers (2026-11-21 live: a UR tile of the target idol was rejected unlogged)
            reasons.append("db:{}:{}:s{}:m{}:v{}:e{}:vm{}".format(*(tile.get(k) for k in (
                "reason", "variant", "score", "margin", "verify", "edge", "variant_margin"))))
    if tier != spec.tier:
        reasons.append(f"frame:{tier}")
    if db_idol != spec.idol:
        reasons.append(f"db_idol:{db_idol}")
    if db_card is not None and db_card != spec.card_id:
        reasons.append(f"db_card:{db_card}")
    if not reasons:
        verdict = "target"
    elif spec.calibrated:
        verdict = "lookalike" if art >= LOOKALIKE_NCC else "other"
    else:
        # A tile the matcher could not place, of the target's idol: it may be the target -> stop (never scrolled past).
        # A tile accepted as another card (even of the same idol) is positively not the target. 2026-11-21 live: a
        # rejected tile whose closest same-idol variant is clearly another card (variant margin >= ci min_margin) is
        # not the target either (三峰結華 SSR Lv80 at r3c7 while looking for UR 2050070010).
        other_variant = (tile.get("variant") not in (None, spec.card_id)
                         and (tile.get("variant_margin") or 0) >= ci.DEFAULT_THRESHOLDS["min_margin"])
        verdict = "lookalike" if db_card is None and db_idol == spec.idol and not other_variant else "other"
    return TileCheck(tile["slot"], box, art, tier, db_idol, db_card, verdict, tuple(reasons))


def _coarse_idols(frame, index, tiles):
    import numpy as np

    crops = [frame.crop((x, y, x + w, y + h)) for x, y, w, h in (t["box"] for t in tiles)]
    if not crops:
        return []
    scores = index.coarse_scores(crops)
    return [index.idols[int(np.argmax(row))] if float(np.max(row)) >= ci.DEFAULT_THRESHOLDS["min_score"] else None
            for row in scores]


# Dimmed (untrainable) tiles, 2026-10-12. The game overlays untrainable cards with grey: every channel is scaled to about
# 0.6 (the art's brightest pixels read V = 152-153 instead of 255). Evidence (native 1280x720 frames, offline):
# train-list-20261012/05 (Vocal順, 8 bright / 13 dimmed tiles), training-selection-execute-20261001f-frames/frame-001
# (デフォルト, 16 / 5) and supervised-tasks-20261007/after_training_r1 (Lv順▼, 21 dimmed). Measured per tile:
#   S badge blue (hue 130-160, S > 150; share 0.239-0.255 on every tile): median V bright 235-239, dimmed 140-143;
#   white badge ring (V > 235, S < 40) share: bright 0.162-0.278, dimmed 0.000-0.001;
#   art 99th-percentile V: bright 254-255, dimmed 152-153.
# A tile is classified only when all three agree with wide margins; anything else is unknown (stop, never tapped).
BADGE_BOX = (6, 4, 38, 36)         # (dx, dy, w, h) of the S badge inside a list tile box
ART_BOX = (16, 2, 88, 68)          # art inside the tile box: right of the star column, above the number band
BADGE_HUE = (130, 160)             # PIL HSV (0-255) hue band of the S badge blue
BADGE_SAT_MIN = 150
BADGE_SHARE_MIN = 0.15
BRIGHT = {"badge_v": 215, "white": 0.08, "art_v": 230}    # minimums of a trainable (bright) tile
DIMMED = {"badge_v": 175, "white": 0.02, "art_v": 170}    # maximums of a dimmed (untrainable) tile
DETAIL_BADGE_BOX = (38, 98, 36, 34)  # S badge of the detail portrait (frame-023 of the 2026-10-01 execute frames)


def badge_features(hsv, x, y, w, h):
    """(blue share, median blue V, white share) of an S-badge region of a uint8 HSV array (None share: off canvas)."""
    import numpy as np

    region = hsv[y:y + h, x:x + w]
    if region.shape[:2] != (h, w):
        return 0.0, 0.0, 0.0
    hue, sat, val = region[..., 0], region[..., 1], region[..., 2]
    blue = (hue > BADGE_HUE[0]) & (hue < BADGE_HUE[1]) & (sat > BADGE_SAT_MIN)
    white = (val > 235) & (sat < 40)
    return float(blue.mean()), float(np.median(val[blue])) if blue.any() else 0.0, float(white.mean())


def tile_state(hsv, box):
    """("bright" | "dimmed" | None, features) of a list tile from a uint8 HSV array of the 1280x720 frame."""
    import numpy as np

    x, y = int(box[0]), int(box[1])
    bx, by, bw, bh = BADGE_BOX
    share, badge_v, white = badge_features(hsv, x + bx, y + by, bw, bh)
    ax, ay, aw, ah = ART_BOX
    art = hsv[y + ay:y + ay + ah, x + ax:x + ax + aw, 2]
    art_v = float(np.percentile(art, 99)) if art.shape == (ah, aw) else 0.0
    features = {"badge": round(share, 3), "badge_v": round(badge_v), "white": round(white, 3), "art_v": round(art_v)}
    if share < BADGE_SHARE_MIN:
        return None, features
    if badge_v >= BRIGHT["badge_v"] and white >= BRIGHT["white"] and art_v >= BRIGHT["art_v"]:
        return "bright", features
    if badge_v <= DIMMED["badge_v"] and white <= DIMMED["white"] and art_v <= DIMMED["art_v"]:
        return "dimmed", features
    return None, features


def detail_s_badge(frame_rgb):
    """True when the detail portrait shows the bright S badge (a support card; P cards carry another badge)."""
    import numpy as np
    from PIL import Image

    hsv = np.asarray(Image.fromarray(np.ascontiguousarray(frame_rgb)).convert("HSV"))
    share, badge_v, white = badge_features(hsv, *DETAIL_BADGE_BOX)
    return share >= BADGE_SHARE_MIN and badge_v >= BRIGHT["badge_v"] and white >= BRIGHT["white"]


def locate_default(frame, index, *, require_top=True):
    """First trainable tile in the visible order, by the game's own dimming (any sort key). Dimmed tiles before it are
    skipped; an unclassifiable tile before it stops (``ambiguous``). Its identity is optional: the DB card id when the
    matcher accepted the tile as an S card, else None (the detail page binds the name). The caller proves the list
    top before scanning downwards. A page of dimmed tiles only is ``absent`` (the bounded search may continue)."""
    import numpy as np
    frame = ci._check_frame(frame)
    result = ci.recognise(frame, index, "idol_list")
    tiles = result["tiles"]
    offset = result["offset"]
    digest = grid_digest(frame)
    if not tiles or (require_top and not any(t["slot"] == FIRST_TILE_SLOT for t in tiles)):
        return ListView("absent", offset, (), (), len(tiles), digest, ("no_first_tile",))
    if require_top and (offset is None or abs(offset - FIRST_ROW_Y) > FIRST_ROW_TOLERANCE):
        return ListView("ambiguous", offset, (), (), len(tiles), digest, (f"list_not_at_top:{offset}",))
    hsv = np.asarray(frame.convert("HSV"))
    skipped = []
    for tile in sorted(tiles, key=lambda t: (t["box"][1], t["box"][0])):
        box = tuple(tile["box"])
        state, features = tile_state(hsv, box)
        if state == "dimmed":
            skipped.append(f"dimmed:{tile['slot']}")
            continue
        card_id = tile["card_id"] if tile["status"] == "accepted" else None
        if card_id is not None and not re.fullmatch(tt.CARD_ID_PATTERN, card_id):
            card_id = None          # identity is optional; never bind a non-S id
        if state is None:
            check = TileCheck(tile["slot"], box, 0.0, None, ci.idol_of(card_id), card_id, "lookalike",
                              (f"tile_unclassified:{tile['slot']}:{features}",))
            return ListView("ambiguous", offset, (), (check,), len(tiles), digest, tuple(skipped) + check.reasons)
        check = TileCheck(tile["slot"], box, 1.0, ci.frame_tier(frame, box), ci.idol_of(card_id), card_id, "target",
                          (f"bright:{tile['slot']}:{card_id or 'unidentified'}",))
        return ListView("found", offset, (check,), (), len(tiles), digest, tuple(skipped) + check.reasons)
    return ListView("absent", offset, (), (), len(tiles), digest, tuple(skipped))


def locate(frame, spec, index, reference):
    """Identity check of every fully visible tile of a 1280x720 PIL list frame -> ListView."""
    import numpy as np

    frame = ci._check_frame(frame)
    result = ci.recognise(frame, index, "idol_list")
    tiles = result["tiles"]
    frame_rgb = np.asarray(frame, dtype=np.float32)
    idols = _coarse_idols(frame, index, tiles)
    checks = [check_tile(frame_rgb, frame, tile, spec, reference, idols[i]) for i, tile in enumerate(tiles)]
    targets = tuple(c for c in checks if c.verdict == "target")
    lookalikes = tuple(c for c in checks if c.verdict == "lookalike")
    if len(targets) == 1 and not lookalikes:
        status = "found"
    elif targets or lookalikes:
        status = "ambiguous"
    else:
        status = "absent"
    return ListView(status, result["offset"], targets, lookalikes, len(checks), grid_digest(frame))


# ---------------------------------------------------------------------------------------------------- click binding
def _inside(inner, box, inset=CLICK_INSET):
    ix, iy, iw, ih = inner
    bx, by, bw, bh = box
    return bx + inset <= ix and by + inset <= iy and ix + iw <= bx + bw - inset and iy + ih <= by + bh - inset


def select_actions(table):
    """Table actions that open a list tile: the legacy slot-2 action and any reviewed per-slot actions."""
    return [a.id for a in table.actions
            if a.enabled and (a.id in SELECT_ACTIONS or a.id.startswith(SELECT_ACTION_PREFIX))]


def click_action(box, table):
    """The one tile-opening action whose sampled inner rect lies inside ``box`` (None: no such action)."""
    hits = [i for i in select_actions(table) if _inside(table.inner_rect(i) or table.get(i).rect, box)]
    if not hits:
        return None
    # Shifted grids overlap: every hit lies inside the tile; take the rect nearest the tile centre.
    cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2

    def distance(action_id):
        x, y, w, h = table.get(action_id).rect
        return (x + w / 2 - cx) ** 2 + (y + h / 2 - cy) ** 2, action_id

    return min(hits, key=distance)


def binding_refusal(table, node, action):
    """None when the action table binds Pipeline click ``node`` to ``action`` (enabled or reserved); else a reason."""
    if not isinstance(node, str) or not isinstance(action, str):
        return "node_binding_missing"
    bound = table.node_actions(include_disabled=True).get(node)
    if bound != action:
        return f"node_binding_mismatch:{node}->{bound}!={action}"
    return None


def scroll_wired(table):
    ids = {a.id for a in table.actions if a.enabled}
    return all(a in ids for a in SCROLL_ACTIONS.values())


# ---------------------------------------------------------------------------------------------------- sort
# The user's list was Lv順/▼降順 on three supervised runs and the configured low-level card was never on the first
# screen. Maa now chooses the direction itself: one tap on the direction button (▼降順 <-> ▲昇順, the same toggle the
# capture plan uses) when the owned levels say the target sits nearer the other end. The sort *key* is never changed
# here (the 並び替え dialog of this list has no sample); the direction toggle is a reserved, disabled action until a
# supervised run proves the ▲昇順 frame.
SORT_ACTION = "training-list-direction-toggle"
SORT_NODE = "SC.Training.List.SortToggle"
MAX_SORT_TOGGLES = 1
MAX_SORT_UNSENT = 2       # a decided toggle whose click node did not run is decided again this often


def sort_wired(table):
    try:
        return table.get(SORT_ACTION).enabled
    except ValueError:
        return False


# Sort KEY (2026-10-11, live: the user's list was デフォルト/▼降順, so the direction alone cannot help). The key button
# (「デフォルト」/「Lv順」, ≈[970,108,163,42] on wing_samples/2026-10-10/20261010T142204827713Z) opens a 並び替え popup.
# 2026-10-11 native popup (sample-live-20261011/15-17,19): title 「並び替え」 ≈[410,128,110,32]; keys デフォルト/Lv順/特訓回数順,
# Vocal/Dance/Visual順, メンタル/加入/リリース順 in exactly the reserved 3x3 cells; 決定 OCR [602,518,76,45]; the selected cell
# is pink (0.105 加入順, 0.085 Lv順, others 0.000); after 決定 the list reads Lv順/▼降順. Same geometry as the
# ノウハウブック一覧 (live-capture-20261008-knowhow/knowhow_sort_options.png: 3x3 key buttons + 決定). The Agent binds the
# key to a cell only through the popup's own OCR (the 「Lv順」 text centre inside exactly one reserved cell) and the
# pink selected state before 決定, so a different layout stops instead of tapping another key. All reserved disabled.
SORT_KEY_TARGET = "Lv順"
SORT_OPEN_ACTION = "training-list-sort-open"
SORT_OPEN_NODE = "SC.Training.List.SortOpen"
SORT_OPEN_RECT = (976, 114, 150, 32)
SORT_POPUP_ROI = (350, 105, 582, 505)
SORT_POPUP_TITLE_ROI = (355, 110, 300, 60)
_CELL_X, _CELL_Y = (373, 557, 741), (220, 315, 409)
SORT_KEY_ACTIONS = {f"training-sort-key-r{r + 1}c{c + 1}": (x, y, 165, 66)
                    for r, y in enumerate(_CELL_Y) for c, x in enumerate(_CELL_X)}
SORT_KEY_NODES = {a: f"SC.Training.SortPopup.Pick.R{a[-3]}C{a[-1]}" for a in SORT_KEY_ACTIONS}
SORT_APPLY_ACTION = "training-sort-apply"
SORT_APPLY_NODE = "SC.Training.SortPopup.Apply"
SORT_APPLY_RECT = (558, 505, 164, 76)
SORT_SELECTED_PINK_MIN = 0.03          # knowhow popup: selected key 0.076 of the cell, others 0.000
MAX_SORT_KEY_CHANGES = 1


@dataclasses.dataclass(frozen=True)
class SortPopup:
    title: bool
    keys: tuple            # ((text, (x, y, w, h)), ...) every key-like text inside the popup
    apply: tuple | None    # box of 「決定」

    def key_box(self, key):
        boxes = [box for text, box in self.keys if text == key]
        return boxes[0] if len(boxes) == 1 else None

    def to_dict(self):
        return {"title": self.title, "keys": [t for t, _ in self.keys], "apply": self.apply}


def _norm(text):
    return re.sub(r"(?i)^lv", "Lv", re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or "")))


def _within(box, roi):
    x, y, w, h = box
    rx, ry, rw, rh = roi
    return rx <= x + w / 2 <= rx + rw and ry <= y + h / 2 <= ry + rh


def parse_sort_popup(items):
    """OCR items ({"text", "box"}) of one frame -> SortPopup (title, key texts, 決定)."""
    title, keys, apply = False, [], None
    for item in items or ():
        text, box = _norm(item.get("text")), tuple(int(v) for v in item.get("box", ()))
        if len(box) != 4 or not _within(box, SORT_POPUP_ROI):
            continue
        if text == "並び替え" and _within(box, SORT_POPUP_TITLE_ROI):
            title = True
        elif text == "決定":
            apply = box
        elif text == "デフォルト" or text.endswith("順"):
            keys.append((text, box))
    return SortPopup(title, tuple(keys), apply)


def sort_key_action(popup, key=SORT_KEY_TARGET):
    """The one reserved key cell containing the centre of ``key``'s text (None: missing, duplicated or unbound)."""
    box = popup.key_box(key)
    if not popup.title or box is None:
        return None
    hits = [a for a, rect in SORT_KEY_ACTIONS.items() if _within(box, rect)]
    return hits[0] if len(hits) == 1 else None


def cell_selected(image_rgb, action):
    """Pink share of a key cell (the selected key is drawn pink, the others grey)."""
    import numpy as np

    x, y, w, h = SORT_KEY_ACTIONS[action]
    region = np.asarray(image_rgb)[y:y + h, x:x + w, :3].astype(int)
    pink = ((region >= (230, 40, 110)) & (region <= (255, 140, 200))).all(axis=2)
    return float(pink.mean()) if pink.size else 0.0


def apply_ready(popup):
    return popup.title and popup.apply is not None and _within(popup.apply, SORT_APPLY_RECT)


def sort_key_wired(table):
    try:
        return all(table.get(a).enabled for a in (SORT_OPEN_ACTION, SORT_APPLY_ACTION, *SORT_KEY_ACTIONS))
    except ValueError:
        return False


def owned_levels(paths=None):
    """{card id: level} from the private passive reading store and the UI owned-levels export (newest wins first)."""
    import json

    root = Path(__file__).resolve().parents[2]
    paths = paths or (root / "dev_assets" / "private" / "readings" / "readings.json",
                      root / "ui" / "game-assets" / "owned-levels.js")
    levels = {}
    for path in paths:
        try:
            text = Path(path).read_text(encoding="utf-8")
            doc = json.loads(text[text.index("{"):text.rindex("}") + 1])
        except (OSError, ValueError):
            continue
        cards = doc.get("cards", doc.get("details", doc)) if isinstance(doc, dict) else {}
        for card_id, value in (cards.items() if isinstance(cards, dict) else ()):
            level = value.get("level") if isinstance(value, dict) else None
            if (isinstance(card_id, str) and re.fullmatch(tt.CARD_ID_PATTERN, card_id) and type(level) is int
                    and level > 0 and card_id not in levels):
                levels[card_id] = level
    return levels


def wanted_direction(order, card_id, levels):
    """(direction or None, reason) that brings ``card_id`` nearest the list top under the current sort key.

    Lv順 only. With a known owned level: the side with fewer S cards strictly before the target (ties keep the current
    direction). Unknown level: ▲昇順 (a card chosen for daily training is a low-level card; under ▼降順 it sits at the
    list end). Another sort key: None (never changed)."""
    if order is None or order.key != "level":
        return None, "sort_key_not_level"
    level = levels.get(card_id) if isinstance(levels, dict) else None
    if type(level) is not int:
        return "asc", "target_level_unknown_low_level_assumed"
    others = [v for k, v in levels.items() if k != card_id and type(v) is int]
    below = sum(1 for v in others if v < level)
    above = sum(1 for v in others if v > level)
    if below < above:
        return "asc", f"target_lv{level}_below{below}_above{above}"
    if above < below:
        return "desc", f"target_lv{level}_below{below}_above{above}"
    return order.direction, f"target_lv{level}_tie"


# ---------------------------------------------------------------------------------------------------- search
@dataclasses.dataclass(frozen=True)
class Decision:
    kind: str                # open | scroll | sort | stop | hold
    reason: str = ""
    slot: str | None = None
    box: tuple | None = None
    direction: str | None = None
    notes: tuple = ()

    def to_dict(self):
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in dataclasses.asdict(self).items() if v}


class ListSearch:
    """Per-run search over the scrolling list. ``observe`` once per new list frame.

    2026-10-11 (live: the game reopened the list at offset 298, not at its top): a list that is not at its top is first
    scrolled UP until the grid offset reads ``FIRST_ROW_Y`` (the top is proven by the offset, a stall elsewhere stops),
    then searched DOWN page by page; from a proven top the end of the list exhausts the search (no second pass). An
    unknown offset keeps the older plan (down to the end, then up to the start). ``top_only`` (default mode: the first
    tile at the top) only seeks the top and never searches downwards. A target found on the way is opened at once; a
    look-alike of the target idol stops (unidentified tiles of other idols are not look-alikes, see ``check_tile``)."""

    def __init__(self, max_steps=0, top_only=False):
        if type(max_steps) is not int or not 0 <= max_steps <= MAX_SCROLL_STEPS:
            raise ValueError("max_scroll_steps 须为 0–%d 的整数" % MAX_SCROLL_STEPS)
        self.max_steps = max_steps
        self.top_only = top_only is True
        self.reset()
        self.invalidations = 0
        self.stopped = None      # a stop is final for the run (the task concludes on it)

    def reset(self):
        self.order = None
        self.digest = None
        self.steps = 0
        self.direction = "down"
        self.turned = False
        self.pending = None
        self.start = None        # top | mid | unknown (offset of the first frame of this search)
        self.seeking = False     # scrolling up to the proven top before the downward search
        self.offsets = []        # grid offsets of the observed frames (report)

    def observe(self, order, view, can_scroll=False):
        if self.stopped is not None:
            return self.stopped
        decision = self._observe(order, view, can_scroll)
        if decision.kind == "stop":
            self.stopped = decision
        return decision

    def _scroll(self, notes):
        if self.steps >= self.max_steps:
            return Decision("stop", "target_not_found_within_budget", notes=notes + (f"steps={self.steps}",))
        self.steps += 1
        self.pending = self.direction
        return Decision("scroll", "seek_list_top" if self.seeking else "target_not_in_view",
                        direction=self.direction, notes=notes)

    def _observe(self, order, view, can_scroll):
        notes = []
        if self.order is not None and order != self.order:
            notes.append("list_order_changed")
        elif self.pending is None and self.digest is not None and view.digest != self.digest:
            notes.append("list_content_changed")
        if notes:
            self.invalidations += 1
            self.reset()
        moved = self.pending is not None and view.digest != self.digest
        stalled = self.pending if self.pending is not None and not moved else None
        self.order, self.digest, self.pending = order, view.digest, None
        self.offsets = (self.offsets + [view.offset])[-40:]
        at_top = view.offset is not None and abs(view.offset - FIRST_ROW_Y) <= FIRST_ROW_TOLERANCE
        if self.start is None:
            self.start = "top" if at_top else ("mid" if view.offset is not None else "unknown")
            if self.start == "mid":
                self.seeking, self.direction = True, "up"
            elif self.start == "top":
                self.turned = True            # a search from the proven top needs one downward pass only
        notes = tuple(notes)
        if self.seeking and at_top:
            self.seeking, self.direction, self.turned = False, "down", True   # top proven: one downward pass
            notes += ("list_top_reached",)
        if view.status == "found" and not (self.top_only and not at_top):
            t = view.target
            return Decision("open", "target_located", t.slot, t.box, notes=notes)
        if view.status == "ambiguous" and not (self.top_only and not at_top):
            return Decision("stop", "target_ambiguous", notes=notes)
        if self.top_only and at_top:
            return Decision("stop", "target_not_visible", notes=notes)
        if not can_scroll or self.max_steps == 0:
            notes += (() if can_scroll else ("scroll_unavailable",))
            if not at_top and view.offset is not None:
                notes += (f"list_not_at_top:{view.offset}",)
            return Decision("stop", "target_not_visible", notes=notes)
        if stalled is not None:
            if self.seeking:
                return Decision("stop", "target_not_visible", notes=notes + (f"list_top_unverified:{view.offset}",))
            if stalled == "down" and not self.turned:
                self.direction, self.turned = "up", True     # unknown start: the start may have been mid-list
            else:
                return Decision("stop", "target_absent", notes=notes + ("list_exhausted",))
        if self.top_only and not self.seeking:
            return Decision("stop", "target_not_visible", notes=notes + (f"list_not_at_top:{view.offset}",))
        return self._scroll(notes)


STOP_OUTCOMES = {
    "target_ambiguous": "target_ambiguous",
    "target_not_visible": "target_not_visible",
    "target_absent": "target_not_visible",
    "target_not_found_within_budget": "target_not_visible",
}
