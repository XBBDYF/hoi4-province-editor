# -*- coding: utf-8 -*-
"""运河修复回归测试：python -m unittest test_canals -v"""
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from province_splitter.canals import index, find, detect, load_rivers
from province_splitter.integrity import (complete_canals, fix_adjacencies, fix_adjacency_rules, load_pid_map)
from province_splitter.core import read_definition

HEADER = "From;To;Type;Through;start_x;start_y;stop_x;stop_y;adjacency_rule_name;Comment"
TERMINATOR = "-1;-1;-1;-1;-1;-1;-1;-1;-1"
RULES = ('adjacency_rule =\n{\n    name = "Demo_CANAL"\n'
         '    required_provinces = { 30 }\n    icon = 30\n}\n')


def route_component(comp, waters, lands, bank_by_pixel=None, water_contacts=None,
                    pixels=None, shape=(1, 5)):
    """Small synthetic river graph for required_provinces route tests."""
    return {
        "comp": comp, "waters": list(waters), "lands": list(lands), "river_px": len(pixels or [0, 1, 2, 3, 4]),
        "pixels": np.asarray(pixels or [0, 1, 2, 3, 4], dtype=np.int32),
        "water_contacts": {int(k): tuple(v) for k, v in (water_contacts or {waters[0]: [0], waters[1]: [4]}).items()},
        "bank_by_pixel": {int(k): tuple(v) for k, v in (bank_by_pixel or {1: [lands[0]], 2: [lands[-1]]}).items()},
        "map_shape": shape,
    }


