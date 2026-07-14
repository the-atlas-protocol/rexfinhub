"""Patch the stubborn Feb '26 AUM literal to be REPORT_DATA-driven."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

p = r'C:\Projects\rex-asia\report_v15.html'
with open(p, 'r', encoding='utf-8') as f:
    content = f.read()

# Two possible forms: literal ’ (U+2019) or escape ’. Handle both.
OLD_A = 'Feb ’26 AUM ($M)'
OLD_B = 'Feb \\u201926 AUM ($M)'  # if file stores the literal escape
NEW   = "' + ((REPORT_DATA && REPORT_DATA.narrative && REPORT_DATA.narrative.month_short) ? REPORT_DATA.narrative.month_short : 'Month') + ' AUM ($M)"

found = False
if OLD_A in content:
    print(f"Found literal ’ form")
    content = content.replace(
        "'<th style=\"text-align:right;padding:4px 6px;font-size:8px;font-weight:700;text-transform:uppercase;letter-spacing:0.6px;color:#5c5c5c;\">Feb ’26 AUM ($M)</th>'",
        "'<th style=\"text-align:right;padding:4px 6px;font-size:8px;font-weight:700;text-transform:uppercase;letter-spacing:0.6px;color:#5c5c5c;\">" + NEW + "</th>'"
    )
    found = True
elif OLD_B in content:
    print(f"Found \\u2019 escaped form")
    content = content.replace(
        "'<th style=\"text-align:right;padding:4px 6px;font-size:8px;font-weight:700;text-transform:uppercase;letter-spacing:0.6px;color:#5c5c5c;\">Feb \\u201926 AUM ($M)</th>'",
        "'<th style=\"text-align:right;padding:4px 6px;font-size:8px;font-weight:700;text-transform:uppercase;letter-spacing:0.6px;color:#5c5c5c;\">" + NEW + "</th>'"
    )
    found = True

if found:
    with open(p, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Patched.")
else:
    # Try the second position Feb '26 AUM header
    print("Not found via exact match — showing line context:")
    for i, line in enumerate(content.split('\n')):
        if 'Feb' in line and '26 AUM' in line:
            print(f"  Line {i+1}: {line[:200]!r}")
