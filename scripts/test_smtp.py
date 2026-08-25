#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Send one test email over the SMTP transport and explain any failure.

Usage:  python scripts/test_smtp.py [recipient]

Exists because the interesting failure here is not in our code — it is whether
the mailbox is allowed to authenticate at all. Microsoft 365 disables SMTP AUTH
per-tenant and per-mailbox by default, and the error it returns says so quite
precisely, so print it verbatim rather than summarising it away.
"""
import base64, logging, os, sys

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config/.env'))

from webapp.services.smtp_email import is_configured, send_email  # noqa: E402

to = sys.argv[1] if len(sys.argv) > 1 else 'ryuogawaelasmar@gmail.com'
print('host   :', os.environ.get('SMTP_HOST'))
print('user   :', os.environ.get('SMTP_USER'))
print('from   :', os.environ.get('SMTP_FROM'))
print('to     :', to)
if not is_configured():
    sys.exit(1)

dot = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z/C/HgAGgwJ/lK3Q6wAAAABJRU5ErkJggg==')
html = ("<html><body style='font-family:Segoe UI,Arial'>"
        "<h2>SMTP transport test</h2>"
        "<p>HTML body, inline CID image and a file attachment — the three things "
        "the daily reports need. If all three arrive, the reports will too.</p>"
        "<p>Inline image: <img src='cid:testimg' width=20 height=20></p>"
        "<p>Sent as %s via %s.</p></body></html>" % (os.environ.get('SMTP_FROM'), os.environ.get('SMTP_HOST')))

ok = send_email(subject='[TEST] rexfinhub SMTP transport', html_body=html, recipients=[to],
                images=[('testimg', dot, 'dot.png')],
                attachments=[('note.txt', b'SMTP transport test attachment.', 'text/plain')])
print('RESULT:', 'SENT' if ok else 'FAILED')
sys.exit(0 if ok else 2)
