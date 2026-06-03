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
CREDENTIALS_FILE = PROJECT_ROOT / 'credentials.json'
TOKEN_FILE = PROJECT_ROOT / 'token.pickle'
LOGS_DIR = PROJECT_ROOT / 'logs'

def authenticate():
    """Authenticate with Gmail API using OAuth."""
    creds = None

    # Token file stores the user's access and refresh tokens
    if TOKEN_FILE.exists():
        with open(TOKEN_FILE, 'rb') as token:
            creds = pickle.load(token)

    # If no valid credentials or scope changed, re-auth
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
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
        with open(TOKEN_FILE, 'wb') as token:
            pickle.dump(creds, token)

    return build('gmail', 'v1', credentials=creds)

def get_label_id(service, label_name: str) -> str:
    """Get or create a label by name."""
    try:
        # List existing labels
        results = service.users().labels().list(userId='me').execute()
        labels = results.get('labels', [])

        # Check if label exists
        for label in labels:
            if label['name'].lower() == label_name.lower():
                return label['id']

        # Create new label if not exists
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

def execute_action(service, action: Dict) -> Dict:
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

    try:
        if action_type == 'archive':
            # Remove INBOX label
            service.users().messages().modify(
                userId='me',
                id=message_id,
                body={'removeLabelIds': ['INBOX']}
            ).execute()

            log_entry['status'] = 'success'
            log_entry['details'] = 'Removed INBOX label (archived)'

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

            # Add label and remove INBOX
            service.users().messages().modify(
                userId='me',
                id=message_id,
                body={
                    'addLabelIds': [label_id],
                    'removeLabelIds': ['INBOX']
                }
            ).execute()

            log_entry['status'] = 'success'
            log_entry['label_applied'] = label_name
            log_entry['details'] = f'Applied label "{label_name}" and removed INBOX'

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
    log_file: Path
):
    """Execute a batch of actions and log results."""

    # Filter by confidence threshold
    filtered_actions = []
    for action in actions:
        action_type = action['suggested_action']
        confidence = action['confidence']

        # Apply thresholds
        if action_type == 'delete' and confidence < delete_threshold:
            continue  # Skip deletes below higher threshold
        elif confidence < confidence_threshold:
            continue  # Skip all other actions below general threshold

        filtered_actions.append(action)

    print(f"Executing {len(filtered_actions)} actions (filtered from {len(actions)} total)")
    print(f"  Confidence threshold: {confidence_threshold}")
    print(f"  Delete threshold: {delete_threshold}")

    # Initialize counters
    counts = {'success': 0, 'error': 0, 'skipped': 0}
    action_counts = {'archive': 0, 'delete': 0, 'label': 0, 'keep': 0}

    # Create log file
    LOGS_DIR.mkdir(exist_ok=True)

    with open(log_file, 'w', encoding='utf-8') as f:
        # Execute each action with progress bar
        for action in tqdm(filtered_actions, desc="Executing actions"):
            log_entry = execute_action(service, action)

            # Write log entry (JSONL format - one JSON per line)
            f.write(json.dumps(log_entry) + '\n')
            f.flush()  # Ensure written even if crash

            # Update counts
            counts[log_entry['status']] = counts.get(log_entry['status'], 0) + 1
            action_type = action['suggested_action']
            if log_entry['status'] == 'success':
                action_counts[action_type] = action_counts.get(action_type, 0) + 1

    # Print summary
    print(f"\n✅ Execution complete!")
    print(f"  Log file: {log_file}")
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
    parser = argparse.ArgumentParser(description='Execute Gmail actions from classified JSON')
    parser.add_argument('--input', required=True, help='Input JSON file with classifications')
    parser.add_argument('--confidence-threshold', type=float, default=0.81,
                        help='Minimum confidence for execution (default: 0.81)')
    parser.add_argument('--delete-threshold', type=float, default=0.95,
                        help='Minimum confidence for delete actions (default: 0.95)')
    parser.add_argument('--log-file', help='Output log file (default: logs/actions-TIMESTAMP.jsonl)')
    args = parser.parse_args()

    input_file = Path(args.input)
    if not input_file.exists():
        print(f"ERROR: Input file not found: {input_file}")
        sys.exit(1)

    # Load actions
    with open(input_file, 'r', encoding='utf-8') as f:
        actions = json.load(f)

    print(f"Loaded {len(actions)} actions from {input_file}")

    # Generate log filename
    if args.log_file:
        log_file = Path(args.log_file)
    else:
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        log_file = LOGS_DIR / f"actions-{timestamp}.jsonl"

    # Authenticate
    print("Authenticating with Gmail API...")
    service = authenticate()

    # Execute batch
    execute_batch(
        service,
        actions,
        args.confidence_threshold,
        args.delete_threshold,
        log_file
    )

if __name__ == '__main__':
    main()
