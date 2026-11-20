"""Keep / lock / sell decision for new ノウハウブック (agent/core/knowhow_keep.py). Synthetic books only."""
import unittest

from agent.core import knowhow_keep as kk
from agent.core.knowhow_books import KnowhowBookRecord, KnowhowInventory

STATS = {"vo": 50, "da": 20, "vi": 10, "me": 20}
WANTED = [{"name": "施設効率相談", "match": "prefix", "min_level": 3}, {"name": "アピールUP(思い出高)", "min_level": 1}]


def book(title, rows, *, protected=None, marked=None, complete=True, count=1):
    rows = [{"name": n, "level": lv} for n, lv in rows]
    return KnowhowBookRecord.from_dict({"title": title, "stats": STATS, "rows": rows,
                                        "knowhow_count": len(rows) if complete else len(rows) + 2,
                                        "protected": protected, "marked": marked, "count": count})


def inventory(*books):
    inv = KnowhowInventory()
    for b in books:
        inv.upsert(b)
        if b.count > 1:
            inv.books[b.fingerprint] = b
    return inv


JUNK = book("【a】櫻木真乃", [("VocalUP+", 3), ("ファンの声援", 5), ("WING優勝", 2)])
USEFUL = book("【b】風野灯織", [("DanceUP", 2), ("施設効率相談+", 4), ("ファンの声援", 2)])


def owned(inv):
    return kk.physical_count(inv)


