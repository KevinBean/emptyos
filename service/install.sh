#!/usr/bin/env bash
# EmptyOS service installer — detects platform, installs auto-start
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
EOS_DIR="$(dirname "$SCRIPT_DIR")"
USER="$(whoami)"

echo "EmptyOS Service Installer"
echo "========================="
echo "  Install dir: $EOS_DIR"
echo "  User:        $USER"
echo ""

case "$(uname -s)" in
    Linux*)
        echo "Platform: Linux"

        # Generate service file with correct paths
        SERVICE_FILE="/etc/systemd/system/emptyos.service"
        PYTHON="$(command -v python3 || echo /usr/bin/python3)"

        echo "  Python: $PYTHON"
        echo "  Service: $SERVICE_FILE"
        echo ""

        sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=EmptyOS — AI-powered personal operating system
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$EOS_DIR
ExecStart=$PYTHON -m emptyos start
ExecStop=/bin/kill -SIGTERM \$MAINPID
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1
StandardOutput=journal
StandardError=journal
SyslogIdentifier=emptyos

[Install]
WantedBy=multi-user.target
EOF

        sudo systemctl daemon-reload
        sudo systemctl enable emptyos
        sudo systemctl start emptyos

        echo ""
        echo "Installed and started."
        echo "  sudo systemctl status emptyos    # check status"
        echo "  sudo systemctl restart emptyos   # restart"
        echo "  sudo systemctl stop emptyos      # stop"
        echo "  journalctl -u emptyos -f         # follow logs"
        ;;

    Darwin*)
        echo "Platform: macOS"

        PLIST_NAME="com.emptyos.plist"
        PLIST_DIR="$HOME/Library/LaunchAgents"
        PLIST_FILE="$PLIST_DIR/$PLIST_NAME"
        PYTHON="$(command -v python3 || echo /usr/local/bin/python3)"

        echo "  Python: $PYTHON"
        echo "  Plist:  $PLIST_FILE"
        echo ""

        mkdir -p "$PLIST_DIR"

        cat > "$PLIST_FILE" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.emptyos</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>-m</string>
        <string>emptyos</string>
        <string>start</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$EOS_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>/tmp/emptyos.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/emptyos-err.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
    </dict>
</dict>
</plist>
EOF

        launchctl load "$PLIST_FILE"

        echo ""
        echo "Installed and started."
        echo "  launchctl start com.emptyos      # start"
        echo "  launchctl stop com.emptyos       # stop"
        echo "  launchctl unload $PLIST_FILE      # disable auto-start"
        echo "  tail -f /tmp/emptyos.log         # follow logs"
        ;;

    *)
        echo "Unsupported platform: $(uname -s)"
        echo "Use restart.sh for manual start/stop."
        exit 1
        ;;
esac
