#!/usr/bin/env python3
"""
Convert mbox to the same CSV format as fetch_unread.py output.
Headers only — no bodies. No API calls.
"""

import argparse
import csv
import mailbox
import sys
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Optional

CANONICAL_ROOT = Path(__file__).parent.parent
DATA_DIR = CANONICAL_ROOT / 'data'

FIELDNAMES = ['message_id', 'thread_id', 'date', 'from_name', 'from_email', 'subject', 'is_reply']


def get_header(msg, name: str) -> str:
    val = msg.get(name, '')
    return str(val).replace('\n', ' ').replace('\r', '').strip()


def parse_date_iso(date_str: str) -> str:
    try:
        return parsedate_to_datetime(date_str).isoformat()
    except Exception:
        return date_str


def convert(mbox_path: str, out_csv: str, limit: Optional[int] = None) -> None:
    print(f"Opening {mbox_path} ...")
    mb = mailbox.mbox(mbox_path)

    total = 0
    written = 0

    with open(out_csv, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()

        for msg in mb:
            total += 1
            if total % 50000 == 0:
                print(f"  ...{total:,} scanned, {written:,} written")

            if limit and written >= limit:
                break

            from_raw = get_header(msg, 'From')
            from_name, from_email = parseaddr(from_raw)
            from_email = from_email.lower().strip()

            subject = get_header(msg, 'Subject')
            date_str = get_header(msg, 'Date')
            msg_id = get_header(msg, 'Message-ID').strip('<>')
            in_reply_to = get_header(msg, 'In-Reply-To')
            references = get_header(msg, 'References')
            is_reply = bool(in_reply_to or references)

            writer.writerow({
                'message_id': msg_id or f'mbox-{total}',
                'thread_id': msg_id or f'mbox-{total}',
                'date': date_str,
                'from_name': from_name,
                'from_email': from_email,
                'subject': subject,
                'is_reply': is_reply,
            })
            written += 1

    mb.close()
    print(f"\nDone. {total:,} scanned, {written:,} written to {out_csv}")


def main():
    parser = argparse.ArgumentParser(description='Convert mbox to CSV (headers only, no API)')
    parser.add_argument('mbox', help='Path to .mbox file')
    parser.add_argument('--output', help='Output CSV path (default: data/mbox_all.csv)')
    parser.add_argument('--limit', type=int, help='Max emails to extract (default: all)')
    args = parser.parse_args()

    out = args.output or str(DATA_DIR / 'mbox_all.csv')
    convert(args.mbox, out, limit=args.limit)


if __name__ == '__main__':
    main()
