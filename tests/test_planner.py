"""Planner tests, including a randomized cross-check against brute force."""

from __future__ import annotations

import random
import unittest

from app.planner import (
    SAFE_REGION_BLOCKED,
    TARGET_REGION_UNREACHABLE,
    Infeasible,
    Plan,
    compile_plan,
)
from verify.reference import brute_force_optimum, reachable_state_counts


def command(cid, mode, correction, energy):
    return {"id": cid, "mode": mode, "correction": list(correction), "energy": energy}


def make_request(*, initial, safe, target, slots):
    return {
        "initialMomentum": list(initial),
        "safeRegion": dict(safe),
        "targetRegion": dict(target),
        "slots": [
            {
                "disturbance": list(slot["disturbance"]),
                "commands": [dict(c) for c in slot["commands"]],
            }
            for slot in slots
        ],
    }


def repeat_slot(count, disturbance, commands):
    return [
        {"disturbance": list(disturbance), "commands": [dict(c) for c in commands]}
        for _ in range(count)
    ]


class FixedWindowTests(unittest.TestCase):
    def test_drifting_window_requires_global_plan(self):
        # Per-slot greedy energy (id 2 everywhere) drifts out of the safe
        # region; the optimum spends energy once to stay inside and lands
        # the cheap command at the last slot for the smallest id sequence.
        request = make_request(
            initial=(0, 0),
            safe={"minX": -6, "maxX": 6, "minY": -6, "maxY": 6},
            target={"minX": -1, "maxX": 1, "minY": -1, "maxY": 1},
            slots=repeat_slot(
                8,
                (2, 0),
                [
                    command(1, "ATLAS", (-2, 0), 5),
                    command(2, "BREEZE", (-1, 0), 1),
                ],
            ),
        )
        plan = compile_plan(request)
        self.assertIsInstance(plan, Plan)
        self.assertEqual(plan.command_ids, (1, 1, 1, 1, 1, 1, 1, 2))
        self.assertEqual(plan.total_energy, 36)
        self.assertEqual(plan.mode_switches, 1)
        self.assertEqual(plan.momenta, ((0, 0),) * 8 + ((1, 0),))

    def test_ties_broken_by_switches_then_ids(self):
        request = make_request(
            initial=(0, 0),
            safe={"minX": -2, "maxX": 2, "minY": -2, "maxY": 2},
            target={"minX": 0, "maxX": 0, "minY": 0, "maxY": 0},
            slots=repeat_slot(
                8,
                (0, 0),
                [
                    command(10, "ATLAS", (0, 0), 2),
                    command(11, "BREEZE", (0, 0), 2),
                    command(12, "ATLAS", (0, 0), 2),
                ],
            ),
        )
        plan = compile_plan(request)
        self.assertIsInstance(plan, Plan)
        self.assertEqual(plan.command_ids, (10,) * 8)
        self.assertEqual(plan.total_energy, 16)
        self.assertEqual(plan.mode_switches, 0)

    def test_energy_dominates_mode_switches(self):
        # Alternating cheap modes costs 8 energy with 7 switches; staying in
        # one mode costs 40.  Energy wins, so the switches must be accepted.
        slots = []
        for index in range(8):
            cheap_atlas = index % 2 == 0
            slots.append(
                {
                    "disturbance": [0, 0],
                    "commands": [
                        command(1, "ATLAS", (0, 0), 1 if cheap_atlas else 9),
                        command(2, "BREEZE", (0, 0), 9 if cheap_atlas else 1),
                    ],
                }
            )
        request = make_request(
            initial=(0, 0),
            safe={"minX": -1, "maxX": 1, "minY": -1, "maxY": 1},
            target={"minX": 0, "maxX": 0, "minY": 0, "maxY": 0},
            slots=slots,
        )
        plan = compile_plan(request)
        self.assertIsInstance(plan, Plan)
        self.assertEqual(plan.command_ids, (1, 2, 1, 2, 1, 2, 1, 2))
        self.assertEqual(plan.total_energy, 8)
        self.assertEqual(plan.mode_switches, 7)

    def test_safe_region_blockage_reports_last_reachable_slot(self):
        request = make_request(
            initial=(0, 0),
            safe={"minX": -3, "maxX": 3, "minY": -3, "maxY": 3},
            target={"minX": -3, "maxX": 3, "minY": -3, "maxY": 3},
            slots=repeat_slot(
                8,
                (2, 0),
                [
                    command(1, "ATLAS", (-1, 0), 1),
                    command(2, "ATLAS", (-1, 0), 4),
                ],
            ),
        )
        result = compile_plan(request)
        self.assertIsInstance(result, Infeasible)
        self.assertEqual(result.reason, SAFE_REGION_BLOCKED)
        self.assertEqual(result.last_reachable_slot, 3)
        self.assertEqual(result.reachable_state_count, 1)

    def test_unreachable_target_reports_full_frontier(self):
        request = make_request(
            initial=(0, 0),
            safe={"minX": -10, "maxX": 10, "minY": -10, "maxY": 10},
            target={"minX": 0, "maxX": 0, "minY": 0, "maxY": 0},
            slots=repeat_slot(
                8,
                (2, 0),
                [
                    command(1, "ATLAS", (-1, 0), 1),
                    command(2, "ATLAS", (-1, 0), 4),
                ],
            ),
        )
        result = compile_plan(request)
        self.assertIsInstance(result, Infeasible)
        self.assertEqual(result.reason, TARGET_REGION_UNREACHABLE)
        self.assertEqual(result.last_reachable_slot, 8)
        self.assertEqual(result.reachable_state_count, 1)

    def test_blocked_at_first_slot_reports_initial_state(self):
        request = make_request(
            initial=(0, 0),
            safe={"minX": 5, "maxX": 6, "minY": 5, "maxY": 6},
            target={"minX": 5, "maxX": 6, "minY": 5, "maxY": 6},
            slots=repeat_slot(
                8,
                (0, 0),
                [
                    command(1, "ATLAS", (0, 0), 1),
                    command(2, "ATLAS", (0, 0), 2),
                ],
            ),
        )
        result = compile_plan(request)
        self.assertIsInstance(result, Infeasible)
        self.assertEqual(result.reason, SAFE_REGION_BLOCKED)
        self.assertEqual(result.last_reachable_slot, 0)
        self.assertEqual(result.reachable_state_count, 1)

    def test_reachable_state_count_tracks_distinct_momenta(self):
        # Momentum walks (k, y) with |y| <= 2; x grows by 1 every slot and
        # crosses the safe boundary after slot 10, killing the whole
        # frontier.  The count must track distinct momenta (3 states at
        # slot 10), not command combinations (2^10 paths).
        request = make_request(
            initial=(0, 0),
            safe={"minX": -10, "maxX": 10, "minY": -2, "maxY": 2},
            target={"minX": 0, "maxX": 0, "minY": 0, "maxY": 0},
            slots=repeat_slot(
                12,
                (0, 0),
                [
                    command(1, "ATLAS", (1, 1), 1),
                    command(2, "ATLAS", (1, -1), 1),
                ],
            ),
        )
        result = compile_plan(request)
        self.assertIsInstance(result, Infeasible)
        self.assertEqual(result.reason, SAFE_REGION_BLOCKED)
        self.assertEqual(result.last_reachable_slot, 10)
        self.assertEqual(result.reachable_state_count, 3)


