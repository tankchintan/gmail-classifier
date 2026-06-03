#!/usr/bin/env python3
"""
Email classifier - analyzes patterns and suggests actions with confidence scores.
"""

import sys
import json
import csv
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict
import re

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / 'data'
RULES_FILE = PROJECT_ROOT / 'scripts' / 'classification_rules.json'

def load_classification_rules() -> Dict:
    """Load explicit classification rules if they exist."""
    if RULES_FILE.exists():
        with open(RULES_FILE, 'r') as f:
            return json.load(f)
    return {
        'whitelist_domains': [],
        'blacklist_domains': [],
        'newsletter_patterns': [],
        'receipt_patterns': [],
        'spam_patterns': []
    }

def parse_date(date_str: str) -> datetime:
    """Parse email date string to datetime."""
    # Gmail dates are usually RFC 2822 format
    # For simplicity, use a rough parser (can be enhanced)
    try:
        # Try common formats
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(date_str)
    except:
        return datetime.now()

def classify_email(email: Dict, rules: Dict) -> Dict:
    """
    Classify a single email and return suggested action with confidence.

    Returns:
        {
            'message_id': str,
            'thread_id': str,
            'suggested_action': 'archive' | 'delete' | 'label' | 'keep',
            'label': str | None,
            'confidence': float (0.0-1.0),
            'reasoning': str
        }
    """
    message_id = email['message_id']
    thread_id = email['thread_id']
    subject = email['subject'].lower()
    from_email = email['from_email'].lower()
    from_name = email['from_name']
    date = parse_date(email['date'])
    is_reply = email['is_reply']

    # Calculate age in days
    age_days = (datetime.now(date.tzinfo or None) - date).days

    # Default classification
    action = 'keep'
    label = None
    confidence = 0.5
    reasoning = "Default - needs manual review"

    # Rule-based classification
    domain = from_email.split('@')[-1] if '@' in from_email else ''

    # 1. Whitelist domains (always keep)
    if domain in rules.get('whitelist_domains', []):
        return {
            'message_id': message_id,
            'thread_id': thread_id,
            'suggested_action': 'keep',
            'label': None,
            'confidence': 0.95,
            'reasoning': f"Domain {domain} is whitelisted"
        }

    # 2. Blacklist domains (delete with high confidence)
    if domain in rules.get('blacklist_domains', []):
        return {
            'message_id': message_id,
            'thread_id': thread_id,
            'suggested_action': 'delete',
            'label': None,
            'confidence': 0.98,
            'reasoning': f"Domain {domain} is blacklisted as spam"
        }

    # 3. Obvious spam patterns
    spam_keywords = ['you won', 'claim your prize', 'click here now', 'limited time offer',
                     'act now', 'congratulations!!!', 'viagra', 'nigerian prince']
    if any(kw in subject for kw in spam_keywords):
        return {
            'message_id': message_id,
            'thread_id': thread_id,
            'suggested_action': 'delete',
            'label': None,
            'confidence': 0.97,
            'reasoning': f"Subject matches spam pattern: {subject}"
        }

    # 4. Newsletter patterns (archive if old)
    newsletter_indicators = ['unsubscribe', 'newsletter', 'weekly digest', 'daily brief',
                              'noreply@', 'no-reply@', 'marketing@', 'news@']
    is_newsletter = any(ind in from_email for ind in newsletter_indicators) or \
                    any(ind in subject for ind in newsletter_indicators)

    if is_newsletter:
        if age_days > 30:
            return {
                'message_id': message_id,
                'thread_id': thread_id,
                'suggested_action': 'archive',
                'label': None,
                'confidence': 0.92,
                'reasoning': f"Newsletter from {from_email}, unread for {age_days} days"
            }
        elif age_days > 14:
            return {
                'message_id': message_id,
                'thread_id': thread_id,
                'suggested_action': 'archive',
                'label': None,
                'confidence': 0.85,
                'reasoning': f"Newsletter from {from_email}, unread for {age_days} days"
            }
        else:
            return {
                'message_id': message_id,
                'thread_id': thread_id,
                'suggested_action': 'keep',
                'label': None,
                'confidence': 0.60,
                'reasoning': f"Recent newsletter ({age_days} days old), might be relevant"
            }

    # 5. Receipt/transactional patterns
    receipt_keywords = ['receipt', 'invoice', 'order confirmation', 'payment received',
                        'transaction', 'billing', 'subscription', 'your order']
    is_receipt = any(kw in subject for kw in receipt_keywords)

    common_receipt_domains = ['amazon.com', 'paypal.com', 'stripe.com', 'apple.com',
                               'google.com', 'uber.com', 'lyft.com']
    is_receipt_domain = any(d in domain for d in common_receipt_domains)

    if is_receipt or is_receipt_domain:
        return {
            'message_id': message_id,
            'thread_id': thread_id,
            'suggested_action': 'label',
            'label': 'receipts',
            'confidence': 0.87,
            'reasoning': f"Transactional email: {subject}"
        }

    # 6. Automated notifications (archive if old)
    notification_keywords = ['notification', 'alert', 'reminder', 'automated', 'do-not-reply']
    is_notification = any(kw in from_email for kw in notification_keywords) or \
                      any(kw in subject for kw in notification_keywords)

    if is_notification and age_days > 7:
        return {
            'message_id': message_id,
            'thread_id': thread_id,
            'suggested_action': 'archive',
            'label': None,
            'confidence': 0.80,
            'reasoning': f"Automated notification, unread for {age_days} days"
        }

    # 7. Questions from real people (keep)
    if '?' in subject and not is_reply and age_days < 30:
        return {
            'message_id': message_id,
            'thread_id': thread_id,
            'suggested_action': 'keep',
            'label': None,
            'confidence': 0.75,
            'reasoning': f"Email contains question, might be important: {subject}"
        }

    # 8. Very old emails (archive with medium confidence)
    if age_days > 90:
        return {
            'message_id': message_id,
            'thread_id': thread_id,
            'suggested_action': 'archive',
            'label': None,
            'confidence': 0.75,
            'reasoning': f"Very old unread email ({age_days} days), likely not urgent"
        }

    # 9. Old promotional patterns
    promo_keywords = ['sale', 'discount', '% off', 'deal', 'offer', 'promo', 'coupon']
    is_promo = any(kw in subject for kw in promo_keywords)

    if is_promo and age_days > 14:
        return {
            'message_id': message_id,
            'thread_id': thread_id,
            'suggested_action': 'archive',
            'label': None,
            'confidence': 0.88,
            'reasoning': f"Promotional email, expired (unread for {age_days} days)"
        }

    # Default: uncertain, keep for manual review
    return {
        'message_id': message_id,
        'thread_id': thread_id,
        'suggested_action': 'keep',
        'label': None,
        'confidence': 0.45,
        'reasoning': f"Uncertain classification - manual review needed"
    }

