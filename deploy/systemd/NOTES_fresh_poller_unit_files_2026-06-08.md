# Fresh-poller unit files — promoted to static (2026-06-08)

The fresh-poller `.service` + `.timer` were previously generated dynamically
by `scripts/install_fresh_poller_timer.sh` (heredoc → `/etc/systemd/system/`).
That left the canonical definition outside the repo, so any drift between
the VPS units and the install script was invisible to code review.

This commit checks in the unit files as static artifacts here in
`deploy/systemd/`. `install_fresh_poller_timer.sh` continues to exist as a
one-time enabler — `daemon-reload` + `enable --now` — but it no longer
authors the unit content.

## Deploy on the VPS

```bash
# From a checkout of fix/pipeline-closure-2026-06-08 on the VPS:
sudo cp deploy/systemd/rexfinhub-fresh-poller.service /etc/systemd/system/
sudo cp deploy/systemd/rexfinhub-fresh-poller.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rexfinhub-fresh-poller.timer
systemctl status rexfinhub-fresh-poller.timer --no-pager | head -12
systemctl list-timers rexfinhub-fresh-poller.timer --no-pager
```

Or, equivalently, re-run the install script — it now does the same:
```bash
bash scripts/install_fresh_poller_timer.sh
```

## Verify

After deploy, the timer should show in `systemctl list-timers` with a next
firing rounded to the nearest :00/:15/:30/:45 ET, Mon-Fri 08:00-20:45.

Logs: `sudo journalctl -u rexfinhub-fresh-poller.service -f`
