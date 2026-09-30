# Extension — in-car destination change

The benchmark's mock APIs are pure, so a stale tool call there costs you a
wrong line in a log. In a car it costs you a wrong turn. This extension runs
the same revision-aware executor (`agent/revision.py`, unchanged) against a
simulated head unit that has real mutable state, so the invariant becomes
visible:

> A stale execution may finish computing, but it must never commit a side effect.

## Run it

```bash
python -m extension.demo --both
```

No network, no API keys, no GPU, no LiveKit. Exits non-zero if the invariant
is violated, so it doubles as a test.

| Flag | What it does |
| --- | --- |
| *(none)* | Protected run only |
| `--unprotected` | The same script with commit-on-execute, to show the failure |
| `--both` | Runs both and prints the difference |

## The scenario

Fixed and deterministic:

```
driver : "Navigate to Chennai Central."
         -> set_destination + start_navigation planned under intent v1
         -> route solving begins (300 ms of pure computation)
driver : "Wait - actually go to the airport."      <- lands 100 ms in
         -> retraction cue bumps the intent to v2
agent  : set_destination + start_navigation planned under v2
```

## Result

| | Routes computed | State changes applied | Outcome |
| --- | --- | --- | --- |
| Without protection | 4 | 4 | Car is briefly guided to **Chennai Central**; navigation starts **twice** |
| With protection | 3 | 2 | Only the airport is ever applied; navigation starts **once** |

The protected run still computes three routes — the retracted one finishes,
which is the whole point. It is discarded at the commit barrier rather than
being cancelled mid-flight, because cancelling a running computation is not
always safe and is never necessary when the commit is what you control.

## How it maps onto the architecture

Each tool is split into the two halves the invariant is about:

| Half | Where | Properties |
| --- | --- | --- |
| `compute` | `NavComputer.__call__` | Expensive, pure, safe to run speculatively |
| `apply` | `HeadUnit.apply` | Mutates the car, only ever called by `commit` |

`RevisionAwareExecutor` runs `compute` eagerly and calls `commit` only for the
calls that survive to the barrier. In the benchmark the same `commit` hook
writes the telemetry line the evaluator reads — the two use cases differ only
in what the irreversible act happens to be.

`set_destination` and `start_navigation` are registered as singleton slots, so
a second destination supersedes the first rather than queueing behind it.
