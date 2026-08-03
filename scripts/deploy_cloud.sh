#!/bin/bash
# scripts/deploy_cloud.sh - 1-Click Automated Cloud Server Installer
set -e

echo "🚀 Starting Anti Gravity Den 24/7 Cloud Deployment..."

# 1. Update Linux System Packages
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y python3-pip python3-venv git curl

# 2. Setup Python Virtual Environment
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 3. Write Environment Credentials if missing
if [ ! -f ".env" ]; then
    cat << 'EOF' > .env
# Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in environment variables or .env file
EOF
    echo "[✓] Environment credentials created in .env"
fi

# 4. Install & Enable Systemd Daemon Service
sudo cp systemd/den_scanner.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now den_scanner.service

echo "=========================================================="
echo "🎉 DEPLOYMENT COMPLETE! 24/7 CLOUD SCANNER IS NOW LIVE!"
echo "=========================================================="
echo "Check status: sudo systemctl status den_scanner.service"
echo "View logs  : journalctl -u den_scanner.service -f"
