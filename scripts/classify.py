#!/usr/bin/env python3
"""
Email classifier - analyzes patterns and suggests actions with confidence scores.
"""

import sys
import json
import csv
import argparse
import re
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional

CANONICAL_ROOT = Path(__file__).parent.parent
DATA_DIR = CANONICAL_ROOT / 'data'
SCRIPTS_DIR = CANONICAL_ROOT / 'scripts'
# Split rules: base (committed, public) + personal (gitignored, private).
# Legacy monolithic file is still supported as a fallback.
# These can be overridden via --profile.
RULES_BASE_FILE     = SCRIPTS_DIR / 'classification_rules.base.json'
RULES_PERSONAL_FILE = SCRIPTS_DIR / 'classification_rules.personal.json'
RULES_FILE          = SCRIPTS_DIR / 'classification_rules.json'  # legacy fallback

# Profile-specific rules override (set by --profile flag at runtime)
_PROFILE_RULES_FILE = None

def load_classification_rules() -> Dict:
    """Load rules, merging personal (private) on top of base (public).

    Resolution order:
      1. If a profile-specific rules file is set, use it as the personal layer.
      2. classification_rules.base.json    — committed, shareable domain/pattern rules
      3. classification_rules.personal.json — gitignored, personal sender overrides
      Personal/profile sender_rules are prepended so they match first (first-match-wins).
      Falls back to legacy classification_rules.json if split files don't exist.
    """
    empty = {
        'sender_rules': [],
        'whitelist_domains': [],
        'blacklist_domains': [],
        'newsletter_patterns': [],
        'receipt_patterns': [],
        'spam_patterns': [],
    }

    # Determine which personal/profile rules file to use
    profile_rules = _PROFILE_RULES_FILE or RULES_PERSONAL_FILE

    if RULES_BASE_FILE.exists():
        with open(RULES_BASE_FILE, 'r') as f:
            merged = json.load(f)
        # Prepend profile/personal sender rules so they take priority
        if profile_rules and profile_rules.exists():
            with open(profile_rules, 'r') as f:
                personal = json.load(f)
            personal_rules = [r for r in personal.get('sender_rules', []) if isinstance(r, dict)]
            merged['sender_rules'] = personal_rules + merged.get('sender_rules', [])
            # Merge whitelist/blacklist domains from profile
            for key in ('whitelist_domains', 'blacklist_domains'):
                extra = personal.get(key, [])
                if extra:
                    merged[key] = list(set(merged.get(key, []) + extra))
        return merged

    # Legacy fallback
    if RULES_FILE.exists():
        with open(RULES_FILE, 'r') as f:
            return json.load(f)

    return empty

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

def _match_sender_rule(rule: Dict, from_email: str, subject: str, age_days: int,
                       from_name: str = '') -> bool:
    """Return True if the rule matches the email. Case-insensitive substring matches."""
    if 'action' not in rule:
        return False
    from_match = rule.get('from_match', '').lower()
    if from_match and from_match not in from_email:
        return False
    from_name_match = rule.get('from_name_match', '').lower()
    if from_name_match and from_name_match not in from_name.lower():
        return False
    subject_match = rule.get('subject_match', '').lower()
    if subject_match and subject_match not in subject:
        return False
    min_age = rule.get('min_age_days')
    if min_age is not None and age_days < min_age:
        return False
    max_age = rule.get('max_age_days')
    if max_age is not None and age_days > max_age:
        return False
    return True


def _apply_sender_rule(rule: Dict, message_id: str, thread_id: str) -> Dict:
    """Convert a matched rule into a classification result."""
    action = rule['action']
    label = rule.get('label')
    result = {
        'message_id': message_id,
        'thread_id': thread_id,
        'suggested_action': action,
        'label': label,
        'archive_after_label': rule.get('archive_after_label', False),
        'confidence': rule.get('confidence', 0.85),
        'reasoning': rule.get('reasoning', f'Matched sender rule: {rule.get("from_match")}'),
    }
    if rule.get('mark_read'):
        result['mark_read'] = True
    return result


