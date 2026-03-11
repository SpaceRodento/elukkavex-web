#!/bin/bash
# Elukkavex — Raspberry Pi -asennusskripti
# Asentaa kuvapalvelimen + Telegram-botin systemd-palveluina.
# Vaatii: Python 3.9+, pip3, sudo-oikeudet
#
# Käyttö:
#   chmod +x install.sh
#   ./install.sh

set -e

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_USER="$(whoami)"
ENV_FILE="$HOME/.elukkavex.env"

echo "╔══════════════════════════════════════╗"
echo "║  Elukkavex — Raspberry Pi asennus   ║"
echo "╚══════════════════════════════════════╝"
echo ""
echo "Repokansio:  $REPO_DIR"
echo "Käyttäjä:    $SERVICE_USER"
echo "Env-tiedosto: $ENV_FILE"
echo ""

# ── 1. Kuvakansio ──────────────────────────────────────────────────────────────
echo "▶ Luodaan kuvakansio..."
mkdir -p "$HOME/elukkavex/images"
echo "  → $HOME/elukkavex/images"

# ── 2. Python-riippuvuudet ─────────────────────────────────────────────────────
echo "▶ Asennetaan Python-riippuvuudet..."
pip3 install --quiet -r "$REPO_DIR/server/requirements.txt"
pip3 install --quiet -r "$REPO_DIR/bot/requirements.txt"
echo "  → OK"

# ── 3. Env-tiedosto ────────────────────────────────────────────────────────────
if [ ! -f "$ENV_FILE" ]; then
    cp "$REPO_DIR/server/.env.example" "$ENV_FILE"
    echo ""
    echo "  ⚠  Ympäristötiedosto luotu: $ENV_FILE"
    echo "  ⚠  MUOKKAA ENNEN JATKAMISTA:"
    echo "       nano $ENV_FILE"
    echo "  Täytä TELEGRAM_TOKEN, CHAT_IDS ja UPLOAD_TOKEN."
    echo ""
    read -p "  Paina Enter kun olet tallentanut muutokset..." -r
else
    echo "  → $ENV_FILE on jo olemassa, ei ylikirjoiteta"
fi

# ── 4. cloudflared ────────────────────────────────────────────────────────────
echo "▶ Asennetaan cloudflared..."
ARCH="$(uname -m)"
if [[ "$ARCH" == "aarch64" ]]; then
    CF_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64"
elif [[ "$ARCH" == "armv7l" ]]; then
    CF_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm"
else
    CF_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
fi
sudo curl -sL "$CF_URL" -o /usr/local/bin/cloudflared
sudo chmod +x /usr/local/bin/cloudflared
echo "  → $(cloudflared --version)"

# ── 5. systemd-palvelut ───────────────────────────────────────────────────────
echo "▶ Luodaan systemd-palvelutiedostot..."

# Kuvapalvelin
sudo tee /etc/systemd/system/elukkavex-server.service > /dev/null <<EOF
[Unit]
Description=Elukkavex kuvapalvelin
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$REPO_DIR
EnvironmentFile=$ENV_FILE
ExecStart=/usr/bin/python3 $REPO_DIR/server/server.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Telegram-botti
sudo tee /etc/systemd/system/elukkavex-bot.service > /dev/null <<EOF
[Unit]
Description=Elukkavex Telegram-botti
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$REPO_DIR
EnvironmentFile=$ENV_FILE
ExecStart=/usr/bin/python3 $REPO_DIR/bot/bot.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Cloudflare Tunnel (tilapäinen — vaihtuu uudelleenkäynnistyksessä)
sudo tee /etc/systemd/system/elukkavex-tunnel.service > /dev/null <<EOF
[Unit]
Description=Elukkavex Cloudflare Tunnel
After=network-online.target elukkavex-server.service
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
ExecStart=/usr/local/bin/cloudflared tunnel --url http://localhost:8080 --no-autoupdate
Restart=on-failure
RestartSec=15
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

echo "  → Palvelutiedostot luotu"

# ── 6. Aktivointi ─────────────────────────────────────────────────────────────
echo "▶ Aktivoidaan ja käynnistetään palvelut..."
sudo systemctl daemon-reload
sudo systemctl enable elukkavex-server elukkavex-bot elukkavex-tunnel
sudo systemctl start  elukkavex-server elukkavex-bot elukkavex-tunnel

echo ""
echo "╔══════════════════════════════════════╗"
echo "║  Asennus valmis!                     ║"
echo "╚══════════════════════════════════════╝"
echo ""
echo "Palvelujen tila:"
sudo systemctl status elukkavex-server --no-pager -l | head -5
echo ""
sudo systemctl status elukkavex-tunnel --no-pager -l | head -8
echo ""
echo "Julkinen URL löytyy tunnelin lokista:"
echo "  journalctl -u elukkavex-tunnel -f"
echo ""
echo "Muut hyödylliset komennot:"
echo "  sudo systemctl restart elukkavex-server"
echo "  journalctl -u elukkavex-server -f"
echo "  journalctl -u elukkavex-bot -f"
