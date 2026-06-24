#!/usr/bin/env python3
"""
Retailer shipping extractor — parses shipping/delivery notification emails
from Walmart, Amazon, Costco, FedEx, UPS and writes a rolling JSON for the
dashboard's Mail & Packages tab.

Output: data/{profile}/retailer-shipments.json
  {
    "shipments": [
      {
        "retailer":   "Walmart",
        "status":     "delivered" | "out_for_delivery" | "shipped" | "arriving_today",
        "item":       "No Boundaries Crochet...",
        "date":       "2026-06-22",
        "eta":        "5:36pm" | "Jun 23" | null,
        "tracking":   "1Z..." | null,
        "order_id":   "123-4567890" | null,
        "message_id": "19ee...",
      }, ...
    ]
  }

Usage:
    python scripts/extractors/retailer_shipping.py --profile personal
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

RETENTION_DAYS = 14

# (gmail_query, parser_key)
# Each entry maps a Gmail search to a parser that knows how to read that sender.
SOURCES = [
    # Walmart: Delivered / Arrived / Shipped / "should arrive by"
    (
        'from:help@walmart.com subject:(Delivered OR Arrived OR Shipped OR "should arrive")',
        'walmart',
    ),
    # Amazon
    (
        'from:shipment-tracking@amazon.com OR (from:amazon.com subject:(shipped OR delivered OR arriving))',
        'amazon',
    ),
    # Costco order emails
    (
        'from:orders.costco.com OR from:logistics.costco.com',
        'costco',
    ),
    # FedEx
    (
        'from:TrackingUpdates@fedex.com OR (from:fedex.com subject:(delivered OR "out for delivery" OR shipped))',
        'fedex',
    ),
    # UPS
    (
        'from:mcinfo@ups.com OR (from:ups.com subject:(delivered OR "out for delivery" OR shipped))',
        'ups',
    ),
]

STATUS_MAP = {
    # subject keyword → status token
    'delivered':           'delivered',
    'arrived':             'delivered',
    'was delivered':       'delivered',
    'has been delivered':  'delivered',
    'out for delivery':    'out_for_delivery',
    'should arrive':       'arriving_today',
    'arriving today':      'arriving_today',
    'shipped':             'shipped',
    'has shipped':         'shipped',
    'on its way':          'shipped',
}

def _status_from_subject(subject: str) -> str:
    sl = subject.lower()
    for kw, status in STATUS_MAP.items():
        if kw in sl:
            return status
    return 'shipped'


def _extract_tracking(text: str):
    # UPS: 1Z...
    m = re.search(r'\b(1Z[A-Z0-9]{16})\b', text)
    if m:
        return m.group(1)
    # FedEx: 12–22 digits
    m = re.search(r'\b(\d{12,22})\b', text)
    if m:
        return m.group(1)
    return None


def _parse_walmart(msg):
    hdrs = {h['name']: h['value'] for h in msg['payload']['headers']}
    subject = hdrs.get('Subject', '')
    snippet = msg.get('snippet', '')

    status = _status_from_subject(subject)

    # Item name: after the colon in "Delivered: Item name..." / "Arrived: Your Item..."
    item = None
    m = re.match(r'^(?:Delivered|Arrived|Shipped):\s*(?:Your\s+)?(.+?)(?:\s+\+\d+\s+item)?\.{0,3}$', subject, re.I)
    if m:
        item = m.group(1).strip()
    elif 'should arrive' in subject.lower():
        # "Your package should arrive by 5:36pm📦"
        item = re.sub(r'^Out for delivery:\s*', '', snippet, flags=re.I)
        item = re.sub(r'\s*‌.*', '', item).strip()  # strip zero-width spaces
        item = item[:80] if item else None

    # ETA: "by 5:36pm" in subject
    eta = None
    m = re.search(r'by\s+(\d{1,2}:\d{2}[ap]m)', subject, re.I)
    if m:
        eta = m.group(1)

    return {
        'retailer': 'Walmart',
        'status':   status,
        'item':     item or subject[:80],
        'eta':      eta,
        'tracking': None,
        'order_id': None,
    }


def _parse_amazon(msg):
    hdrs = {h['name']: h['value'] for h in msg['payload']['headers']}
    subject = hdrs.get('Subject', '')
    snippet = msg.get('snippet', '')

    status = _status_from_subject(subject)

    # Item: "Your Amazon order of "Item name" has shipped"
    item = None
    m = re.search(r'of\s+"(.+?)"', subject)
    if m:
        item = m.group(1)
    else:
        m = re.search(r'(?:shipped|delivered)[\s:]+(.+?)(?:\s+has|\s+was|$)', subject, re.I)
        if m:
            item = m.group(1).strip()

    # Order id
    order_id = None
    m = re.search(r'(\d{3}-\d{7}-\d{7})', subject + ' ' + snippet)
    if m:
        order_id = m.group(1)

    tracking = _extract_tracking(snippet)

    return {
        'retailer': 'Amazon',
        'status':   status,
        'item':     item or subject[:80],
        'eta':      None,
        'tracking': tracking,
        'order_id': order_id,
    }


def _parse_costco(msg):
    hdrs = {h['name']: h['value'] for h in msg['payload']['headers']}
    subject = hdrs.get('Subject', '')
    snippet = msg.get('snippet', '')

    # "Your Costco.com order 1291507869 was delivered!"
    # "Your Costco.com order 1291507869 has shipped!"
    sl = subject.lower()
    if not any(kw in sl for kw in ['shipped', 'delivered', 'arrived', 'on its way']):
        return None

    status = _status_from_subject(subject)

    order_id = None
    m = re.search(r'order\s+(\d{8,12})', subject, re.I)
    if m:
        order_id = m.group(1)

    # ETA from snippet: "Estimated Delivery: Tuesday, June 16"
    eta = None
    m = re.search(r'Estimated Delivery:\s*([A-Za-z]+,\s*[A-Za-z]+ \d+)', snippet)
    if m:
        eta = m.group(1)

    tracking = _extract_tracking(snippet)

    return {
        'retailer': 'Costco',
        'status':   status,
        'item':     f'Order {order_id}' if order_id else 'Costco order',
        'eta':      eta,
        'tracking': tracking,
        'order_id': order_id,
    }


def _parse_fedex(msg):
    hdrs = {h['name']: h['value'] for h in msg['payload']['headers']}
    subject = hdrs.get('Subject', '')
    snippet = msg.get('snippet', '')
    status = _status_from_subject(subject)
    tracking = _extract_tracking(subject + ' ' + snippet)
    eta = None
    m = re.search(r'Scheduled delivery:\s*(.+?)(?:\s*by|\.|$)', snippet, re.I)
    if m:
        eta = m.group(1).strip()
    return {
        'retailer': 'FedEx',
        'status':   status,
        'item':     subject[:80],
        'eta':      eta,
        'tracking': tracking,
        'order_id': None,
    }


def _parse_ups(msg):
    hdrs = {h['name']: h['value'] for h in msg['payload']['headers']}
    subject = hdrs.get('Subject', '')
    snippet = msg.get('snippet', '')
    status = _status_from_subject(subject)
    tracking = _extract_tracking(subject + ' ' + snippet)
    eta = None
    m = re.search(r'scheduled delivery[:\s]+(.+?)(?:\s+by|\.|$)', snippet, re.I)
    if m:
        eta = m.group(1).strip()
    return {
        'retailer': 'UPS',
        'status':   status,
        'item':     subject[:80],
        'eta':      eta,
        'tracking': tracking,
        'order_id': None,
    }


PARSERS = {
    'walmart': _parse_walmart,
    'amazon':  _parse_amazon,
    'costco':  _parse_costco,
    'fedex':   _parse_fedex,
    'ups':     _parse_ups,
}

STATUS_SORT = {'arriving_today': 0, 'out_for_delivery': 1, 'delivered': 2, 'shipped': 3}


def extract_retailer_shipments(profile, days_back=RETENTION_DAYS):
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from profile_loader import build_gmail_service
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

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
            print(f"ERROR: token not found/invalid at {token_path}")
            sys.exit(1)

    svc = build_gmail_service(creds)
    after = (datetime.now() - timedelta(days=days_back)).strftime('%Y/%m/%d')

    shipments = []
    seen_ids = set()

    for query, parser_key in SOURCES:
        full_q = f'({query}) after:{after}'
        try:
            result = svc.users().messages().list(
                userId='me', q=full_q, maxResults=30).execute()
        except Exception as e:
            print(f"  ⚠️  query failed ({parser_key}): {e}")
            continue

        msgs = result.get('messages', [])
        parser = PARSERS[parser_key]

        for mref in msgs:
            mid = mref['id']
            if mid in seen_ids:
                continue
            seen_ids.add(mid)

            try:
                msg = svc.users().messages().get(
                    userId='me', id=mid, format='metadata',
                    metadataHeaders=['Subject', 'From', 'Date']).execute()
            except Exception as e:
                print(f"  ⚠️  failed to fetch {mid}: {e}")
                continue

            parsed = parser(msg)
            if not parsed:
                continue

            internal_ts = int(msg.get('internalDate', 0)) / 1000
            date_str = datetime.fromtimestamp(internal_ts).strftime('%Y-%m-%d')
            parsed['date'] = date_str
            parsed['message_id'] = mid
            shipments.append(parsed)

    # Sort: urgency first (arriving_today → out_for_delivery → delivered → shipped),
    # then most-recent date first within each status tier.
    shipments.sort(key=lambda s: (
        STATUS_SORT.get(s['status'], 9),
        s['date'],
    ), reverse=False)
    # Within same status tier keep newest first
    shipments.sort(key=lambda s: (
        STATUS_SORT.get(s['status'], 9),
        [-ord(c) for c in s['date']],
    ))

    print(f"Found {len(shipments)} retailer shipment emails")
    return {'shipments': shipments}


def main():
    parser = argparse.ArgumentParser(description='Extract retailer shipping emails')
    add_profile_arg(parser)
    parser.add_argument('--days', type=int, default=RETENTION_DAYS,
                        help=f'Days to look back (default: {RETENTION_DAYS})')
    args = parser.parse_args()

    profile_name = args.profile or 'personal'
    profile = load_profile(profile_name)

    data = extract_retailer_shipments(profile, days_back=args.days)

    output_path = profile['data_path'] / 'retailer-shipments.json'
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2))
    print(f"Saved → {output_path}")


if __name__ == '__main__':
    main()
