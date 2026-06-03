#!/usr/bin/env python3
"""Rename a Gmail label (preserves label ID, so tagged emails keep the label).
Uses gmail.modify scope. Nesting is done with '/' in the name."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from create_label import authenticate  # reuses token-modify.pickle


def main():
    parser = argparse.ArgumentParser(description='Rename a Gmail label')
    parser.add_argument('--old', required=True, help='Current label name')
    parser.add_argument('--new', required=True, help='New label name (use "/" to nest)')
    args = parser.parse_args()

    service = authenticate()
    labels = service.users().labels().list(userId='me').execute().get('labels', [])
    by_name = {l['name']: l for l in labels}

    if args.old not in by_name:
        print(f"Label {args.old!r} not found.")
        sys.exit(1)
    if args.new in by_name:
        print(f"Label {args.new!r} already exists — cannot rename onto it.")
        sys.exit(1)

    label_id = by_name[args.old]['id']
    updated = service.users().labels().patch(
        userId='me', id=label_id, body={'name': args.new}
    ).execute()
    print(f"Renamed {args.old!r} -> {updated['name']!r} (id preserved: {label_id})")


if __name__ == '__main__':
    main()
