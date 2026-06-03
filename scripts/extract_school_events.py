#!/usr/bin/env python3
"""
Extract upcoming events/dates from school newsletter emails using Claude Haiku.
Reads School-labeled emails from classified batches, fetches bodies, sends to Haiku,
persists results to data/school-events.json.

Run:  python scripts/extract_school_events.py [--days-ahead 60]
The daily_run.sh calls this automatically after classify+execute.
"""

import argparse
import csv
import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

CANONICAL_ROOT = Path(__file__).parent.parent
DATA_DIR = CANONICAL_ROOT / 'data'
LOGS_DIR = CANONICAL_ROOT / 'logs'
EVENTS_FILE = DATA_DIR / 'school-events.json'

EXTRACT_PROMPT = """\
You are extracting structured data from a school newsletter email.

Email subject: {subject}
Email date: {date}
Body:
{body}

Extract all upcoming events, dates, deadlines, and action items mentioned.
Focus on: school events, minimum days, no-school days, deadlines, signup deadlines, fundraisers, field trips, performances.
Ignore past events (before the email date).

Reply with a JSON array. Each item:
{{
  "date": "YYYY-MM-DD or null if no specific date",
  "event": "short description (max 80 chars)",
  "urgent": true/false (true if deadline/action needed within 7 days of email date),
  "category": "event|deadline|no-school|minimum-day|signup|other"
}}

If nothing notable, return [].
Return ONLY the JSON array, no other text.
"""


def _gmail_service():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'fetch_unread', str(CANONICAL_ROOT / 'scripts' / 'fetch_unread.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.authenticate()


def _fetch_body(service, message_id: str) -> str:
    import base64
    try:
        msg = service.users().messages().get(userId='me', id=message_id, format='full').execute()
        payload = msg.get('payload', {})

        def walk(p):
            plain, html = '', ''
            for part in p.get('parts', []):
                ct = part.get('mimeType', '')
                if ct == 'text/plain':
                    data = part.get('body', {}).get('data', '')
                    if data:
                        plain += base64.urlsafe_b64decode(data + '==').decode('utf-8', errors='replace')
                elif ct == 'text/html':
                    data = part.get('body', {}).get('data', '')
                    if data:
                        html += base64.urlsafe_b64decode(data + '==').decode('utf-8', errors='replace')
                elif ct.startswith('multipart/'):
                    p2, h2 = walk(part)
                    plain += p2; html += h2
            if not p.get('parts'):
                ct = p.get('mimeType', '')
                data = p.get('body', {}).get('data', '')
                if data:
                    text = base64.urlsafe_b64decode(data + '==').decode('utf-8', errors='replace')
                    if 'plain' in ct: plain = text
                    elif 'html' in ct: html = text
            return plain, html

        plain, html = walk(payload)
        if plain and len(plain.strip()) > 100:
            return plain[:3000]
        if html:
            # strip tags
            html = re.sub(r'<(style|script)[^>]*>.*?</\1>', ' ', html, flags=re.S | re.I)
            html = re.sub(r'<[^>]+>', ' ', html)
            html = re.sub(r'&nbsp;', ' ', html)
            html = re.sub(r'\s+', ' ', html).strip()
            return html[:3000]
        return msg.get('snippet', '')
    except Exception:
        return ''


def _ai_client():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'ai_client', str(CANONICAL_ROOT / 'scripts' / 'ai_client.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.make_client()


def extract_events(body: str, subject: str, email_date: str, client) -> list:
    prompt = EXTRACT_PROMPT.format(
        subject=subject, date=email_date[:16], body=body[:3000])
    try:
        msg = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=512,
            messages=[{'role': 'user', 'content': prompt}],
        )
        raw = msg.content[0].text.strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.I)
        raw = re.sub(r'\s*```$', '', raw)
        return json.loads(raw)
    except Exception:
        return []


def load_existing_events() -> dict:
    """Returns {message_id: [events]} from existing file."""
    if EVENTS_FILE.exists():
        try:
            return json.loads(EVENTS_FILE.read_text())
        except (ValueError, OSError):
            pass
    return {}


