"""Unit tests for autoresponder pure logic (utils/ar_logic.py).

Stdlib unittest only — no discord/motor needed. Run with:
    python3 -m unittest discover -s tests -v
"""

import unittest

from utils import ar_logic as logic


class MatchWordModeTests(unittest.TestCase):
    def test_exact_match(self):
        self.assertTrue(logic.matches_keyword("meow", "meow", "word"))

    def test_case_insensitive(self):
        self.assertTrue(logic.matches_keyword("MEOW", "meow", "word"))
        self.assertTrue(logic.matches_keyword("meow", "MEOW", "word"))
        self.assertTrue(logic.matches_keyword("MeOw", "mEoW", "word"))

    def test_embedded_no_match(self):
        # WORD mode must NOT fire on meowww / meowing / kmeown
        self.assertFalse(logic.matches_keyword("meowww", "meow", "word"))
        self.assertFalse(logic.matches_keyword("meowing", "meow", "word"))
        self.assertFalse(logic.matches_keyword("kmeown", "meow", "word"))

    def test_word_in_sentence(self):
        self.assertTrue(logic.matches_keyword("the cat says meow loudly", "meow", "word"))
        self.assertFalse(logic.matches_keyword("the cat says meowww loudly", "meow", "word"))

    def test_punctuation_boundaries(self):
        self.assertTrue(logic.matches_keyword("meow!", "meow", "word"))
        self.assertTrue(logic.matches_keyword("(meow)", "meow", "word"))
        self.assertTrue(logic.matches_keyword("well...meow?", "meow", "word"))

    def test_multiword_keyword(self):
        self.assertTrue(logic.matches_keyword("say bad word here", "bad word", "word"))
        self.assertFalse(logic.matches_keyword("say badword here", "bad word", "word"))

    def test_special_chars_escaped(self):
        self.assertTrue(logic.matches_keyword("price is $5.00 ok", "$5.00", "word"))
        self.assertFalse(logic.matches_keyword("price is $500 ok", "$5.00", "word"))

    def test_empty_inputs(self):
        self.assertFalse(logic.matches_keyword("", "meow", "word"))
        self.assertFalse(logic.matches_keyword("meow", "", "word"))
        self.assertFalse(logic.matches_keyword("meow", "   ", "word"))


class MatchContainsModeTests(unittest.TestCase):
    def test_substring_fires(self):
        # CONTAINS mode fires on meowww / meowing / kmeown
        self.assertTrue(logic.matches_keyword("meowww", "meow", "contains"))
        self.assertTrue(logic.matches_keyword("meowing", "meow", "contains"))
        self.assertTrue(logic.matches_keyword("kmeown", "meow", "contains"))
        self.assertTrue(logic.matches_keyword("meow", "meow", "contains"))

    def test_case_insensitive(self):
        self.assertTrue(logic.matches_keyword("KMEOWN", "MeOw", "contains"))

    def test_no_match(self):
        self.assertFalse(logic.matches_keyword("purr", "meow", "contains"))

    def test_empty_inputs(self):
        self.assertFalse(logic.matches_keyword("", "meow", "contains"))
        self.assertFalse(logic.matches_keyword("meow", "", "contains"))


class DurationTests(unittest.TestCase):
    def test_units(self):
        self.assertEqual(logic.parse_duration("5m"), 5)
        self.assertEqual(logic.parse_duration("1h"), 60)
        self.assertEqual(logic.parse_duration("2d"), 2880)
        self.assertAlmostEqual(logic.parse_duration("30s"), 0.5)

    def test_bare_number_is_minutes(self):
        self.assertEqual(logic.parse_duration("5"), 5)

    def test_case_and_whitespace_tolerant(self):
        self.assertEqual(logic.parse_duration(" 10M "), 10)
        self.assertEqual(logic.parse_duration("1H"), 60)

    def test_invalid(self):
        for bad in (None, "", "abc", "5x", "-5m", "m", "1.2.3h"):
            self.assertIsNone(logic.parse_duration(bad), f"input {bad!r}")