class RandomizedCrossCheckTests(unittest.TestCase):
    def test_random_instances_match_brute_force(self):
        rng = random.Random(20261002)
        for trial in range(60):
            request = self._random_request(rng)
            result = compile_plan(request)
            best = brute_force_optimum(request)
            if best is None:
                self.assertIsInstance(
                    result, Infeasible, f"trial {trial}: expected infeasible"
                )
                counts = reachable_state_counts(request)
                self.assertEqual(result.last_reachable_slot, len(counts) - 1)
                self.assertEqual(result.reachable_state_count, counts[-1])
                expected_reason = (
                    SAFE_REGION_BLOCKED
                    if len(counts) <= len(request["slots"])
                    else TARGET_REGION_UNREACHABLE
                )
                self.assertEqual(result.reason, expected_reason)
            else:
                self.assertIsInstance(
                    result, Plan, f"trial {trial}: expected a plan"
                )
                energy, switches, ids = best
                self.assertEqual(result.total_energy, energy, f"trial {trial}")
                self.assertEqual(result.mode_switches, switches, f"trial {trial}")
                self.assertEqual(result.command_ids, ids, f"trial {trial}")
                self.assertEqual(len(result.momenta), len(request["slots"]) + 1)
                for momentum in result.momenta[1:]:
                    self.assertTrue(
                        request["safeRegion"]["minX"]
                        <= momentum[0]
                        <= request["safeRegion"]["maxX"]
                    )
                    self.assertTrue(
                        request["safeRegion"]["minY"]
                        <= momentum[1]
                        <= request["safeRegion"]["maxY"]
                    )

    @staticmethod
    def _random_request(rng):
        safe = {"minX": -6, "maxX": 6, "minY": -6, "maxY": 6}
        x1, x2 = sorted(rng.sample(range(-6, 7), 2))
        y1, y2 = sorted(rng.sample(range(-6, 7), 2))
        target = {"minX": x1, "maxX": x2, "minY": y1, "maxY": y2}
        slots = []
        for _ in range(8):
            ids = rng.sample(range(1, 30), rng.randint(2, 3))
            commands = [
                command(
                    cid,
                    rng.choice(["ATLAS", "BREEZE", "CALM"]),
                    (rng.randint(-3, 3), rng.randint(-3, 3)),
                    rng.randint(0, 9),
                )
                for cid in ids
            ]
            slots.append(
                {
                    "disturbance": [rng.randint(-2, 2), rng.randint(-2, 2)],
                    "commands": commands,
                }
            )
        return make_request(
            initial=(rng.randint(-3, 3), rng.randint(-3, 3)),
            safe=safe,
            target=target,
            slots=slots,
        )


if __name__ == "__main__":
    unittest.main()
