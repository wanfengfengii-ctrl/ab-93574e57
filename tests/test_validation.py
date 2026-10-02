"""Validation tests: every rule must locate the offending field."""

from __future__ import annotations

import unittest

from app.validation import validate_request


def valid_request():
    return {
        "initialMomentum": [0, 0],
        "safeRegion": {"minX": -6, "maxX": 6, "minY": -6, "maxY": 6},
        "targetRegion": {"minX": -1, "maxX": 1, "minY": -1, "maxY": 1},
        "slots": [
            {
                "disturbance": [2, 0],
                "commands": [
                    {"id": 1, "mode": "ATLAS", "correction": [-2, 0], "energy": 5},
                    {"id": 2, "mode": "BREEZE", "correction": [-1, 0], "energy": 1},
                ],
            }
            for _ in range(8)
        ],
    }


class ValidationTests(unittest.TestCase):
    def assert_field(self, request, expected_field):
        normalized, errors = validate_request(request)
        self.assertIsNone(normalized)
        self.assertTrue(errors, "expected at least one validation error")
        fields = [entry["field"] for entry in errors]
        self.assertTrue(
            any(
                field == expected_field
                or field.startswith(expected_field + "[")
                or field.startswith(expected_field + ".")
                for field in fields
            ),
            f"expected an error at {expected_field!r}, got {fields}",
        )
        for entry in errors:
            self.assertIn("field", entry)
            self.assertIn("message", entry)

    def test_valid_request_passes(self):
        normalized, errors = validate_request(valid_request())
        self.assertEqual(errors, [])
        self.assertIsNotNone(normalized)
        self.assertEqual(normalized["initialMomentum"], [0, 0])
        self.assertEqual(len(normalized["slots"]), 8)

    def test_unknown_top_level_fields_are_ignored(self):
        request = valid_request()
        request["operatorNote"] = "window B-17"
        _, errors = validate_request(request)
        self.assertEqual(errors, [])

    def test_body_must_be_an_object(self):
        for bad in ([], "nope", 42, None):
            _, errors = validate_request(bad)
            self.assertEqual(errors[0]["field"], "$")

    def test_slot_count_bounds(self):
        request = valid_request()
        request["slots"] = request["slots"][:7]
        self.assert_field(request, "slots")
        request = valid_request()
        request["slots"] = request["slots"] * 3  # 24 slots
        self.assert_field(request, "slots")

    def test_slots_must_be_a_list(self):
        request = valid_request()
        request["slots"] = {"0": {}}
        self.assert_field(request, "slots")

    def test_initial_momentum_shape(self):
        request = valid_request()
        request["initialMomentum"] = [0]
        self.assert_field(request, "initialMomentum")
        request = valid_request()
        request["initialMomentum"] = [0, 1.5]
        self.assert_field(request, "initialMomentum[1]")
        request = valid_request()
        request["initialMomentum"] = [0, True]
        self.assert_field(request, "initialMomentum[1]")
        request = valid_request()
        del request["initialMomentum"]
        self.assert_field(request, "initialMomentum")

    def test_region_contradictions(self):
        request = valid_request()
        request["safeRegion"]["minX"] = 10
        self.assert_field(request, "safeRegion")
        request = valid_request()
        request["targetRegion"]["minY"] = 10
        self.assert_field(request, "targetRegion")
        request = valid_request()
        del request["safeRegion"]["maxX"]
        self.assert_field(request, "safeRegion.maxX")
        request = valid_request()
        request["safeRegion"] = [-6, 6]
        self.assert_field(request, "safeRegion")

    def test_disturbance_must_be_integer_pair(self):
        request = valid_request()
        request["slots"][3]["disturbance"] = [1.5, 0]
        self.assert_field(request, "slots[3].disturbance[0]")
        request = valid_request()
        del request["slots"][0]["disturbance"]
        self.assert_field(request, "slots[0].disturbance")

    def test_command_count_bounds(self):
        request = valid_request()
        request["slots"][0]["commands"] = request["slots"][0]["commands"][:1]
        self.assert_field(request, "slots[0].commands")
        request = valid_request()
        extra = dict(request["slots"][0]["commands"][0])
        extra["id"] = 99
        request["slots"][0]["commands"] = request["slots"][0]["commands"] + [extra] * 4
        self.assert_field(request, "slots[0].commands")

    def test_duplicate_command_ids_within_slot(self):
        request = valid_request()
        request["slots"][2]["commands"][1]["id"] = 1
        self.assert_field(request, "slots[2].commands[1].id")

    def test_same_id_in_different_slots_is_fine(self):
        request = valid_request()
        _, errors = validate_request(request)
        self.assertEqual(errors, [])

    def test_command_id_must_be_an_integer(self):
        request = valid_request()
        request["slots"][0]["commands"][0]["id"] = "1"
        self.assert_field(request, "slots[0].commands[0].id")

    def test_mode_must_be_a_non_empty_string(self):
        request = valid_request()
        request["slots"][0]["commands"][0]["mode"] = ""
        self.assert_field(request, "slots[0].commands[0].mode")
        request = valid_request()
        request["slots"][0]["commands"][0]["mode"] = 7
        self.assert_field(request, "slots[0].commands[0].mode")

    def test_correction_must_be_integer_pair(self):
        request = valid_request()
        request["slots"][1]["commands"][1]["correction"] = [0, 0, 0]
        self.assert_field(request, "slots[1].commands[1].correction")

    def test_energy_must_be_non_negative_number(self):
        request = valid_request()
        request["slots"][0]["commands"][0]["energy"] = -1
        self.assert_field(request, "slots[0].commands[0].energy")
        request = valid_request()
        request["slots"][0]["commands"][0]["energy"] = False
        self.assert_field(request, "slots[0].commands[0].energy")
        request = valid_request()
        request["slots"][0]["commands"][0]["energy"] = "high"
        self.assert_field(request, "slots[0].commands[0].energy")

    def test_zero_energy_is_allowed(self):
        request = valid_request()
        request["slots"][0]["commands"][0]["energy"] = 0
        _, errors = validate_request(request)
        self.assertEqual(errors, [])

    def test_float_energy_is_allowed(self):
        request = valid_request()
        request["slots"][0]["commands"][0]["energy"] = 1.5
        _, errors = validate_request(request)
        self.assertEqual(errors, [])

    def test_multiple_errors_are_all_reported(self):
        request = valid_request()
        request["slots"][0]["commands"][0]["energy"] = -1
        request["slots"][4]["commands"][1]["id"] = 1
        _, errors = validate_request(request)
        fields = {entry["field"] for entry in errors}
        self.assertIn("slots[0].commands[0].energy", fields)
        self.assertIn("slots[4].commands[1].id", fields)


if __name__ == "__main__":
    unittest.main()
