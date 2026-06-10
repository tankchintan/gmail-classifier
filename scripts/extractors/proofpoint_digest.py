#!/usr/bin/env python3
"""
Proofpoint quarantine digest extractor — parses quarantine summary emails
and writes a rolling JSON of quarantined senders/subjects.

Usage:
    python scripts/extractors/proofpoint_digest.py --profile work
"""

import sys
import json
import re
import base64
import argparse
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from profile_loader import add_profile_arg, load_profile

CANONICAL_ROOT = Path(__file__).parent.parent.parent

PROOFPOINT_SENDER = 'spamadmin@digest.salesforce.com'
RETENTION_DAYS = 7


def _gmail_service(profile):
    """Build Gmail service from profile's readonly token."""
    import pickle
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


def _get_html_body(msg):
    """Extract HTML body from message payload."""
    payload = msg.get('payload', {})

    def walk(p):
        if 'parts' in p:
            for part in p['parts']:
                result = walk(part)
                if result:
                    return result
        elif p.get('mimeType') == 'text/html':
            data = p.get('body', {}).get('data', '')
            if data:
                return base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
        return None

    return walk(payload)


def _parse_quarantined_emails(html):
    """Parse quarantined email entries from Proofpoint HTML digest."""
    entries = []
    trs = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL | re.IGNORECASE)

    for tr in trs:
        text = re.sub(r'<[^>]+>', ' ', tr)
        text = re.sub(r'\s+', ' ', text).strip()
        text = text.replace('&nbsp;', ' ').strip()

        if 'Release' in text and 'Block Sender' in text:
            # Format: "sender@email.com Subject line Release Release and Allow..."
            raw = text.split('Release')[0].strip()
            # Decode HTML entities
            raw = raw.replace('&#x2728;', '✨').replace('&#39;', "'")
            raw = re.sub(r'&#x([0-9a-fA-F]+);', lambda m: chr(int(m.group(1), 16)), raw)
            raw = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), raw)

            # First token is the sender email, rest is subject
            parts = raw.split(None, 1)
            if len(parts) == 2:
                entries.append({'sender': parts[0], 'subject': parts[1]})
            elif len(parts) == 1:
                entries.append({'sender': parts[0], 'subject': '(no subject)'})

    return entries


def extract_proofpoint_digests(profile, days_back=7):
    """Fetch recent Proofpoint digests and extract quarantine info."""
    service = _gmail_service(profile)

    after_date = (datetime.now() - timedelta(days=days_back)).strftime('%Y/%m/%d')
    query = f'from:{PROOFPOINT_SENDER} subject:"Proofpoint Quarantined" after:{after_date}'

    results = service.users().messages().list(userId='me', q=query, maxResults=50).execute()
    messages = results.get('messages', [])

    if not messages:
        print("No Proofpoint digests found in the last 7 days")
        return []

    print(f"Found {len(messages)} Proofpoint digests")

    digests = []
    for mref in messages:
        msg = service.users().messages().get(userId='me', id=mref['id'], format='full').execute()
        headers = msg.get('payload', {}).get('headers', [])
        subject = next((h['value'] for h in headers if h['name'] == 'Subject'), '')
        date_str = next((h['value'] for h in headers if h['name'] == 'Date'), '')

        # Extract count from subject
        count_match = re.search(r'(\d+)\s+New\s+Message', subject)
        count = int(count_match.group(1)) if count_match else 0

        html = _get_html_body(msg)
        entries = _parse_quarantined_emails(html) if html else []

        # Parse date
        internal_ts = int(msg.get('internalDate', 0)) / 1000
        iso_date = datetime.fromtimestamp(internal_ts).strftime('%Y-%m-%d %H:%M')

        digests.append({
            'date': iso_date,
            'count': count,
            'quarantined': entries,
        })

    digests.sort(key=lambda d: d['date'], reverse=True)
    return digests


def main():
    parser = argparse.ArgumentParser(description='Extract Proofpoint quarantine digests')
    add_profile_arg(parser)
    parser.add_argument('--days', type=int, default=7, help='Days to look back (default: 7)')
    args = parser.parse_args()

    profile_name = args.profile or 'work'
    profile = load_profile(profile_name)

    digests = extract_proofpoint_digests(profile, days_back=args.days)

    if not digests:
        print("No digests to save")
        return

    output_path = profile['data_path'] / 'proofpoint-quarantine.json'
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(digests, indent=2))

    total_blocked = sum(d['count'] for d in digests)
    print(f"\nSaved {len(digests)} digests ({total_blocked} total quarantined) → {output_path}")


if __name__ == '__main__':
    main()
