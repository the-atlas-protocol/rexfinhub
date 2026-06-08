# Blue Ocean report unit files (2026-06-08)

Static systemd unit files for the weekly Blue Ocean L&I Overnight Trading
report builder (`screener/li_engine/analysis/blue_ocean_report.py`). Fires
Monday 07:10 ET so the HTML is on disk before the Monday-morning send
bundle reads it.

Recipients are loaded from the DB via `list_type='blue_ocean'`. As of
2026-06-08 the list contains: gking, gcollett, meschmann, proddevelopment,
sacheychek @rexfin.com.

## Deploy on the VPS

```bash
# From a checkout of feat/blue-ocean-integration on the VPS:
scp deploy/systemd/rexfinhub-blue-ocean.service jarvis@46.224.126.196:/tmp/
scp deploy/systemd/rexfinhub-blue-ocean.timer   jarvis@46.224.126.196:/tmp/

ssh jarvis@46.224.126.196
sudo mv /tmp/rexfinhub-blue-ocean.service /etc/systemd/system/
sudo mv /tmp/rexfinhub-blue-ocean.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rexfinhub-blue-ocean.timer
systemctl status rexfinhub-blue-ocean.timer --no-pager | head -12
systemctl list-timers rexfinhub-blue-ocean.timer --no-pager
```

## Verify

After deploy:

```bash
# Should show next firing rounded to Mon 07:10 America/New_York.
systemctl list-timers rexfinhub-blue-ocean.timer

# Live tail of report builds:
sudo journalctl -u rexfinhub-blue-ocean.service -f

# Manual one-shot (test):
cd /home/jarvis/rexfinhub
sudo -u jarvis /home/jarvis/venv/bin/python -m screener.li_engine.analysis.blue_ocean_report
ls -lh reports/blue_ocean_$(date +%F).html
```

## Send-pipeline integration

The `blue_ocean` key is registered in `scripts/send_all.py` BUNDLES /
REPORTS. To fire it on demand (after the timer has built the HTML):

```bash
cd /home/jarvis/rexfinhub
/home/jarvis/venv/bin/python -m scripts.send_all --bundle blue_ocean          # dry-run
/home/jarvis/venv/bin/python -m scripts.send_all --bundle blue_ocean --send   # real send
```

The `all` bundle also now includes blue_ocean.
