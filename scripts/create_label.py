#!/usr/bin/env python3
"""Create a Gmail label. Uses gmail.modify scope (separate token from fetch)."""

import argparse
import os
import pickle
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

CANONICAL_ROOT = Path(__file__).parent.parent
CREDENTIALS_FILE = CANONICAL_ROOT / 'credentials.json'
# Separate token cache for gmail.modify scope so we don't blow away the
# read-only token used by the fetcher.
TOKEN_FILE = CANONICAL_ROOT / 'token-modify.pickle'


def authenticate():
    creds = None
    if TOKEN_FILE.exists():
        with open(TOKEN_FILE, 'rb') as f:
            creds = pickle.load(f)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, 'wb') as f:
            pickle.dump(creds, f)
    return build('gmail', 'v1', credentials=creds)


def main():
    parser = argparse.ArgumentParser(description='Create a Gmail label')
    parser.add_argument('--name', required=True, help='Label name (e.g., "Memberships")')
    args = parser.parse_args()

    service = authenticate()
    existing = {l['name']: l['id'] for l in service.users().labels().list(userId='me').execute().get('labels', [])}

    if args.name in existing:
        print(f"Label {args.name!r} already exists (id={existing[args.name]})")
        return

    label = service.users().labels().create(
        userId='me',
        body={
            'name': args.name,
            'labelListVisibility': 'labelShow',
            'messageListVisibility': 'show',
        },
    ).execute()
    print(f"Created label {label['name']!r} (id={label['id']})")


if __name__ == '__main__':
    main()
