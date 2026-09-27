#!/bin/bash
# Build ~/Applications/Finance.app: clicking it starts the local server if it isn't
# running, then opens the app in its own Chrome window (or the default browser).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="${1:-$HOME/Applications/Finance.app}"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Finance</string>
  <key>CFBundleIdentifier</key><string>local.finance.launcher</string>
  <key>CFBundleExecutable</key><string>Finance</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSUIElement</key><true/>
</dict></plist>
EOF

cat > "$APP/Contents/MacOS/Finance" <<EOF
#!/bin/bash
ROOT="$ROOT"
PORT=8770
URL="http://127.0.0.1:\$PORT/"
up() { curl -s -m 1 -o /dev/null "\${URL}api/status"; }
if ! up; then
  mkdir -p "\$ROOT/data"
  nohup "\$ROOT/start.sh" >> "\$ROOT/data/server.log" 2>&1 &
  disown
  for _ in \$(seq 1 40); do up && break; sleep 0.25; done
fi
if [ -d "/Applications/Google Chrome.app" ]; then
  open -na "Google Chrome" --args --app="\$URL"
else
  open "\$URL"
fi
EOF
chmod +x "$APP/Contents/MacOS/Finance"
echo "Built $APP"
