#!/usr/bin/env python3
"""
Test script to verify Gmail API authentication and basic access.
Run this first to ensure OAuth is working before using CU agents.
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from fetch_unread import authenticate

def main():
    print("Testing Gmail API authentication...")
    print("=" * 60)

    try:
        service = authenticate()
        print("✅ Authentication successful!")

        # Test basic API call
        print("\nTesting basic API access...")
        profile = service.users().getProfile(userId='me').execute()

        print(f"✅ Connected to Gmail account:")
        print(f"   Email: {profile['emailAddress']}")
        print(f"   Total messages: {profile['messagesTotal']}")
        print(f"   Total threads: {profile['threadsTotal']}")

        # Test query for unread Primary inbox
        print("\nTesting query for unread Primary inbox...")
        results = service.users().messages().list(
            userId='me',
            q='label:inbox is:unread category:primary',
            maxResults=10
        ).execute()

        unread_count = len(results.get('messages', []))
        print(f"✅ Found {unread_count} unread emails in first batch (showing max 10)")

        if unread_count == 0:
            print("   (No unread emails - inbox zero! 🎉)")

        print("\n" + "=" * 60)
        print("✅ All tests passed! You're ready to use CU agents.")
        print("\nNext steps:")
        print("  1. Review QUICKSTART.md")
        print("  2. Run your first batch with gmail-fetcher agent")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        print("\nTroubleshooting:")
        print("  1. Ensure credentials.json is in project root")
        print("  2. Check you have enabled Gmail API in Google Cloud Console")
        print("  3. Try deleting token.pickle and re-authenticating")
        sys.exit(1)

if __name__ == '__main__':
    main()
