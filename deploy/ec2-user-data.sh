#!/bin/bash
# First-boot script for an Ubuntu 24.04 EC2 instance (paste into "User data" when launching).
#
# Installs yt2obsidian under /home/ubuntu/yt2obsidian, pre-downloads the Whisper model, and
# registers a systemd service that starts as soon as /home/ubuntu/yt2obsidian/.env exists.
# Takes about 5 minutes. Progress: sudo tail -f /var/log/cloud-init-output.log
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive

# Point this at your fork if you have one.
REPO_URL="https://github.com/mkhlndrv/yt2obsidian.git"

apt-get update
apt-get install -y ffmpeg python3 python3-venv git

sudo -u ubuntu -H env REPO_URL="$REPO_URL" bash -euxo pipefail <<'EOF'
cd ~
git clone "$REPO_URL" yt2obsidian
cd yt2obsidian
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
# Download the transcription model now so the first job does not wait for 1.5 GB.
.venv/bin/python -c "from faster_whisper import WhisperModel; WhisperModel('distil-large-v3', device='cpu', compute_type='int8')"
EOF

cat > /etc/systemd/system/yt2obsidian.service <<'EOF'
[Unit]
Description=yt2obsidian Telegram bot
Wants=network-online.target
After=network-online.target
ConditionPathExists=/home/ubuntu/yt2obsidian/.env

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/yt2obsidian
ExecStart=/home/ubuntu/yt2obsidian/.venv/bin/python bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable yt2obsidian

cat > /home/ubuntu/NEXT_STEPS.txt <<'EOF'
yt2obsidian is installed. To start it:
  cd ~/yt2obsidian && cp .env.example .env && nano .env   # bot token, Anthropic key, your Telegram user id
  chmod 600 .env
  sudo systemctl start yt2obsidian
  journalctl -u yt2obsidian -f
EOF
chown ubuntu:ubuntu /home/ubuntu/NEXT_STEPS.txt
