#!/usr/bin/env bash
# Quick dev loop (inside WSL): run a few scenarios end to end and print
# expected vs actual tool calls. Assumes install + Kokoro already done.
#
#   bash scripts/smoke_test.sh                       # default mix
#   bash scripts/smoke_test.sh travel_09 finance_18  # specific example IDs
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
V3="$ROOT/third_party/Full-Duplex-Bench/v3"
LABEL="${PROVIDER_LABEL:-smoke}"
EXAMPLES=("$@")
[[ ${#EXAMPLES[@]} -gt 0 ]] || EXAMPLES=(travel_01 travel_09 travel_10 finance_18 ecommerce_01)

cd "$ROOT"
source .venv/bin/activate
cp .env "$V3/.env.local"
set -a; source .env; set +a
export FDB_V3_DIR="$V3"

# VAD + turn-detector weights. Cheap when already cached; without it the job
# crashes at session start on a missing "languages.json".
python -m agent.main download-files

if ! curl -sf http://127.0.0.1:8880/v1/models >/dev/null; then
  python -m agent.kokoro_server > /tmp/smoke_kokoro.log 2>&1 &
  KOKORO_PID=$!
  for _ in $(seq 1 60); do curl -sf http://127.0.0.1:8880/v1/models >/dev/null && break; sleep 2; done
fi

: > /tmp/agent_tool_calls.log
python -m agent.main start > /tmp/smoke_agent.log 2>&1 &
AGENT_PID=$!
trap 'kill $AGENT_PID ${KOKORO_PID:-} 2>/dev/null || true' EXIT
sleep 12

cd "$V3"
for ex in "${EXAMPLES[@]}"; do
  python run_tool_benchmark.py --provider "$LABEL" --example "$ex" --force 2>&1 | grep -E "Transcript|Perceived|❌|⚠️" || true
done

python - "$LABEL" "${EXAMPLES[@]}" <<'PY'
import json, sys, glob
label, examples = sys.argv[1], sys.argv[2:]
for ex in examples:
    for d in sorted(glob.glob(f"fdb_v3_data_released/{ex}_*")):
        meta = json.load(open(f"{d}/metadata.json"))
        try:
            res = json.load(open(f"{d}/result_{label}.json"))
        except FileNotFoundError:
            print(f"\n{d}: NO RESULT"); continue
        exp = [(c["function"], c["args"]) for c in meta["expected_tool_calls"]]
        act = [(c["function"], c["args"]) for c in res.get("actual_tool_calls", [])]
        names_ok = sorted(f for f, _ in exp) == sorted(f for f, _ in act)
        print(f"\n{'OK ' if names_ok else 'BAD'} {d.split('/')[-1]}  latency={res.get('perceived_total_latency')}s")
        print("   expected:", exp)
        print("   actual:  ", act)
        print("   said:    ", (res.get("transcript") or "")[:160])
PY
