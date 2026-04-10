#!/bin/bash
# REX FinHub Pipeline Server Setup (Oracle Cloud ARM / Ubuntu 22.04+)
# Run once to provision the server.
set -e

echo "=== REX FinHub Server Setup ==="

# 1. System packages
sudo apt-get update
sudo apt-get install -y python3.13 python3.13-venv python3-pip git rsync

# 2. Create app user
sudo useradd -m -s /bin/bash rexfinhub || echo "User already exists"

# 3. Clone repo
cd /home/rexfinhub
if [ ! -d "rexfinhub" ]; then
    sudo -u rexfinhub git clone https://github.com/ryuoelasmar/rexfinhub.git
fi
cd rexfinhub

# 4. Install Python dependencies
sudo -u rexfinhub python3.13 -m pip install --user -r requirements.txt

# 5. Create data directories
sudo -u rexfinhub mkdir -p data/DASHBOARD cache/sec logs

# 6. Prompt for config
if [ ! -f "config/.env" ]; then
    echo ""
    echo "IMPORTANT: Copy config/.env from your desktop to this server."
    echo "  scp your-desktop:C:/Projects/rexfinhub/config/.env /home/rexfinhub/rexfinhub/config/.env"
    echo ""
fi

# 7. Install systemd timers
echo "Installing systemd timers..."
sudo cp deploy/systemd/*.service /etc/systemd/system/
sudo cp deploy/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload

# Enable timers (but don't start yet — need data bootstrap first)
for timer in rexfinhub-sec-scrape rexfinhub-bloomberg rexfinhub-daily; do
    sudo systemctl enable ${timer}.timer
    echo "  Enabled: ${timer}.timer"
done

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Copy config/.env from desktop"
echo "  2. Bootstrap data: scp etp_tracker.db, structured_notes.db, discovered_trusts.json"
echo "  3. Initialize DB: python3.13 -c 'from webapp.database import init_db; init_db()'"
echo "  4. Test: python3.13 scripts/run_all_pipelines.py --skip-email --skip-market"
echo "  5. Start timers: sudo systemctl start rexfinhub-sec-scrape.timer rexfinhub-bloomberg.timer rexfinhub-daily.timer"
