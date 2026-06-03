#!/usr/bin/env python3
"""
Body scanner — reads email bodies from mbox for target domains only.
Looks for: unsubscribe links, pressure language, newsletter footers,
promotional patterns. No API calls. Outputs findings JSON.
"""

import json
import mailbox
import re
import sys
from collections import Counter, defaultdict
from email.utils import parseaddr
from pathlib import Path
from typing import Optional

CANONICAL_ROOT = Path(__file__).parent.parent
DATA_DIR = CANONICAL_ROOT / 'data'

UNSUBSCRIBE_RE = re.compile(r'unsubscribe|opt.?out|manage.*preferences|email.*preferences', re.I)
PRESSURE_RE = re.compile(r'\bfinal notice\b|\burgent\b|\bexpires? (today|tonight|soon|in \d+)\b|last chance|act now|limited time|don.t miss', re.I)
NEWSLETTER_RE = re.compile(r'newsletter|weekly digest|daily digest|this week in|you.re receiving this|sent to.*because you', re.I)
PROMO_RE = re.compile(r'\b\d+%\s*off\b|\bfree shipping\b|\bsale ends\b|\bcoupon\b|\bdiscount code\b', re.I)
TRANSACTIONAL_RE = re.compile(r'order (number|#|confirmation)|tracking number|shipment|your receipt|invoice #|payment (received|confirmed|processed)|your (reservation|booking|appointment)', re.I)


def get_body_text(msg) -> str:
    """Extract plain text body from email message."""
    body = ''
    try:
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == 'text/plain':
                    try:
                        body += part.get_payload(decode=True).decode('utf-8', errors='replace')
                    except Exception:
                        pass
                    if len(body) > 3000:
                        break
        else:
            if msg.get_content_type() == 'text/plain':
                try:
                    body = msg.get_payload(decode=True).decode('utf-8', errors='replace')
                except Exception:
                    pass
    except Exception:
        pass
    return body[:3000]


def extract_domain(addr: str) -> str:
    _, email = parseaddr(addr)
    email = email.lower().strip()
    return email.split('@', 1)[1] if '@' in email else 'unknown'


def scan(mbox_path: str, target_domains: set, max_per_domain: int = 50) -> dict:
    print(f"Opening {mbox_path} ...")
    mb = mailbox.mbox(mbox_path)

    domain_signals = defaultdict(lambda: {
        'count': 0, 'unsubscribe': 0, 'pressure': 0, 'newsletter': 0,
        'promo': 0, 'transactional': 0, 'sample_subjects': [], 'pressure_examples': [],
    })
    domain_scanned = Counter()
    total = 0

    print(f"Scanning bodies for {len(target_domains)} target domains...")
    for msg in mb:
        total += 1
        if total % 50000 == 0:
            print(f"  ...{total:,} scanned")

        from_raw = msg.get('From', '')
        domain = extract_domain(from_raw)

        if domain not in target_domains:
            continue
        if domain_scanned[domain] >= max_per_domain:
            continue

        domain_scanned[domain] += 1
        subject = str(msg.get('Subject', '')).replace('\n', ' ')[:100]
        body = get_body_text(msg)
        combined = subject + ' ' + body

        sig = domain_signals[domain]
        sig['count'] += 1

        if UNSUBSCRIBE_RE.search(combined):
            sig['unsubscribe'] += 1
        if PRESSURE_RE.search(combined):
            sig['pressure'] += 1
            if len(sig['pressure_examples']) < 3:
                m = PRESSURE_RE.search(combined)
                sig['pressure_examples'].append(m.group(0)[:50] if m else '')
        if NEWSLETTER_RE.search(combined):
            sig['newsletter'] += 1
        if PROMO_RE.search(combined):
            sig['promo'] += 1
        if TRANSACTIONAL_RE.search(combined):
            sig['transactional'] += 1
        if len(sig['sample_subjects']) < 3 and subject:
            sig['sample_subjects'].append(subject)

    mb.close()
    print(f"\nDone. {total:,} total scanned, {len(domain_signals)} target domains found.")
    return dict(domain_signals)


def classify_domain(sig: dict) -> str:
    """Heuristically classify a domain based on body signals."""
    n = sig['count']
    if n == 0:
        return 'unknown'
    unsub_rate = sig['unsubscribe'] / n
    promo_rate = sig['promo'] / n
    newsletter_rate = sig['newsletter'] / n
    transactional_rate = sig['transactional'] / n
    pressure_rate = sig['pressure'] / n

    if transactional_rate > 0.4:
        return 'transactional'
    if unsub_rate > 0.5 or newsletter_rate > 0.4:
        return 'newsletter'
    if promo_rate > 0.4:
        return 'marketing'
    if pressure_rate > 0.3:
        return 'pressure_marketing'
    return 'unclear'


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Scan email bodies for target domains')
    parser.add_argument('mbox', help='Path to .mbox file')
    parser.add_argument('--targets', default=str(DATA_DIR / 'body_scan_targets.json'),
                        help='JSON file with target domains list')
    parser.add_argument('--max-per-domain', type=int, default=50)
    args = parser.parse_args()

    with open(args.targets) as f:
        target_domains = set(json.load(f))

    # Remove gmail.com and other obviously-personal domains
    personal = {'gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'icloud.com', 'umail.iu.edu', 'indiana.edu', 'cornell.edu'}
    target_domains -= personal
    print(f"Scanning {len(target_domains)} domains (personal domains excluded)")

    signals = scan(args.mbox, target_domains, max_per_domain=args.max_per_domain)

    # Build report
    report = []
    for domain, sig in sorted(signals.items(), key=lambda x: -x[1]['count']):
        category = classify_domain(sig)
        n = sig['count']
        report.append({
            'domain': domain,
            'scanned': n,
            'category': category,
            'unsubscribe_rate': round(sig['unsubscribe'] / n, 2) if n else 0,
            'promo_rate': round(sig['promo'] / n, 2) if n else 0,
            'newsletter_rate': round(sig['newsletter'] / n, 2) if n else 0,
            'transactional_rate': round(sig['transactional'] / n, 2) if n else 0,
            'pressure_rate': round(sig['pressure'] / n, 2) if n else 0,
            'pressure_examples': sig['pressure_examples'],
            'sample_subjects': sig['sample_subjects'],
        })

    out = DATA_DIR / 'body_scan_results.json'
    with open(out, 'w') as f:
        json.dump(report, f, indent=2)
    print(f"\nResults saved to {out}")

    # Print summary table
    print(f"\n{'Domain':<40} {'Cat':<20} {'n':>4} {'unsub':>6} {'promo':>6} {'news':>6} {'xact':>6} {'press':>6}")
    print('-' * 100)
    for r in report:
        print(f"{r['domain']:<40} {r['category']:<20} {r['scanned']:>4} "
              f"{r['unsubscribe_rate']:>6.0%} {r['promo_rate']:>6.0%} "
              f"{r['newsletter_rate']:>6.0%} {r['transactional_rate']:>6.0%} "
              f"{r['pressure_rate']:>6.0%}")
        if r['sample_subjects']:
            print(f"  → {r['sample_subjects'][0][:80]}")


if __name__ == '__main__':
    main()
