#!/bin/bash
# Start the finance app on http://127.0.0.1:8770 (this Mac only).
cd "$(dirname "$0")"
exec .venv/bin/uvicorn finance.app:app --host 127.0.0.1 --port "${PORT:-8770}" --log-level warning
