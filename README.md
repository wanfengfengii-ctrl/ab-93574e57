# momentum-plans

Momentum-unload plan compiler for a LEO satellite attitude-control team.

For every unloading slot the team precomputes a handful of candidate magnetic
torque commands.  This service compiles a whole-window plan: it picks **exactly
one command per slot**, evolves the reaction-wheel momentum as
`previous + disturbance + correction`, keeps every slot's momentum inside a
rectangular safe region, lands the final momentum inside a target rectangle,
and optimizes, in strict priority order:

1. **`totalEnergy`** — sum of the chosen commands' energy;
2. **`modeSwitches`** — number of slots `i > 1` whose mode differs from slot `i-1`;
3. **`commandIdSequence`** — the per-slot command ids, compared lexicographically.

The point of the service is to avoid the naive trap: picking the cheapest
command slot by slot can push the momentum across the safety boundary halfway
through the window.  The compiler plans the window globally with an exact
dynamic program over `(momentum, last mode)` states.

Everything runs on the Python standard library — no package installation, no
external accounts, no manual initialization.

## Repository layout

```
Dockerfile            application image (python:3.12-slim, non-root, HEALTHCHECK)
docker-compose.yml    api service (health-checked) + one-shot verify service
app/
  main.py             HTTP front-end (routes, JSON, status codes)
  planner.py          exact dynamic-programming optimizer
  validation.py       request validation with field-level error locations
tests/                unit tests (planner, validation, live HTTP API)
verify/
  verify.py           one-shot verification entry point
  reference.py        independent brute-force reference solver
  smoke_cases.py      feasible / tie-break / infeasible / invalid scenarios
```

## Quick start

```bash
# API on http://localhost:8080 (override the host port via HOST_PORT)
docker compose up --build

# custom host port
HOST_PORT=9090 docker compose up --build

# full verification: builds, starts the API, waits for health, runs the
# unit tests, checks the image build contract, and fires business smoke
# requests; the compose exit code is the verify service's exit code
docker compose up --build --exit-code-from verify
docker compose down
```

`verify` exits `0` when every check passes and `1` otherwise, printing a
`[PASS]`/`[FAIL]` line per check and a final summary.

## API

### `POST /api/momentum-plans/compile`

Request body:

| field | type | constraints |
| --- | --- | --- |
| `initialMomentum` | `[int, int]` | components within ±10⁶ |
| `safeRegion` | object | `minX ≤ maxX`, `minY ≤ maxY`, integers within ±10⁶ |
| `targetRegion` | object | same shape as `safeRegion` |
| `slots` | array | **8–20** slot objects |
| `slots[i].disturbance` | `[int, int]` | components within ±10⁶ |
| `slots[i].commands` | array | **2–5** command objects |
| `slots[i].commands[j].id` | int | unique within the slot, within ±10⁹ |
| `slots[i].commands[j].mode` | string | non-empty, ≤ 64 chars |
| `slots[i].commands[j].correction` | `[int, int]` | components within ±10⁶ |
| `slots[i].commands[j].energy` | number | **non-negative**, ≤ 10⁹ |

Momentum after slot `i` is `momentum[i-1] + disturbance[i] + correction[chosen]`;
it must stay inside `safeRegion` for every slot, and the final momentum must
additionally lie inside `targetRegion`.

#### 200 — plan compiled

```json
{
  "status": "OK",
  "selectedCommands": [
    {"slot": 1, "commandId": 1, "mode": "ATLAS", "correction": [-2, 0], "energy": 5}
  ],
  "momenta": [[0, 0], [0, 0], [1, 0]],
  "objectives": {"totalEnergy": 36, "modeSwitches": 1, "commandIdSequence": [1, 2]}
}
```

`momenta[0]` is the initial momentum; `momenta[i]` is the momentum after
slot `i` (`N+1` entries for `N` slots).  `selectedCommands` always contains
exactly one entry per slot.

#### 400 — contradictory input, field located

```json
{
  "status": "INVALID_INPUT",
  "message": "request failed validation; 'errors' pinpoints each offending field",
  "errors": [
    {"field": "slots[3].commands[1].energy", "message": "must be non-negative"},
    {"field": "safeRegion", "message": "contradictory bounds: minX (5) is greater than maxX (-5)"}
  ]
}
```

#### 422 — no safe path exists

```json
{
  "status": "INFEASIBLE",
  "reason": "SAFE_REGION_BLOCKED",
  "lastReachableSlot": 3,
  "reachableStateCount": 1
}
```

`reason` is one of:

