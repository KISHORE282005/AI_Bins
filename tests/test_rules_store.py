"""Unit tests for the persistent bin rule master store and its CRUD surface.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from core import bin_rules, rules_store
from core.bin_rules import get_rules


class TestRulesStore(unittest.TestCase):
    """Create / read / update / delete / reorder against a temp store file."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._path = os.path.join(self._tmp.name, "rules.json")
        self._old_path = rules_store.set_store_path(self._path)

    def tearDown(self):
        rules_store.set_store_path(self._old_path)
        self._tmp.cleanup()

    def test_store_seeds_from_config(self):
        rules = rules_store.list_rules()
        self.assertEqual(len(rules), len(config.BIN_RULES))
        self.assertEqual([r["name"] for r in rules],
                         [e["name"] for e in config.BIN_RULES])
        self.assertTrue(all(r["id"] for r in rules))
        self.assertEqual([r["priority"] for r in rules], list(range(len(rules))))

    def test_create_appends_with_priority(self):
        created = rules_store.create_rule({
            "name": "XL PLASTIC",
            "min_volume": 0,
            "max_volume": 500_000_000,
            "min_weight": 40,
            "max_weight": 60,
        })
        self.assertEqual(created["priority"], len(config.BIN_RULES))
        self.assertIn(created["id"], [r["id"] for r in rules_store.list_rules()])

    def test_engine_uses_edited_rules(self):
        # A demand that is outside every default band now fits a new category.
        rules_store.create_rule({
            "name": "GIANT",
            "min_volume": 0,
            "max_volume": 2_000_000_000,
            "min_weight": 0,
            "max_weight": 5_000,
        })
        rules = get_rules()
        self.assertEqual(rules[-1].name, "GIANT")
        decision = bin_rules.recommend_bin(1_000_000_000, 1_000, rules)
        self.assertEqual(decision.recommended_bin, "GIANT")
        self.assertEqual(decision.status, "Matched")

    def test_update_changes_fields(self):
        rule_id = rules_store.list_rules()[0]["id"]
        updated = rules_store.update_rule(rule_id, {"max_weight": 12})
        self.assertEqual(updated["max_weight"], 12)
        self.assertEqual(rules_store.get(rule_id)["name"], config.BIN_RULES[0]["name"])

    def test_update_renames_and_keeps_priority(self):
        rule_id = rules_store.list_rules()[2]["id"]
        updated = rules_store.update_rule(rule_id, {"name": "M BIN"})
        self.assertEqual(updated["name"], "M BIN")
        self.assertEqual(updated["priority"], 2)

    def test_delete_removes_rule(self):
        rule_id = rules_store.list_rules()[0]["id"]
        rules_store.delete_rule(rule_id)
        names = [r["name"] for r in rules_store.list_rules()]
        self.assertNotIn(config.BIN_RULES[0]["name"], names)
        self.assertIsNone(rules_store.get(rule_id))

    def test_cannot_delete_last_rule(self):
        for rule in rules_store.list_rules()[1:]:
            rules_store.delete_rule(rule["id"])
        remaining = rules_store.list_rules()
        self.assertEqual(len(remaining), 1)
        with self.assertRaises(ValueError):
            rules_store.delete_rule(remaining[0]["id"])

    def test_reorder_changes_priority(self):
        before = rules_store.list_rules()
        ids = [r["id"] for r in before]
        ids.reverse()
        after = rules_store.reorder_rules(ids)
        self.assertEqual([r["name"] for r in after],
                         list(reversed([r["name"] for r in before])))
        self.assertEqual([r["priority"] for r in after], list(range(len(after))))

    def test_reorder_rejects_partial_lists(self):
        ids = [r["id"] for r in rules_store.list_rules()][:2]
        with self.assertRaises(ValueError):
            rules_store.reorder_rules(ids)

    def test_duplicate_name_is_rejected(self):
        with self.assertRaises(ValueError):
            rules_store.create_rule({
                "name": config.BIN_RULES[0]["name"],
                "min_volume": 0,
                "max_volume": 100,
                "min_weight": 0,
                "max_weight": 10,
            })

    def test_invalid_ranges_are_rejected(self):
        with self.assertRaises(ValueError):
            rules_store.create_rule({
                "name": "BROKEN",
                "min_volume": 10,
                "max_volume": 5,
                "min_weight": 0,
                "max_weight": 10,
            })
        with self.assertRaises(ValueError):
            rules_store.create_rule({
                "name": "BROKEN",
                "min_volume": 0,
                "max_volume": 100,
                "min_weight": 0,
                "max_weight": -5,
            })
        with self.assertRaises(ValueError):
            rules_store.create_rule({"name": "", "max_volume": 1, "max_weight": 1})

    def test_changes_persist_to_disk(self):
        rules_store.create_rule({
            "name": "PERSISTED",
            "min_volume": 0,
            "max_volume": 123_000,
            "min_weight": 1,
            "max_weight": 2,
        })
        self.assertTrue(os.path.exists(self._path))
        names = [r["name"] for r in rules_store.list_rules()]
        self.assertIn("PERSISTED", names)


if __name__ == "__main__":
    unittest.main()