class NameKeyTests(unittest.TestCase):
    def test_case_insensitive_identity(self):
        self.assertEqual(logic.name_key("Cat Words"), logic.name_key("cat words"))
        self.assertEqual(logic.name_key("CAT WORDS"), logic.name_key("  cat words  "))


class NewKeywordTests(unittest.TestCase):
    def test_inherits_default_off(self):
        kw = logic.new_keyword("meow", "word")
        self.assertEqual(kw, {"text": "meow", "mode": "word", "enabled": False})

    def test_inherits_default_on(self):
        kw = logic.new_keyword("  meow  ", "contains", default_enabled=True)
        self.assertEqual(kw, {"text": "meow", "mode": "contains", "enabled": True})


def _groups():
    return [
        {"name": "Cat Words", "thread_id": 1,
         "keywords": [{"text": "meow", "mode": "word", "enabled": False},
                      {"text": "purr", "mode": "contains", "enabled": False}]},
        {"name": "Dog Words", "thread_id": 2,
         "keywords": [{"text": "woof", "mode": "word", "enabled": False}]},
    ]


class ResolveTargetTests(unittest.TestCase):
    def test_all(self):
        kind, hits = logic.resolve_target(_groups(), "ALL")
        self.assertEqual(kind, "all")
        self.assertEqual(len(hits), 2)
        self.assertEqual(sum(len(kws) for _, kws in hits), 3)

    def test_all_empty_groups(self):
        kind, hits = logic.resolve_target([], "all")
        self.assertEqual((kind, hits), ("all", []))

    def test_group_case_insensitive(self):
        kind, hits = logic.resolve_target(_groups(), "cAt WoRdS")
        self.assertEqual(kind, "group")
        self.assertEqual(len(hits), 1)
        self.assertEqual(len(hits[0][1]), 2)

    def test_single_keyword(self):
        kind, hits = logic.resolve_target(_groups(), "MEOW")
        self.assertEqual(kind, "keyword")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][1][0]["text"], "meow")

    def test_group_wins_over_keyword(self):
        groups = _groups() + [{"name": "woof", "thread_id": 3, "keywords": []}]
        kind, hits = logic.resolve_target(groups, "woof")
        self.assertEqual(kind, "group")
        self.assertEqual(hits[0][0]["thread_id"], 3)

    def test_none(self):
        self.assertEqual(logic.resolve_target(_groups(), "moo"), ("none", []))


class EndorsementTests(unittest.TestCase):
    def test_priority(self):
        self.assertEqual(
            logic.pick_endorsement_status([logic.REJECT, logic.APPROVE]),
            logic.STATUS_APPROVED)
        self.assertEqual(
            logic.pick_endorsement_status([logic.REJECT, logic.HINT]),
            logic.STATUS_HINT)
        self.assertEqual(
            logic.pick_endorsement_status([logic.REJECT]),
            logic.STATUS_REJECTED)

    def test_none(self):
        self.assertIsNone(logic.pick_endorsement_status([]))
        self.assertIsNone(logic.pick_endorsement_status([logic.FLAG]))
        self.assertIsNone(logic.pick_endorsement_status(None))


class SelectPoolTests(unittest.TestCase):
    def test_hint_off_always_approved(self):
        pool, _ = logic.select_pool(False, 5, 1000, 1100)
        self.assertEqual(pool, logic.STATUS_APPROVED)

    def test_never_triggered_approved(self):
        pool, restamp = logic.select_pool(True, 5, None, 1100)
        self.assertEqual(pool, logic.STATUS_APPROVED)
        self.assertTrue(restamp)

    def test_inside_window_hint(self):
        pool, _ = logic.select_pool(True, 5, 1000, 1000 + 4 * 60)
        self.assertEqual(pool, logic.STATUS_HINT)

    def test_outside_window_approved(self):
        pool, _ = logic.select_pool(True, 5, 1000, 1000 + 6 * 60)
        self.assertEqual(pool, logic.STATUS_APPROVED)

    def test_zero_window_approved(self):
        pool, _ = logic.select_pool(True, 0, 1000, 1001)
        self.assertEqual(pool, logic.STATUS_APPROVED)


if __name__ == "__main__":
    unittest.main()
