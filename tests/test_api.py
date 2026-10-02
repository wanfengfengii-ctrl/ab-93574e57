"""End-to-end HTTP tests against a live in-process server."""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from app.main import COMPILE_PATH, ApiHandler


def feasible_payload():
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


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), ApiHandler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def request(self, method, path, payload=None, raw=None):
        data = raw
        if data is None and payload is not None:
            data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            body = exc.read()
            try:
                return exc.code, json.loads(body)
            except ValueError:
                return exc.code, {"raw": body.decode("utf-8", "replace")}

    def test_health(self):
        status, body = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"status": "ok"})

    def test_root_service_info(self):
        status, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertEqual(body["service"], "momentum-plans")

    def test_compile_feasible(self):
        status, body = self.request("POST", COMPILE_PATH, feasible_payload())
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "OK")
        self.assertEqual(len(body["selectedCommands"]), 8)
        self.assertEqual(len(body["momenta"]), 9)
        self.assertEqual(body["momenta"][0], [0, 0])
        self.assertEqual(body["momenta"][-1], [1, 0])
        self.assertEqual(
            body["objectives"],
            {
                "totalEnergy": 36,
                "modeSwitches": 1,
                "commandIdSequence": [1, 1, 1, 1, 1, 1, 1, 2],
            },
        )
        first = body["selectedCommands"][0]
        self.assertEqual(first["slot"], 1)
        self.assertEqual(first["commandId"], 1)
        self.assertEqual(first["mode"], "ATLAS")

    def test_compile_invalid_input_locates_field(self):
        payload = feasible_payload()
        payload["slots"][3]["commands"][1]["energy"] = -2
        status, body = self.request("POST", COMPILE_PATH, payload)
        self.assertEqual(status, 400)
        self.assertEqual(body["status"], "INVALID_INPUT")
        fields = [entry["field"] for entry in body["errors"]]
        self.assertIn("slots[3].commands[1].energy", fields)

    def test_compile_infeasible_reports_reachability(self):
        payload = feasible_payload()
        payload["safeRegion"] = {"minX": -3, "maxX": 3, "minY": -3, "maxY": 3}
        payload["targetRegion"] = {"minX": -3, "maxX": 3, "minY": -3, "maxY": 3}
        for slot in payload["slots"]:
            # Corrections can no longer cancel the +2 disturbance: momentum
            # drifts +1 per slot and leaves the safe region after slot 3.
            slot["commands"] = [
                {"id": 1, "mode": "ATLAS", "correction": [-1, 0], "energy": 1},
                {"id": 2, "mode": "ATLAS", "correction": [-1, 0], "energy": 4},
            ]
        status, body = self.request("POST", COMPILE_PATH, payload)
        self.assertEqual(status, 422)
        self.assertEqual(body["status"], "INFEASIBLE")
        self.assertEqual(body["reason"], "SAFE_REGION_BLOCKED")
        self.assertEqual(body["lastReachableSlot"], 3)
        self.assertEqual(body["reachableStateCount"], 1)

    def test_unknown_path_is_404(self):
        status, body = self.request("GET", "/nope")
        self.assertEqual(status, 404)
        self.assertEqual(body["status"], "NOT_FOUND")

    def test_post_to_unknown_path_is_404(self):
        status, body = self.request("POST", "/nope", {"x": 1})
        self.assertEqual(status, 404)

    def test_get_on_compile_is_405(self):
        status, body = self.request("GET", COMPILE_PATH)
        self.assertEqual(status, 405)
        self.assertEqual(body["status"], "METHOD_NOT_ALLOWED")

    def test_put_is_405(self):
        status, body = self.request("PUT", COMPILE_PATH, {"x": 1})
        self.assertEqual(status, 405)

    def test_malformed_json_is_400(self):
        status, body = self.request("POST", COMPILE_PATH, raw=b"{not json")
        self.assertEqual(status, 400)
        self.assertEqual(body["status"], "BAD_REQUEST")

    def test_non_object_json_is_400_with_field(self):
        status, body = self.request("POST", COMPILE_PATH, [1, 2, 3])
        self.assertEqual(status, 400)
        self.assertEqual(body["status"], "INVALID_INPUT")
        self.assertEqual(body["errors"][0]["field"], "$")


if __name__ == "__main__":
    unittest.main()
