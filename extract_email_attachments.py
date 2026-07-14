"""Extract attachments from a .eml file into an output directory."""
import email
import sys
from email import policy
from pathlib import Path

def extract(eml_path: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with eml_path.open('rb') as f:
        msg = email.message_from_binary_file(f, policy=policy.default)

    print(f"Subject: {msg['Subject']}")
    print(f"From:    {msg['From']}")
    print(f"Date:    {msg['Date']}")
    print()

    body = msg.get_body(preferencelist=('plain',))
    if body:
        print("--- BODY ---")
        print(body.get_content().strip())
        print("--- END BODY ---\n")

    print("--- ATTACHMENTS ---")
    for part in msg.iter_attachments():
        name = part.get_filename()
        if not name:
            continue
        data = part.get_payload(decode=True)
        out = out_dir / name
        out.write_bytes(data)
        print(f"  [{len(data):>10,} B]  {name}")

if __name__ == '__main__':
    eml = Path(sys.argv[1])
    out = Path(sys.argv[2])
    extract(eml, out)
