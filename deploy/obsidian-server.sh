#!/bin/bash
# Install the Obsidian desktop app on a headless Ubuntu server and keep it running on a
# virtual display, so Obsidian Sync can push the bot's notes to all your devices.
#
#   sudo bash deploy/obsidian-server.sh            # installs the version pinned below
#   sudo bash deploy/obsidian-server.sh 1.13.7     # or a specific version
#
# Afterwards a one-time sign-in through VNC is needed (instructions are printed at the end).
set -euo pipefail

VER="${1:-1.13.7}"
USER_NAME="${SUDO_USER:-ubuntu}"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
VAULT="$HOME_DIR/vault"
ARCH="$(dpkg --print-architecture)"
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y --no-install-recommends xvfb x11vnc curl ca-certificates fonts-dejavu-core

if [ "$ARCH" = "amd64" ]; then
  # The .deb pulls in every library Electron needs.
  curl -fsSL -o /tmp/obsidian.deb \
    "https://github.com/obsidianmd/obsidian-releases/releases/download/v${VER}/obsidian_${VER}_amd64.deb"
  apt-get install -y /tmp/obsidian.deb
  BIN=/opt/Obsidian/obsidian
else
  # ARM: extract the AppImage (no FUSE needed) and install Electron's runtime libraries.
  # Package names differ between Ubuntu releases; try the newer name, then the older one.
  curl -fsSL -o /tmp/Obsidian.AppImage \
    "https://github.com/obsidianmd/obsidian-releases/releases/download/v${VER}/Obsidian-${VER}-arm64.AppImage"
  chmod +x /tmp/Obsidian.AppImage
  rm -rf /opt/obsidian && mkdir -p /opt/obsidian
  (cd /opt/obsidian && /tmp/Obsidian.AppImage --appimage-extract >/dev/null)
  for pair in libgtk-3-0t64:libgtk-3-0 libnss3:libnss3 libasound2t64:libasound2 libgbm1:libgbm1 \
              libxss1:libxss1 libatk-bridge2.0-0t64:libatk-bridge2.0-0 libcups2t64:libcups2 \
              libxkbcommon0:libxkbcommon0 libdrm2:libdrm2 libxcomposite1:libxcomposite1 \
              libxdamage1:libxdamage1 libxrandr2:libxrandr2 libnotify4:libnotify4 libsecret-1-0:libsecret-1-0; do
    apt-get install -y --no-install-recommends "${pair%%:*}" 2>/dev/null \
      || apt-get install -y --no-install-recommends "${pair##*:}"
  done
  BIN=/opt/obsidian/squashfs-root/obsidian
fi

sudo -u "$USER_NAME" mkdir -p "$VAULT"

cat > /etc/systemd/system/obsidian-xvfb.service <<EOF
[Unit]
Description=Virtual display for Obsidian

[Service]
User=$USER_NAME
ExecStart=/usr/bin/Xvfb :99 -screen 0 1280x800x24 -nolisten tcp
Restart=always

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/obsidian.service <<EOF
[Unit]
Description=Obsidian desktop on a virtual display (for Obsidian Sync)
Requires=obsidian-xvfb.service
After=obsidian-xvfb.service network-online.target

[Service]
User=$USER_NAME
Environment=DISPLAY=:99
Environment=HOME=$HOME_DIR
ExecStart=$BIN --no-sandbox --disable-gpu --disable-dev-shm-usage
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now obsidian-xvfb obsidian

cat <<EOF

Obsidian is running on virtual display :99 as $USER_NAME. Sign in once through VNC:

  on the server:   x11vnc -display :99 -localhost -nopw -once
  on your Mac:     ssh -L 5900:localhost:5900 $USER_NAME@SERVER
                   then Finder > Go > Connect to Server > vnc://localhost:5900

In the Obsidian window: "Open folder as vault" -> $VAULT, sign in to your Obsidian
account, Settings > Sync > connect the remote vault your phone uses, and enable sync.
Then set OBSIDIAN_VAULT_PATH=$VAULT/YouTube in ~/yt2obsidian/.env and restart the bot:
  sudo systemctl restart yt2obsidian
EOF