def save_events(events_map: dict):
    DATA_DIR.mkdir(exist_ok=True)
    with open(EVENTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(events_map, f, indent=2, ensure_ascii=False)
        f.write('\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days-ahead', type=int, default=60,
                        help='Only surface events within this many days from today')
    parser.add_argument('--reprocess', action='store_true',
                        help='Re-extract even if already processed')
    args = parser.parse_args()

    print('=== School Events Extractor ===')

    existing = load_existing_events()
    client = _ai_client()
    service = _gmail_service()

    # Collect all School-labeled emails not yet processed
    school_emails = []
    seen = set()
    for clf_path in sorted(glob.glob(str(DATA_DIR / 'batch-*-classified.json'))):
        if 'temp' in clf_path or 'janfeb' in clf_path:
            continue
        batch = Path(clf_path).name.replace('batch-', '').replace('-classified.json', '')
        csv_path = DATA_DIR / f'batch-{batch}.csv'
        if not csv_path.exists():
            continue
        try:
            clf_map = {e['message_id']: e for e in json.load(open(clf_path, encoding='utf-8'))}
            with open(csv_path, newline='', encoding='utf-8') as f:
                for row in csv.DictReader(f):
                    mid = row['message_id']
                    if mid in seen:
                        continue
                    c = clf_map.get(mid, {})
                    # Pick School-labeled emails from ParentSquare / ClassDojo / dublinusd
                    is_school = (
                        c.get('label') == 'School' or
                        any(d in row.get('from_email', '').lower()
                            for d in ['parentsquare', 'classdojo', 'dublinusd', 'dpie.org'])
                    )
                    if not is_school:
                        continue
                    seen.add(mid)
                    if mid in existing and not args.reprocess:
                        continue
                    # Only newsletters/monthly updates worth parsing — skip short notices
                    subj = row.get('subject', '').lower()
                    is_newsletter = any(kw in subj for kw in [
                        'newsletter', 'happenings', 'upcoming', 'reminder', 'calendar',
                        'events', 'dates', 'important', 'minimum day', 'no school',
                        'schedule', 'information', 'announcement', 'update'
                    ])
                    if not is_newsletter:
                        continue
                    school_emails.append({
                        'message_id': mid,
                        'subject': row.get('subject', ''),
                        'date': row.get('date', ''),
                        'from_email': row.get('from_email', ''),
                    })
        except (OSError, ValueError):
            continue

    print(f'Found {len(school_emails)} new school newsletters to process')

    new_count = 0
    total_events = 0
    for i, email in enumerate(school_emails):
        mid = email['message_id']
        print(f'  [{i+1}/{len(school_emails)}] {email["subject"][:60]}')
        body = _fetch_body(service, mid)
        if not body:
            existing[mid] = []
            continue
        events = extract_events(body, email['subject'], email['date'], client)
        existing[mid] = [{
            **e,
            '_subject': email['subject'],
            '_email_date': email['date'][:16],
            '_from': email['from_email'],
            '_message_id': mid,
        } for e in events if isinstance(e, dict)]
        new_count += 1
        total_events += len(events)
        print(f'    → {len(events)} events extracted')

    save_events(existing)

    # Print upcoming events summary
    today = datetime.now(timezone.utc).date()
    cutoff = today + timedelta(days=args.days_ahead)
    upcoming = []
    for mid, events in existing.items():
        for e in events:
            date_str = e.get('date')
            if not date_str:
                continue
            try:
                event_date = datetime.strptime(date_str[:10], '%Y-%m-%d').date()
                if today <= event_date <= cutoff:
                    upcoming.append({**e, '_date_obj': event_date})
            except ValueError:
                continue

    upcoming.sort(key=lambda x: x['_date_obj'])
    print(f'\n✅ Processed {new_count} newsletters, {total_events} events extracted')
    print(f'📅 Upcoming in next {args.days_ahead} days: {len(upcoming)}')
    for e in upcoming:
        urgent = ' ⚠️' if e.get('urgent') else ''
        print(f"  {e['_date_obj']}  {e.get('event','')[:70]}{urgent}")


if __name__ == '__main__':
    main()
