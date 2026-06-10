#!/usr/bin/env python3
"""
USPS delivery extractor — parses Informed Delivery daily digests and
package tracking emails into a rolling JSON for the dashboard.

Two sources:
  - USPSInformeddelivery@email.informeddelivery.usps.com  (daily digest)
  - auto-reply@usps.com  (individual package out-for-delivery / expected delivery)

Usage:
    python scripts/extractors/usps_digest.py
    python scripts/extractors/usps_digest.py --profile personal
"""

import sys
import json
import re
import base64
import pickle
import argparse
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from profile_loader import add_profile_arg, load_profile

RETENTION_DAYS = 7

INFORMED_DELIVERY_SENDER = 'usps.com'
PACKAGE_TRACKING_SENDER = 'auto-reply@usps.com'


def _gmail_service(profile):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    token_path = profile['token_readonly']
    creds = None
    if token_path.exists():
        if str(token_path).endswith('.json'):
            creds = Credentials.from_authorized_user_file(str(token_path))
        else:
            with open(token_path, 'rb') as f:
                creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            if str(token_path).endswith('.json'):
                token_path.write_text(creds.to_json())
            else:
                with open(token_path, 'wb') as f:
                    pickle.dump(creds, f)
        else:
            print(f"ERROR: Token not found or invalid at {token_path}")
            sys.exit(1)

    return build('gmail', 'v1', credentials=creds)


def _get_html(payload):
    if 'parts' in payload:
        for part in payload['parts']:
            result = _get_html(part)
            if result:
                return result
    elif payload.get('mimeType') == 'text/html':
        data = payload.get('body', {}).get('data', '')
        if data:
            return base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
    return None


def _table_rows(html):
    """Extract non-trivial text rows from HTML table."""
    clean = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    trs = re.findall(r'<tr[^>]*>(.*?)</tr>', clean, re.DOTALL | re.IGNORECASE)
    rows = []
    for tr in trs:
        text = re.sub(r'<[^>]+>', ' ', tr)
        text = re.sub(r'\s+', ' ', text).strip().replace('&nbsp;', ' ').strip()
        if text and len(text) > 5 and not text.startswith('.'):
            rows.append(text)
    return rows


def _parse_informed_digest(msg):
    """Parse a USPS Informed Delivery daily digest email."""
    headers = msg['payload']['headers']
    subject = next((h['value'] for h in headers if h['name'] == 'Subject'), '')
    internal_ts = int(msg.get('internalDate', 0)) / 1000
    date_str = datetime.fromtimestamp(internal_ts).strftime('%Y-%m-%d')

    html = _get_html(msg['payload'])
    if not html:
        return None

    rows = _table_rows(html)

    mail_count = 0
    package_count = 0
    senders = []

    for row in rows:
        # "You have 4 mailpiece(s) and 0 inbound package(s) arriving soon."
        m = re.search(r'You have (\d+) mailpiece.*?and (\d+) .*?package', row, re.I)
        if m:
            mail_count = int(m.group(1))
            package_count = int(m.group(2))
            continue
        # "FROM: COSTCO"
        m = re.match(r'^FROM:\s*(.+)$', row, re.I)
        if m:
            senders.append(m.group(1).strip())

    return {
        'type': 'informed_delivery',
        'date': date_str,
        'subject': subject,
        'mail_count': mail_count,
        'package_count': package_count,
        'mail_senders': senders,
    }


def _parse_package_tracking(msg):
    """Parse a USPS package tracking / out-for-delivery email."""
    headers = msg['payload']['headers']
    subject = next((h['value'] for h in headers if h['name'] == 'Subject'), '')
    snippet = msg.get('snippet', '')
    internal_ts = int(msg.get('internalDate', 0)) / 1000
    date_str = datetime.fromtimestamp(internal_ts).strftime('%Y-%m-%d')

    # Extract tracking number from subject (last long number)
    tracking_match = re.search(r'(\d{10,})', subject)
    tracking = tracking_match.group(1) if tracking_match else None

    # Extract delivery date from subject
    date_match = re.search(r'on (.+?) arriving by', subject)
    delivery_date = date_match.group(1).strip() if date_match else None

    # Extract delivery time
    time_match = re.search(r'arriving by (.+?)$', subject)
    delivery_time = time_match.group(1).strip() if time_match else None

    # Extract status from snippet
    status = snippet[:200] if snippet else ''

    return {
        'type': 'package_tracking',
        'date': date_str,
        'tracking_number': tracking,
        'expected_delivery': delivery_date,
        'delivery_by': delivery_time,
        'status_snippet': status,
    }


def extract_usps(profile, days_back=7):
    service = _gmail_service(profile)
    after_date = (datetime.now() - timedelta(days=days_back)).strftime('%Y/%m/%d')

    results_informed = service.users().messages().list(
        userId='me',
        q=f'from:informeddelivery.usps.com subject:"Daily Digest" after:{after_date}',
        maxResults=14
    ).execute()

    results_packages = service.users().messages().list(
        userId='me',
        q=f'from:auto-reply@usps.com subject:"Expected Delivery" after:{after_date}',
        maxResults=20
    ).execute()

    digests = []
    packages = []

    for mref in results_informed.get('messages', []):
        msg = service.users().messages().get(userId='me', id=mref['id'], format='full').execute()
        parsed = _parse_informed_digest(msg)
        if parsed:
            digests.append(parsed)

    seen_tracking = set()
    for mref in results_packages.get('messages', []):
        msg = service.users().messages().get(userId='me', id=mref['id'], format='full').execute()
        parsed = _parse_package_tracking(msg)
        if parsed and parsed['tracking_number']:
            if parsed['tracking_number'] not in seen_tracking:
                seen_tracking.add(parsed['tracking_number'])
                packages.append(parsed)

    digests.sort(key=lambda d: d['date'], reverse=True)
    packages.sort(key=lambda p: p['date'], reverse=True)

    print(f"Found {len(digests)} Informed Delivery digests, {len(packages)} package tracking emails")
    return {'digests': digests, 'packages': packages}


def main():
    parser = argparse.ArgumentParser(description='Extract USPS delivery info')
    add_profile_arg(parser)
    parser.add_argument('--days', type=int, default=7, help='Days to look back (default: 7)')
    args = parser.parse_args()

    profile_name = args.profile or 'personal'
    profile = load_profile(profile_name)

    data = extract_usps(profile, days_back=args.days)

    output_path = profile['data_path'] / 'usps-deliveries.json'
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2))
    print(f"Saved → {output_path}")


if __name__ == '__main__':
    main()
