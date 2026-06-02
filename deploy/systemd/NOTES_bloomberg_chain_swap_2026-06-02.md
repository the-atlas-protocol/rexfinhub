# Bloomberg timer chain-service swap — VPS deploy steps

**Date:** 2026-06-02
**Audit ref:** O5 — `rexfinhub-bloomberg.timer` was activating the bare
`rexfinhub-bloomberg.service` (no `Unit=` directive → systemd default of
matching basename). The bare service lacks the `ExecStartPost` that runs
`apply_bloomberg_post_steps.py` (and thus `apply_issuer_brands.py`), so
every scheduled run left ~64.5% of `issuer_display` NULL.

## Change in this commit

`deploy/systemd/rexfinhub-bloomberg.timer` now has an explicit
`Unit=rexfinhub-bloomberg-chain.service` line in `[Timer]`. The bare
`rexfinhub-bloomberg.service` is kept on disk for ad-hoc manual runs.

## VPS deploy procedure (Ryu runs these)

Run from local repo root after pulling this branch on the VPS, or scp
the file directly:

```bash
# 1. Ship the modified timer to the VPS
scp deploy/systemd/rexfinhub-bloomberg.timer \
    jarvis@46.224.126.196:/tmp/rexfinhub-bloomberg.timer

# 2. SSH in and install it
ssh jarvis@46.224.126.196
sudo mv /tmp/rexfinhub-bloomberg.timer \
        /etc/systemd/system/rexfinhub-bloomberg.timer
sudo chown root:root /etc/systemd/system/rexfinhub-bloomberg.timer
sudo chmod 644 /etc/systemd/system/rexfinhub-bloomberg.timer

# 3. Reload systemd so it picks up the new Unit= line
sudo systemctl daemon-reload

# 4. Restart the timer (the timer unit itself, not the service)
sudo systemctl restart rexfinhub-bloomberg.timer

# 5. Verify chain.service is what fires next
systemctl list-timers rexfinhub-bloomberg.timer --all
# Look under "ACTIVATES" column — it must read:
#   rexfinhub-bloomberg-chain.service
# NOT:
#   rexfinhub-bloomberg.service

# 6. Belt-and-suspenders: dump the timer status
systemctl status rexfinhub-bloomberg.timer
# The "Triggers" line should reference rexfinhub-bloomberg-chain.service.
```

## Post-deploy verification (next scheduled run, 17:15 or 21:00 ET)

```bash
# Confirm chain.service ran, not the bare service
journalctl -u rexfinhub-bloomberg-chain.service --since today | tail -50

# Look for the apply_bloomberg_post_steps.py log lines (issuer brand apply)
journalctl -u rexfinhub-bloomberg-chain.service --since today | grep -i issuer

# Spot-check the DB: issuer_display NULL ratio should drop sharply
sqlite3 /home/jarvis/rexfinhub/data/rexfinhub.db \
  "SELECT COUNT(*) FILTER (WHERE issuer_display IS NULL) AS nulls,
          COUNT(*) AS total
   FROM products;"
```

## Rollback

If chain.service breaks for any reason, restore the prior behavior by
deleting the `Unit=` line from `/etc/systemd/system/rexfinhub-bloomberg.timer`
and running `sudo systemctl daemon-reload && sudo systemctl restart
rexfinhub-bloomberg.timer`. The bare service will then resume firing
(accepting the issuer_display NULL regression as a known cost).
