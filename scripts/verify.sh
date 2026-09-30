#!/usr/bin/env bash
# Everything that can be verified without API keys, a GPU or the benchmark data.
# Runs on Windows (Git Bash), macOS and Linux with a plain Python 3.10+.
#
#   bash scripts/verify.sh
#
# For the full benchmark (needs keys + Ubuntu + GPU) see scripts/run_fdb_v3.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python}"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

log "Python"
"$PY" --version

log "Syntax check (every module, no third-party imports needed)"
"$PY" - <<'EOF'
import ast, pathlib, sys
bad = []
for p in sorted(pathlib.Path(".").rglob("*.py")):
    if any(part in {".venv", "third_party", "__pycache__"} for part in p.parts):
        continue
    try:
        ast.parse(p.read_text(encoding="utf-8"))
    except SyntaxError as e:
        bad.append(f"{p}: {e}")
    else:
        print(f"  ok  {p}")
if bad:
    print("\n".join(bad)); sys.exit(1)
EOF

log "Import check (dependency-free modules)"
"$PY" -c "import agent.revision, extension.nav; print('  agent.revision + extension.nav import cleanly')"

log "Unit tests — revision-aware execution"
"$PY" -m unittest discover -s tests -v

log "Extension demo — in-car destination change"
"$PY" -m extension.demo --both

log "All offline checks passed."
