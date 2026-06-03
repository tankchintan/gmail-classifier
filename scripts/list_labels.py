#!/usr/bin/env python3
"""List existing Gmail labels (user-defined + system)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fetch_unread import authenticate


def main():
    service = authenticate()
    labels = service.users().labels().list(userId='me').execute().get('labels', [])
    user_labels = sorted(
        [l for l in labels if l.get('type') == 'user'],
        key=lambda l: l['name'].lower(),
    )
    system_labels = [l for l in labels if l.get('type') == 'system']

    print(f'=== USER LABELS ({len(user_labels)}) ===')
    for l in user_labels:
        print(f"  {l['name']}")
    print()
    print(f'=== SYSTEM LABELS ({len(system_labels)}) ===')
    for l in system_labels:
        print(f"  {l['name']}")


if __name__ == '__main__':
    main()
