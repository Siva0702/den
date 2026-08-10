#!/usr/bin/env bash
#
# Den Engine — one-shot bootstrap for an Oracle Cloud Always Free ARM instance.
#
# Run ON THE SERVER as the `ubuntu` user:
#   curl -fsSL https://raw.githubusercontent.com/Siva0702/den/main/scripts/oracle_bootstrap.sh | bash
#
# Idempotent: safe to re-run. Re-running pulls latest code and restarts cleanly.
#
# Why this exists: Render's free tier caps egress at 5 GB/month and the engine burns
# ~27 GB/month polling 87 assets across 5 timeframes. The workspace was suspended
# mid-session with no warning beyond an email. Oracle Always Free allows 10 TB/month,
# so the same workload uses ~0.3% of the allowance and cannot be suspended for it.
set -euo pipefail

REPO="https://github.com/Siva0702/den.git"
DIR="/home/ubuntu/den"
SVC="den_scanner"

echo "==> [1/7] system packages"
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip git curl >/dev/null

echo "==> [2/7] source"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch --quiet origin main && git -C "$DIR" reset --hard origin/main --quiet
else
  git clone --quiet "$REPO" "$DIR"
fi

echo "==> [3/7] virtualenv + dependencies"
[ -d "$DIR/venv" ] || python3 -m venv "$DIR/venv"
"$DIR/venv/bin/pip" install --quiet --upgrade pip
"$DIR/venv/bin/pip" install --quiet -r "$DIR/requirements.txt"

echo "==> [4/7] environment"
# .env is NEVER committed. It must already exist, or be created here once.
if [ ! -f "$DIR/.env" ]; then
  cat <<'WARN'

  !! No .env found. Create it now with your four values, then re-run this script:

     nano /home/ubuntu/den/.env

     TELEGRAM_BOT_TOKEN=...
     TELEGRAM_CHAT_ID=...
     UPSTASH_REDIS_REST_URL=...
     UPSTASH_REDIS_REST_TOKEN=...

  All engine state (1000+ ledger records) lives in Upstash Redis, so once these
  are set the engine restores everything on first boot. Nothing is lost in the move.

WARN
  exit 1
fi
chmod 600 "$DIR/.env"

echo "==> [5/7] firewall"
# Oracle images ship with a REJECT-all iptables policy that silently blocks every
# inbound port. Opening the Security List in the web console alone is not enough —
# this is the step people miss and then conclude the instance is broken.
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 10000 -j ACCEPT 2>/dev/null || true
sudo netfilter-persistent save >/dev/null 2>&1 || sudo apt-get install -y -qq iptables-persistent >/dev/null 2>&1 || true

echo "==> [6/7] systemd service"
sudo tee /etc/systemd/system/${SVC}.service >/dev/null <<UNIT
[Unit]
Description=Den Engine 24/7 Quant Scanner
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=${DIR}
EnvironmentFile=${DIR}/.env
ExecStart=${DIR}/venv/bin/python3 -u models/auto_scanner.py
Restart=always
RestartSec=15
# A crash loop must not spin forever silently.
StartLimitIntervalSec=300
StartLimitBurst=5
StandardOutput=append:/var/log/den_scanner.log
StandardError=append:/var/log/den_scanner.log

[Install]
WantedBy=multi-user.target
UNIT

sudo touch /var/log/den_scanner.log && sudo chown ubuntu:ubuntu /var/log/den_scanner.log
sudo tee /etc/logrotate.d/den_scanner >/dev/null <<'ROT'
/var/log/den_scanner.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
    copytruncate
}
ROT

sudo systemctl daemon-reload
sudo systemctl enable ${SVC} >/dev/null 2>&1
sudo systemctl restart ${SVC}

echo "==> [7/7] verify"
sleep 20
sudo systemctl is-active ${SVC} && echo "    service: active"
echo "    logs:    tail -f /var/log/den_scanner.log"
echo "    status:  curl -s http://localhost:10000/"
echo
echo "First scan takes ~2-4 min (87 assets x 5 timeframes, cold cache)."
echo "'total_scans: 0' during that window is normal, not a hang."
