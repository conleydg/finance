#!/bin/bash
# Build ~/Applications/Finance.app: clicking it starts the local server if it isn't
# running, then opens the app in its own Chrome window (or the default browser).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# DEMO=1 builds "Finance Demo.app", which runs demo.sh (made-up data) on port 8771.
if [ "${DEMO:-}" = 1 ]; then
  NAME="Finance Demo"; START="demo.sh"; PORT=8771; BUNDLE_ID=local.finance.demo
else
  NAME="Finance"; START="start.sh"; PORT=8770; BUNDLE_ID=local.finance.launcher
fi
APP="${1:-$HOME/Applications/$NAME.app}"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>$NAME</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleExecutable</key><string>Finance</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSUIElement</key><true/>
</dict></plist>
EOF

cat > "$APP/Contents/MacOS/Finance" <<EOF
#!/bin/bash
ROOT="$ROOT"
PORT=$PORT
URL="http://127.0.0.1:\$PORT/"
up() { curl -s -m 1 -o /dev/null "\${URL}api/status"; }
if ! up; then
  mkdir -p "\$ROOT/data"
  nohup "\$ROOT/$START" >> "\$ROOT/data/server.log" 2>&1 &
  disown
  for _ in \$(seq 1 40); do up && break; sleep 0.25; done
fi
if [ -d "/Applications/Google Chrome.app" ]; then
  # A fresh query string each launch means Chrome never reuses a stale copy of the page.
  open -na "Google Chrome" --args --app="\${URL}?launch=\$(date +%s)"
else
  open "\$URL"
fi
EOF
chmod +x "$APP/Contents/MacOS/Finance"
echo "Built $APP"
