"""Maa custom recognitions/actions of the Training task (B5). Rules live in ``agent.core.training_task``."""
from __future__ import annotations

import dataclasses

from agent.core import task_common as tc
from agent.core import task_custom
from agent.core import training_select as ts
from agent.core import training_task as tt

PREFIX = "SC.Training"
CONFIG_NODES = ("SC.Training.Config.Card", "SC.Training.Config.Daily", "SC.Training.Config.List")


def _default_card_index():
    from agent.core import card_inventory as ci
    return ci.CardIndex.from_assets(kind="S")


def _default_table():
    from agent.core import pipeline_actions
    return pipeline_actions.default_table()


class TrainingRuntime(task_custom.TaskRuntime):
    KIND = "training"
    RUN = tt.TrainingRun
    CONFIG = tt.TrainingConfig
    SPEND_ACTION = tt.SPEND_ACTION
    _shared_index = None   # the DB thumbnail index is large; shared by every runtime of the process

    def __init__(self, *args, card_index=None, reference_loader=ts.load_reference, table=None,
                 open_uncalibrated=None, level_source=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._open_uncalibrated = open_uncalibrated   # None: generic identity guards; False disables these cards in tests
        self._card_index = card_index            # CardIndex or a zero-argument factory (tests inject one)
        self._reference_loader = reference_loader
        self._references = {}
        self._table = table
        self._level_source = level_source   # zero-argument callable -> {card id: owned level}; default ts.owned_levels
        self._levels = None

    # -- list: sort/filter reading and identity-based location, see agent/core/training_select.py
    def card_index(self):
        if self._card_index is None:
            if TrainingRuntime._shared_index is None:
                TrainingRuntime._shared_index = _default_card_index()
            self._card_index = TrainingRuntime._shared_index
        elif callable(self._card_index):
            self._card_index = self._card_index()
        return self._card_index

    @staticmethod
    def _meta(index, card_id):
        meta = getattr(index, "meta", None)
        return meta.get(card_id) if isinstance(meta, dict) else None

    def table(self):
        if self._table is None:
            self._table = _default_table()
        return self._table

    def reference(self, spec):
        if spec.name not in self._references:
            self._references[spec.name] = self._reference_loader(spec)
        return self._references[spec.name]

    def read_order(self, context, image):
        vision = self.vision(context)
        texts = {name: vision.text(image, list(box)) for name, box in
                 (("sort", ts.SORT_BOX), ("direction", ts.DIRECTION_BOX), ("filter", ts.FILTER_BOX))}
        order, reasons = ts.parse_order(texts["sort"], texts["direction"], texts["filter"])
        return texts, order, reasons

    def list_decision(self, context, image):
        """(Decision, action id or None) for this list frame, evaluated once per frame digest. Concludes the run on
        every stop (unknown order, filter, target not identified/visible/clickable); never reuses a box of another
        frame."""
        run = self.run
        digest = tc.image_digest(image)
        if run.list_frame is not None and run.list_frame[0] == digest:
            return run.list_frame[1], run.list_frame[2]
        decision, action = self._list_decision(context, image)
        run.list_frame = (digest, decision, action)
        return decision, action

    def _list_decision(self, context, image):
        import numpy as np
        from PIL import Image

        run = self.run
        texts, order, reasons = self.read_order(context, image)
        if reasons:
            run.note({"stage": "List", "ocr": texts, "reasons": reasons})
            if ts.only_unreadable(reasons):
                run.list_unreadable += 1
                if run.list_unreadable <= tt.MAX_UNREADABLE_FRAMES:
                    return ts.Decision("hold", "order_unreadable"), None
            outcome = ts.order_outcome(reasons)
            run.finish(outcome, *reasons)
            return ts.Decision("stop", outcome), None
        run.list_unreadable = 0
        spec = None
        try:
            index = self.card_index()
            if run.config.card_id:      # explicit choice: any confirmed S card known to the DB thumbnail index
                spec = ts.spec_for(run.config.card_id, self._meta(index, run.config.card_id))
                if spec is None:
                    raise LookupError("no list identity for " + run.config.card_id)
                reference = self.reference(spec)
        except Exception as exc:  # missing private bundle / ui/game-assets: the target cannot be identified
            run.finish("identity_reference_missing", str(exc)[:160])
            return ts.Decision("stop", "identity_reference_missing"), None
        frame = Image.fromarray(np.ascontiguousarray(np.asarray(image)[:, :, ::-1]))
        if spec:
            view = ts.locate(frame, spec, index, reference)
            if view.status == "ambiguous" and view.lookalikes:
                view = self._level_filter(context, image, spec, view)
        else:
            previous_order = getattr(run, "default_order", None)
            if previous_order is not None and previous_order != order:
                run.default_top_verified = False
                run.list_search = None
            run.default_order = order
            at_top = getattr(run, "default_top_verified", False)
            view = ts.locate_default(frame, index, require_top=not at_top)
        settle = (view.offset, view.digest, view.status)
        if getattr(run, "list_settle", None) != settle and getattr(run, "list_settle_holds", 0) < tt.MAX_SETTLE_HOLDS:
            run.list_settle, run.list_settle_holds = settle, getattr(run, "list_settle_holds", 0) + 1
            return ts.Decision("hold", "list_settling"), None
        run.list_settle_holds = 0
        if spec is None and view.offset is not None and abs(view.offset - ts.FIRST_ROW_Y) <= ts.FIRST_ROW_TOLERANCE:
            run.default_top_verified = True      # only a settled reading proves the top (2026-10-12: 184 -> 286 drift)
        if run.list_search is None:
            run.list_search = ts.ListSearch(run.config.max_scroll_steps)
        table = self.table()
        sort = self._sort_step(order, spec, view, table)
        if sort is not None:
            return sort
        if spec is None and not getattr(run, "default_top_verified", False):
            run.list_search.top_only = True
        elif spec is None:
            run.list_search.top_only = False
        decision = run.list_search.observe(order, view, can_scroll=ts.scroll_wired(table))
        action = None
        record = {"stage": "List", "order": order.to_dict(), "view": view.to_dict(), "decision": decision.to_dict(),
                  "search": {"start": run.list_search.start, "steps": run.list_search.steps,
                             "offsets": run.list_search.offsets[-6:]}}
        if decision.kind == "open":
            found_id = spec.card_id if spec else view.target.db_card
            found_spec = spec or (ts.spec_for(found_id, self._meta(index, found_id)) if found_id else None)
            record["mode"] = "explicit" if spec else "default"
            record["card_id"] = found_id
            if found_spec is None and spec is None:
                # Default mode: a bright tile the DB cannot name is still the first trainable card; the
                # detail page binds its name (``bind_default_identity``) before any spend.
                record["identity"] = "detail_page"
                found_id = None
            allowed = ts.OPEN_UNCALIBRATED if self._open_uncalibrated is None else self._open_uncalibrated
            if (found_spec is None or not found_spec.calibrated) and not allowed:
                # Explicit test/deployment override may disable the generic identity path.
                run.note(record)
                run.finish("target_uncalibrated", found_id or "unidentified", found_spec.name if found_spec else "-",
                           f"order={order.key}/{order.direction}")
                return ts.Decision("stop", "target_uncalibrated", decision.slot, decision.box), None
            action = ts.click_action(decision.box, table)
            record["action"] = action
            if action is None:
                run.note(record)
                run.finish("target_slot_unbound", f"slot={decision.slot}", f"order={order.key}/{order.direction}")
                return ts.Decision("stop", "target_slot_unbound", decision.slot, decision.box), None
            run.target_id, run.target_name = found_id, (found_spec.name if found_spec else None)
            run.target_mode = record["mode"]
        elif decision.kind == "scroll":
            action = ts.SCROLL_ACTIONS[decision.direction]
            record["action"] = action
        else:
            run.note(record)
            hint = (getattr(run, "sort_hint", None),) if getattr(run, "sort_hint", None) else ()
            run.finish(ts.STOP_OUTCOMES[decision.reason], decision.reason, *decision.notes, *hint,
                       f"order={order.key}/{order.direction}")
            return decision, None
        run.note(record)
        return decision, action

    def tile_level(self, context, image, box):
        """Lv.NN of one list tile (owned_levels.read_level on this frame), None when not a clean read."""
        import numpy as np

        from agent.core import owned_levels as ol
        from agent.core.resource_observer import MaaVision

        vision = MaaVision(context)
        level, _, _ = ol.read_level(lambda bgr, roi: [(t, s) for t, s, _ in vision.ocr(bgr, roi)],
                                    np.asarray(image), box)
        return level

    def _level_filter(self, context, image, spec, view):
        """2026-11-21 live: several tiles of the target's idol that the art matcher cannot place (三峰結華 SSR Lv80 vs
        the UR target at Lv66). A tile whose Lv band reads another level than the target's owned level is not the
        target. One remaining tile with the target's level and frame is opened; the detail page's card name must
        still equal the target before any spend (identity_mismatch -> leave). Without a known level nothing changes."""
        want = self.levels().get(spec.card_id)
        if type(want) is not int:
            return view
        keep, matched, notes = [], [], []
        for look in view.lookalikes:
            level = self.tile_level(context, image, look.box)
            notes.append(f"lv:{look.slot}:{level}")
            if level is not None and level != want:
                continue
            keep.append(look)
            if level == want and look.tier == spec.tier:
                matched.append(look)
        self.run.note({"stage": "List", "level_filter": {"want": want, "tiles": notes}})
        targets = view.targets
        if not targets and len(keep) == 1 and matched == keep:
            targets = (dataclasses.replace(keep[0], verdict="target", reasons=keep[0].reasons + (f"level:{want}",)),)
            keep = []
        status = ("found" if len(targets) == 1 and not keep else "ambiguous" if targets or keep else "absent")
        return dataclasses.replace(view, status=status, targets=tuple(targets), lookalikes=tuple(keep))

    def levels(self):
        if self._levels is None:
            try:
                self._levels = (self._level_source or ts.owned_levels)()
            except Exception:  # a missing/corrupt private store only means "level unknown"
                self._levels = {}
        return self._levels

    def swapped_ids(self):
        """Ids whose art-manifest and card-library names cross (tt.swapped_names): no library alias for them."""
        if getattr(self, "_swapped", None) is None:
            meta = getattr(self.card_index(), "meta", None) or {}
            manifest = {cid: tt.card_name(m) for cid, m in meta.items() if tt.card_name(m)}
            library = {cid: c.get("name") for cid, c in self.card_library().items() if c.get("name")}
            self._swapped = tt.swapped_names(manifest, library)
        return self._swapped

    def card_library(self):
        """Local S-card stats for the sort plan (picker_sort.training_key); missing data -> {} (Lv順 as before)."""
        if getattr(self, "_library", None) is None:
            try:
                source = getattr(self, "_library_source", None)
                if source is not None:
                    self._library = source() or {}
                else:
                    from agent.core import team_recommend
                    self._library = team_recommend.load_library().get("cards") or {}
            except Exception:  # noqa: BLE001 - optional private data
                self._library = {}
        return self._library

    def sort_key_want(self, spec):
        """The 並び替え key this run wants (picker_sort.training_key, decided once per run): 「Lv順」 unless the owned
        levels and local stats put the target strictly earlier under its attribute key (▼降順)."""
        run = self.run
        if getattr(run, "sort_key_want", None) is None:
            from agent.core import picker_sort as ps
            levels = self.levels()
            if len(levels) <= ps.MIN_OWNED_FOR_ESTIMATE:
                key, why = "Lv順", f"owned_levels_sparse:{len(levels)}"     # no library load needed
            else:
                key, why = ps.training_key(spec.card_id if spec else None, levels, self.card_library())
            run.sort_key_want, run.sort_key_why = key, why
        return run.sort_key_want

    def _sort_step(self, order, spec, view, table):
        """Explicit target not on this screen: set the list direction the target needs (at most one toggle per run).

        Returns a (Decision, action) to send the toggle, a stop when a requested toggle did not take effect, or None
        to continue with the bounded search. The toggle is planned only on a frame proving the list has no target and
        no look-alike; the next list frame must read the requested direction (else ``sort_toggle_unverified``)."""
        run = self.run
        if getattr(run, "sort_key_phase", None) == "applied":
            # 決定 was pressed for the wanted key: this first list frame must read it, otherwise stop (never again)
            run.sort_key_phase = "done"
            expected_key = ts.SORT_KEYS[getattr(run, "sort_key_want", None) or ts.SORT_KEY_TARGET]
            if order.key != expected_key:
                run.note({"stage": "List", "sort_key": {"expected": expected_key, "read": order.to_dict()}})
                run.finish("sort_key_unverified", f"order={order.key}/{order.direction}")
                return ts.Decision("stop", "sort_key_unverified"), None
            run.list_search.reset()
            run.list_search.order = order
        expected = getattr(run, "sort_expected", None)
        if expected is not None and not getattr(run, "sort_sent", False):
            # 2026-11-21 live: the toggle was decided but its click node never ran (no tap in the host log); the next
            # frame still read ▼降順 and the run stopped. Not sent = nothing to verify: decide again (bounded).
            run.sort_expected = None
            run.sort_unsent = getattr(run, "sort_unsent", 0) + 1
            run.note({"stage": "List", "sort_not_sent": run.sort_unsent})
            if run.sort_unsent <= ts.MAX_SORT_UNSENT:
                run.sort_toggles = max(0, getattr(run, "sort_toggles", 0) - 1)
            expected = None
        if expected is not None:
            run.sort_expected, run.sort_sent = None, False
            if order.direction != expected:
                run.note({"stage": "List", "sort": {"expected": expected, "read": order.to_dict()}})
                run.finish("sort_toggle_unverified", f"expected={expected}", f"order={order.key}/{order.direction}")
                return ts.Decision("stop", "sort_toggle_unverified"), None
            run.list_search.reset()   # a list the task re-sorted itself: a new search, not an unexpected change
            run.list_search.order = order
        if spec is None:
            return self._default_direction_step(order, view, table)
        if view.status != "absent":
            return None
        want_label = self.sort_key_want(spec)
        want_key = ts.SORT_KEYS[want_label]
        if order.key != want_key:
            # 2026-10-11: the user's list was デフォルト; choose Lv順 in the 並び替え popup first (once per run).
            # 2026-10-14: or the target's attribute key when the stats put it earlier (agent/core/picker_sort.py).
            spent = getattr(run, "sort_key_changes", 0) >= ts.MAX_SORT_KEY_CHANGES
            record = {"stage": "List", "sort_plan": {"order": order.to_dict(), "want_key": want_key}}
            if want_key != "level":
                record["sort_plan"]["why"] = getattr(run, "sort_key_why", None)
            if spent or not ts.sort_key_wired(table):
                if not spent:
                    record["sort_plan"]["action"] = "unavailable"
                    run.note(record)
                if want_key == "level" or order.key != "level":
                    if not spent:
                        run.sort_hint = f"sort_change_needed:{want_key}"
                    return None
                # an attribute key cannot be set but the list is Lv順: keep the Lv順 direction plan below
            else:
                return self._open_sort_key(record)
        return self._direction_step(order, spec, table)

    def _default_direction_step(self, order, view, table):
        """Default mode (训练卡留空) on Lv順▼降順: every maxed (grey) card comes first and the first trainable one sits
        near the list end (live 2026-10-31: three scrolls, then ``target_slot_unbound`` at offset 286 where the grid no
        longer meets the slot rects). A first screen without a bright tile toggles once to ▲昇順, which puts the lowest
        level (trainable) card at the top; the next frame must read it (``sort_toggle_unverified``). Other keys are the
        user's order and are kept."""
        run = self.run
        if order.key != "level" or order.direction != "desc" or view.status != "absent":
            return None
        if getattr(run, "sort_toggles", 0) >= ts.MAX_SORT_TOGGLES:
            return None
        record = {"stage": "List", "sort_plan": {"order": order.to_dict(), "want": "asc", "why": "default_lv_desc_all_dimmed"}}
        if not ts.sort_wired(table):
            record["sort_plan"]["action"] = "unavailable"
            run.note(record)
            run.sort_hint = "sort_change_needed:level/asc"
            return None
        run.sort_toggles = getattr(run, "sort_toggles", 0) + 1
        run.sort_expected = "asc"
        record["action"] = ts.SORT_ACTION
        run.note(record)
        return ts.Decision("sort", "set_direction", direction="asc", notes=("default_lv_desc_all_dimmed",)), ts.SORT_ACTION

    def _open_sort_key(self, record):
        run = self.run
        run.sort_key_changes = getattr(run, "sort_key_changes", 0) + 1
        run.sort_key_phase = "open_requested"
        record["action"] = ts.SORT_OPEN_ACTION
        run.note(record)
        return ts.Decision("sort_open", "set_sort_key"), ts.SORT_OPEN_ACTION

    def _direction_step(self, order, spec, table):
        run = self.run
        if order.key != "level":
            return None
        if getattr(run, "sort_toggles", 0) >= ts.MAX_SORT_TOGGLES:
            return None
        want, why = ts.wanted_direction(order, spec.card_id, self.levels())
        record = {"stage": "List", "sort_plan": {"order": order.to_dict(), "want": want, "why": why}}
        if want is None or want == order.direction:
            run.note(record)
            return None
        if not ts.sort_wired(table):
            record["sort_plan"]["action"] = "unavailable"
            run.note(record)
            run.sort_hint = f"sort_change_needed:{order.key}/{want}"
            return None
        run.sort_toggles = getattr(run, "sort_toggles", 0) + 1
        run.sort_expected = want
        record["action"] = ts.SORT_ACTION
        run.note(record)
        return ts.Decision("sort", "set_direction", direction=want, notes=(why,)), ts.SORT_ACTION

    def list_step(self, context, image, p):
        """``{"want": "open", "action": id, "node": click node}`` (tap that tile), ``{"want": "scroll",
        "direction": d, "node": click node}`` or ``{"want": "hold"}`` (another frame: order unreadable, or the run
        just concluded on this frame).

        The open/scroll answer is bound to its Pipeline click node: the action table must bind ``node`` to the
        requested action, otherwise the run stops (``list_binding_invalid``) instead of tapping another target. A
        positive answer consumes the frame's cached decision, so the capture after the input (even a pixel-identical
        one: the list end) is a new observation for the bounded search."""
        run, want = self.run, p.get("want")
        if run is None:
            return False
        if want == "sort_sent":
            # SC.Training.List.Sorted runs only after the toggle's click. 2026-11-21 live: Plan.Sort answered yes on a
            # frame whose list guard failed, so no click ran; the toggle counts as sent only here.
            if getattr(run, "sort_expected", None) is not None:
                run.sort_sent = True
            return True
        if want in ("sort_pick", "sort_apply", "sort_popup_unexpected"):
            return self.sort_popup_step(context, image, p)
        if want == "hold":
            if run.finished:
                return not run.halted
            held = run.list_frame is not None and run.list_frame[1].kind == "hold"
            if held and run.list_frame[1].reason == "list_settling":
                # The hold ends this round: the next capture (even pixel-identical = a still list) is a new reading.
                # Consuming here, never on a timer, keeps every Want of one capture on the same decision.
                run.list_frame = None
            return held
        if run.finished or run.phase != "active":
            return False
        if want not in ("open", "scroll", "sort", "sort_open"):
            return False
        requested = ({"open": p.get("action"), "sort": ts.SORT_ACTION, "sort_open": ts.SORT_OPEN_ACTION}.get(want)
                     or ts.SCROLL_ACTIONS.get(p.get("direction")))
        refusal = ts.binding_refusal(self.table(), p.get("node"), requested)
        if refusal is not None:
            run.note({"stage": "List", "binding": refusal})
            run.finish("list_binding_invalid", refusal)
            return False
        decision, action = self.list_decision(context, image)
        ok = decision.kind == want and action == requested
        if ok:
            run.list_frame = None
            run.list_settle = None       # the frames after this input need their own settled reading
            # default mode, tile without DB identity: only the detail page this tap opens may bind the card name
            run.default_bind_pending = want == "open" and run.target_mode == "default" and run.target_name is None
        return ok

    def sort_popup_step(self, context, image, p):
        """並び替え popup (opened by this run only): pick Lv順 once, then 決定 once with Lv順 shown selected.

        ``sort_pick``: the popup OCR binds 「Lv順」 to exactly one reserved key cell, which must be this node's action.
        ``sort_apply``: after the pick, Lv順's cell is pink and 「決定」 sits in the reserved apply rect; when no key
        could be picked (layout differs) 決定 closes the popup with the selection unchanged (nothing was tapped).
        ``sort_popup_unexpected``: a popup this run did not open, or a phase that does not expect it -> stop."""
        run, want = self.run, p.get("want")
        phase = getattr(run, "sort_key_phase", None)
        if want == "sort_popup_unexpected":
            if phase in ("open_requested", "pick_requested", "abandon"):
                return False
            if not run.finished:
                run.finish("sort_popup_unexpected", f"phase={phase}")
            return True
        if run.finished and phase != "abandon":
            return False
        from agent.core.task_actions import _ocr_items

        popup = ts.parse_sort_popup(_ocr_items(context.run_recognition("SC.Training.SortPopup.OCR", image)))
        table = self.table()
        if want == "sort_pick":
            if phase != "open_requested" or not popup.title:
                return False
            want_label = getattr(run, "sort_key_want", None) or ts.SORT_KEY_TARGET
            action = ts.sort_key_action(popup, want_label)
            record = {"stage": "SortPopup", "popup": popup.to_dict(), "action": action, "key": want_label}
            if action is None:
                run.note(record)
                run.sort_key_phase = "abandon"
                run.finish("sort_key_unavailable", ("lv" if want_label == "Lv順" else ts.SORT_KEYS[want_label]) + "_key_not_bound_to_one_cell")
                return False
            if action != p.get("action") or ts.binding_refusal(table, p.get("node"), action) is not None:
                return False
            run.note(record)
            run.sort_key_phase = "pick_requested"
            run.sort_pick = action
            return True
        if want == "sort_apply":
            if not ts.apply_ready(popup) or ts.binding_refusal(table, p.get("node"), ts.SORT_APPLY_ACTION) is not None:
                return False
            if phase == "abandon":
                run.sort_key_phase = "closed"
                return True
            if phase != "pick_requested":
                return False
            share = ts.cell_selected(image[:, :, ::-1], run.sort_pick)
            if share < ts.SORT_SELECTED_PINK_MIN:
                run.note({"stage": "SortPopup", "selected_share": round(share, 4)})
                return False      # the pick has not shown yet: another frame (the node times out -> stop)
            run.sort_key_phase = "applied"
            return True
        return False

    def abort(self, context, p):
        """Shared abort; a list input whose effect was not observed (``list_error``) gets its own outcome."""
        run, reason = self.run, p.get("list_error")
        if run is not None and reason and not self.awaiting_spend():
            if not run.finished:
                run.finish("list_input_unverified", str(reason))
            self.report()
            return False
        return super().abort(context, p)

    # -- identity re-check after the list tap (detail / One / confirm): OCR of the card name line
    def identity(self, context, image):
        run = self.run
        digest = tc.image_digest(image)
        if run.identity_frame is not None and run.identity_frame[0] == digest:
            return list(run.identity_frame[1])
        name = self.vision(context).text(image, tt.CARD_NAME_BOX, tt.CARD_NAME_THRESHOLD)
        reasons = tt.identity_reasons(run.config, name, run.target_name)
        if reasons == ["identity_mismatch"] and run.target_id:
            # 2026-11-22 live: the art manifest names 2040060090 【当冬の隙間】白瀬咲耶 (data typo; the game and the card
            # library say 【冬の隙間】). The read name may equal the card library's name of the same id instead.
            alias = (self.card_library().get(run.target_id) or {}).get("name")
            if alias and run.target_id not in self.swapped_ids() and not tt.identity_reasons(run.config, name, alias):
                reasons = []
        run.identity_frame = (digest, tuple(reasons))
        if reasons:
            run.note({"stage": "Identity", "name": name, "reasons": reasons})
        return reasons

    def check_identity(self, context, image):
        """Detail shell of any card before the spend. True = no further step on this frame (a different card is
        open -> identity_mismatch and leave; the name is unreadable -> another frame). False = the configured card."""
        run = self.run
        if run.finished or run.phase not in ("active", "plus_tapped", "submitted"):
            return False
        if run.config.default_mode and run.target_name is None and run.phase == "active":
            if getattr(run, "default_bind_pending", False):
                return self.bind_default_identity(context, image)
            return False  # started on another detail: the existing guarded BackOne returns to the list to select
        reasons = self.identity(context, image)
        if not reasons:
            return False
        if reasons == ["card_name_unreadable"]:
            run.unreadable += 1
            if run.unreadable > tt.MAX_UNREADABLE_FRAMES:
                run.finish("ocr_unreadable", "card_name")
            return True
        run.finish("identity_mismatch", *reasons)
        return True

    def bind_default_identity(self, context, image):
        """Default mode, first bright tile without DB identity: the detail page this run's tap opened names
        the target. Requires the bright S badge and a 「【title】name」 line; the card id is bound when exactly one DB
        card has that name. True = hold/stop on this frame (unreadable / not an S card), False = bound, continue."""
        run = self.run
        name = self.vision(context).text(image, tt.CARD_NAME_BOX, tt.CARD_NAME_THRESHOLD)
        reasons = tt.default_name_reasons(name)
        if not reasons and not ts.detail_s_badge(image[:, :, ::-1]):
            reasons = ["detail_not_s_card"]
        if reasons:
            run.note({"stage": "Identity", "mode": "default", "name": name, "reasons": reasons})
            if reasons == ["detail_not_s_card"]:
                run.finish("default_card_not_trainable", *reasons)
                return True
            run.unreadable += 1
            if run.unreadable > tt.MAX_UNREADABLE_FRAMES:
                run.finish("ocr_unreadable", "card_name")
            return True
        matches = []
        try:
            meta = getattr(self.card_index(), "meta", None) or {}
            want = tt.name_key(name)
            matches = [cid for cid, m in meta.items() if tt.card_name(m) and tt.name_key(tt.card_name(m)) == want]
        except Exception:  # identity by name only; a missing DB index never blocks the name binding
            matches = []
        run.target_name, run.default_bind_pending, run.unreadable = name, False, 0
        run.target_id = matches[0] if len(matches) == 1 else None
        run.identity_frame = None
        run.note({"stage": "Identity", "mode": "default", "bound": name, "card_id": run.target_id,
                  "db_matches": len(matches)})
        return False

    def started(self, run):
        # No chosen card = default mode (first trainable S card of the current list sort), never a silent skip.
        if run.config.selected:
            try:
                spec = ts.spec_for(run.config.card_id, self._meta(self.card_index(), run.config.card_id))
                if spec is not None:
                    run.target_id, run.target_name, run.target_mode = spec.card_id, spec.name, "explicit"
            except Exception:
                pass  # the list step reports a missing reference; no detail identity is accepted without a name
        events = self.log.events()
        state = tt.log_state(events)
        intent = tt.open_intent(events)
        if intent is not None:
            # A still-open result dialog resolves it (read-only, then 閉じる + After check); any other screen starts
            # this run afresh (Resolve.Elsewhere; an earlier uncertain result only warns).
            run.config_target = (run.target_id, run.target_name, run.target_mode)
            run.intent, run.phase = intent, "resolving"
            run.baseline = {n: intent[n] for n in ("inventory", "level", "remaining_exp")}
            run.target_id, run.target_name = intent.get("card_id"), intent.get("card")
            run.ticket_layout = intent.get("ticket_layout", "lv50")
        elif state.uncertain:
            run.notes.append("uncertain_spend_earlier_today")
        elif state.verified and run.config.skip_if_done_today:
            run.finish("nothing_to_do", "trained_earlier_today")

    def read(self, context, image, stage):
        vision = self.vision(context)
        texts = {}
        for name in tt.STAGE_FIELDS[stage]:
            crop, kind, cutoff, pattern, unanimous = tt.NUMERIC_FIELDS[name]
            if name == "inventory" and getattr(self.run, "ticket_layout", None) == "top":
                crop = tt.TOP_INVENTORY_BOX
            texts[name] = vision.number(image, crop, kind, pattern, cutoff=cutoff, unanimous=unanimous)
        return texts, tt.parse_fields(texts)

    def _unreadable(self, run, stage):
        run.unreadable += 1
        if run.unreadable > tt.MAX_UNREADABLE_FRAMES:
            run.finish("ocr_unreadable", stage)
        return False

    # -- recognitions
    def extra_state(self, want, context, image, p):
        run = self.run
        if want == "closing":
            return run.phase == "result_closing"
        if want == "resolve_elsewhere":
            if run.phase == "resolving":
                # No result dialog: the earlier spend stays uncertain (the UI warns); this run trains from the screen.
                run.notes.append("uncertain_spend_earlier_today")
                run.intent, run.baseline, run.phase = None, None, "active"
                run.target_id, run.target_name, run.target_mode = run.config_target
                run.ticket_layout = "lv50"
            return False
        if want == "confirm_cancel":
            if run.phase == "awaiting_result":
                return False
            if run.finished:
                return True
            reasons = run.confirm_rejected or (["unexpected_confirm"] if run.phase != "submitted" else
                                               ["spend_not_enabled"] if not self.can_spend() else None)
            if not reasons and not self.vision(context).hit("SC.Training.Confirm.Guard", image):
                # The dialog shell is shown but the sole-material proof (Bronze x1 only) is missing: never confirm.
                reasons = ["material_not_sole_bronze_x1"]
            if not reasons:
                return False
            outcome = ("unexpected_confirm" if reasons == ["unexpected_confirm"] else
                       "spend_not_enabled" if reasons == ["spend_not_enabled"] else "confirm_mismatch")
            run.finish(outcome, *reasons)
            return True
        return False

    def plan(self, context, image, p):
        run, step = self.run, p.get("step")
        if run is None or run.finished:
            return False
        if (step == "plus" and run.phase == "active" and run.target_name is None
                and getattr(run, "default_bind_pending", False) and self.bind_default_identity(context, image)):
            return False  # default mode: the detail name is not bound on this frame
        if step in ("plus", "submit") and self.identity(context, image):
            return False  # not proven the configured card on this frame (Detail.Identity concludes or holds)
        if step == "plus" and run.phase == "active":
            layout = p.get("layout", "lv50")
            run.ticket_layout = layout
            texts, fields = self.read(context, image, "Zero")
            reasons = tt.baseline_reasons(fields)
            run.note({"stage": "Zero", "ocr": texts, "reasons": reasons})
            if reasons == ["ocr_unreadable"]:
                return self._unreadable(run, "Zero")
            if reasons:
                outcome = ("insufficient_tickets" if "insufficient_tickets" in reasons else
                           "default_card_not_trainable" if run.target_mode == "default" else "baseline_mismatch")
                run.finish(outcome, *reasons)
                return False
            if not self.can_spend():
                run.finish("spend_not_enabled", "zero_page_read_only")  # nothing to gain from a preview
                return False
            run.baseline, run.unreadable, run.phase = dict(fields), 0, "plus_tapped"
            return True
        if step == "submit" and run.phase == "plus_tapped":
            texts, fields = self.read(context, image, "One")
            reasons = tt.one_reasons(run.baseline, fields)
            run.note({"stage": "One", "ocr": texts, "reasons": reasons})
            if reasons == ["ocr_unreadable"]:
                return self._unreadable(run, "One")
            if reasons:
                run.finish("baseline_mismatch", *reasons)
                return False
            run.unreadable, run.phase = 0, "submitted"
            return True
        return False

    def check(self, context, image, p):
        stage, run = p.get("stage"), self.run
        if stage == "result":
            return self.check_result(context, image)  # also reached from SC.Popup.CloseKnown of other tasks
        if run is None:
            return False
        if stage == "confirm":
            return self.check_confirm(context, image)
        if stage == "identity":
            return self.check_identity(context, image)
        if stage == "after":
            return self.check_after(context, image)
        return False

    def check_confirm(self, context, image):
        run = self.run
        if run.finished or run.phase != "submitted":
            run.confirm_rejected = run.confirm_rejected or ["unexpected_confirm"]
            return False
        texts, fields = self.read(context, image, "Confirm")
        reasons = tt.confirm_reasons(run.config, run.baseline, fields)
        reasons += self.identity(context, image)  # the dimmed detail behind the dialog still names the card
        if not self.can_spend():
            reasons.append("spend_not_enabled")
        run.note({"stage": "Confirm", "ocr": texts, "reasons": reasons})
        if (reasons and set(reasons) <= {"level_unreadable", "card_name_unreadable"}
                and run.unreadable < tt.MAX_UNREADABLE_FRAMES):
            run.unreadable += 1
            return False
        if reasons:
            run.confirm_rejected = reasons
            return False
        intent = {"card": run.target_name or run.config.card, "ticket": tt.TICKET, "quantity": tt.QUANTITY,
                  "ticket_layout": getattr(run, "ticket_layout", "lv50"),
                  **({"card_id": run.target_id} if run.target_id else {}), **run.baseline}
        if not self._log({"event": "intent", **intent}):
            run.confirm_rejected = ["log_write_failed"]
            return False
        refusal = self.authorize(image, tt.APPROVER)
        if refusal:
            self._log({"event": "refused", **intent, "reason": refusal})
            run.confirm_rejected = ["spend_grant_refused:" + str(refusal)]
            return False
        run.intent, run.phase = intent, "awaiting_result"
        return True

    def result_title(self, context, image):
        """(title text, kind) of the result dialog by fixed-box OCR; kind None when not an accepted title."""
        text = self.vision(context).text(image, tt.RESULT_TITLE_BOX)
        return text, tt.result_kind(text)

    def check_result(self, context, image):
        """Result dialog (any accepted title) -> True = press 閉じる (non-consuming: the spend already happened)."""
        run = self.run
        title, kind = self.result_title(context, image)
        if run is None or (run.finished and run.phase != "result_closing"):
            # Another task (or this task after its conclusion) meets a left-over result dialog: a known blocker.
            if kind is None:
                return False
            self._log({"event": "result_closed_elsewhere", "title": title, "kind": kind,
                       "run_outcome": run.outcome if run is not None else None})
            return True
        if run.phase in ("awaiting_result", "resolving"):
            if kind is None:
                run.note({"stage": "Result", "title": title})
                run.unreadable += 1
                if run.unreadable > tt.MAX_UNREADABLE_FRAMES:
                    if run.phase == "awaiting_result":
                        self._log({"event": "uncertain", **(run.intent or {}), "reason": "unknown_result_title",
                                   "title": title})
                        run.phase = "spend_error"
                        run.finish("spend_uncertain", "unknown_result_title")
                    else:
                        run.finish("spend_uncertain", "uncertain_spend_earlier_today", "unknown_result_title")
                return False
            event = "result" if run.phase == "awaiting_result" else "resolved_" + kind
            if not self._log({"event": event, "title": title, "kind": kind, **(run.intent or {})}):
                return False  # no 閉じる without the record (the dialog stays; the task stops on its timeout)
            if run.phase == "resolving":
                run.notes.append("resolved_" + kind)
            run.result_kind, run.phase, run.unreadable = kind, "result_closing", 0
            return True
        if run.phase == "result_closing":
            return kind is not None  # the first 閉じる had no effect yet: Result.Close's max_hit bounds the retries
        self._log({"event": "unexpected_result", "phase": run.phase, "title": title})
        run.finish("unexpected_result_dialog")
        return kind is not None  # even a non-consuming close requires an accepted result title

    def check_after(self, context, image):
        run = self.run
        if run.phase != "result_closing":
            return False
        texts, fields = self.read(context, image, "After")
        reasons = tt.after_reasons(run.baseline, fields) + self.identity(context, image)
        run.note({"stage": "After", "ocr": texts, "reasons": reasons})
        if reasons and run.unreadable < tt.MAX_UNREADABLE_FRAMES:
            run.unreadable += 1  # numbers may still animate; the Hold node waits for another frame
            return False
        if reasons:
            self._log({"event": "uncertain", **run.intent, "reason": reasons, "after": fields})
            run.finish("result_unverified", *reasons)
        else:
            self._log({"event": "verified", **run.intent, "after": fields})
            run.after = dict(fields)
            run.finish("trained", f"inventory {run.baseline['inventory']}->{fields['inventory']}",
                       f"level {run.baseline['level']}->{fields['level']}")
        return True


RUNTIME = TrainingRuntime()
RECOGNITIONS = {"State": "state", "Plan": "plan", "Check": "check", "List": "list_step"}
ACTIONS = {"Begin": "begin", "Finish": "finish_action", "Abort": "abort", "SpendError": "spend_error"}


def training_registrations(runtime):
    return task_custom.registrations(runtime, PREFIX, RECOGNITIONS, ACTIONS)
