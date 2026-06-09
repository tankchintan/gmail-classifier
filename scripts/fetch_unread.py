#!/usr/bin/env python3
"""
Gmail metadata fetcher - fetches unread Primary inbox emails without bodies.
Uses gmail.readonly scope (read-only, supports search queries).
"""

import os
import sys
import pickle
import argparse
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional
import csv

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from tqdm import tqdm

# gmail.readonly: required for the `q` search parameter; gmail.metadata can't search.
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

PROJECT_ROOT = Path(__file__).parent.parent
CANONICAL_ROOT = Path(__file__).parent.parent
CREDENTIALS_FILE = CANONICAL_ROOT / 'credentials.json'
TOKEN_FILE = CANONICAL_ROOT / 'token.pickle'
DATA_DIR = CANONICAL_ROOT / 'data'

def authenticate():
    """Authenticate with Gmail API using OAuth.
    Supports both pickle (.pickle) and JSON (.json) token formats."""
    creds = None

    # Token file stores the user's access and refresh tokens
    if TOKEN_FILE.exists():
        if str(TOKEN_FILE).endswith('.json'):
            # Don't enforce scope — gmail.modify is a superset of gmail.readonly
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE))
        else:
            with open(TOKEN_FILE, 'rb') as token:
                creds = pickle.load(token)

    # If no valid credentials, let the user log in
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Save refreshed token
            if str(TOKEN_FILE).endswith('.json'):
                TOKEN_FILE.write_text(creds.to_json())
            else:
                with open(TOKEN_FILE, 'wb') as token:
                    pickle.dump(creds, token)
        else:
            if not CREDENTIALS_FILE.exists():
                print(f"ERROR: {CREDENTIALS_FILE} not found!")
                print("Please download OAuth credentials from Google Cloud Console")
                sys.exit(1)

            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)

            # Save credentials for next run
            if str(TOKEN_FILE).endswith('.json'):
                TOKEN_FILE.write_text(creds.to_json())
            else:
                with open(TOKEN_FILE, 'wb') as token:
                    pickle.dump(creds, token)

    return build('gmail', 'v1', credentials=creds)

def get_message_metadata(service, message_id: str) -> Optional[Dict]:
    """Fetch metadata for a single message."""
    try:
        msg = service.users().messages().get(
            userId='me',
            id=message_id,
            format='metadata',
            metadataHeaders=['From', 'Subject', 'Date', 'In-Reply-To', 'References']
        ).execute()

        return msg
    except HttpError as e:
        print(f"Error fetching message {message_id}: {e}")
        return None

def parse_from_header(from_header: str) -> tuple[str, str]:
    """Parse 'From' header into name and email."""
    # Format: "Name <email@example.com>" or just "email@example.com"
    if '<' in from_header:
        name_part = from_header.split('<')[0].strip().strip('"')
        email_part = from_header.split('<')[1].strip('>')
        return name_part, email_part
    else:
        return '', from_header.strip()

def get_header_value(headers: List[Dict], name: str) -> Optional[str]:
    """Extract header value by name."""
    for header in headers:
        if header['name'].lower() == name.lower():
            return header['value']
    return None

def is_reply(msg: Dict, thread_message_count: int) -> bool:
    """Determine if message is a reply."""
    headers = msg.get('payload', {}).get('headers', [])

    # Check if thread has multiple messages
    if thread_message_count > 1:
        return True

    # Check for reply headers
    in_reply_to = get_header_value(headers, 'In-Reply-To')
    references = get_header_value(headers, 'References')

    return bool(in_reply_to or references)

