#!/bin/bash
# Build the Mac apps into ~/Applications: Finance.app (your data) and Finance Demo.app (made-up data).
# Alias mode: the apps run the code in this folder with its .venv, so code changes need no rebuild.
set -euo pipefail
cd "$(dirname "$0")"
PY=../.venv/bin/python
[ -f Finance.icns ] || $PY make_icon.py
mkdir -p "$HOME/Applications"
for demo in 0 1; do
  rm -rf build dist
  FINANCE_APP_DEMO=$demo $PY setup_app.py py2app -A >/dev/null
  app=$(ls -d dist/*.app)
  name=$(basename "$app")
  rm -rf "$HOME/Applications/$name"
  mv "$app" "$HOME/Applications/$name"
  echo "Built $HOME/Applications/$name"
done
rm -rf build dist