def classify_batch(input_csv: Path, output_json: Path):
    """Classify a batch of emails from CSV."""
    rules = load_classification_rules()

    # Read emails
    emails = []
    with open(input_csv, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        emails = list(reader)

    print(f"Classifying {len(emails)} emails from {input_csv}...")

    # Classify each email
    classifications = []
    for email in emails:
        classification = classify_email(email, rules)
        classifications.append(classification)

    # Save to JSON
    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump(classifications, f, indent=2)

    print(f"✅ Saved {len(classifications)} classifications to {output_json}")

    # Print summary
    action_counts = {}
    confidence_ranges = {'0.8+': 0, '0.6-0.8': 0, '0.4-0.6': 0, '<0.4': 0}

    for c in classifications:
        action = c['suggested_action']
        action_counts[action] = action_counts.get(action, 0) + 1

        conf = c['confidence']
        if conf >= 0.8:
            confidence_ranges['0.8+'] += 1
        elif conf >= 0.6:
            confidence_ranges['0.6-0.8'] += 1
        elif conf >= 0.4:
            confidence_ranges['0.4-0.6'] += 1
        else:
            confidence_ranges['<0.4'] += 1

    print("\n📊 Classification Summary:")
    print(f"  Actions: {action_counts}")
    print(f"  Confidence ranges: {confidence_ranges}")

    # Highlight any deletes (require scrutiny)
    deletes = [c for c in classifications if c['suggested_action'] == 'delete']
    if deletes:
        print(f"\n⚠️  {len(deletes)} emails suggested for deletion:")
        for d in deletes[:5]:  # Show first 5
            print(f"    - {d['reasoning']} (confidence: {d['confidence']})")

def main():
    parser = argparse.ArgumentParser(description='Classify emails from CSV')
    parser.add_argument('--input', required=True, help='Input CSV file')
    parser.add_argument('--output', required=True, help='Output JSON file')
    args = parser.parse_args()

    input_csv = Path(args.input)
    output_json = Path(args.output)

    if not input_csv.exists():
        print(f"ERROR: Input file not found: {input_csv}")
        sys.exit(1)

    classify_batch(input_csv, output_json)

if __name__ == '__main__':
    main()
