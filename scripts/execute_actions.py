#!/usr/bin/env python3
"""
Gmail action executor - executes approved actions with comprehensive audit logging.
Requires gmail.modify scope (not gmail.metadata).
"""

import os
import sys
import json
import pickle
import argparse
from datetime import datetime
from pathlib import Path
from typing import List, Dict

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from tqdm import tqdm

# OAuth scopes - need modify to change labels/archive/delete
SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

PROJECT_ROOT = Path(__file__).parent.parent
CANONICAL_ROOT = Path(__file__).parent.parent
CREDENTIALS_FILE = CANONICAL_ROOT / 'credentials.json'
# Use the gmail.modify token (shared with create_label.py), NOT the read-only
# fetcher token. Reusing the read-only token would fail and clobber it.
TOKEN_FILE = CANONICAL_ROOT / 'token-modify.pickle'
LOGS_DIR = CANONICAL_ROOT / 'logs' / 'personal'

def authenticate():
    """Authenticate with Gmail API using OAuth.
    Supports both pickle (.pickle) and JSON (.json) token formats."""
    creds = None

    # Token file stores the user's access and refresh tokens
    if TOKEN_FILE.exists():
        if str(TOKEN_FILE).endswith('.json'):
            from google.oauth2.credentials import Credentials as OAuthCreds
            creds = OAuthCreds.from_authorized_user_file(str(TOKEN_FILE))
        else:
            with open(TOKEN_FILE, 'rb') as token:
                creds = pickle.load(token)

    # If no valid credentials or scope changed, re-auth
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                # Save refreshed token
                if str(TOKEN_FILE).endswith('.json'):
                    TOKEN_FILE.write_text(creds.to_json())
                else:
                    with open(TOKEN_FILE, 'wb') as token:
                        pickle.dump(creds, token)
            except Exception as e:
                print(f"Token refresh failed: {e}")
                print("Re-authenticating...")
                creds = None

        if not creds:
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

def already_executed_ids() -> set:
    """message_ids already SUCCESSFULLY executed per the audit logs.
    Used to make re-runs idempotent — never re-act on (or re-log) a message
    that's already been handled, even across runs with different thresholds."""
    import glob
    done = set()
    for path in glob.glob(str(LOGS_DIR / 'actions-*.jsonl')):
        try:
            with open(path, encoding='utf-8') as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    # Only successful, real (non-skipped) actions count as "done"
                    if entry.get('status') == 'success' and entry.get('message_id'):
                        done.add(entry['message_id'])
        except OSError:
            pass
    return done

_SYSTEM_LABELS = {
    'CATEGORY_PROMOTIONS', 'CATEGORY_UPDATES', 'CATEGORY_FORUMS',
    'CATEGORY_SOCIAL', 'CATEGORY_PERSONAL', 'INBOX', 'SPAM', 'TRASH',
    'UNREAD', 'STARRED', 'IMPORTANT', 'SENT', 'DRAFT',
}

def get_label_id(service, label_name: str) -> str:
    """Get or create a label by name. System labels (CATEGORY_*) return their id directly."""
    if label_name.upper() in _SYSTEM_LABELS:
        return label_name.upper()
    try:
        results = service.users().labels().list(userId='me').execute()
        labels = results.get('labels', [])
        for label in labels:
            if label['name'].lower() == label_name.lower():
                return label['id']
        label_object = {
            'name': label_name,
            'labelListVisibility': 'labelShow',
            'messageListVisibility': 'show'
        }
        created = service.users().labels().create(userId='me', body=label_object).execute()
        print(f"Created new label: {label_name}")
        return created['id']
    except HttpError as e:
        print(f"Error getting/creating label {label_name}: {e}")
        return None