def classify_email(email: Dict, rules: Dict) -> Dict:
    """
    Classify a single email and return suggested action with confidence.

    Returns:
        {
            'message_id': str,
            'thread_id': str,
            'suggested_action': 'archive' | 'delete' | 'label' | 'keep',
            'label': str | None,
            'archive_after_label': bool,
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

    # 0. Explicit sender rules (data-driven, FIRST MATCH WINS)
    for rule in rules.get('sender_rules', []):
        if _match_sender_rule(rule, from_email, subject, age_days, from_name):
            return _apply_sender_rule(rule, message_id, thread_id)

    # Default classification
    action = 'keep'
    label = None
    confidence = 0.5
    reasoning = "Default - needs manual review"

    # Rule-based classification
    domain = from_email.split('@')[-1] if '@' in from_email else ''

    # NOTE: whitelist_domains is NOT a blanket "always keep" — it only blocks
    # the blunt subject-keyword spam tripwire below (step 3) from deleting.
    # Sender rules and age-based archiving still apply to these domains.

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

    # 3. Obvious spam patterns — BUT never auto-delete from personal/known
    # senders. Real people (and services you use) write "act now" too; the
    # subject keyword alone is too blunt to delete a family member's forward.
    spam_keywords = ['you won', 'claim your prize', 'click here now', 'limited time offer',
                     'act now', 'congratulations!!!', 'viagra', 'nigerian prince']
    # Personal/free-mail domains: a keyword match here is almost always a real
    # email (forward, advocacy, family), not bulk spam.
    PERSONAL_DOMAINS = ('gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com',
                        'icloud.com', 'me.com', 'aol.com', 'proton.me')
    if any(kw in subject for kw in spam_keywords):
        is_personal_sender = any(domain.endswith(pd) for pd in PERSONAL_DOMAINS)
        wl = rules.get('whitelist_domains', [])
        is_whitelisted = any(domain == w or domain.endswith('.' + w) for w in wl)
        if is_personal_sender or is_whitelisted:
            # Downgrade: flag for review, do NOT delete.
            return {
                'message_id': message_id,
                'thread_id': thread_id,
                'suggested_action': 'keep',
                'label': None,
                'confidence': 0.55,
                'reasoning': f"Spam keyword in subject but sender is personal/known ({domain}) — keep for review"
            }
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
        # Same pattern as Chase: label always; archive once user has had time
        # to see it (7+ days unread = effectively decided not urgent).
        if age_days >= 7:
            return {
                'message_id': message_id,
                'thread_id': thread_id,
                'suggested_action': 'label',
                'label': 'Receipts',
                'archive_after_label': True,
                'confidence': 0.90,
                'reasoning': f"Transactional/receipt ≥7 days unread — label and archive"
            }
        return {
            'message_id': message_id,
            'thread_id': thread_id,
            'suggested_action': 'label',
            'label': 'Receipts',
            'archive_after_label': False,
            'confidence': 0.85,
            'reasoning': f"Recent receipt — label but leave in inbox"
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

    # Global age gate: non-personal emails that have sat unread for 180+ days
    # are almost certainly stale — archive them rather than clog the inbox forever.
    # Personal senders (gmail, yahoo, etc.) are always exempt.
    if age_days >= 180 and not any(domain.endswith(pd) for pd in PERSONAL_DOMAINS):
        return {
            'message_id': message_id,
            'thread_id': thread_id,
            'suggested_action': 'archive',
            'label': None,
            'confidence': 0.80,
            'reasoning': f"Unread for {age_days} days — stale, auto-archiving"
        }

    # Personal sender domains get higher confidence — real people emailing you
    if any(domain.endswith(pd) for pd in PERSONAL_DOMAINS):
        return {
            'message_id': message_id,
            'thread_id': thread_id,
            'suggested_action': 'keep',
            'label': None,
            'confidence': 0.75,
            'reasoning': f"Personal sender ({domain}) — keep"
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

BODY_UNSUBSCRIBE_RE = re.compile(r'unsubscribe|opt.?out|manage.*preferences', re.I)
BODY_TRANSACTIONAL_RE = re.compile(
    r'order (number|#|confirmation)|tracking number|your receipt|invoice #'
    r'|payment (received|confirmed|processed)|your (reservation|booking|appointment)'
    r'|shipment (confirmed|shipped|delivered)|your (statement|account statement)',
    re.I
)
BODY_PROMO_RE = re.compile(r'\b\d+%\s*off\b|\bfree shipping\b|\bsale ends\b|\bcoupon\b|\bdiscount code\b', re.I)
# Recruiter: personal opener + role/team language
BODY_RECRUITER_RE = re.compile(
    r'(wanted to reach out|i\'m recruiting|i\'m a recruiter|talent acquisition'
    r'|i came across your (profile|background|experience)'
    r'|open to (exploring|new opportunities|hearing about)'
    r'|exciting (opportunity|role|position) at'
    r'|we\'re (hiring|looking for|building a team)'
    r'|would you be (open|interested|available)'
    r'|engineering (role|position|opportunity|team))',
    re.I
)


def _gmail_service():
    """Lazy-load Gmail service using the existing read-only token."""
    import importlib.util, sys
    # Import authenticate from fetch_unread without executing its main()
    spec = importlib.util.spec_from_file_location(
        'fetch_unread',
        str(CANONICAL_ROOT / 'scripts' / 'fetch_unread.py'),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.authenticate()


def _decode_part(part) -> str:
    import base64
    data = part.get('body', {}).get('data', '')
    if not data:
        return ''
    return base64.urlsafe_b64decode(data + '==').decode('utf-8', errors='replace')


def _strip_html(html: str) -> str:
    """Very lightweight HTML tag stripper — no external deps."""
    # Remove style/script blocks entirely
    html = re.sub(r'<(style|script)[^>]*>.*?</\1>', ' ', html, flags=re.S | re.I)
    # Replace block-level tags with newlines
    html = re.sub(r'<(br|p|div|li|tr)[^>]*>', '\n', html, flags=re.I)
    # Strip remaining tags
    html = re.sub(r'<[^>]+>', ' ', html)
    # Decode common HTML entities
    html = html.replace('&nbsp;', ' ').replace('&zwnj;', '').replace('&amp;', '&')
    html = html.replace('&lt;', '<').replace('&gt;', '>').replace('&#39;', "'")
    # Collapse whitespace
    html = re.sub(r'[ \t]+', ' ', html)
    html = re.sub(r'\n{3,}', '\n\n', html)
    return html.strip()


def _walk_parts(payload) -> tuple:
    """Walk MIME parts, returning (plain_text, html_text)."""
    plain = ''
    html = ''
    parts = payload.get('parts', [])
    if not parts:
        # Single-part message
        ct = payload.get('mimeType', '')
        text = _decode_part(payload)
        if 'plain' in ct:
            plain = text
        elif 'html' in ct:
            html = text
        return plain, html
    for part in parts:
        ct = part.get('mimeType', '')
        if ct == 'text/plain':
            plain += _decode_part(part)
        elif ct == 'text/html':
            html += _decode_part(part)
        elif ct.startswith('multipart/'):
            # Recurse into nested multipart
            p, h = _walk_parts(part)
            plain += p
            html += h
    return plain, html


def _is_garbage(text: str) -> bool:
    """True if text is mostly HTML entities / tracking spacers with no real words."""
    stripped = re.sub(r'&\w+;|https?://\S+|\s', '', text)
    return len(stripped) < 80


def fetch_body_snippet(service, message_id: str) -> str:
    """
    Fetch readable body text for a single message (up to 2000 chars).
    Prefers text/plain; falls back to stripped HTML if plain is empty/garbage.
    Final fallback: Gmail snippet.
    """
    try:
        import base64
        msg = service.users().messages().get(
            userId='me',
            id=message_id,
            format='full',
        ).execute()
        payload = msg.get('payload', {})
        plain, html = _walk_parts(payload)

        # Use plain if it has real content
        if plain and not _is_garbage(plain):
            return plain[:2000]

        # Fall back to stripped HTML
        if html:
            text = _strip_html(html)
            if text and not _is_garbage(text):
                return text[:2000]

        # Last resort: Gmail's pre-computed snippet
        return msg.get('snippet', '')
    except Exception:
        return ''


def classify_with_body(classification: Dict, body: str) -> Dict:
    """
    Upgrade an uncertain classification using body signals.
    Only called when Pass 1 confidence < BODY_FETCH_THRESHOLD.
    Returns a new (or same) classification dict.
    """
    if not body:
        return classification

    has_unsub = bool(BODY_UNSUBSCRIBE_RE.search(body))
    has_transactional = bool(BODY_TRANSACTIONAL_RE.search(body))
    has_promo = bool(BODY_PROMO_RE.search(body))
    has_recruiter = bool(BODY_RECRUITER_RE.search(body))

    conf = classification['confidence']
    reasoning = classification['reasoning']

    # Recruiter outreach — keep, flag clearly
    if has_recruiter and not has_unsub:
        return {**classification,
                'suggested_action': 'keep',
                'label': None,
                'confidence': 0.82,
                'reasoning': 'Body matches recruiter outreach pattern — keep'}

    if has_transactional and not has_promo:
        return {**classification,
                'suggested_action': 'label',
                'label': 'Receipts',
                'archive_after_label': True,
                'confidence': 0.82,
                'reasoning': 'Body contains transactional signals (order/receipt/booking) — label Receipts'}

    if has_unsub and not has_transactional:
        return {**classification,
                'suggested_action': 'archive',
                'label': None,
                'archive_after_label': False,
                'confidence': 0.82,
                'reasoning': 'Body contains unsubscribe link, no transactional content — archive'}

    if has_promo and not has_transactional:
        return {**classification,
                'suggested_action': 'archive',
                'label': None,
                'archive_after_label': False,
                'confidence': 0.80,
                'reasoning': 'Body contains promotional content — archive'}

    # Body didn't help — bump confidence slightly and keep original action
    return {**classification,
            'confidence': min(conf + 0.10, 0.70),
            'reasoning': reasoning + ' (body checked, no clear signal)'}


AI_PROMPT = """\
You are classifying a single email for inbox management. Be terse — reply with JSON only.

