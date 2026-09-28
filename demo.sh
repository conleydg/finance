#!/bin/bash
# Run a separate copy of the app on made-up data: http://127.0.0.1:8771
# The demo database lives in data-demo/ and is rebuilt on every start; your real data/ is never touched.
cd "$(dirname "$0")"
export FINANCE_DATA="$PWD/data-demo"
rm -rf "$FINANCE_DATA"
.venv/bin/python -m demo.seed || exit 1
exec .venv/bin/uvicorn finance.app:app --host 127.0.0.1 --port "${PORT:-8771}" --log-level warning
