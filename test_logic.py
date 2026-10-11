# -*- coding: utf-8 -*-
"""合成地图回归测试。运行：python test_logic.py（不需要真实 mod 文件）。"""
import unittest
import numpy as np

from province_splitter.core import (
    Config,
    Definition,
    split_active_components,
    split_region,
    apply_neighbor_transition,
)


class SplitLogicTests(unittest.TestCase):
    def test_white_side_is_not_part_of_active_split(self):
        # 30x30 的原地块中，右侧 5 列是白色保护区，只把左侧 25 列传给切割器。
        h, w = 30, 30
        yy, xx = np.mgrid[0:h, 0:w]
        active = xx < 25
        active_y, active_x = np.nonzero(active)
        whole_y, whole_x = np.nonzero(np.ones((h, w), dtype=bool))
        result = split_active_components(
            active_y, active_x, 8, np.random.default_rng(4), 20,
            target_pixels=100, split_margin=1.25, optimize_rounds=6,
            province_ys=whole_y, province_xs=whole_x,
        )
        self.assertIsNotNone(result)
        grid, _y0, _x0, order = result
        areas = [int((grid == lab).sum()) for lab in order]
        self.assertGreaterEqual(len(order), 2)
        self.assertGreaterEqual(min(areas), 20)
        self.assertLessEqual(grid.shape[1], 25)

    def test_150_area_with_120_target_is_not_split(self):
        # 两个相邻陆地省份各 150 px，省份 1 的密度目标为 120 px。
        # 1.25 的临界缓冲要求 150 <= 120*1.25 时保留。
        pid_map = np.array([[1] * 15 + [2] * 15 for _ in range(10)], dtype=np.int32)
        records = {
            1: ['1', '1', '1', '1', 'land', 'false', 'plains', '1'],
            2: ['2', '2', '2', '2', 'land', 'false', 'plains', '1'],
        }
        definition = Definition(b'', [], records, {1: 1, 2: 2}, {1: 1, 2: 2})
        cfg = Config(threshold=120, target_pixels=120, min_part_pixels=70,
                     continents=[1], neighbor_transition_enabled=True,
                     split_hysteresis_ratio=1.25)
        rows = [{'prov_id': 1, 'type': 'land', 'area': 150, 'split_area': 150,
                 'base_target_pixels': 120, 'target_pixels': 120, 'k': 2}]
        result, removed = apply_neighbor_transition(
            rows, pid_map, definition, cfg, target_of=lambda _pid: 120)
        self.assertEqual(result, [])
        self.assertEqual(removed, 1)

    def test_neighbor_transition_can_be_disabled(self):
        pid_map = np.array([[1] * 15 + [2] * 15 for _ in range(10)], dtype=np.int32)
        records = {
            1: ['1', '1', '1', '1', 'land', 'false', 'plains', '1'],
            2: ['2', '2', '2', '2', 'land', 'false', 'plains', '1'],
        }
        definition = Definition(b'', [], records, {1: 1, 2: 2}, {1: 1, 2: 2})
        cfg = Config(threshold=120, target_pixels=120, min_part_pixels=70,
                     continents=[1], neighbor_transition_enabled=False,
                     split_hysteresis_ratio=1.25)
        rows = [{'prov_id': 1, 'type': 'land', 'area': 150, 'split_area': 150,
                 'base_target_pixels': 120, 'target_pixels': 120, 'k': 2}]
        result, removed = apply_neighbor_transition(
            rows, pid_map, definition, cfg, target_of=lambda _pid: 120)
        self.assertEqual(len(result), 1)
        self.assertEqual(removed, 0)

    def test_search_is_repeatable_for_same_seed(self):
        yy, xx = np.nonzero(np.ones((40, 60), dtype=bool))
        a = split_region(yy, xx, 6, np.random.default_rng(123), 80, optimize_rounds=6)
        b = split_region(yy, xx, 6, np.random.default_rng(123), 80, optimize_rounds=6)
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertEqual(a[3], b[3])
        np.testing.assert_array_equal(a[0], b[0])


if __name__ == '__main__':
    unittest.main(verbosity=2)