class CanalRepairTests(unittest.TestCase):
    def test_river_detection_does_not_wrap_top_to_bottom_or_left_to_right(self):
        # The single river pixel at (0, 0) is not adjacent to provinces across the image edges.
        recs = {
            1: ["1", "0", "0", "1", "sea", "false", "ocean", "1"],
            2: ["2", "0", "0", "2", "sea", "false", "ocean", "1"],
            3: ["3", "0", "0", "3", "land", "false", "plains", "1"],
        }
        pid = np.array([[3, 3, 2], [3, 3, 3], [1, 3, 3]], dtype=np.int32)
        rivers = np.full((3, 3), 254, dtype=np.uint8)
        rivers[0, 0] = 3
        self.assertEqual(detect(pid, SimpleNamespace(recs=recs), rivers), [])

    def test_mismatched_bitmap_dimensions_are_rejected(self):
        recs = {1: ["1", "0", "0", "1", "sea", "false", "ocean", "1"]}
        with self.assertRaises(ValueError):
            detect(np.zeros((2, 2), dtype=np.int32), SimpleNamespace(recs=recs),
                   np.zeros((3, 3), dtype=np.uint8))

    def test_pair_collision_is_disambiguated_by_through(self):
        idx = index([
            {"comp": 1, "waters": [1, 2], "lands": [10], "river_px": 10},
            {"comp": 2, "waters": [1, 2], "lands": [20], "river_px": 8},
        ])
        self.assertEqual(find(1, 2, idx, through={20})["comp"], 2)
        self.assertIsNone(find(1, 2, idx))

    def test_through_matching_must_be_unique(self):
        idx = index([
            {"comp": 1, "waters": [1, 2], "lands": [20], "river_px": 10},
            {"comp": 2, "waters": [1, 2], "lands": [20], "river_px": 8},
        ])
        self.assertIsNone(find(1, 2, idx, through={20}))

    def test_plain_river_does_not_create_adjacency(self):
        source = HEADER + "\n" + TERMINATOR + "\n"
        river = [{"comp": 1, "waters": [1, 2], "lands": [10, 11], "river_px": 20}]
        result, count, rows, removed = complete_canals(source, river, {}, {1, 2})
        self.assertEqual((result, count, rows, removed), (source, 0, [], 0))

    def test_unique_declared_canal_is_added_with_rule_in_correct_column(self):
        source = HEADER + "\n" + TERMINATOR + "\n"
        river = [route_component(1, [1, 2], [30, 31],
                                 bank_by_pixel={1: [30], 2: [31]},
                                 water_contacts={1: [0], 2: [4]})]
        result, count, rows, removed = complete_canals(source, river, {}, {1, 2}, rules_text=RULES)
        self.assertEqual(count, 1)
        self.assertEqual(removed, 0)
        fields = rows[0].split(";")
        self.assertEqual(fields[8], "Demo_CANAL")
        self.assertEqual(fields[9], "auto_canal")
        self.assertIn(rows[0] + "\n" + TERMINATOR, result)

    def test_legacy_unbound_auto_canal_rows_are_removed(self):
        stale = "5;6;sea;99;-1;-1;-1;-1;;auto_canal"
        source = HEADER + "\n" + stale + "\n" + TERMINATOR + "\n"
        result, count, rows, removed = complete_canals(source, [], {}, {5, 6})
        self.assertEqual((count, rows, removed), (0, [], 1))
        self.assertNotIn(stale, result)
        self.assertIn(TERMINATOR, result)

    def test_branch_with_three_water_endpoints_is_not_guessed(self):
        source = HEADER + "\n" + TERMINATOR + "\n"
        branch = [{"comp": 1, "waters": [1, 2, 3], "lands": [30, 31], "river_px": 40}]
        _result, count, rows, removed = complete_canals(source, branch, {}, {1, 2, 3}, rules_text=RULES)
        self.assertEqual((count, rows, removed), (0, [], 0))

    def test_split_canal_updates_endpoint_and_through_children(self):
        source = HEADER + "\n" + "10;20;sea;30;-1;-1;-1;-1;Demo_CANAL;test\n"
        river = index([{"comp": 1, "waters": [11, 21], "lands": [31, 32], "river_px": 20}])
        result, changed, _fixed, _unfixed = fix_adjacencies(
            source, {}, {10: [11], 20: [21], 30: [31]}, river)
        self.assertEqual(changed, 1)
        self.assertIn("11;21;sea;31;-1;-1;-1;-1;Demo_CANAL;test", result)

    def test_unique_explicit_canal_falls_back_when_through_is_not_on_river_pixels(self):
        source = HEADER + "\n" + "10;20;sea;99;-1;-1;-1;-1;Demo_CANAL;test\n"
        river = index([{"comp": 1, "waters": [11, 21], "lands": [31], "river_px": 20}])
        result, changed, _fixed, _unfixed = fix_adjacencies(
            source, {}, {10: [11], 20: [21]}, river)
        self.assertEqual(changed, 1)
        # The endpoints are safely repairable, but the unrelated Through ID is not guessed.
        self.assertIn("11;21;sea;99;-1;-1;-1;-1;Demo_CANAL;test", result)

    def test_canal_required_provinces_are_rebuilt_from_actual_bank_pixels(self):
        rules = ('adjacency_rule =\n{\n'
                 '    name = "Demo_CANAL"\n'
                 '    required_provinces = { 10 20 30 }\n'
                 '    icon = 10\n}\n')
        adj = HEADER + "\n1;2;sea;10;-1;-1;-1;-1;Demo_CANAL;demo\n" + TERMINATOR + "\n"
        river = [route_component(7, [1, 2], [10, 20, 30, 40, 50],
                                 bank_by_pixel={1: [20], 2: [40], 3: [50]},
                                 water_contacts={1: [0], 2: [4]})]
        fixed, changed = fix_adjacency_rules(rules, {}, {}, adjacency_text=adj, canals_list=river)
        self.assertEqual(changed, 1)
        self.assertIn('required_provinces = { 20 40 50 }', fixed)
        report = fix_adjacency_rules.last_report[0]
        self.assertEqual(report["removed"], "10 30")
        self.assertEqual(report["added"], "40 50")
        self.assertEqual(report["status"], "UPDATED")

    def test_split_children_are_added_only_if_they_touch_the_canal(self):
        rules = ('adjacency_rule =\n{\n'
                 '    name = "Demo_CANAL"\n'
                 '    required_provinces = { 10 20 }\n'
                 '    icon = 10\n}\n')
        adj = HEADER + "\n1;2;sea;10;-1;-1;-1;-1;Demo_CANAL;demo\n" + TERMINATOR + "\n"
        river = [route_component(1, [1, 2], [20, 9001],
                                 bank_by_pixel={1: [20], 2: [9001]},
                                 water_contacts={1: [0], 2: [4]})]
        fixed, changed = fix_adjacency_rules(
            rules, {}, {10: [9001, 9002]}, adjacency_text=adj, canals_list=river)
        self.assertEqual(changed, 1)
        self.assertIn('required_provinces = { 20 9001 }', fixed)
        self.assertNotIn('9002', fixed)

    def test_equal_shortest_routes_through_a_river_loop_are_not_guessed(self):
        # Ring graph: left and right water contacts have two equal-length routes.
        ring = route_component(
            8, [1, 2], [30, 40], pixels=[0, 1, 2, 3, 5, 6, 7, 8],
            water_contacts={1: [3], 2: [5]},
            bank_by_pixel={0: [30], 1: [30], 2: [30], 6: [40], 7: [40], 8: [40]},
            shape=(3, 3),
        )
        rules = ('adjacency_rule =\n{\n'
                 '    name = "Demo_CANAL"\n'
                 '    required_provinces = { 30 40 }\n'
                 '    icon = 30\n}\n')
        adj = HEADER + "\n1;2;sea;30;-1;-1;-1;-1;Demo_CANAL;demo\n" + TERMINATOR + "\n"
        fixed, changed = fix_adjacency_rules(rules, {}, {}, adjacency_text=adj, canals_list=[ring])
        self.assertEqual(fixed, rules)
        self.assertEqual(changed, 0)
        self.assertEqual(fix_adjacency_rules.last_report[0]["status"], "UNRESOLVED_ROUTE")

    def test_ambiguous_river_component_leaves_rule_unchanged(self):
        rules = ('adjacency_rule =\n{\n'
                 '    name = "Demo_CANAL"\n'
                 '    required_provinces = { 10 20 }\n'
                 '    icon = 10\n}\n')
        adj = HEADER + "\n1;2;sea;99;-1;-1;-1;-1;Demo_CANAL;demo\n" + TERMINATOR + "\n"
        rivers = [
            route_component(1, [1, 2], [30], bank_by_pixel={2: [30]},
                            water_contacts={1: [0], 2: [4]}),
            route_component(2, [1, 2], [40], bank_by_pixel={2: [40]},
                            water_contacts={1: [0], 2: [4]}),
        ]
        fixed, changed = fix_adjacency_rules(rules, {}, {}, adjacency_text=adj, canals_list=rivers)
        self.assertEqual(fixed, rules)
        self.assertEqual(changed, 0)
        self.assertEqual(fix_adjacency_rules.last_report[0]["status"], "UNRESOLVED_COMPONENT")

    def test_required_provinces_excludes_tributary_banks(self):
        # Main canal runs left-to-right. A tributary branches upward and touches province 99.
        # The connected component includes 99, but the From->To route does not.
        river = route_component(
            5, [1, 2, 3], [20, 21, 99],
            pixels=[10, 11, 12, 13, 14, 7, 2],
            water_contacts={1: [10], 2: [14], 3: [2]},
            bank_by_pixel={11: [20], 12: [21], 7: [99], 2: [99]},
            shape=(5, 5),
        )
        rules = ('adjacency_rule =\n{\n'
                 '    name = "Demo_CANAL"\n'
                 '    required_provinces = { 20 21 99 }\n'
                 '    icon = 20\n}\n')
        adj = HEADER + "\n1;2;sea;20;-1;-1;-1;-1;Demo_CANAL;demo\n" + TERMINATOR + "\n"
        fixed, changed = fix_adjacency_rules(rules, {}, {}, adjacency_text=adj, canals_list=[river])
        self.assertEqual(changed, 1)
        self.assertIn('required_provinces = { 20 21 }', fixed)
        self.assertNotIn('99', fixed)
        self.assertEqual(fix_adjacency_rules.last_report[0]["removed"], "99")

    def test_non_canal_rules_are_not_modified_by_canal_repair(self):
        rules = ('adjacency_rule =\n{\n'
                 '    name = "BOSPHORUS_STRAIT"\n'
                 '    required_provinces = { 10 20 }\n'
                 '    icon = 10\n}\n')
        fixed, changed = fix_adjacency_rules(rules, {}, {10: [9001]})
        self.assertEqual(fixed, rules)
        self.assertEqual(changed, 0)

    def test_orphan_adjacency_canal_is_recorded_in_audit(self):
        # Canal rows and rule blocks are inventoried together; a CSV-only canal must
        # not silently disappear from the audit report.
        adj = HEADER + "\n1;2;sea;10;-1;-1;-1;-1;Missing_CANAL;demo\n" + TERMINATOR + "\n"
        fixed, changed = fix_adjacency_rules("", {}, {}, adjacency_text=adj,
                                             canals_list=[{"comp": 1, "waters": [1, 2],
                                                           "lands": [30, 31], "river_px": 20}])
        self.assertEqual(fixed, "")
        self.assertEqual(changed, 0)
        report = fix_adjacency_rules.last_report
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["rule"], "Missing_CANAL")
        self.assertEqual(report[0]["status"], "MISSING_RULE_DEFINITION")

    def test_bundled_map_canals_are_all_resolved_from_bmp_overlay(self):
        root = Path(__file__).resolve().parent
        files = [root / name for name in ("provinces.bmp", "rivers.bmp", "definition.csv",
                                           "adjacencies.csv", "adjacency_rules.txt")]
        if not all(path.is_file() for path in files):
            self.skipTest("bundled map fixtures are not installed")
        definition = read_definition(str(root / "definition.csv"))
        pid_map = load_pid_map(str(root / "provinces.bmp"), definition)
        river_pixels = load_rivers(str(root / "rivers.bmp"))
        canal_components = detect(pid_map, definition, river_pixels)
        adj = (root / "adjacencies.csv").read_text(encoding="utf-8-sig")
        rules = (root / "adjacency_rules.txt").read_text(encoding="utf-8-sig")
        fixed, changed = fix_adjacency_rules(rules, {}, {}, adjacency_text=adj,
                                             canals_list=canal_components)
        report = fix_adjacency_rules.last_report
        self.assertEqual(len(report), 10)
        self.assertTrue(all(r["status"] in ("OK", "UPDATED") for r in report))
        self.assertEqual(changed, 7)
        self.assertEqual(fix_adjacency_rules.last_removed, 10)
        self.assertEqual(fix_adjacency_rules.last_added, 46)
        report_by_name = {row["rule"]: row for row in report}
        self.assertEqual(len(report_by_name["Bourget_CANAL"]["banks_detected"].split()), 38)
        self.assertEqual(len(report_by_name["Bourget_CANAL"]["component_banks"].split()), 52)
        self.assertEqual(len(report_by_name["Bourget_CANAL"]["off_route_banks"].split()), 14)
        self.assertEqual(report_by_name["Bourget_CANAL"]["component_pixels"], 422)
        self.assertEqual(report_by_name["Bourget_CANAL"]["route_pixels"], 299)
        self.assertEqual(len(report_by_name["Onega_CANAL"]["banks_detected"].split()), 16)
        self.assertEqual(len(report_by_name["Onega_CANAL"]["component_banks"].split()), 37)
        self.assertEqual(len(report_by_name["Onega_CANAL"]["off_route_banks"].split()), 21)
        self.assertEqual(report_by_name["Onega_CANAL"]["component_pixels"], 276)
        self.assertEqual(report_by_name["Onega_CANAL"]["route_pixels"], 97)
        self.assertNotEqual(fixed, rules)


if __name__ == "__main__":
    unittest.main(verbosity=2)
