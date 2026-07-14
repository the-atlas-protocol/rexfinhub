"""Replace methodology bullets with the approved 2-paragraph format."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
p = r'C:\Projects\rex-asia\report_v15.html'
with open(p, 'r', encoding='utf-8') as f:
    c = f.read()

# Find the start of the Asia AUM ul block and end
start_marker = '<ul style="margin: 0 0 12px 14px; padding: 0; list-style-type: disc;">\n      <li>Collected monthly from brokerage firms'
end_marker = '<li><strong>Closed positions</strong>: when a vendor has fully liquidated its REX holdings, no positions are reported</li>\n    </ul>'

start_idx = c.find(start_marker)
end_idx = c.find(end_marker)
if start_idx < 0 or end_idx < 0:
    print(f"start={start_idx}, end={end_idx}")
    sys.exit(1)
end_idx += len(end_marker)

new_block = (
    '<p style="margin: 0 0 8px 0;">Korean retail holdings are sourced directly from the Korea Securities Depository (KSD), '
    'while Korean institutional holdings come from publicly available sources. Both are updated monthly. '
    'The majority of Japanese brokerage data is received monthly from individual brokers. '
    'Other Asian brokerage, intermediary, and market data are received monthly, quarterly, or on an ad-hoc basis. '
    'Asia-related investor positions reported in 13F filings are reviewed and updated on a quarterly basis.</p>\n'
    '    <p style="margin: 0 0 12px 0;">In the event of delayed data, share counts are held constant and AUM is '
    'marked-to-market based on the latest month-end NAV. Share counts are updated and back-adjusted once the new data is received.</p>'
)

c2 = c[:start_idx] + new_block + c[end_idx:]
with open(p, 'w', encoding='utf-8') as f:
    f.write(c2)
print(f"Patched. Removed {end_idx - start_idx} chars, inserted {len(new_block)} chars.")
