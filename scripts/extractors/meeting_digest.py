#!/usr/bin/env python3
"""
Meeting digest extractor — pulls key points from meeting notes emails
and builds a daily/weekly digest JSON.

Looks for emails matching configurable sender patterns (e.g. Gemini notes,
Otter.ai, Fireflies.ai) and extracts summaries from the body.

Usage:
    python scripts/extractors/meeting_digest.py --profile work
    python scripts/extractors/meeting_digest.py  # uses personal profile
"""

import sys
import json
import re
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from profile_loader import add_profile_arg, load_profile

CANONICAL_ROOT = Path(__file__).parent.parent.parent


# Default meeting notes senders — override in profile's extractor config
DEFAULT_SENDERS = [
    'gemini-notes@google.com',
    'notifications@otter.ai',
    'digest@fireflies.ai',
]

# Regex to extract key points from typical meeting notes format
KEY_POINTS_RE = re.compile(
    r'(?:key points?|summary|highlights?|action items?|decisions?|takeaways?)[:\s]*\n((?:[-•*]\s*.+\n?)+)',
    re.I | re.MULTILINE
)


def _gmail_service(profile):
    """Build Gmail service from profile's readonly token. Supports pickle and JSON."""
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
            print("Run test_auth.py with the appropriate profile first.")
            sys.exit(1)

    from profile_loader import build_gmail_service
    return build_gmail_service(creds)


def _decode_body(msg) -> str:
    """Extract readable text from a Gmail message payload."""
    import base64

    payload = msg.get('payload', {})
    parts = payload.get('parts', [])

    if not parts:
        data = payload.get('body', {}).get('data', '')
        if data:
            return base64.urlsafe_b64decode(data + '==').decode('utf-8', errors='replace')
        return msg.get('snippet', '')

    for part in parts:
        if part.get('mimeType') == 'text/plain':
            data = part.get('body', {}).get('data', '')
            if data:
                return base64.urlsafe_b64decode(data + '==').decode('utf-8', errors='replace')

    # Fallback to snippet
    return msg.get('snippet', '')


GEMINI_SUMMARY_RE = re.compile(
    r'Summary\s*\n(.+?)(?:\n\n|\n[A-Z])',
    re.S
)

def _extract_summary(body: str, max_length: int = 500) -> str:
    """Extract meeting summary from body text."""
    # Gemini format: "Summary\n<paragraph text>\n\n<next section>"
    match = GEMINI_SUMMARY_RE.search(body)
    if match:
        summary = match.group(1).strip().replace('\n', ' ')
        if len(summary) > max_length:
            summary = summary[:max_length] + '...'
        return summary

    # Generic format: "Key Points:" / "Summary:" followed by bullets
    match = KEY_POINTS_RE.search(body)
    if match:
        points = match.group(1).strip()
        if len(points) > max_length:
            points = points[:max_length] + '...'
        return points

    # Fallback: skip boilerplate header lines, take first real paragraph
    lines = body.strip().split('\n')
    skip_patterns = ['notes from', 'these notes have been', 'open meeting',
                     'the content was auto-generated', 'may contain']
    content_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            if content_lines:
                break
            continue
        if any(p in line.lower() for p in skip_patterns):
            continue
        if len(line) < 20 and not content_lines:
            continue
        content_lines.append(line)

    summary = ' '.join(content_lines)
    if len(summary) > max_length:
        summary = summary[:max_length] + '...'
    return summary or '(no summary extracted)'


def extract_meeting_notes(profile):
    """Find and summarize meeting notes emails."""
    data_dir = profile['data_path']
    output_file = data_dir / 'meeting-digest.json'

    # Load existing digest
    existing = []
    if output_file.exists():
        try:
            existing = json.loads(output_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    existing_ids = {e['message_id'] for e in existing}

    # Get meeting notes sender patterns from profile or use defaults
    senders = profile.get('meeting_notes_senders', DEFAULT_SENDERS)

    service = _gmail_service(profile)

    # Search for meeting notes emails from the last 7 days
    sender_queries = ' OR '.join(f'from:{s}' for s in senders)
    query = f'({sender_queries}) newer_than:7d'

    print(f"=== Meeting Digest Extractor ===")
    print(f"Searching for meeting notes: {query}")

    results = service.users().messages().list(
        userId='me', q=query, maxResults=50
    ).execute()

    messages = results.get('messages', [])
    new_messages = [m for m in messages if m['id'] not in existing_ids]

    print(f"Found {len(new_messages)} new meeting notes to process")

    new_entries = []
    for i, msg_ref in enumerate(new_messages):
        msg = service.users().messages().get(
            userId='me', id=msg_ref['id'], format='full'
        ).execute()

        headers = {h['name']: h['value']
                   for h in msg.get('payload', {}).get('headers', [])}
        subject = headers.get('Subject', '(no subject)')
        from_addr = headers.get('From', '')
        date_str = headers.get('Date', '')

        body = _decode_body(msg)
        summary = _extract_summary(body)

        # Parse meeting title from subject (strip "Notes: " prefix etc.)
        meeting_title = re.sub(r'^(Notes?:?\s*|Summary:?\s*|Minutes:?\s*)', '', subject, flags=re.I).strip()

        entry = {
            'message_id': msg_ref['id'],
            'date': date_str,
            'meeting_title': meeting_title,
            'from': from_addr,
            'subject': subject,
            'summary': summary,
            'extracted_at': datetime.now(timezone.utc).isoformat(),
        }
        new_entries.append(entry)
        print(f"  [{i+1}/{len(new_messages)}] {meeting_title}")
        print(f"    → {summary[:80]}...")

    # Merge and save
    all_entries = existing + new_entries
    # Keep only last 30 days
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    all_entries = [e for e in all_entries if e.get('extracted_at', '') > cutoff.isoformat()]

    # Sort by date descending
    all_entries.sort(key=lambda e: e.get('date', ''), reverse=True)

    data_dir.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(all_entries, indent=2))

    print(f"\n✅ Processed {len(new_entries)} new notes, {len(all_entries)} total in digest")
    return new_entries


def main():
    parser = argparse.ArgumentParser(description='Extract meeting notes digest')
    add_profile_arg(parser)
    args = parser.parse_args()

    profile = load_profile(args.profile)
    extract_meeting_notes(profile)


if __name__ == '__main__':
    main()
