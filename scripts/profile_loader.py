#!/usr/bin/env python3
"""
Profile loader — resolves paths and config for a named profile.

Usage:
    from profile_loader import load_profile
    profile = load_profile('work')  # or 'personal', or None for default

All paths in the returned dict are resolved to absolute Path objects.
"""

import json
import sys
from pathlib import Path

CANONICAL_ROOT = Path(__file__).parent.parent
PROFILES_DIR = CANONICAL_ROOT / 'profiles'

DEFAULT_PROFILE = 'personal'

# Socket timeout (seconds) for every Gmail API call. Without this, httplib2's
# socket has NO timeout — a connection that stalls (e.g. across a laptop
# sleep/wake) blocks forever, wedging the whole daily run and starving the
# launchd schedule. With it, a stalled call raises instead of hanging, so the
# run fails fast and the next scheduled run starts clean.
GMAIL_HTTP_TIMEOUT = 120


def build_gmail_service(creds):
    """Build a Gmail API client whose HTTP transport has a socket timeout.

    Use this instead of build('gmail', 'v1', credentials=creds) so a hung
    network call can't block a run indefinitely. Imports the transport libs
    lazily so this module stays importable for path-only callers.
    """
    import httplib2
    import google_auth_httplib2
    from googleapiclient.discovery import build

    authed_http = google_auth_httplib2.AuthorizedHttp(
        creds, http=httplib2.Http(timeout=GMAIL_HTTP_TIMEOUT))
    return build('gmail', 'v1', http=authed_http)


def load_profile(name: str = None) -> dict:
    """Load a profile by name. Returns resolved config dict.

    Resolution order:
      1. profiles/{name}.json (gitignored, real config)
      2. profiles/{name}.example.json (committed, template)
      3. Falls back to legacy hardcoded paths if no profile file exists and name is 'personal'

    All relative paths in the profile are resolved relative to CANONICAL_ROOT.
    """
    name = name or DEFAULT_PROFILE

    profile_file = PROFILES_DIR / f'{name}.json'
    if not profile_file.exists():
        example_file = PROFILES_DIR / f'{name}.example.json'
        if example_file.exists():
            profile_file = example_file
        elif name == 'personal':
            return _legacy_personal_profile()
        else:
            print(f"ERROR: Profile '{name}' not found at {profile_file}")
            print(f"  Copy {PROFILES_DIR / f'{name}.example.json'} to {profile_file} and configure it.")
            sys.exit(1)

    with open(profile_file, 'r') as f:
        raw = json.load(f)

    return _resolve_paths(raw)


def _resolve_paths(raw: dict) -> dict:
    """Resolve relative paths in profile config to absolute paths.

    Paths can be absolute or relative:
      - Absolute paths (starting with / or ~) are used as-is (expanded).
      - Relative paths are resolved relative to CANONICAL_ROOT.
    """
    profile = dict(raw)

    profile['_root'] = CANONICAL_ROOT

    def _resolve(path_str: str, default: str) -> Path:
        p = path_str or default
        path = Path(p).expanduser()
        if path.is_absolute():
            return path
        return CANONICAL_ROOT / path

    name = profile.get('name', 'personal')

    # Token paths — supports both pickle and JSON formats
    tokens = profile.get('tokens', {})
    profile['token_readonly'] = _resolve(tokens.get('readonly', ''), 'token.pickle')
    profile['token_modify'] = _resolve(tokens.get('modify', ''), 'token-modify.pickle')

    # Credentials
    profile['credentials_path'] = _resolve(profile.get('credentials', ''), 'credentials.json')

    # Data directory — always data/{profile_name}/
    data_rel = profile.get('data_dir', f'data/{name}')
    profile['data_path'] = _resolve(data_rel, f'data/{name}')

    # Rules file
    rules_rel = profile.get('rules_file', f'scripts/classification_rules.{name}.json')
    profile['rules_path'] = _resolve(rules_rel, f'scripts/classification_rules.{name}.json')

    # Logs directory — per-profile: logs/{profile_name}/
    profile['logs_path'] = CANONICAL_ROOT / 'logs' / name

    return profile


def _legacy_personal_profile() -> dict:
    """Fallback for personal profile when no profiles/ file exists."""
    return {
        'name': 'personal',
        'description': 'Personal Gmail (legacy fallback)',
        '_root': CANONICAL_ROOT,
        'token_readonly': CANONICAL_ROOT / 'token.pickle',
        'token_modify': CANONICAL_ROOT / 'token-modify.pickle',
        'credentials_path': CANONICAL_ROOT / 'credentials.json',
        'data_path': CANONICAL_ROOT / 'data' / 'personal',
        'rules_path': CANONICAL_ROOT / 'scripts' / 'classification_rules.personal.json',
        'logs_path': CANONICAL_ROOT / 'logs' / 'personal',
        'fetch': {'limit': 300, 'query': 'label:inbox is:unread category:primary'},
        'classify': {
            'with_body': True,
            'with_ai': True,
            'confidence_threshold': 0.75,
            'delete_threshold': 0.97,
        },
        'extractors': ['school_events', 'job_leads'],
        'schedule': {'frequency_hours': 8, 'run_at_load': False},
    }


def add_profile_arg(parser):
    """Add --profile argument to an argparse parser."""
    parser.add_argument(
        '--profile',
        default=None,
        help='Profile name (e.g., personal, work). Defaults to personal.',
    )
