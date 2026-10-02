"""One-shot verification service.

Waits for the API to become healthy, then checks, in order:

  1. the repository's unit-test suite (``tests/``);
  2. the image build contract (Dockerfile directives, non-root runtime,
     live health endpoint);
  3. business smoke requests against the compile endpoint: a feasible
     window, a tie-adjudication window, infeasible windows (safe-region
     blocked and target unreachable), and contradictory-input handling.

Every check is reported on stdout and the process exit code carries the
verdict: 0 when everything passed, 1 otherwise.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from verify import smoke_cases
from verify.reference import brute_force_optimum, reachable_state_counts

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPILE_PATH = "/api/momentum-plans/compile"


# ---------------------------------------------------------------------------
# reporting


class Results:
    def __init__(self) -> None:
        self.entries = []

    def add(self, name, ok, detail=""):
        self.entries.append((name, ok, detail))
        marker = "PASS" if ok else "FAIL"
        line = f"[{marker}] {name}"
        if detail and not ok:
            line += f"\n       {detail}"
        elif detail:
            line += f" — {detail}"
        print(line, flush=True)

    @property
    def failed(self):
        return [name for name, ok, _ in self.entries if not ok]

    def finish(self):
        passed = sum(1 for _, ok, _ in self.entries if ok)
        failed = len(self.entries) - passed
        print("-" * 60, flush=True)
        print(f"VERIFY SUMMARY: {passed} passed, {failed} failed", flush=True)
        if failed:
            for name in self.failed:
                print(f"  failed: {name}", flush=True)
        return 0 if failed == 0 else 1


def run_check(results, name, fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - any failure is a failed check
        results.add(name, False, f"{type(exc).__name__}: {exc}")
    else:
        results.add(name, True)


# ---------------------------------------------------------------------------
# HTTP helpers


def http_get(base, path):
    try:
        with urllib.request.urlopen(base + path, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def http_post(base, path, payload):
    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def wait_for_api(base, timeout_seconds):
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    while time.monotonic() < deadline:
        attempts += 1
        try:
            with urllib.request.urlopen(base + "/health", timeout=5) as response:
                if response.status == 200:
                    return True, f"healthy after {attempts} attempt(s)"
        except Exception:  # noqa: BLE001 - keep waiting until the deadline
            pass
        time.sleep(1)
    return False, f"API not healthy within {timeout_seconds}s"


# ---------------------------------------------------------------------------
# 1. unit tests


def check_unit_tests():
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-2000:]
        raise AssertionError(f"unit tests exited {proc.returncode}:\n{tail}")


# ---------------------------------------------------------------------------
# 2. image build contract


def check_image_contract(base):
    dockerfile = REPO_ROOT / "Dockerfile"
    assert dockerfile.is_file(), "Dockerfile missing from the image filesystem"
    text = dockerfile.read_text(encoding="utf-8")
    for needle in ("FROM", "EXPOSE 8080", "HEALTHCHECK", "CMD"):
        assert needle in text, f"Dockerfile is missing {needle!r}"
    users = [
        line.split()[-1]
        for line in text.splitlines()
        if line.strip().upper().startswith("USER ")
    ]
    assert users, "Dockerfile never drops privileges (no USER directive)"
    assert users[-1] not in ("root", "0"), "container must not run as root"
    if Path("/.dockerenv").exists():
        assert os.geteuid() != 0, "container process is running as root"

    status, body = http_get(base, "/health")
    assert status == 200 and body.get("status") == "ok", (
        f"unexpected /health response: {status} {body}"
    )
    status, body = http_get(base, "/")
    assert status == 200 and body.get("service") == "momentum-plans", (
        f"unexpected service info response: {status} {body}"
    )


# ---------------------------------------------------------------------------
# 3. business smokes


def _inside(point, rect):
    return (
        rect["minX"] <= point[0] <= rect["maxX"]
        and rect["minY"] <= point[1] <= rect["maxY"]
    )


def assert_plan_consistent(request, body):
    """Re-simulate the returned plan and check every reported number."""

    slots = request["slots"]
    selected = body["selectedCommands"]
    momenta = body["momenta"]
    assert len(selected) == len(slots), "must select exactly one command per slot"
    assert len(momenta) == len(slots) + 1, (
        "momenta must hold the initial state plus one entry per slot"
    )
    assert list(momenta[0]) == list(request["initialMomentum"]), (
        "momenta[0] must be the initial momentum"
    )
    total_energy = 0
    switches = 0
    previous_mode = None
    ids = []
    for index, (slot, choice) in enumerate(zip(slots, selected)):
        assert choice["slot"] == index + 1, "selections must be in slot order"
        commands = {c["id"]: c for c in slot["commands"]}
        assert choice["commandId"] in commands, (
            f"slot {index + 1}: selected id not among the candidates"
        )
        command = commands[choice["commandId"]]
        assert choice["mode"] == command["mode"]
        expected = [
            momenta[index][0] + slot["disturbance"][0] + command["correction"][0],
            momenta[index][1] + slot["disturbance"][1] + command["correction"][1],
        ]
        assert list(momenta[index + 1]) == expected, (
            f"slot {index + 1}: momentum evolution mismatch"
        )
        assert _inside(expected, request["safeRegion"]), (
            f"slot {index + 1}: momentum leaves the safe region"
        )
        total_energy += command["energy"]
        if previous_mode is not None and command["mode"] != previous_mode:
            switches += 1
        previous_mode = command["mode"]
        ids.append(command["id"])
    assert _inside(momenta[-1], request["targetRegion"]), (
        "final momentum must enter the target region"
    )
    objectives = body["objectives"]
    assert objectives["totalEnergy"] == total_energy, "totalEnergy mismatch"
    assert objectives["modeSwitches"] == switches, "modeSwitches mismatch"
    assert objectives["commandIdSequence"] == ids, "commandIdSequence mismatch"


def check_optimal_plan(base, request):
    status, body = http_post(base, COMPILE_PATH, request)
    assert status == 200, f"expected 200, got {status}: {body}"
    assert body["status"] == "OK", f"unexpected status payload: {body}"
    assert_plan_consistent(request, body)
    best = brute_force_optimum(request)
    assert best is not None, "reference solver says this window is infeasible"
    energy, switches, ids = best
    objectives = body["objectives"]
    assert objectives["totalEnergy"] == energy, (
        f"expected totalEnergy {energy}, got {objectives['totalEnergy']}"
    )
    assert objectives["modeSwitches"] == switches, (
        f"expected modeSwitches {switches}, got {objectives['modeSwitches']}"
    )
    assert objectives["commandIdSequence"] == list(ids), (
        f"expected commandIdSequence {list(ids)}, "
        f"got {objectives['commandIdSequence']}"
    )


def check_infeasible(base, request, expected_reason):
    status, body = http_post(base, COMPILE_PATH, request)
    assert status == 422, f"expected 422, got {status}: {body}"
    assert body["status"] == "INFEASIBLE", f"unexpected payload: {body}"
    assert body["reason"] == expected_reason, (
        f"expected reason {expected_reason}, got {body.get('reason')}"
    )
    counts = reachable_state_counts(request)
    assert body["lastReachableSlot"] == len(counts) - 1, (
        f"expected lastReachableSlot {len(counts) - 1}, "
        f"got {body['lastReachableSlot']}"
    )
    assert body["reachableStateCount"] == counts[-1], (
        f"expected reachableStateCount {counts[-1]}, "
        f"got {body['reachableStateCount']}"
    )
    assert brute_force_optimum(request) is None, (
        "reference solver found a plan; the window is not infeasible"
    )


def check_invalid_input(base, name, bad_request, expected_field):
    status, body = http_post(base, COMPILE_PATH, bad_request)
    assert status == 400, f"expected 400, got {status}: {body}"
    assert body["status"] == "INVALID_INPUT", f"unexpected payload: {body}"
    fields = [entry["field"] for entry in body["errors"]]
    assert any(
        field == expected_field
        or field.startswith(expected_field + "[")
        or field.startswith(expected_field + ".")
        for field in fields
    ), f"expected an error at {expected_field!r}, got fields {fields}"


# ---------------------------------------------------------------------------
# entry point


def main() -> int:
    base = os.environ.get("API_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
    wait_seconds = int(os.environ.get("VERIFY_API_WAIT_SECONDS", "90"))
    results = Results()

    print(f"verify: targeting API at {base}", flush=True)

    ok, detail = wait_for_api(base, wait_seconds)
    results.add("api becomes healthy", ok, detail)
    if not ok:
        return results.finish()

    run_check(results, "unit test suite passes", check_unit_tests)
    run_check(results, "image build contract", lambda: check_image_contract(base))

    run_check(
        results,
        "smoke: feasible window is planned optimally",
        lambda: check_optimal_plan(base, smoke_cases.feasible_request()),
    )
    run_check(
        results,
        "smoke: ties adjudicated by switches then command ids",
        lambda: check_optimal_plan(base, smoke_cases.tie_break_request()),
    )
    run_check(
        results,
        "smoke: safe-region blockage reports last reachable slot",
        lambda: check_infeasible(
            base, smoke_cases.infeasible_blocked_request(), "SAFE_REGION_BLOCKED"
        ),
    )
    run_check(
        results,
        "smoke: unreachable target reports reachable frontier",
        lambda: check_infeasible(
            base,
            smoke_cases.infeasible_target_request(),
            "TARGET_REGION_UNREACHABLE",
        ),
    )
    for name, bad_request, field in smoke_cases.invalid_requests():
        run_check(
            results,
            f"smoke: contradictory input located ({name})",
            lambda b=bad_request, f=field: check_invalid_input(base, name, b, f),
        )

    return results.finish()


if __name__ == "__main__":
    sys.exit(main())
