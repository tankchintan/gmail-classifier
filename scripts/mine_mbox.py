#!/usr/bin/env python3
"""
Offline mbox miner — no API calls, no bodies read.
Streams headers only (From, Subject, Date) from the mbox file.
Outputs a CSV of sender frequencies and a top-N report.
"""

import argparse
import csv
import mailbox
import sys
from collections import Counter, defaultdict
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Optional

CANONICAL_ROOT = Path(__file__).parent.parent
DATA_DIR = CANONICAL_ROOT / 'data'


def extract_domain(addr: str) -> str:
    _, email = parseaddr(addr)
    email = email.lower().strip()
    if '@' in email:
        return email.split('@', 1)[1]
    return email or 'unknown'


def extract_email(addr: str) -> str:
    _, email = parseaddr(addr)
    return email.lower().strip()


def parse_year(date_str: str) -> Optional[int]:
    try:
        return parsedate_to_datetime(date_str).year
    except Exception:
        return None


def mine(mbox_path: str, top_n: int = 50, min_count: int = 5) -> None:
    print(f"Opening {mbox_path} ...")
    mb = mailbox.mbox(mbox_path)

    total = 0
    domain_counts: Counter = Counter()
    sender_counts: Counter = Counter()
    domain_subjects: defaultdict = defaultdict(list)  # domain -> sample subjects
    domain_years: defaultdict = defaultdict(Counter)   # domain -> year -> count

    print("Streaming headers (this may take a few minutes for large files)...")
    for i, msg in enumerate(mb):
        total += 1
        if total % 50000 == 0:
            print(f"  ...{total:,} messages scanned")

        from_raw = msg.get('From', '')
        subject = msg.get('Subject', '')
        date_str = msg.get('Date', '')

        domain = extract_domain(from_raw)
        sender = extract_email(from_raw)

        domain_counts[domain] += 1
        sender_counts[sender] += 1

        # Keep up to 3 sample subjects per domain
        if len(domain_subjects[domain]) < 3 and subject:
            # Decode encoded subjects crudely
            subj = str(subject).replace('\n', ' ').replace('\r', '').strip()[:80]
            domain_subjects[domain].append(subj)

        year = parse_year(date_str)
        if year and 2000 <= year <= 2030:
            domain_years[domain][year] += 1

    mb.close()
    print(f"\nDone. {total:,} total messages scanned.")

    # --- Report ---
    top_domains = domain_counts.most_common(top_n)

    print(f"\n{'='*80}")
    print(f"TOP {top_n} SENDER DOMAINS (min {min_count} emails)")
    print(f"{'='*80}")
    print(f"{'#':<4} {'Count':>7}  {'Domain':<45}  {'Sample subject'}")
    print('-' * 120)

    for rank, (domain, count) in enumerate(top_domains, 1):
        if count < min_count:
            break
        sample = domain_subjects[domain][0] if domain_subjects[domain] else ''
        years = domain_years[domain]
        year_range = f"{min(years)}-{max(years)}" if years else "?"
        print(f"{rank:<4} {count:>7,}  {domain:<45}  [{year_range}] {sample[:60]}")

    # Save full domain CSV
    out_csv = DATA_DIR / 'mbox_domain_counts.csv'
    with open(out_csv, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['rank', 'domain', 'count', 'year_range', 'sample_subject_1', 'sample_subject_2', 'sample_subject_3'])
        for rank, (domain, count) in enumerate(domain_counts.most_common(), 1):
            years = domain_years[domain]
            year_range = f"{min(years)}-{max(years)}" if years else "?"
            samples = domain_subjects[domain] + ['', '', '']
            writer.writerow([rank, domain, count, year_range, samples[0], samples[1], samples[2]])

    print(f"\nFull domain CSV saved to: {out_csv}")

    # Save top sender CSV
    out_senders = DATA_DIR / 'mbox_sender_counts.csv'
    with open(out_senders, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['rank', 'sender_email', 'count'])
        for rank, (sender, count) in enumerate(sender_counts.most_common(200), 1):
            writer.writerow([rank, sender, count])

    print(f"Top 200 senders CSV saved to: {out_senders}")
    print(f"\nTotal messages: {total:,}")
    print(f"Unique domains: {len(domain_counts):,}")
    print(f"Unique senders: {len(sender_counts):,}")


def main():
    parser = argparse.ArgumentParser(description='Mine sender patterns from mbox (no API, headers only)')
    parser.add_argument('mbox', help='Path to .mbox file')
    parser.add_argument('--top', type=int, default=50, help='Show top N domains (default 50)')
    parser.add_argument('--min-count', type=int, default=5, help='Min emails to include domain (default 5)')
    args = parser.parse_args()

    mine(args.mbox, top_n=args.top, min_count=args.min_count)


if __name__ == '__main__':
    main()
