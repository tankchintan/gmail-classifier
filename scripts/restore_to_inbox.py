#!/usr/bin/env python3
"""One-off: restore wrongly-archived emails to the inbox.

Re-adds INBOX and removes a bogus label that the AI pass applied. Operates at
thread level (matching how execute_actions.py archived them).

Usage:
  python scripts/restore_to_inbox.py --profile personal \
      --message-id 19ee1c88a241e521 --remove-label Shopping
"""
import argparse
import pickle
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials as OAuthCreds
from googleapiclient.discovery import build

from profile_loader import add_profile_arg, load_profile


def authenticate(token_file: Path):
    if str(token_file).endswith('.json'):
        creds = OAuthCreds.from_authorized_user_file(str(token_file))
    else:
        with open(token_file, 'rb') as fh:
            creds = pickle.load(fh)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        if str(token_file).endswith('.json'):
            token_file.write_text(creds.to_json())
        else:
            with open(token_file, 'wb') as fh:
                pickle.dump(creds, fh)
    return build('gmail', 'v1', credentials=creds)


def label_id_by_name(service, name):
    for lbl in service.users().labels().list(userId='me').execute().get('labels', []):
        if lbl['name'].lower() == name.lower():
            return lbl['id']
    return None


def main():
    parser = argparse.ArgumentParser()
    add_profile_arg(parser)
    parser.add_argument('--message-id', required=True)
    parser.add_argument('--remove-label', help='Label name to strip (optional)')
    args = parser.parse_args()

    profile = load_profile(args.profile)
    service = authenticate(profile['token_modify'])

    # Resolve thread id from the message so the whole thread returns to inbox.
    msg = service.users().messages().get(
        userId='me', id=args.message_id, format='minimal').execute()
    thread_id = msg.get('threadId', args.message_id)

    add = ['INBOX']
    remove = []
    if args.remove_label:
        lid = label_id_by_name(service, args.remove_label)
        if lid:
            remove.append(lid)
        else:
            print(f"  (label '{args.remove_label}' not found — skipping removal)")

    body = {'addLabelIds': add}
    if remove:
        body['removeLabelIds'] = remove
    service.users().threads().modify(userId='me', id=thread_id, body=body).execute()
    print(f"  ✅ Restored {args.message_id} (thread {thread_id}) to INBOX"
          + (f", removed '{args.remove_label}'" if remove else ""))


if __name__ == '__main__':
    main()