| reason | meaning |
| --- | --- |
| `SAFE_REGION_BLOCKED` | the safe region cut the window off after `lastReachableSlot` |
| `TARGET_REGION_UNREACHABLE` | the whole window stays safe but the target rectangle cannot be entered |
| `STATE_SPACE_LIMIT_EXCEEDED` | safety guard: the reachable frontier exceeded 10⁶ states per slot |

`lastReachableSlot` is the last slot with a non-empty reachable set (`0` means
only the initial state is reachable) and `reachableStateCount` counts the
distinct momenta reachable at that slot — so the duty officer can tell a
genuine no-solution window (422) apart from a parameter mistake (400).

Other endpoints: `GET /health` → `{"status": "ok"}`, `GET /` → service info.
Unknown paths return 404, wrong methods 405, malformed JSON 400.

### Example

```bash
curl -s http://localhost:${HOST_PORT:-8080}/api/momentum-plans/compile \
  -H 'Content-Type: application/json' \
  -d '{
    "initialMomentum": [0, 0],
    "safeRegion": {"minX": -6, "maxX": 6, "minY": -6, "maxY": 6},
    "targetRegion": {"minX": -1, "maxX": 1, "minY": -1, "maxY": 1},
    "slots": [
      {"disturbance": [2, 0], "commands": [{"id": 1, "mode": "ATLAS", "correction": [-2, 0], "energy": 5}, {"id": 2, "mode": "BREEZE", "correction": [-1, 0], "energy": 1}]},
      {"disturbance": [2, 0], "commands": [{"id": 1, "mode": "ATLAS", "correction": [-2, 0], "energy": 5}, {"id": 2, "mode": "BREEZE", "correction": [-1, 0], "energy": 1}]},
      {"disturbance": [2, 0], "commands": [{"id": 1, "mode": "ATLAS", "correction": [-2, 0], "energy": 5}, {"id": 2, "mode": "BREEZE", "correction": [-1, 0], "energy": 1}]},
      {"disturbance": [2, 0], "commands": [{"id": 1, "mode": "ATLAS", "correction": [-2, 0], "energy": 5}, {"id": 2, "mode": "BREEZE", "correction": [-1, 0], "energy": 1}]},
      {"disturbance": [2, 0], "commands": [{"id": 1, "mode": "ATLAS", "correction": [-2, 0], "energy": 5}, {"id": 2, "mode": "BREEZE", "correction": [-1, 0], "energy": 1}]},
      {"disturbance": [2, 0], "commands": [{"id": 1, "mode": "ATLAS", "correction": [-2, 0], "energy": 5}, {"id": 2, "mode": "BREEZE", "correction": [-1, 0], "energy": 1}]},
      {"disturbance": [2, 0], "commands": [{"id": 1, "mode": "ATLAS", "correction": [-2, 0], "energy": 5}, {"id": 2, "mode": "BREEZE", "correction": [-1, 0], "energy": 1}]},
      {"disturbance": [2, 0], "commands": [{"id": 1, "mode": "ATLAS", "correction": [-2, 0], "energy": 5}, {"id": 2, "mode": "BREEZE", "correction": [-1, 0], "energy": 1}]}
    ]
  }'
```

Here the per-slot cheapest command (id 2) drifts the momentum `+1` per slot
and would cross the `+6` boundary; the optimal plan spends energy once and
answers `totalEnergy 36`, `modeSwitches 1`, `commandIdSequence
[1,1,1,1,1,1,1,2]`.

## Configuration

| variable | default | purpose |
| --- | --- | --- |
| `HOST_PORT` | `8080` | host port published by the `api` service (see `.env.example`) |
| `PORT` | `8080` | container listen port of the API |
| `API_BASE_URL` | `http://api:8080` | where `verify` finds the API |
| `VERIFY_API_WAIT_SECONDS` | `90` | how long `verify` waits for API health |

## Development

No dependencies beyond Python 3.11+:

```bash
python -m unittest discover -s tests -t .   # run the test suite
PORT=8080 python -m app.main                # run the API locally
API_BASE_URL=http://127.0.0.1:8080 python -m verify.verify
```

## Design notes

- **Exactness.** The optimizer keeps, per slot, the best
  `(energy, switches, id sequence)` prefix for every reachable
  `(momentum, last mode)` pair.  All three objectives are additive and the
  comparison is lexicographic, so one prefix per state is sufficient and the
  result is provably optimal — the unit tests cross-check it against an
  independent brute-force solver on randomized instances.
- **Diagnosability.** The same sweep yields the reachable frontier per slot,
  which is what the 422 responses report (`lastReachableSlot`,
  `reachableStateCount`).
- **Robustness.** Validation rejects contradictory input with field-level
  locations before any planning happens; a frontier-size guard
  (10⁶ states/slot) keeps pathological instances from exhausting memory.
