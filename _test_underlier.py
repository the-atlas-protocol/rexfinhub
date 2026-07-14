"""Quick test of underlier extraction on JPMorgan filings."""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sec_client import SECClient
from parsers import get_parser, clean_html
from database import init_db, get_db
from models import Filing

init_db()
db = get_db()
client = SECClient(pause=0.25)

# Test on 5 random JPMorgan filings
filings = db.query(Filing).filter_by(issuer_id=1, extracted=True).limit(5).all()
total = len(filings)
got_names = 0
got_type = 0

for f in filings:
    html = client.fetch_text(f.primary_doc_url)
    parser = get_parser('1665650', html, 'JPMorgan')
    names = parser.extract_underlier_names()
    utype = parser.extract_underlier_type()
    if names:
        got_names += 1
    if utype:
        got_type += 1
    print(f'{f.filing_date} | underlier={names} | type={utype}')

print(f'\nResults: {got_names}/{total} got underlier names ({got_names*100//total}%)')
print(f'         {got_type}/{total} got underlier type ({got_type*100//total}%)')

db.close()
