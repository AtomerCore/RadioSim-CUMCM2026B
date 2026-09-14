# Radio Interference Source Environment Simulator (Local Replica)

English | [简体中文](README.zh-CN.md)

Problem: [2026 CUMCM Problem B](https://www.mcm.edu.cn/html_cn/node/27b6e148f8113f09b0269f64a02629fb.html)

A local replica of the official simulator for the 2026 CUMCM Problem B
**"Rapid Automatic Location and Clearance of Radio Interference Sources"**.

The robot interface — port, the four commands, request/response fields, error
codes, timing rules and idempotency — follows the official *Simulator User
Guide* (Attachment 1) and *Communication Interface Specification and
Programming Guide* (Attachment 2) item by item. On top of that, this replica
provides a web admin UI, configurable simulation parameters and a robot
visualization view. See [Differences from the Official Simulator](#differences-from-the-official-simulator)
for what could not be replicated locally.

## Quick Start

```bash
cd simulator
python simulator.py            # default port 2026, opens the browser automatically
```

- Admin UI: <http://127.0.0.1:2026/>
- Robot interface: `POST http://127.0.0.1:2026/enter | /measure | /clear | /exit`
- Python 3.9+, standard library only; listens on the loopback address only
  (same as the official simulator)
- Options: `--port 3000` (change port), `--no-open` (do not open the browser)

Typical workflow: click "Problem 3 practice test" in the UI → 5-second
countdown → the UI shows "robot interface ready" → run your robot program.

## Simulator Operation Guide

The four test modules — **Problem 3 practice**, **Problem 4 practice**,
**Problem 3 formal** and **Problem 4 formal** — work as in the official
simulator:

- **Practice tests are unlimited.** Problem 3 and Problem 4 formal tests each
  have 3 attempts. A formal test is confirmed once more before it starts;
  starting it consumes one attempt, and so does aborting it.
- After you confirm a start, the simulator prepares the test data, then shows
  a 5-second countdown. When the countdown ends, the 25-minute test window
  opens and the robot interface becomes available.
- After the robot successfully calls `/enter`, the 20-minute program runtime
  limit starts. The real deadline is the earlier of the window deadline and
  the program deadline. The virtual-world time limit is 100 hours.
- A test ends when the robot calls `/exit`, you abort it manually, the window
  times out, the program time is exceeded, or the virtual time is exceeded.
  The UI shows the ending reason. After a test ends it cannot be resumed; you
  can only start a new one.
- "Abort test" requires double confirmation. If your robot program hangs or
  never calls `/enter`, abort manually instead of waiting for the timeout.
- The "Commands & Feedback" panel shows the latest robot requests and
  simulator responses, 1000 by default (adjustable to 100–5000 in Settings).
  This is a display limit only; the complete behavior is always written to
  the log.
- Every test shows a **test case code**. Practice tests reveal the source
  count (total / omni / directional) after they end; formal tests never reveal
  the case truth.
- A behavior log (plaintext JSON in this replica) is generated automatically
  when each test ends. In the "Test Logs" page you can export it as a JSON
  file, delete a single log, clear all logs with one click, or open the log
  folder in the system file manager.

## Batch Testing

The "Batch Testing" tab runs many tests fully automatically and aggregates
statistics — useful for evaluating the robustness of your robot program:

- **Custom groups**: one batch may contain several groups; each group sets the
  problem (3/4), mode (practice/formal), run count and the robot program
  command (executed via shell, e.g. `python my_robot.py` or a full exe path) —
  for example problem 3 and problem 4, 10 runs each. Formal runs consume
  formal attempts as usual.
- **Per-run flow**: start a session automatically → when the countdown ends
  and the interface opens, spawn the robot program → wait for the run to end
  (per-run timeout configurable, default countdown+window+60 s; on timeout the
  run is aborted and the process is reaped) → record the run summary, with an
  optional inter-run delay. Robot stdout/stderr is saved to
  `simulator/data/batches/robot_logs/<case_code>.log` and viewable in the UI.
- **Connection info**: the robot program reads it from environment variables:
  `SIMULATOR_PORT`, `SIMULATOR_BASE_URL`, `SIMULATOR_CASE_CODE`,
  `SIMULATOR_PROBLEM`, `SIMULATOR_MODE`, `SIMULATOR_SEED`.
- **Seeds & reproducibility**: you may give the batch a seed list (cycled
  across runs and recorded per run). The same seed produces exactly the same
  case; run two robot programs with the same seed list to compare them on an
  identical set of cases. The batch ignores the seed from the Settings page.
- **Statistics**: after the batch ends the report aggregates completed runs,
  overall clear rate, full-clear rate, end-reason distribution, timeout/failure
  counts, and per-run mean/std/min/median/max for cleared count, clear ratio,
  measures, clear success/fail, virtual time, average locate-and-clear time,
  program run time and travel distance — overall and per group. Reports are
  stored in `simulator/data/batches/`, exportable as JSON / CSV; past batches
  can be viewed or deleted at any time (single-run logs are not affected).
- While a batch is running you cannot start single tests or change settings;
  "Stop batch" aborts the current run, skips the rest, and keeps the
  statistics of completed runs.
- Note: simulation advances in real time, so a batch takes the sum of the real
  durations of its runs. For quicker debugging, shorten the window/program
  limits in Settings first.

## Robot Behavior Rules

Initial state: the robot starts at (0, 0) with the direction finder on
channel 1. Movement and channel switching have no commands of their own —
the simulator infers them from the position and channel parameters of
`/measure` and `/clear`.

| Action | Command | Virtual-time cost (s) |
|---|---|---|
| Start | `POST /enter` | 0 |
| End | `POST /exit` | 0 |
| Measure | `POST /measure` | 5 |
| Locate & clear | `POST /clear` | 3 (no target) / 5 (success) |
| Switch channel | implied by `/measure` channel | 1 |
| Move | implied by `position` | straight-line distance / 5 m/s |

## Commands and Virtual-Time Rules

- Only an accepted request (`accepted=true`) can advance the virtual clock.
  `/enter` and `/exit` never advance it. When `accepted=false`, the action has
  no effect and `virtual_time_s` in that response is 0 — it is **not** the
  current virtual time; use the last `accepted=true` response instead.
- A legal `/measure` costs *move time + channel-switch time (1 s, only when
  the channel differs from the current one) + 5 s*. After a legal measure the
  current channel becomes the measured channel.
- A legal `/clear` costs *move time + 3 s (no target found) or 5 s (success)*.
  It never switches the direction-finder channel. The 5 s breaks down as 3 s
  of optical precise location plus 2 s of laser clearance; when no target is
  found, only the optical location step runs.
- `virtual_time_s` is a JSON number with up to 6 decimal places.

Worked example from the official specification (Attachment 2, Section 10):

| Step | Command | Position | Channel meaning | Move (m) | Move (s) | Switch (s) | Action (s) | Total (s) | Virtual time (s) |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `/enter` | – | – | 0 | 0 | 0 | 0 | 0 | 0 |
| 2 | `/measure` | (300,400) | detect ch 1 | 500 | 100 | 0 | 5 | 105 | 105 |
| 3 | `/measure` | (300,400) | detect ch 2 | 0 | 0 | 1 | 5 | 6 | 111 |
| 4 | `/clear` | (300,0) | target ch 3 | 400 | 80 | 0 | 3 | 83 | 194 |
| 5 | `/measure` | (300,0) | detect ch 2 | 0 | 0 | 0 | 5 | 5 | 199 |
| 6 | `/exit` | – | – | 0 | 0 | 0 | 0 | 0 | 199 |

Step 4's channel is the *target* channel; `/clear` does not switch the
direction finder, so step 5 still measures on channel 2 with no switch cost.

## Communication Format

All four commands use HTTP + JSON:

- Method `POST`; path must be exactly `/enter`, `/measure`, `/clear` or
  `/exit` (no trailing slash, no query parameters).
- `Content-Type` must be `application/json` (only parameter allowed:
  `charset=utf-8`); `Content-Encoding` may be omitted or `identity`.
  Otherwise HTTP 415.
- The body must be a BOM-free UTF-8 JSON object, no duplicate keys, nesting
  no deeper than 16, at most 65536 bytes. Otherwise HTTP 400/413.
- `channel` must be an integer in 1..20 (`1.0` is accepted; `1.5` → 400).
  Coordinates must be finite and |value| ≤ 2 000 000.
- Undeclared fields are **not** ignored: the simulator answers
  HTTP 200 + `accepted=false` to help you catch typos.
- `arena_id` must be the ASCII string `"default"`.
- `robot_id` must be a non-empty string of 1–64 UTF-8 bytes (see
  [Differences](#differences-from-the-official-simulator)); `request_id` is
  the idempotency key, 1–128 bytes. Neither may contain control or invisible
  formatting characters.

Every JSON business response contains at least:

| Field | Type | Meaning |
|---|---|---|
| `accepted` | boolean | whether the simulator accepted the request |
| `real_timestamp_ms` | number | real-world timestamp of the response, in ms |
| `virtual_time_s` | number | virtual clock, in seconds |

If `accepted=false`, the response contains only these three fields.

| HTTP status | Meaning |
|---|---|
| 200, `accepted=true` | action executed |
| 200, `accepted=false` | valid JSON but rejected by the test state; unknown field; `arena_id`/`robot_id` mismatch |
| 400 | JSON syntax, duplicate key, missing field, field type, identifier format, channel or coordinate range error |
| 404 | unknown or inexact path |
| 405 | known path with a non-POST method |
| 409 | same `request_id` with different content, or concurrent different actions |
| 413 | body larger than 65536 bytes |
| 415 | unsupported Content-Type or Content-Encoding |
| 429 | invalid-traffic protection, or the per-test idempotency record limit reached |
| 500 | simulator internal error |

Idempotency: use a **new `request_id` for every new action**; reuse the
original payload and `request_id` only when retrying the identical action
after a network failure. The same ID with the same content replays the first
complete response; the same ID with different content returns 409. Structural
errors, unknown fields and identifier mismatches do not consume the ID.

Not every error produces an HTTP response: before the countdown finishes,
while the interface is closed, and after the test has ended, the connection
may simply be dropped. Your program must handle both "cannot connect" and
"received JSON" — always check the HTTP status **and** `accepted`.

## Command Reference (official examples)

### POST /enter — enter the target area

```json
{
  "arena_id": "default",
  "robot_id": "<team number>",
  "request_id": "enter-1"
}
```

Accepted response (does not advance the virtual clock):

```json
{
  "accepted": true,
  "real_timestamp_ms": 1760000000000,
  "virtual_time_s": 0,
  "max_virtual_duration_s": 360000,
  "max_real_duration_s": 1200,
  "remaining_real_duration_s": 1200
}
```

`remaining_real_duration_s` is the real time actually remaining for this
test (integer 0..1200) — do not assume it is always 1200.

### POST /measure — measure

```json
{
  "arena_id": "default",
  "robot_id": "<team number>",
  "request_id": "measure-1",
  "position": {"x": 300, "y": 400},
  "channel": 1
}
```

Three possible results:

```json
{"accepted": true, "real_timestamp_ms": 1760000000000,
 "virtual_time_s": 105, "measure_result": "no_signal"}
```

```json
{"accepted": true, "real_timestamp_ms": 1760000000000,
 "virtual_time_s": 105, "measure_result": "near"}
```

```json
{"accepted": true, "real_timestamp_ms": 1760000000000,
 "virtual_time_s": 105, "measure_result": "direction", "svd_deg": 123.45}
```

- `no_signal` — no receivable signal on that channel at this position (no
  uncleared source on the channel / beyond its reception radius of
  1000–1500 m / outside a directional source's ±90° coverage).
- `near` — signal detected but the source is within 5 m; no `svd_deg`.
- `direction` — `svd_deg` is the measured bearing to the source (0° = east,
  counter-clockwise, [0,360)), with an error within ±1°, rounded to two
  decimals. Do not treat a single reading as the exact true bearing.

### POST /clear — locate and clear

```json
{
  "arena_id": "default",
  "robot_id": "<team number>",
  "request_id": "clear-1",
  "position": {"x": 300, "y": 0},
  "channel": 3
}
```

Two possible results:

```json
{"accepted": true, "real_timestamp_ms": 1760000000000,
 "virtual_time_s": 194, "clear_result": "no_target_in_range"}
```

```json
{"accepted": true, "real_timestamp_ms": 1760000000000,
 "virtual_time_s": 196, "clear_result": "success"}
```

On a legal `/clear` the simulator first runs the optical detector for precise
location (3 s); if a target is found within 20 m, the laser gun is fired
immediately to clear it (2 s more). The clearance radius is 20 m, regardless
of the source's orientation. A source can be cleared only once; clearing it
again returns `no_target_in_range`. The channel field targets the source to
clear and never switches the direction finder.

### POST /exit — leave the target area

```json
{
  "arena_id": "default",
  "robot_id": "<team number>",
  "request_id": "exit-1"
}
```

```json
{
  "accepted": true,
  "real_timestamp_ms": 1760000000000,
  "virtual_time_s": 199,
  "exit_reason": "user_exit"
}
```

## Official Example Program

The code below (from Attachment 2, Section 11) only demonstrates how to send
the four requests and read the responses — it contains no search strategy.

Python:

```python
import json
from urllib.request import Request, urlopen

# Change this to your own team number. In this local replica any
# non-empty string works as robot_id.
BASE_URL = "http://127.0.0.1:2026"
ROBOT_ID = "<team number>"


def post(path, payload):
    request = Request(
        BASE_URL + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as http_response:
        response = json.loads(http_response.read().decode("utf-8"))
    print(path, response)
    return response


def base(request_id):
    return {
        "arena_id": "default",
        "robot_id": ROBOT_ID,
        "request_id": request_id,
    }


def action(request_id, x, y, channel):
    payload = base(request_id)
    payload["position"] = {"x": x, "y": y}
    payload["channel"] = channel
    return payload


def main():
    enter_response = post("/enter", base("enter-1"))
    if enter_response.get("accepted") is not True:
        print("enter failed")
        return

    remaining_time = enter_response["remaining_real_duration_s"]
    print("remaining real time:", remaining_time, "s")

    # The same four actions as the timing example in Section 10.
    actions = [
        ("/measure", action("measure-1", 300, 400, 1)),
        ("/measure", action("measure-2", 300, 400, 2)),
        ("/clear", action("clear-1", 300, 0, 3)),
        ("/measure", action("measure-3", 300, 0, 2)),
    ]

    for path, payload in actions:
        # When retrying this action after a network failure, reuse this
        # payload and its request_id unchanged.
        response = post(path, payload)
        if response.get("accepted") is not True:
            print("request not executed")
            return

        if path == "/measure":
            if response["measure_result"] == "direction":
                print("svd:", response["svd_deg"], "deg")
            elif response["measure_result"] == "near":
                print("too close, no svd")
            else:
                print("no signal")
        else:
            if response["clear_result"] == "success":
                print("cleared")
            else:
                print("no target near the clear position")

    exit_response = post("/exit", base("exit-1"))
    if exit_response.get("accepted") is True:
        print("exit reason:", exit_response["exit_reason"])


main()
```

MATLAB:

```matlab
function demo_robot()

% Change this to your own team number.
baseUrl = 'http://127.0.0.1:2026';
robotId = '<team number>';
options = weboptions('MediaType', 'application/json', 'Timeout', 5);

enterRequest = baseRequest(robotId, 'enter-1');
enterResponse = post(baseUrl, '/enter', enterRequest, options);
if enterResponse.accepted ~= true
    fprintf('enter failed\n');
    return;
end

remainingTime = enterResponse.remaining_real_duration_s;
fprintf('remaining real time: %d s\n', remainingTime);

% The same four actions as the timing example in Section 10.
actions = {
    '/measure', actionRequest(robotId, 'measure-1', 300, 400, 1);
    '/measure', actionRequest(robotId, 'measure-2', 300, 400, 2);
    '/clear',   actionRequest(robotId, 'clear-1', 300, 0, 3);
    '/measure', actionRequest(robotId, 'measure-3', 300, 0, 2)
};

for i = 1:size(actions, 1)
    path = actions{i, 1};
    payload = actions{i, 2};
    % When retrying this action after a network failure, reuse this
    % payload and its request_id unchanged.
    response = post(baseUrl, path, payload, options);
    if response.accepted ~= true
        fprintf('request not executed\n');
        return;
    end

    if strcmp(path, '/measure')
        if strcmp(response.measure_result, 'direction')
            fprintf('svd: %.2f deg\n', response.svd_deg);
        elseif strcmp(response.measure_result, 'near')
            fprintf('too close, no svd\n');
        else
            fprintf('no signal\n');
        end
    else
        if strcmp(response.clear_result, 'success')
            fprintf('cleared\n');
        else
            fprintf('no target near the clear position\n');
        end
    end
end

exitRequest = baseRequest(robotId, 'exit-1');
exitResponse = post(baseUrl, '/exit', exitRequest, options);
if exitResponse.accepted == true
    fprintf('exit reason: %s\n', exitResponse.exit_reason);
end
end

function request = baseRequest(robotId, requestId)
request = struct( ...
    'arena_id', 'default', ...
    'robot_id', robotId, ...
    'request_id', requestId);
end

function request = actionRequest(robotId, requestId, x, y, channel)
request = baseRequest(robotId, requestId);
request.position = struct('x', x, 'y', y);
request.channel = channel;
end

function response = post(baseUrl, path, payload, options)
response = webwrite([baseUrl path], payload, options);
fprintf('%s  %s\n', path, jsonencode(response));
end
```

## Programming Notes

- Start a test in the simulator UI first and wait until it shows the robot
  interface is ready.
- Use a new `request_id` for every new action; reuse the original ID and
  payload only when retrying the identical action.
- Send actions strictly one by one and wait for each full response; do not
  send different actions concurrently. Serial legal actions have no rate
  limit.
- Check the HTTP status **and** `accepted`. When `accepted=false` the action
  had no effect, and `virtual_time_s=0` is not the current virtual time.
- Control your real running time with `remaining_real_duration_s` from
  `/enter`; do not assume 1200 s every time.
- The 5 seconds added per measure are virtual time — no need to actually wait
  in real time.
- Read `svd_deg` only when `measure_result="direction"`; it carries an error.
- `/clear` does not switch the direction finder; only a legal `/measure`
  updates the current channel.
- `no_signal` does not mean there is no source nearby — it may be beyond the
  reception radius or outside a directional source's coverage.
- After the test ends the interface is closed; do not call `/exit` to query
  the ending reason — it is shown by the UI.
- Judge results by the actual strings `direction`, `near`, `no_signal`,
  `success`, `no_target_in_range`, `user_exit`, not by UI captions.
- Your robot program should record its own command/response history; the
  official simulator does not do this for you (this replica does write a
  behavior log).

## Differences from the Official Simulator

The official online login, server-time verification and encrypted log upload
cannot be replicated locally, therefore:

- **No account/login and no 17:30 deadline.** Any non-empty `robot_id`
  (1–64 bytes) is accepted — the protocol field itself is unchanged.
- Logs are plaintext JSON files stored in `simulator/data/logs/`, exported,
  deleted or opened from the "Test Logs" page.
- The admin UI is served by the same port under `/` and `/api/*` paths; the
  robot interface (the four exact paths above) is unaffected.
- Simulation parameters are configurable in the Settings page (defaults
  equal the official values): port, command-feedback display count, arena
  radius, source count / channel / reception-radius ranges, svd error,
  random seed (for reproducible cases), move speed, action durations,
  near threshold, clear radius, initial channel, countdown / window /
  program / virtual time limits, formal attempt count, and a custom
  source-case editor. Formal attempt counts can be reset.
- Extra local aids: a visualization page, a batch-testing page (automated
  multi-run testing with aggregated statistics, see above) and an optional
  "reveal truth" debug switch for formal tests (practice always reveals).
- **Visualization page**: live map plus event replay (robot, source truth,
  svd rays, trail and grid; truth and trail are shown by default, detection
  ranges and rays are off). Clicking any point (source, measure point, clear
  point, rejected request or the robot) opens a detail card; each source can
  individually toggle its detection range, svd rays and visibility (a hidden
  source fades to a ghost that stays clickable); the sidebar global switches
  bulk-apply to all sources and show an indeterminate checkbox when mixed;
  the detail card follows its point while panning and zooming, and hides
  temporarily while the point is out of view.
- **Settings page**: leaving with unsaved changes raises a three-way dialog
  (save and leave / discard / stay), marks the tab with a dot, and the
  browser warns before the page is closed or reloaded.
- **Admin API hardening**: `/api/*` POST requests must carry
  `Content-Type: application/json` (otherwise 415), and every request must
  have a loopback Host header (127.0.0.1 / localhost, otherwise 421),
  blocking cross-site form posts and DNS rebinding. The robot interface
  (the four exact paths) is unchanged; browsers and ordinary HTTP clients
  satisfy these checks automatically.

## Data Location

Configuration, formal attempt counts and logs are stored in
`simulator/data/`; batch reports and robot output logs are stored in
`simulator/data/batches/`. When moving the simulator, move the whole directory
together.

## License

[MIT](LICENSE)
