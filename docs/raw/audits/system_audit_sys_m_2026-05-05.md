# Sys-M: Network + DNS + VPS Hardening — 2026-05-05

## TL;DR
- SPF (rexfin.com): ✅ Strict `-all`, M365 + 5 includes
- SPF (rexfinhub.com): ⚠️ Weak `~all` registrar forwarder only
- DKIM (rexfin.com): ✅ M365 selector1+selector2 rotation
- DMARC (rexfin.com): ✅ `p=quarantine; pct=100`
- **DMARC (rexfinhub.com): ❌ MISSING** — anyone can spoof
- **VPS port 8000: ❌ PUBLIC, Shodan-indexed** — uvicorn bypasses nginx
- **SMTP fallback: ❌ Breaks DMARC silently** — Gmail authenticates `gmail.com` not `rexfin.com`
- VPS firewall: ❓ Inferred permissive (8000 reachable)
- TLS cert: ✅ Let's Encrypt, auto-renews via Render, expires 2026-07-18
- Email reputation: ✅ All IPs clean on Spamhaus

## Top 3 Risks

### 1. HIGH — VPS port 8000 publicly exposed
- uvicorn bound to `0.0.0.0:8000` instead of `127.0.0.1`
- Shodan indexed 70KB of A.R.C. application on 2026-05-02
- nginx auth/rate-limiting fully bypassable by hitting `:8000` directly
- No TLS on port 8000 → credentials in plaintext if accessed
- **Fix**: `--host 127.0.0.1` on uvicorn + `ufw deny 8000`

### 2. HIGH — Gmail SMTP fallback breaks DMARC silently
- App has `SMTP_USER=ryuogawaelasmar@gmail.com` configured as Graph API fallback
- If Graph fails and Gmail fires: From = `relasmar@rexfin.com` but auth = `gmail.com`
- SPF + DKIM alignment FAIL → DMARC quarantines or drops at recipient
- Pipeline logs "sent" anyway — silent delivery failure
- **Fix**: disable Gmail fallback OR replace with Mimecast/Mailgun (already in rexfin.com SPF)

### 3. MEDIUM — rexfinhub.com has no DMARC
- `_dmarc.rexfinhub.com` → NXDOMAIN
- Anyone can spoof @rexfinhub.com → clean inbox delivery
- Subscriber-facing email phishing risk
- **Fix**: 5-min DNS change → `v=DMARC1; p=quarantine; pct=100; rua=mailto:info@rexfin.com`

## Sender Authentication Status (rexfin.com)

| Check | Status |
|---|---|
| SPF | ✅ `v=spf1 include:us._netblocks.mimecast.com include:spf.protection.outlook.com include:_spf.salesforce.com include:spf.maropost.com include:mailgun.org -all` |
| DKIM selector1 | ✅ RSA-2048 (M365) |
| DKIM selector2 | ✅ M365 rotation pattern |
| DMARC | ✅ `p=quarantine; pct=100; rua=mailto:info@rexfin.com` |
| MX | ✅ Mimecast inbound |

## VPS Network Exposure (Hetzner Dubai, 46.224.126.196)

| Port | Service | Public | Risk |
|---|---|---|---|
| 22 | OpenSSH 9.6p1 | ✅ Expected | Acceptable if key-only |
| 80 | nginx | ✅ Expected | Reverse proxy |
| **8000** | **uvicorn (A.R.C.)** | **❌ SHOULD NOT BE** | **HIGH** |

Other ports (postgres/mysql/redis/jupyter): ✅ Not exposed.

## TLS / HTTPS — rexfinhub.com

| Check | Status |
|---|---|
| Cert | Let's Encrypt (WE1/WR1) |
| Expiry | 2026-07-18 (auto-renew via Render) |
| Chain | ✅ Trusted across all platforms |
| TLS 1.2/1.3 | ✅ Default |
| TLS 1.0/1.1 | ❌ Disabled |
| HSTS header | ❓ Unconfirmed (app must set) |
| CAA records | ❌ Missing — any CA can issue |

## Email Reputation

| IP | Spamhaus ZEN |
|---|---|
| 46.224.126.196 (VPS) | ✅ Not listed |
| 15.197.225.128 (rexfin.com) | ✅ Not listed |
| 216.24.57.1 (Render) | ✅ Not listed |

## Cloudflare / WAF / Edge Protection

| Domain | Edge layer |
|---|---|
| rexfinhub.com | ❌ None — direct Render IP |
| rexfin.com | ✅ Mimecast inbound + M365 outbound |
| VPS | ❌ None |

rexfinhub.com has no DDoS mitigation, no bot protection, no edge rate limiting. Combined with no app-level rate limit on `/login` (Sys-H), brute-force is unrestricted.

## Subdomain Sprawl

| Domain | Subdomains |
|---|---|
| rexfin.com | www, autodiscover only — clean |
| rexfinhub.com | www only — clean |

No dangling CNAMEs. No wildcard. Minimal surface.

## Recommendations

### P0 — Before next deploy
1. Bind uvicorn to localhost: `--host 127.0.0.1` + `ufw deny 8000`
2. Disable Gmail SMTP fallback OR replace with Mimecast/Mailgun

### P1 — Within 48h
3. Add DMARC for rexfinhub.com: `_dmarc TXT "v=DMARC1; p=quarantine; pct=100; rua=mailto:info@rexfin.com"`
4. UptimeRobot free tier → https://rexfinhub.com → alert to relasmar@
5. SSH to VPS → `sudo ufw status verbose`. If inactive: `ufw allow 22,80,443; ufw deny 8000; ufw enable`

### P2 — Within 1 week
6. Upgrade rexfin.com DMARC: `p=quarantine` → `p=reject` (after 1 week of rua review)
7. Cloudflare free tier in front of rexfinhub.com (orange-cloud)
8. CAA records for both domains
9. Trim SPF includes if Salesforce/Maropost/Mailgun unused
10. HSTS header in FastAPI: `Strict-Transport-Security: max-age=31536000; includeSubDomains`

### P3 — Ongoing
11. Custom PTR for VPS in Hetzner Robot panel
12. Monthly SSH audit: `last -20`, `/etc/passwd`, authorized_keys
13. Verify fail2ban: `sudo fail2ban-client status sshd`. Install if absent.
14. Monitor DMARC aggregate reports at info@rexfin.com

---

*Audit by Sys-M bot, 2026-05-05. Read-only. DNS via Google Public DNS. VPS exposure via Shodan passive scan (2026-05-02).*
