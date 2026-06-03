#!/usr/bin/env python3
"""
Extract structured job lead data from recruiter/job emails using Claude Haiku.
Reads Jobs-labeled emails from classified batches, fetches bodies, extracts
role/company/seniority/remote/comp, persists to data/job-leads.json.

Run:  python scripts/extract_job_leads.py [--reprocess]
The daily_run.sh calls this automatically.
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
LEADS_FILE = DATA_DIR / 'job-leads.json'

EXTRACT_PROMPT = """\
You are extracting structured data from a recruiter or job opportunity email.

From: {from_email}
Subject: {subject}
Date: {date}
Body:
{body}

Extract the job opportunity details. Reply with exactly this JSON (no other text):
{{
  "role": "job title / role name",
  "company": "company name (not the recruiting agency)",
  "agency": "recruiting agency/firm if via recruiter, else null",
  "seniority": "intern|junior|mid|senior|staff|principal|director|vp|other",
  "remote": true|false|null,
  "location": "city/region or null if fully remote",
  "comp_range": "e.g. $200-250k or null if not mentioned",
  "tech_stack": ["list", "of", "technologies"] or [],
  "is_recruiter_outreach": true if someone is reaching out to you, false if you applied,
  "summary": "one sentence describing the opportunity",
  "status": "new"
}}

If this is NOT a job opportunity email, return {{"not_job": true}}.
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
        if plain and len(plain.strip()) > 80:
            return plain[:3000]
        if html:
            html = re.sub(r'<(style|script)[^>]*>.*?</\1>', ' ', html, flags=re.S | re.I)
            html = re.sub(r'<[^>]+>', ' ', html)
            html = re.sub(r'&nbsp;', ' ', html)
            html = re.sub(r'\s+', ' ', html).strip()
            return html[:3000]
        return msg.get('snippet', '')
    except Exception:
        return ''


def _ai_client():
    import anthropic
    settings_path = Path.home() / '.claude' / 'settings.json'
    if settings_path.exists():
        settings = json.loads(settings_path.read_text())
        base_url = settings.get('env', {}).get('ANTHROPIC_BASE_URL')
        helper = settings.get('apiKeyHelper')
        if helper and base_url:
            token = subprocess.check_output(helper, shell=True, text=True,
                                             stderr=subprocess.DEVNULL).strip()
            if token:
                return anthropic.Anthropic(api_key=token, base_url=base_url)
    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if api_key:
        return anthropic.Anthropic(api_key=api_key)
    raise RuntimeError('No Anthropic credentials found')


BODY_RECRUITER_RE = re.compile(
    r'(wanted to reach out|i\'m recruiting|i\'m a recruiter|talent acquisition'
    r'|i came across your (profile|background|experience)'
    r'|open to (exploring|new opportunities|hearing about)'
    r'|exciting (opportunity|role|position) at'
    r'|we\'re (hiring|looking for|building a team)'
    r'|would you be (open|interested|available)'
    r'|engineering (role|position|opportunity|team)'
    r'|staff (engineer|engineering|swe)'
    r'|senior (engineer|engineering|swe|software)'
    r'|principal (engineer|engineering)'
    r'|hirefly|greenhouse|lever\.co|workday)',
    re.I
)


def is_job_email(clf_entry: dict, from_email: str, subject: str) -> bool:
    """True if this email is likely a job/recruiter email."""
    if clf_entry.get('label') == 'Jobs':
        return True
    subj = subject.lower()
    job_keywords = ['opportunity', 'role', 'position', 'hiring', 'recruiter',
                    'job', 'engineer', 'swe', 'principal', 'staff', 'director']
    if any(k in subj for k in job_keywords):
        return True
    return False


def extract_lead(body: str, from_email: str, subject: str, date: str, client) -> dict:
    prompt = EXTRACT_PROMPT.format(
        from_email=from_email, subject=subject, date=date[:16], body=body[:3000])
    try:
        msg = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=400,
            messages=[{'role': 'user', 'content': prompt}],
        )
        raw = msg.content[0].text.strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.I)
        raw = re.sub(r'\s*```$', '', raw)
        result = json.loads(raw)
        if result.get('not_job'):
            return None
        return result
    except Exception:
        return None


def load_existing() -> dict:
    if LEADS_FILE.exists():
        try:
            return json.loads(LEADS_FILE.read_text())
        except (ValueError, OSError):
            pass
    return {}


def save_leads(leads: dict):
    DATA_DIR.mkdir(exist_ok=True)
    with open(LEADS_FILE, 'w', encoding='utf-8') as f:
        json.dump(leads, f, indent=2, ensure_ascii=False)
        f.write('\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--reprocess', action='store_true',
                        help='Re-extract even if already processed')
    args = parser.parse_args()

    print('=== Job Leads Extractor ===')

    existing = load_existing()
    client = _ai_client()
    service = _gmail_service()

    job_emails = []
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
                    if not is_job_email(c, row.get('from_email', ''), row.get('subject', '')):
                        continue
                    seen.add(mid)
                    if mid in existing and not args.reprocess:
                        continue
                    job_emails.append({
                        'message_id': mid,
                        'subject': row.get('subject', ''),
                        'date': row.get('date', ''),
                        'from_email': row.get('from_email', ''),
                        'from_name': row.get('from_name', ''),
                    })
        except (OSError, ValueError):
            continue

    print(f'Found {len(job_emails)} new job emails to process')

    new_count = 0
    for i, email in enumerate(job_emails):
        mid = email['message_id']
        print(f'  [{i+1}/{len(job_emails)}] {email["subject"][:60]}')
        body = _fetch_body(service, mid)
        if not body:
            existing[mid] = None
            continue
        # Quick check: does body look like recruiter outreach?
        if not BODY_RECRUITER_RE.search(body) and email.get('label') != 'Jobs':
            existing[mid] = None
            continue
        lead = extract_lead(body, email['from_email'], email['subject'], email['date'], client)
        if lead:
            lead['_message_id'] = mid
            lead['_date'] = email['date'][:16]
            lead['_from'] = email.get('from_name') or email['from_email']
            lead['_subject'] = email['subject']
            print(f'    → {lead.get("role","?")} @ {lead.get("company","?")}')
            new_count += 1
        existing[mid] = lead

    save_leads(existing)

    # Summary
    leads = [v for v in existing.values() if v and not v.get('not_job')]
    leads.sort(key=lambda x: x.get('_date', ''), reverse=True)
    print(f'\n✅ Processed {new_count} new emails')
    print(f'📋 Total job leads in pipeline: {len(leads)}')
    for l in leads[:10]:
        remote = '🌐' if l.get('remote') else '📍'
        comp = f" {l['comp_range']}" if l.get('comp_range') else ''
        print(f"  {l.get('_date','?')}  {remote} {l.get('role','?'):35} @ {l.get('company','?')}{comp}")


if __name__ == '__main__':
    main()
