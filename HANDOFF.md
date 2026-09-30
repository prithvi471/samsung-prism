# Handoff — getting a real FDB-v3 result

Branch `feat/revision-aware-execution` is code-complete and offline-verified.
**The benchmark has never been run and the agent has never joined a live LiveKit
room.** This document is the shortest path from here to a real number.

Read §5 before §4. Do not start the 100-scenario run first.

---

## 1. Required environment

| Requirement | Why | Notes |
|---|---|---|
| Ubuntu 22.04 (or WSL2 Ubuntu) | Every script is bash; telemetry paths are `/tmp/...` and the harness hardcodes them | Windows-native will not work |
| Python 3.10 | `python3.10-venv`; the harness pins `livekit-agents~=1.3`, we pin `1.8.3` | 3.11 untested against this stack |
| NVIDIA GPU + CUDA 12.x | NeMo `parakeet-tdt-0.6b-v2` ASR, run by the harness, not by us | ~3 GB download on first use |
| `apt install ffmpeg unzip espeak-ng python3.10-venv` | audio conversion, data extract, Kokoro phonemes | `espeak-ng` is required by local Kokoro |
| ~15 GB free disk | torch + NeMo + models + benchmark audio | |
| Outbound HTTPS | LiveKit Cloud, Groq, Ollama Cloud, OpenAI | |

Docker is **optional** (`KOKORO_MODE=docker`). The default runs Kokoro as a
local Python server.

## 2. Required API keys

All go in `.env` at the repo root (`cp .env.example .env`). Nothing is committed.

| Key | Used for | Required? |
|---|---|---|
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | Transport. Both the agent and the harness client join the same room | **Yes** — free tier at cloud.livekit.io |
| `GROQ_API_KEY` | STT, `whisper-large-v3-turbo` | **Yes** |
| `OLLAMA_API_KEY` | Planner, `gpt-oss:120b` → `gemma4:31b` | **Yes** — both tags confirmed present on Ollama Cloud with tool support |
| `OPENAI_API_KEY` | gpt-4o judge in the evaluators (`--use-llm`) | **Yes for a real score.** Preflight only warns if absent; without it argument matching degrades to exact string match and response accuracy is skipped entirely |

## 3. Setup

```bash
git clone -b feat/revision-aware-execution https://github.com/SaumyaBish-t/Samsung.git
cd Samsung
cp .env.example .env    # fill in the keys from §2
```

Offline sanity check first — no keys needed, takes seconds:

```bash
bash scripts/verify.sh
```

Expect: 14 modules syntax-clean, 17/17 unit tests pass, extension demo PASS,
exit 0. If this fails, stop; nothing downstream will work.

`scripts/run_fdb_v3.sh` does the rest of the setup itself (venv, deps, pinned
FDB-v3 checkout at `3e799c45` — verified as upstream `main` — benchmark audio
via gdown, Kokoro server, model files).

## 4. Full benchmark command

```bash
bash scripts/run_fdb_v3.sh
```

Budget 60–90 minutes for a cold first run; most of it is torch/NeMo install and
the audio download, both cached afterwards.

### Output locations

| Path | Contents |
|---|---|
| `eval/results/<timestamp>/preflight.txt` | Key + provider + tool-call checks |
| `eval/results/<timestamp>/agent.log` | Worker log — **the revision events live here** |
| `eval/results/<timestamp>/inference.log` | Per-scenario harness output |
| `eval/results/<timestamp>/interrupt_agent_evaluation_report.json` | Tool-selection F1, argument accuracy, response accuracy |
| `eval/results/<timestamp>/interrupt_agent_pass_rate_report.json` | Strict binary pass rate + breakdowns by domain/difficulty/disfluency |
| `eval/results/<timestamp>/eval_latency.log` | First-response / tool-call / task-completion latency |
| `eval/results/<timestamp>/agent_tool_calls.log` | The committed set — what the evaluator actually scores |
| `eval/results/<timestamp>/agent_heartbeat.log` | `LATENCY_TRACK_JSON` lines |
| `eval/results/<timestamp>/config.env`, `pip-freeze.txt` | Reproducibility, secrets stripped |

## 5. First live test — do this before anything else

**One scenario, end to end.** The single highest-risk unknown is not the
planner; it is whether the commit barrier fires at all. If it does not, nothing
is written to `agent_tool_calls.log` and **every scenario scores zero** while
the agent appears to work perfectly.

```bash
bash scripts/run_fdb_v3.sh        # Ctrl-C after preflight passes, or:
bash scripts/smoke_test.sh travel_01
```

Then, in order:

1. **Preflight must pass on its own.** `python scripts/preflight.py` — checks
   keys, both planner models emit a tool call on a self-correction probe, Groq
   lists the STT model, Kokoro returns audio bytes.
2. **Run `travel_01`** (easy, one expected call: `search_flights` to Tokyo).
3. **Check the telemetry file is non-empty:**
   ```bash
   wc -l /tmp/agent_tool_calls.log && cat /tmp/agent_tool_calls.log
   ```
   One line, `"function": "search_flights"`, args Tokyo / July 15.
   **If this file is empty, stop and debug the barrier — see §6.**