def fetch_unread_emails(
    service,
    limit: int = 500,
    offset: int = 0,
    batch_size: int = 100,
    before: Optional[str] = None,
    after: Optional[str] = None,
) -> List[Dict]:
    """
    Fetch unread emails from Primary inbox.
    `before` / `after` are YYYY/MM/DD strings (Gmail's native format).
    Returns list of email metadata dicts.
    """
    query_parts = ['label:inbox', 'is:unread', 'category:primary']
    if before:
        query_parts.append(f'before:{before}')
    if after:
        query_parts.append(f'after:{after}')
    query = ' '.join(query_parts)

    print(f"Fetching unread emails (limit={limit}, offset={offset})...")

    # Get list of message IDs
    messages = []
    page_token = None

    # Skip to offset if needed
    current_offset = 0
    while current_offset < offset:
        results = service.users().messages().list(
            userId='me',
            q=query,
            maxResults=min(500, offset - current_offset),
            pageToken=page_token
        ).execute()

        current_offset += len(results.get('messages', []))
        page_token = results.get('nextPageToken')

        if not page_token:
            print(f"Reached end of results at offset {current_offset}")
            return []

    # Now fetch actual messages
    while len(messages) < limit:
        results = service.users().messages().list(
            userId='me',
            q=query,
            maxResults=min(500, limit - len(messages)),
            pageToken=page_token
        ).execute()

        batch_messages = results.get('messages', [])
        if not batch_messages:
            break

        messages.extend(batch_messages)
        page_token = results.get('nextPageToken')

        if not page_token:
            break

    print(f"Found {len(messages)} message IDs")

    # Fetch metadata for each message (with progress bar)
    emails = []
    threads_seen = {}  # Track oldest email per thread

    with tqdm(total=len(messages), desc="Fetching metadata") as pbar:
        for i in range(0, len(messages), batch_size):
            batch = messages[i:i+batch_size]

            for msg_ref in batch:
                msg = get_message_metadata(service, msg_ref['id'])
                if not msg:
                    continue

                headers = msg.get('payload', {}).get('headers', [])
                thread_id = msg.get('threadId')
                message_id = msg.get('id')

                # Parse headers
                from_header = get_header_value(headers, 'From') or ''
                subject = get_header_value(headers, 'Subject') or '(no subject)'
                date_str = get_header_value(headers, 'Date') or ''

                from_name, from_email = parse_from_header(from_header)

                # Get thread info to determine if reply
                thread_info = service.users().threads().get(
                    userId='me',
                    id=thread_id,
                    format='minimal'
                ).execute()
                thread_message_count = len(thread_info.get('messages', []))

                email_data = {
                    'message_id': message_id,
                    'thread_id': thread_id,
                    'date': date_str,
                    'from_name': from_name,
                    'from_email': from_email,
                    'subject': subject,
                    'is_reply': is_reply(msg, thread_message_count),
                    'internal_date': int(msg.get('internalDate', 0))  # For sorting
                }

                # Deduplicate by thread - keep only oldest unread per thread
                if thread_id not in threads_seen:
                    threads_seen[thread_id] = email_data
                else:
                    # Compare dates, keep older one
                    if email_data['internal_date'] < threads_seen[thread_id]['internal_date']:
                        threads_seen[thread_id] = email_data

                pbar.update(1)

                # Progress update every 100
                if len(emails) % 100 == 0 and len(emails) > 0:
                    print(f"  Processed {len(emails)} emails...")

    # Convert to list (deduplicated)
    emails = list(threads_seen.values())

    print(f"After deduplication: {len(emails)} unique threads")

    return emails

def save_to_csv(emails: List[Dict], output_file: Path):
    """Save emails to CSV."""
    DATA_DIR.mkdir(exist_ok=True)

    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'message_id', 'thread_id', 'date', 'from_name', 'from_email', 'subject', 'is_reply'
        ])
        writer.writeheader()

        for email in emails:
            # Remove internal_date (only used for sorting)
            row = {k: v for k, v in email.items() if k != 'internal_date'}
            writer.writerow(row)

    print(f"Saved {len(emails)} emails to {output_file}")

def main():
    from profile_loader import add_profile_arg, load_profile

    parser = argparse.ArgumentParser(description='Fetch unread Gmail metadata')
    add_profile_arg(parser)
    parser.add_argument('--batch', default='001', help='Batch number (e.g., 001)')
    parser.add_argument('--limit', type=int, default=500, help='Max emails to fetch')
    parser.add_argument('--offset', type=int, default=0, help='Skip first N emails')
    parser.add_argument('--before', help='Only emails before this date (YYYY/MM/DD, Gmail format)')
    parser.add_argument('--after', help='Only emails after this date (YYYY/MM/DD, Gmail format)')
    args = parser.parse_args()

    # Apply profile overrides
    global CREDENTIALS_FILE, TOKEN_FILE, DATA_DIR
    if args.profile:
        profile = load_profile(args.profile)
        CREDENTIALS_FILE = profile['credentials_path']
        TOKEN_FILE = profile['token_readonly']
        DATA_DIR = profile['data_path']

    # Authenticate
    service = authenticate()

    # Fetch emails
    limit = args.limit
    emails = fetch_unread_emails(
        service,
        limit=limit,
        offset=args.offset,
        before=args.before,
        after=args.after,
    )

    if not emails:
        print("No emails found")
        return

    # Save to CSV
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    output_file = DATA_DIR / f"batch-{args.batch}.csv"
    save_to_csv(emails, output_file)

    print(f"\n✅ Complete! Output: {output_file.absolute()}")

if __name__ == '__main__':
    main()