def execute_action(service, action: Dict, dry_run: bool = False) -> Dict:
    """
    Execute a single action on an email.

    Returns log entry dict with status.
    """
    message_id = action['message_id']
    action_type = action['suggested_action']
    confidence = action['confidence']
    reasoning = action['reasoning']

    timestamp = datetime.utcnow().isoformat() + 'Z'

    log_entry = {
        'timestamp': timestamp,
        'message_id': message_id,
        'thread_id': action['thread_id'],
        'action': action_type,
        'confidence': confidence,
        'reasoning': reasoning,
        'status': 'pending'
    }

    if dry_run:
        label = action.get('label')
        archive_after = action.get('archive_after_label', False)
        if action_type == 'label':
            desc = f'WOULD label "{label}"' + (' + archive' if archive_after else ' (keep in inbox)')
        elif action_type == 'archive':
            desc = 'WOULD archive (remove INBOX)'
        elif action_type == 'delete':
            desc = 'WOULD move to trash'
        else:
            desc = f'WOULD skip ({action_type})'
        log_entry['status'] = 'dry-run'
        log_entry['details'] = desc
        return log_entry

    try:
        if action_type == 'archive':
            # Remove INBOX label from entire thread so the thread disappears from inbox
            thread_id = action.get('thread_id', '')
            if thread_id:
                service.users().threads().modify(
                    userId='me',
                    id=thread_id,
                    body={'removeLabelIds': ['INBOX']}
                ).execute()
            else:
                service.users().messages().modify(
                    userId='me',
                    id=message_id,
                    body={'removeLabelIds': ['INBOX']}
                ).execute()

            log_entry['status'] = 'success'
            log_entry['details'] = 'Removed INBOX label (archived thread)'

        elif action_type == 'delete':
            # Move to trash
            service.users().messages().trash(
                userId='me',
                id=message_id
            ).execute()

            log_entry['status'] = 'success'
            log_entry['details'] = 'Moved to trash (recoverable for 30 days)'

        elif action_type == 'label':
            label_name = action.get('label')
            if not label_name:
                log_entry['status'] = 'error'
                log_entry['error'] = 'No label specified'
                return log_entry

            label_id = get_label_id(service, label_name)
            if not label_id:
                log_entry['status'] = 'error'
                log_entry['error'] = f'Could not create/find label: {label_name}'
                return log_entry

            # Respect archive_after_label: only remove INBOX if the rule said so.
            # A label WITHOUT archive_after_label means "tag it but keep it visible".
            archive_after = action.get('archive_after_label', False)
            mark_read = action.get('mark_read', False)
            body = {'addLabelIds': [label_id]}
            remove_labels = []
            if archive_after:
                remove_labels.append('INBOX')
            if mark_read:
                remove_labels.append('UNREAD')
            if remove_labels:
                body['removeLabelIds'] = remove_labels

            # When archiving, use thread-level modify so entire thread leaves inbox
            thread_id = action.get('thread_id', '')
            if archive_after and thread_id:
                service.users().threads().modify(
                    userId='me',
                    id=thread_id,
                    body=body
                ).execute()
            else:
                service.users().messages().modify(
                    userId='me',
                    id=message_id,
                    body=body
                ).execute()

            log_entry['status'] = 'success'
            log_entry['label_applied'] = label_name
            log_entry['archived'] = archive_after
            # If also archived, reflect that in the action field so the dashboard
            # timeline counts it as an archive, not just a label.
            if archive_after:
                log_entry['action'] = 'archive'
            log_entry['details'] = (
                f'Applied label "{label_name}"'
                + (' and removed INBOX (archived)' if archive_after else ' (kept in inbox)')
            )

        elif action_type == 'keep':
            # No action needed
            log_entry['status'] = 'skipped'
            log_entry['details'] = 'Keep in inbox (no action taken)'

        else:
            log_entry['status'] = 'error'
            log_entry['error'] = f'Unknown action type: {action_type}'

    except HttpError as e:
        if e.resp.status == 404:
            log_entry['status'] = 'error'
            log_entry['error'] = 'Email not found (may have been deleted)'
        else:
            log_entry['status'] = 'error'
            log_entry['error'] = str(e)

    return log_entry