class KeepDecisionTest(unittest.TestCase):
    def plan(self, new, lib, settings, **kw):
        inv = inventory(*lib, *new)
        kw.setdefault("owned_now", owned(inv))
        return kk.plan_new_books(new, inv, WANTED, settings, **kw)

    def test_default_is_advice_only(self):
        out = self.plan([JUNK, USEFUL], [], kk.KeepSettings())
        self.assertEqual([v["action"] for v in out["verdicts"]], ["keep", "keep"])
        self.assertEqual([v["reason"] for v in out["verdicts"]], ["useless", "scarce"])

    def test_default_strategy_sells_only_books_without_useful_rows(self):
        out = self.plan([JUNK, USEFUL], [], kk.KeepSettings(sell=True))
        self.assertEqual([v["action"] for v in out["verdicts"]], ["sell", "keep"])
        self.assertEqual(out["verdicts"][0]["text"], "没有有用的条目")

    def test_wanted_name_below_min_level_is_kept_by_default(self):
        low = book("【c】八宮めぐる", [("VisualUP", 1), ("施設効率相談", 1)])
        out = self.plan([low], [], kk.KeepSettings(sell=True))
        self.assertEqual((out["verdicts"][0]["action"], out["verdicts"][0]["reason"]), ("keep", "near"))

    def test_head_plus_plus_is_not_useless(self):
        head = book("【d】月岡恋鐘", [("Vocal上限UP++", 4), ("ファンの声援", 1)])
        out = self.plan([head], [], kk.KeepSettings(sell=True))
        self.assertEqual(out["verdicts"][0]["action"], "keep")
        self.assertEqual(out["verdicts"][0]["needs"][0]["want"], "head")

    def test_lock_scarce_useful_book(self):
        out = self.plan([USEFUL], [], kk.KeepSettings(lock=True, sell=True))
        v = out["verdicts"][0]
        self.assertEqual((v["action"], v["reason"]), ("lock", "scarce"))
        self.assertEqual(v["text"], "库存少：施設効率相談+ Lv4 另有 0 本")

    def test_keep3_sells_when_three_copies_exist(self):
        same = book("【e】白瀬咲耶", [("施設効率相談+", 5), ("DanceUP", 1)], count=3)
        out = self.plan([USEFUL], [same], kk.KeepSettings(lock=True, sell=True, strategy="keep3"))
        v = out["verdicts"][0]
        self.assertEqual((v["action"], v["reason"]), ("sell", "enough"))
        self.assertEqual(v["needs"][0]["have"], 3)

    def test_copies_count_same_name_at_same_or_higher_level_only(self):
        lower = book("【e】白瀬咲耶", [("施設効率相談+", 3)], count=3)
        other = book("【f】幽谷霧子", [("施設効率相談", 5)], count=3)
        out = self.plan([USEFUL], [lower, other], kk.KeepSettings(sell=True, strategy="keep1"))
        self.assertEqual(out["verdicts"][0]["action"], "keep")
        self.assertEqual(out["verdicts"][0]["needs"][0]["have"], 0)

    def test_keep1_sells_with_one_copy_when_locking_is_off(self):
        one = book("【e】白瀬咲耶", [("施設効率相談+", 4)])
        out = self.plan([USEFUL], [one], kk.KeepSettings(sell=True, strategy="keep1"))
        self.assertEqual(out["verdicts"][0]["action"], "sell")
        locked = self.plan([USEFUL], [one], kk.KeepSettings(sell=True, lock=True, strategy="keep1"))
        self.assertEqual(locked["verdicts"][0]["action"], "lock")

    def test_count_strategy_needs_matching_owned_count(self):
        same = book("【e】白瀬咲耶", [("施設効率相談+", 5)], count=3)
        for owned_now in (None, 99):
            out = self.plan([USEFUL, JUNK], [same], kk.KeepSettings(sell=True, strategy="keep3"), owned_now=owned_now)
            self.assertFalse(out["counts_ok"])
            # falls back to the default rule: the useless book is still sold, the useful one kept
            self.assertEqual([v["action"] for v in out["verdicts"]], ["keep", "sell"])

    def test_new_copy_is_not_counted_against_itself(self):
        out = self.plan([USEFUL], [], kk.KeepSettings(sell=True, strategy="keep1"))
        self.assertEqual(out["verdicts"][0]["needs"][0]["have"], 0)
        self.assertEqual(out["verdicts"][0]["action"], "keep")

    def test_second_identical_new_book_sees_the_first(self):
        out = self.plan([USEFUL, USEFUL], [], kk.KeepSettings(sell=True, strategy="keep1"))
        self.assertEqual([v["action"] for v in out["verdicts"]], ["keep", "sell"])
        self.assertEqual(out["verdicts"][1]["needs"][0]["have"], 1)

    def test_guards_keep(self):
        cases = {"protected": book("【g】杜野凛世", [("ファンの声援", 1)], protected=True),
                 "incomplete": book("【h】有栖川夏葉", [("ファンの声援", 1)], complete=False),
                 "marked": book("【i】園田智代子", [("ファンの声援", 1)], marked=True)}
        for reason, b in cases.items():
            out = self.plan([b], [], kk.KeepSettings(sell=True, lock=True))
            self.assertEqual((out["verdicts"][0]["action"], out["verdicts"][0]["reason"]), ("keep", reason), reason)

    def test_no_wanted_list_never_sells(self):
        inv = inventory(JUNK)
        out = kk.plan_new_books([JUNK], inv, [], kk.KeepSettings(sell=True), owned_now=1)
        self.assertEqual((out["verdicts"][0]["action"], out["verdicts"][0]["reason"]), ("keep", "no_wanted"))

    def test_sell_cap(self):
        many = [book(f"【x{i}】櫻木真乃", [("ファンの声援", 1 + i % 5), ("WING優勝", 1 + i // 5)]) for i in range(kk.SELL_MAX + 2)]
        out = self.plan(many, [], kk.KeepSettings(sell=True))
        self.assertEqual(out["sell"], kk.SELL_MAX)
        self.assertEqual(out["verdicts"][-1]["reason"], "sell_cap")

    def test_not_yet_in_inventory(self):
        inv = inventory()
        out = kk.plan_new_books([JUNK], inv, WANTED, kk.KeepSettings(sell=True, strategy="keep3"), owned_now=1,
                                in_inventory=False)
        self.assertFalse(out["counts_ok"])  # an empty library proves nothing
        self.assertEqual(out["verdicts"][0]["action"], "sell")

    def test_head_tier(self):
        self.assertEqual(kk.head_tier("VocalUP＋＋＋"), ("VocalUP", 3))
        self.assertEqual(kk.head_tier("メンタル上限UP+"), ("メンタル上限UP", 1))
        self.assertEqual(kk.head_tier("DanceUP"), ("DanceUP", 0))
        self.assertIsNone(kk.head_tier("Vocal上限+"))           # G.R.A.D. ability, not a head
        self.assertIsNone(kk.head_tier("施設効率相談+"))

    def test_low_value(self):
        for name in ("ファンの声援++", "ノービス目標達成(Vi)", "エキスパート目標遑成(Da)", "G.R.A.D.(Vo)+", "WING準決勝(Vi)",
                     "ファン感謝祭クリア+", "W.I.N.G.(Me)", "LandingPoint(Vo)+", "アピール値UP(トワコレ)"):
            self.assertTrue(kk.is_low_value(name), name)
        for name in ("Leader適正◯", "LandingPoint大成功(SP)+", "HOPPINJAM(Vo)", "SP獲得(+10)", "スタートダッシュ"):
            self.assertFalse(kk.is_low_value(name), name)

    def test_other_rows_are_judged_against_the_library(self):
        rare = book("【j】大崎甘奈", [("VocalUP", 1), ("Leader適正◯", 5), ("ファンの声援", 3)])
        out = self.plan([rare], [], kk.KeepSettings(sell=True))
        v = out["verdicts"][0]
        self.assertEqual((v["action"], v["reason"], v["text"]), ("keep", "unique", "库存没有：Leader適正◯ Lv5"))
        lower = book("【k】大崎甜花", [("Leader適正◯", 4)])
        self.assertEqual(self.plan([rare], [lower], kk.KeepSettings(sell=True))["verdicts"][0]["action"], "keep")
        held = book("【k】大崎甜花", [("Leader適正◯", 5)])
        self.assertEqual(self.plan([rare], [held], kk.KeepSettings(sell=True))["verdicts"][0]["action"], "sell")
        # without a verified 所持数 the library proves nothing
        self.assertEqual(self.plan([rare], [held], kk.KeepSettings(sell=True), owned_now=None)["verdicts"][0]["reason"],
                         "unique")

    def test_count_strategy_also_needs_other_rows_covered(self):
        mixed = book("【l】桑山千雪", [("施設効率相談+", 4), ("スタートダッシュ", 5)])
        same = book("【e】白瀬咲耶", [("施設効率相談+", 5)], count=3)
        out = self.plan([mixed], [same], kk.KeepSettings(sell=True, strategy="keep3"))
        self.assertEqual((out["verdicts"][0]["action"], out["verdicts"][0]["reason"]), ("keep", "needed"))
        both = book("【e】白瀬咲耶", [("施設効率相談+", 5), ("スタートダッシュ", 5)], count=3)
        out = self.plan([mixed], [both], kk.KeepSettings(sell=True, strategy="keep3"))
        self.assertEqual(out["verdicts"][0]["action"], "sell")

    def test_library_sync_gates_selling(self):
        held = book("【k】大崎甜花", [("Leader適正◯", 5)])
        rare = book("【j】大崎甘奈", [("VocalUP", 1), ("Leader適正◯", 5)])
        inv = inventory(held, rare, JUNK)
        up = {"state": "up_to_date", "owned_now": owned(inv)}
        out = kk.plan_new_books([rare, JUNK], inv, WANTED, kk.KeepSettings(sell=True), sync=up)
        self.assertTrue(out["counts_ok"] and out["synced"])
        self.assertEqual([v["action"] for v in out["verdicts"]], ["sell", "sell"])
        for state in ("new_books", "fewer_owned", "too_many", "needs_full_scan", "no_baseline", "unknown"):
            out = kk.plan_new_books([USEFUL, JUNK], inventory(USEFUL, JUNK), WANTED,
                                    kk.KeepSettings(sell=True, lock=True), sync={"state": state, "owned_now": 2})
            self.assertFalse(out["synced"] or out["counts_ok"], state)
            self.assertEqual([(v["action"], v["reason"]) for v in out["verdicts"]],
                             [("lock", "scarce"), ("keep", "unsynced")], state)
            self.assertEqual(out["verdicts"][1]["text"], "书库与所持数不一致")
        # the sync's owned_now replaces the argument
        out = kk.plan_new_books([JUNK], inv, WANTED, kk.KeepSettings(sell=True), owned_now=owned(inv),
                                sync={"state": "up_to_date", "owned_now": 99})
        self.assertFalse(out["counts_ok"])

    def test_wanted_head(self):
        head = book("【m】西城樹里", [("DanceUP+", 2), ("ファンの声援", 1)])
        wanted = [{"name": "DanceUP", "match": "prefix"}]
        inv = inventory(head)
        out = kk.plan_new_books([head], inv, wanted, kk.KeepSettings(sell=True), owned_now=1)
        self.assertEqual((out["verdicts"][0]["action"], out["verdicts"][0]["reason"]), ("keep", "scarce"))

    def test_bad_strategy(self):
        with self.assertRaises(ValueError):
            kk.KeepSettings(strategy="all")


if __name__ == "__main__":
    unittest.main()