4. **Check the commit barrier logged:**
   ```bash
   grep -E "TASK_COMMITTED|commit barrier|shutdown flush" /tmp/smoke_agent.log
   ```
5. **Then run one correction scenario:** `bash scripts/smoke_test.sh housing_25`.
   Expect exactly **3** committed calls — `pets_allowed`, `max_price=3500`,
   `search_apartments` — and a `TASK_STALE` line with
   `phase=superseded_by_slot` for the discarded `max_price=3000`.

Only after both pass should you start the 100-scenario run.

## 6. Diagnostic checklist

Work top to bottom; each stage depends on the ones above it.

### LiveKit startup
- `grep "AGENT JOINING ROOM" /tmp/agent_heartbeat.log` — agent reached the room.
- `grep "registered worker" /tmp/smoke_agent.log` — worker registered with Cloud.
- Room names must match between agent log and `result_*.json` `room_name`; the
  harness filters telemetry by room, so a mismatch silently yields zero calls.
- `sleep 15` in `run_fdb_v3.sh` is the registration window. If the worker is slow
  to register, the first scenarios run with no agent present.

### STT
- `grep "STT (final=" /tmp/smoke_agent.log` — should show partial then final.
- Empty transcripts → check `GROQ_API_KEY` and that `whisper-large-v3-turbo` is
  listed (preflight covers this).
- Compare the final transcript against `result_*.json` `input_transcript`.

### Planner tool call
- `grep "TASK_STARTED" /tmp/smoke_agent.log` — the planner emitted a call.
- No `TASK_STARTED` but a spoken reply → the model answered from memory. Check
  the fallback did not silently take over: `grep -i "fallback\|gemma4"`.
- `FallbackAdapter` attempt timeout is 15 s; a stalled primary can consume the
  entire recording window.

### TTS
- `curl -sf http://127.0.0.1:8880/v1/models` — Kokoro alive.
- `output_interrupt_agent.wav` should be non-silent:
  `ffmpeg -i output_*.wav -af volumedetect -f null - 2>&1 | grep mean_volume`
- Silent output but correct tool calls → TTS path broken; pass rate survives,
  response accuracy and all latency metrics go to zero.

### Tool execution
- `grep -c "TASK_STARTED" /tmp/smoke_agent.log` vs
  `wc -l /tmp/agent_tool_calls.log` — started ≥ committed, always.
- Committed but wrong args → compare against `expected_tool_calls` in the
  scenario's `metadata.json`.

### Interruption / revision
- `grep -E "INTENT_REVISED|TASK_STALE|DUPLICATE_SUPPRESSED"` on a correction
  scenario. No `INTENT_REVISED` on `housing_25` means the retraction cue regex
  did not match the ASR transcript — check what Whisper actually produced.
- `phase=after_execution` proves the post-execution re-validation fired.

### Latency logging
- `grep LATENCY_TRACK_JSON /tmp/agent_heartbeat.log` — one line per turn.
- `result_*.json` should carry `perceived_total_latency` and
  `search_latency_breakdown`.
- Negative latencies are normal here and are **filtered out** of the aggregate
  statistics by `analyze_tool_latency.py`; do not read the mean as covering 100
  samples.

## 7. Known risks

1. **The commit barrier is unexercised.** It fires on `agent_state_changed ->
   speaking`, with `ctx.add_shutdown_callback` as a safety net. Neither has run
   against livekit-agents 1.8.3. **This is the failure that looks like success** —
   verify §5 step 3 before anything else.
2. **Live LiveKit behaviour is entirely untested.** No session has ever started.
3. **`search_apartments` optional args and `update_search_filter` coercion**
   change logged arguments. Intended, but never seen by the gpt-4o judge.
4. **Endpointing is untuned** (~1.0 s trigger) against scenarios containing
   literal `"um...... um...... um"`. 29 scenarios carry PAUSE or HESITATION.
5. **`MOCK_LATENCY=instant`**, matching the reference `cascaded_agent.py` and
   every published baseline. It also makes the correction-during-execution
   window vanish, so that path is exercised only by the unit tests.
6. **`OPENAI_API_KEY` absent degrades scoring silently** — preflight warns, it
   does not fail.
7. **Ground truth is noisy** (`travel_02` expects `P9-9-9-90011` for a spoken
   `P-8-8-9-9-0-0-1-1`). A perfect score is not attainable.
8. **Push access.** The remote is `SaumyaBish-t/Samsung`; if you are not a
   collaborator, fork and open a PR instead.

## 8. Getting the baseline comparison

To claim the innovation helps, run twice and diff `pass_rate_report.json`:

```bash
PROVIDER_LABEL=baseline    bash scripts/run_fdb_v3.sh   # on 24c027a
PROVIDER_LABEL=revision    bash scripts/run_fdb_v3.sh   # on this branch
```

Pay attention to the 21 `state_rollback_test` scenarios specifically — that is
where the mechanism is supposed to act. If pass rate does not move there, the
innovation did not do its job, and README §7 should say so.