Email metadata:
  From: {from_email}
  Subject: {subject}
  Age (days unread): {age_days}

Body snippet:
{body}

Classify this email. Reply with exactly this JSON:
{{
  "action": "archive" | "label" | "keep" | "delete",
  "label": null or a label name (only if action is "label"),
  "archive_after_label": true or false,
  "confidence": 0.0-1.0,
  "reasoning": "one short sentence"
}}

Rules:
- "delete" only for pure spam/marketing with zero personal value
- "label" + archive_after_label=true for transactional (receipts, banking, shipping)
- "label" + archive_after_label=false for time-sensitive (bills due, appointments)
- "keep" for anything personal, actionable, or genuinely useful
- "archive" for newsletters, notifications, old automated emails
- Label options: Receipts, Finance, Investing, Bank/Chase, Bank/BofA, Bank/Citi, Bank/Axis,
  Bills/Utilities, Bills/Subscriptions, Bills/Insurance, School, Shipping, Travel, Jobs,
  Memberships, Home, Finance/Crypto, Shopping, CATEGORY_PROMOTIONS
"""


def classify_with_ai(classification: Dict, email_meta: Dict, body: str, client) -> Dict:
    """
    Use Claude Haiku to classify an email that keyword rules couldn't resolve.
    Only called for emails still uncertain after body-keyword pass.
    """
    if not body:
        return classification
    try:
        prompt = AI_PROMPT.format(
            from_email=email_meta.get('from_email', ''),
            subject=email_meta.get('subject', ''),
            age_days=classification.get('_age_days', '?'),
            body=body[:1500],
        )
        message = client.messages.create(
            max_tokens=256,
            messages=[{'role': 'user', 'content': prompt}],
        )
        raw = message.content[0].text.strip()
        # Strip markdown fences if present
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.I)
        raw = re.sub(r'\s*```$', '', raw)
        result = json.loads(raw)
        return {
            'message_id': classification['message_id'],
            'thread_id': classification['thread_id'],
            'suggested_action': result.get('action', classification['suggested_action']),
            'label': result.get('label', classification.get('label')),
            'archive_after_label': result.get('archive_after_label', classification.get('archive_after_label', False)),
            'confidence': float(result.get('confidence', 0.82)),
            'reasoning': result.get('reasoning', 'AI classification') + ' [haiku]',
        }
    except Exception as e:
        # If anything goes wrong, return the original rather than crashing
        return {**classification, 'reasoning': classification['reasoning'] + f' (ai-err: {e})'}


def run_body_pass(classifications: List[Dict], threshold: float = 0.75,
                  with_ai: bool = False, email_meta_map: Dict = None) -> List[Dict]:
    """
    Pass 2: for emails below `threshold` confidence, fetch body and re-classify.
    If with_ai=True and still uncertain after keyword pass, call Claude Haiku.
    Returns updated classifications list.
    """
    def _is_generic(c: Dict) -> bool:
        """True if the classification looks like a heuristic fallback rather than a specific rule match.
        These are candidates for AI review even if confidence is 0.75–0.84."""
        reasoning = c.get('reasoning', '').lower()
        generic_signals = [
            'uncertain', 'manual review', 'very old unread', 'automated notification',
            'newsletter from', 'recent newsletter', 'promotional email',
            'body checked, no clear signal', 'body contains unsubscribe',
            'auto-label:', 'misc',
        ]
        return any(s in reasoning for s in generic_signals)

    # AI threshold is higher (0.85) for generic/uncertain classifications
    # so emails in the 0.75-0.84 confidence band still get AI eyes if they look heuristic
    ai_threshold = 0.85 if with_ai else threshold
    candidates = [c for c in classifications
                  if c['confidence'] < threshold
                  or (with_ai and c['confidence'] < ai_threshold and _is_generic(c))]
    if not candidates:
        print("  No uncertain emails — skipping body fetch pass.")
        return classifications

    print(f"  Body-fetch pass: {len(candidates)} emails below {threshold:.0%} confidence...")

    try:
        service = _gmail_service()
    except Exception as e:
        print(f"  ⚠️  Could not authenticate for body fetch: {e}")
        return classifications

    # Set up AI client if requested
    ai_client = None
    if with_ai:
        try:
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location(
                'ai_client', str(CANONICAL_ROOT / 'scripts' / 'ai_client.py'))
            _mod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            ai_client = _mod.make_client()
            print(f"  🤖 AI pass enabled (model: {_mod.DEFAULT_MODEL})")
        except Exception as e:
            print(f"  ⚠️  AI client unavailable — skipping AI pass ({e})")

    updated = {c['message_id']: c for c in classifications}
    fetched = 0
    upgraded = 0
    ai_calls = 0

    for c in candidates:
        mid = c['message_id']
        body = fetch_body_snippet(service, mid)
        fetched += 1
        new_c = classify_with_body(c, body)

        # If still uncertain after keyword body pass, try AI
        if ai_client and new_c['confidence'] < 0.75 and email_meta_map:
            meta = email_meta_map.get(mid, {})
            new_c = classify_with_ai(new_c, meta, body, ai_client)
            ai_calls += 1

        if new_c['confidence'] != c['confidence'] or new_c['suggested_action'] != c['suggested_action']:
            upgraded += 1
        updated[mid] = new_c
        if fetched % 10 == 0:
            print(f"    ...{fetched}/{len(candidates)} fetched, {upgraded} upgraded, {ai_calls} AI calls")

    print(f"  ✅ Body pass done: {fetched} fetched, {upgraded} upgraded, {ai_calls} AI calls")
    return list(updated.values())


_DOMAIN_LABEL_MAP = [
    # (substring_in_from_email, label)
    ('parentsquare', 'School'), ('classdojo', 'School'), ('dublinusd', 'School'),
    ('dpie.org', 'School'), ('teamsnap', 'School'), ('peachjar', 'School'),
    ('chase.com', 'Bank/Chase'), ('bankofamerica', 'Bank/BofA'), ('citi.com', 'Bank/Citi'),
    ('axisbank', 'Bank/Axis'), ('alerts.sbi', 'Bank/Axis'), ('penfed', 'Bank/PenFed'),
    ('fidelity', 'Investing'), ('vanguard', 'Investing'), ('fundrise', 'Investing'),
    ('proxyvote', 'Investing'), ('investordelivery', 'Investing'), ('camsonline', 'Investing'),
    ('kfintech', 'Investing'), ('tatamf', 'Investing'), ('seshaasai', 'Investing'),
    ('coinbase', 'Finance/Crypto'), ('cointracker', 'Finance/Crypto'),
    ('monarchmoney', 'Finance'), ('monarch.com', 'Finance'), ('experian', 'Finance'),
    ('wageworks', 'Finance'), ('bayareafastrak', 'Finance'),
    ('pge.com', 'Bills/Utilities'), ('billpay.pge', 'Bills/Utilities'),
    ('xfinity', 'Bills/Utilities'), ('comcast', 'Bills/Utilities'), ('dsrsd', 'Bills/Utilities'),
    ('fedex', 'Shipping'), ('usps', 'Shipping'), ('ups.com', 'Shipping'),
    ('airbnb', 'Travel'), ('aircanada', 'Travel'), ('united', 'Travel'),
    ('iete', 'Memberships'), ('ieee', 'Memberships'), ('sigmaxi', 'Memberships'),
    ('rachio', 'Home'), ('nest.com', 'Home'), ('ring.com', 'Home'),
    ('costco', 'Shopping'), ('walmart', 'Shopping'), ('amazon', 'Receipts'),
    ('paypal', 'Receipts'), ('starbucks', 'Receipts'), ('brightwheel', 'Receipts'),
    ('homedepot', 'Receipts'), ('uber', 'Receipts'), ('lyft', 'Receipts'),
    ('linkedin', 'Jobs'), ('meta.com', 'Jobs'), ('rivierapartners', 'Jobs'),
]


def _infer_label(from_email: str, reasoning: str) -> str:
    """Guess a label from the sender domain or reasoning string."""
    fe = from_email.lower()
    for substr, label in _DOMAIN_LABEL_MAP:
        if substr in fe:
            return label
    # Fallback: scan reasoning
    r = reasoning.lower()
    if 'school' in r or 'parentsquare' in r or 'classdojo' in r:
        return 'School'
    if 'bank' in r or 'chase' in r or 'citi' in r:
        return 'Finance'
    if 'receipt' in r or 'transactional' in r or 'order' in r:
        return 'Receipts'
    if 'shipping' in r or 'fedex' in r or 'usps' in r or 'deliver' in r:
        return 'Shipping'
    if 'investing' in r or 'mutual fund' in r or 'vanguard' in r:
        return 'Investing'
    if 'bills' in r or 'utility' in r or 'pge' in r or 'xfinity' in r:
        return 'Bills/Utilities'
    return 'Misc'


def _enrich_archives(classifications: List[Dict], email_meta_map: Dict) -> List[Dict]:
    """Convert bare archive actions into label+archive so emails always get a tag."""
    enriched = []
    for c in classifications:
        if c.get('suggested_action') == 'archive' and not c.get('label'):
            meta = email_meta_map.get(c['message_id'], {})
            label = _infer_label(meta.get('from_email', ''), c.get('reasoning', ''))
            c = {**c,
                 'suggested_action': 'label',
                 'label': label,
                 'archive_after_label': True,
                 'reasoning': c.get('reasoning', '') + f' [auto-label: {label}]'}
        enriched.append(c)
    return enriched


def classify_batch(input_csv: Path, output_json: Path, with_body: bool = False,
                   with_ai: bool = False):
    """Classify a batch of emails from CSV."""
    rules = load_classification_rules()

    # Read emails
    emails = []
    with open(input_csv, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        emails = list(reader)

    print(f"Classifying {len(emails)} emails from {input_csv}...")

    # Build meta map for AI pass (message_id -> row)
    email_meta_map = {e['message_id']: e for e in emails}

    # Classify each email
    classifications = []
    for email in emails:
        classification = classify_email(email, rules)
        classification.setdefault('archive_after_label', False)
        # Stash age_days for AI prompt (not saved to output)
        from email.utils import parsedate_to_datetime
        try:
            dt = parsedate_to_datetime(email.get('date', ''))
            from datetime import timezone
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            classification['_age_days'] = (datetime.now(timezone.utc) - dt).days
        except Exception:
            classification['_age_days'] = 0
        classifications.append(classification)

    # Optional Pass 2: body fetch (+ optional AI) for uncertain emails
    if with_body or with_ai:
        classifications = run_body_pass(classifications, with_ai=with_ai,
                                        email_meta_map=email_meta_map)

    # Strip internal-only fields before saving
    for c in classifications:
        c.pop('_age_days', None)

    # Enrich bare archive actions with a label so emails always get tagged
    classifications = _enrich_archives(classifications, email_meta_map)

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
    from profile_loader import add_profile_arg, load_profile

    parser = argparse.ArgumentParser(description='Classify emails from CSV')
    add_profile_arg(parser)
    parser.add_argument('--input', required=True, help='Input CSV file')
    parser.add_argument('--output', required=True, help='Output JSON file')
    parser.add_argument('--with-body', action='store_true',
                        help='Fetch body for uncertain emails (requires Gmail auth)')
    parser.add_argument('--with-ai', action='store_true',
                        help='Use Claude Haiku for emails still uncertain after body pass (requires ANTHROPIC_API_KEY)')
    args = parser.parse_args()

    # Apply profile overrides
    global _PROFILE_RULES_FILE, CREDENTIALS_FILE, TOKEN_FILE
    if args.profile:
        profile = load_profile(args.profile)
        _PROFILE_RULES_FILE = profile['rules_path']

    input_csv = Path(args.input)
    output_json = Path(args.output)

    if not input_csv.exists():
        print(f"ERROR: Input file not found: {input_csv}")
        sys.exit(1)

    classify_batch(input_csv, output_json, with_body=args.with_body or args.with_ai,
                   with_ai=args.with_ai)

if __name__ == '__main__':
    main()