def execute_batch(
    service,
    actions: List[Dict],
    confidence_threshold: float,
    delete_threshold: float,
    log_file: Path,
    skip_deletes: bool = True,
    only_deletes: bool = False,
    dry_run: bool = False
):
    """Execute a batch of actions and log results."""

    # Idempotency: skip anything already executed in a prior run (per audit
    # logs), unless this is a dry-run. Makes re-runs safe no-ops and prevents
    # duplicate log entries / double-counting.
    done_ids = set() if dry_run else already_executed_ids()

    # Filter by confidence threshold + delete policy
    filtered_actions = []
    skipped_deletes = 0
    already_done = 0
    for action in actions:
        action_type = action['suggested_action']
        confidence = action['confidence']

        if action_type == 'keep':
            continue  # Never act on keeps

        if action.get('message_id') in done_ids:
            already_done += 1
            continue  # Already executed in a prior run — skip (idempotent)

        if action_type == 'delete':
            if skip_deletes:
                skipped_deletes += 1
                continue  # Conservative default: deletes need explicit --only-deletes
            if confidence < delete_threshold:
                skipped_deletes += 1
                continue  # Delete below the higher delete threshold
        else:
            if only_deletes:
                continue  # Delete-only pass: skip archives/labels
            if confidence < confidence_threshold:
                continue  # Non-delete below general threshold

        filtered_actions.append(action)

    mode = 'DELETES ONLY' if only_deletes else ('archives/labels (deletes skipped)' if skip_deletes else 'all actions')
    dry_tag = ' [DRY RUN — no changes]' if dry_run else ''
    print(f"Executing {len(filtered_actions)} actions (filtered from {len(actions)} total){dry_tag}")
    print(f"  Mode: {mode}")
    print(f"  Confidence threshold: {confidence_threshold}")
    print(f"  Delete threshold: {delete_threshold}")
    if skip_deletes and not only_deletes:
        print(f"  Deletes skipped (use --only-deletes to execute them): {skipped_deletes}")
    if already_done:
        print(f"  Already executed in a prior run (skipped, idempotent): {already_done}")

    # Initialize counters
    counts = {'success': 0, 'error': 0, 'skipped': 0, 'dry-run': 0}
    action_counts = {'archive': 0, 'delete': 0, 'label': 0, 'keep': 0}

    # Dry-runs do NOT write to the audit log (the dashboard counts log lines
    # as real executions; a dry-run must never inflate that).
    if dry_run:
        import io
        f = io.StringIO()
        log_file_note = '(dry-run — not written to disk)'
    else:
        LOGS_DIR.mkdir(exist_ok=True)
        f = open(log_file, 'w', encoding='utf-8')
        log_file_note = str(log_file)

    try:
        # Execute each action with progress bar
        for action in tqdm(filtered_actions, desc="Executing actions"):
            log_entry = execute_action(service, action, dry_run=dry_run)

            # Write log entry (JSONL format - one JSON per line)
            f.write(json.dumps(log_entry) + '\n')
            f.flush()  # Ensure written even if crash

            # Update counts
            counts[log_entry['status']] = counts.get(log_entry['status'], 0) + 1
            action_type = action['suggested_action']
            if log_entry['status'] in ('success', 'dry-run'):
                action_counts[action_type] = action_counts.get(action_type, 0) + 1
    finally:
        f.close()

    # Print summary
    print(f"\n✅ Execution complete!")
    print(f"  Log file: {log_file_note}")
    print(f"\n📊 Summary:")
    print(f"  Total actions: {len(filtered_actions)}")
    print(f"  Successful: {counts.get('success', 0)}")
    print(f"  Errors: {counts.get('error', 0)}")
    print(f"  Skipped: {counts.get('skipped', 0)}")
    print(f"\n  By action type:")
    for action_type, count in action_counts.items():
        if count > 0:
            print(f"    {action_type}: {count}")

    if counts.get('error', 0) > 0:
        print(f"\n⚠️  {counts['error']} errors occurred. Check log file for details.")

def main():
    from profile_loader import add_profile_arg, load_profile

    parser = argparse.ArgumentParser(description='Execute Gmail actions from classified JSON')
    add_profile_arg(parser)
    parser.add_argument('--input', required=True, help='Input JSON file with classifications')
    parser.add_argument('--confidence-threshold', type=float, default=0.75,
                        help='Minimum confidence for execution (default: 0.75)')
    parser.add_argument('--delete-threshold', type=float, default=0.97,
                        help='Minimum confidence for delete actions (default: 0.97)')
    parser.add_argument('--log-file', help='Output log file (default: logs/actions-TIMESTAMP.jsonl)')
    parser.add_argument('--only-deletes', action='store_true',
                        help='Execute ONLY deletes (the separate, explicit delete pass)')
    parser.add_argument('--include-deletes', action='store_true',
                        help='Include deletes alongside archives/labels (default: deletes are skipped)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what WOULD happen without touching Gmail')
    args = parser.parse_args()

    # Apply profile overrides
    global CREDENTIALS_FILE, TOKEN_FILE, LOGS_DIR
    profile = load_profile(args.profile)
    CREDENTIALS_FILE = profile['credentials_path']
    TOKEN_FILE = profile['token_modify']
    LOGS_DIR = profile['logs_path']

    input_file = Path(args.input)
    if not input_file.exists():
        print(f"ERROR: Input file not found: {input_file}")
        sys.exit(1)

    # Load actions
    with open(input_file, 'r', encoding='utf-8') as f:
        actions = json.load(f)

    print(f"Loaded {len(actions)} actions from {input_file}")

    # Generate log filename. Include microseconds + the input stem so two runs
    # in the same second (e.g. a loop over batches) never collide and clobber
    # each other's audit trail.
    if args.log_file:
        log_file = Path(args.log_file)
    else:
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        stem = input_file.stem.replace('-classified', '')
        log_file = LOGS_DIR / f"actions-{timestamp}-{stem}.jsonl"

    # Resolve delete policy:
    #   default                -> skip deletes (conservative)
    #   --include-deletes      -> run deletes alongside archives/labels
    #   --only-deletes         -> run ONLY deletes (separate explicit pass)
    only_deletes = args.only_deletes
    skip_deletes = not (args.include_deletes or args.only_deletes)

    # Authenticate (skip for dry-run so we don't need the modify token to preview)
    if args.dry_run:
        print("DRY RUN — not authenticating, no changes will be made.")
        service = None
    else:
        print("Authenticating with Gmail API...")
        service = authenticate()

    # Execute batch
    execute_batch(
        service,
        actions,
        args.confidence_threshold,
        args.delete_threshold,
        log_file,
        skip_deletes=skip_deletes,
        only_deletes=only_deletes,
        dry_run=args.dry_run
    )

if __name__ == '__main__':
    main()
