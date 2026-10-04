#!/data/data/com.termux/files/usr/bin/bash
# EmptyOS — Termux setup script
# Run once: bash termux-setup.sh

set -e

REPO_URL="https://github.com/your-username/emptyos.git"  # replace with your repo URL
EMPTYOS_DIR="$HOME/emptyos"
VAULT_DIR="$HOME/storage/shared/Vault"  # adjust to your vault location

echo ""
echo "=== EmptyOS Termux Setup ==="
echo ""

# ── Storage permission ────────────────────────────────────────────────────────
echo ">> Requesting storage access..."
termux-setup-storage
sleep 2  # wait for user to grant permission

# ── System packages ───────────────────────────────────────────────────────────
echo ">> Installing system packages..."
pkg update -y
pkg install -y python git nodejs sqlite curl

# ── Clone repo ────────────────────────────────────────────────────────────────
if [ -d "$EMPTYOS_DIR" ]; then
    echo ">> EmptyOS already cloned — pulling latest..."
    git -C "$EMPTYOS_DIR" pull
else
    echo ">> Cloning EmptyOS..."
    git clone "$REPO_URL" "$EMPTYOS_DIR"
fi

cd "$EMPTYOS_DIR"

# ── Python dependencies ───────────────────────────────────────────────────────
echo ">> Installing Python dependencies..."
pip install --upgrade pip
pip install -e ".[dev]"

# ── Vault directory ───────────────────────────────────────────────────────────
echo ">> Creating vault directory at $VAULT_DIR ..."
mkdir -p "$VAULT_DIR"

# ── emptyos.toml (only if not already present) ────────────────────────────────
TOML_PATH="$EMPTYOS_DIR/emptyos.toml"
if [ ! -f "$TOML_PATH" ]; then
    echo ">> Writing default emptyos.toml..."
    cat > "$TOML_PATH" <<EOF
[notes]
path = "$VAULT_DIR"

[network]
mode = "private"   # binds 0.0.0.0 — nearby devices can connect via LAN

[capabilities.think]
provider = "openai"

[capabilities.think.openai]
api_key = ""        # add your OpenAI key here, or set OPENAI_API_KEY env var
model = "gpt-4o-mini"

[data]
path = "$EMPTYOS_DIR/data"
EOF
    echo ""
    echo "  emptyos.toml created. Edit it to add your API key:"
    echo "  nano $TOML_PATH"
fi

# ── Start script ──────────────────────────────────────────────────────────────
START_SCRIPT="$EMPTYOS_DIR/start.sh"
cat > "$START_SCRIPT" <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
cd "$HOME/emptyos"
# Acquire wakelock so Android doesn't kill the daemon
termux-wake-lock
echo "EmptyOS starting on http://localhost:9000"
echo "From another device on the same WiFi: http://$(hostname -I | awk '{print $1}'):9000"
python -m emptyos start
termux-wake-unlock
EOF
chmod +x "$START_SCRIPT"

# ── Termux widget shortcut ────────────────────────────────────────────────────
SHORTCUT_DIR="$HOME/.shortcuts"
mkdir -p "$SHORTCUT_DIR"
cp "$START_SCRIPT" "$SHORTCUT_DIR/EmptyOS"
chmod +x "$SHORTCUT_DIR/EmptyOS"

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit your config:   nano $TOML_PATH"
echo "  2. Start EmptyOS:      bash $START_SCRIPT"
echo "  3. Open in browser:    http://localhost:9000"
echo ""
echo "For one-tap launch: install the 'Termux:Widget' app and add the EmptyOS shortcut."
echo ""
