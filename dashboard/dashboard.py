#!/usr/bin/env python3
"""Local dashboard server for the Gmail cleanup project.

Python stdlib only (http.server). Serves the single-page dashboard and the
JSON aggregation APIs defined in dashboard/CONTRACT.md.

Run:  python dashboard/dashboard.py  ->  http://localhost:5001
"""

import csv
import glob
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"
RULES_PATH = PROJECT_ROOT / "scripts" / "classification_rules.json"
TEMPLATE_PATH = Path(__file__).parent / "templates" / "dashboard.html"


def _profile_data_dir(profile: str = None) -> Path:
    """Return data directory for a given profile. Always data/{profile}/."""
    name = profile or 'personal'
    return DATA_DIR / name


def _profile_logs_dir(profile: str = None) -> Path:
    """Return logs directory for a given profile. Always logs/{profile}/."""
    name = profile or 'personal'
    return LOGS_DIR / name


def _profile_message_ids(profile: str = None) -> set:
    """Return all message_ids that exist in a profile's batch CSVs."""
    data_dir = _profile_data_dir(profile)
    ids = set()
    for csv_path in glob.glob(str(data_dir / "batch-*.csv")):
        if 'temp' in csv_path or 'janfeb' in csv_path:
            continue
        try:
            with open(csv_path, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    mid = (row.get("message_id") or "").strip()
                    if mid:
                        ids.add(mid)
        except (OSError, csv.Error):
            pass
    return ids

HOST = "localhost"
PORT = 5001

_BATCH_RE = re.compile(r"batch-(\d+)-classified\.json$")


# ---------------------------------------------------------------------------
# Data loading / aggregation helpers
# ---------------------------------------------------------------------------

def discover_batches(profile: str = None):
    """Return sorted list of batch ids (e.g. '001') that have BOTH a
    classified JSON and a sibling CSV."""
    data_dir = _profile_data_dir(profile)
    batches = []
    for path in glob.glob(str(data_dir / "batch-*-classified.json")):
        m = _BATCH_RE.search(os.path.basename(path))
        if not m:
            continue
        bid = m.group(1)
        if (data_dir / f"batch-{bid}.csv").exists():
            batches.append(bid)
    return sorted(batches)


def load_csv(batch_id, profile: str = None):
    """Return {message_id: row_dict} for a batch CSV. Empty dict if missing/bad."""
    data_dir = _profile_data_dir(profile)
    rows = {}
    csv_path = data_dir / f"batch-{batch_id}.csv"
    try:
        with open(csv_path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                mid = (row.get("message_id") or "").strip()
                if mid:
                    rows[mid] = row
    except (OSError, csv.Error):
        pass
    return rows


def load_classified(batch_id, profile: str = None):
    """Return list of classification dicts for a batch. Empty list if missing/bad."""
    data_dir = _profile_data_dir(profile)
    json_path = data_dir / f"batch-{batch_id}-classified.json"
    try:
        with open(json_path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def load_rule_reasonings():
    """Set of reasoning strings declared in classification_rules.json sender_rules."""
    reasonings = set()
    try:
        with open(RULES_PATH, encoding="utf-8") as fh:
            rules = json.load(fh)
        for rule in rules.get("sender_rules", []):
            if isinstance(rule, dict) and rule.get("reasoning"):
                reasonings.add(rule["reasoning"])
    except (OSError, ValueError):
        pass
    return reasonings


def count_executed(profile: str = None):
    """Count unique successfully executed message_ids; scoped to profile if set."""
    return len(executed_message_ids(profile))


def executed_message_ids(profile: str = None):
    """Set of message_ids that were SUCCESSFULLY executed (any action) per the
    audit logs. Reads from logs/{profile}/ directory."""
    logs_dir = _profile_logs_dir(profile)
    done = set()
    for path in glob.glob(str(logs_dir / "actions-*.jsonl")):
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    if entry.get("status") == "success" and entry.get("message_id"):
                        done.add(entry["message_id"])
        except OSError:
            pass
    return done


def compute_age_days(date_str):
    """Integer days between the parsed email date and now (tz-aware).
    Returns None if the date cannot be parsed."""
    if not date_str:
        return None
    try:
        dt = parsedate_to_datetime(date_str)
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    tz = dt.tzinfo or timezone.utc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    now = datetime.now(tz)
    return (now - dt).days


def domain_of(email_addr):
    """Lowercased domain (part after '@'); '' if absent."""
    if not email_addr or "@" not in email_addr:
        return ""
    return email_addr.rsplit("@", 1)[1].strip().lower()


# ---------------------------------------------------------------------------
# API builders
# ---------------------------------------------------------------------------

def build_timeline(profile: str = None):
    """Return per-day action counts from audit logs for the timeline chart."""
    logs_dir = _profile_logs_dir(profile)

    by_date: dict = defaultdict(lambda: defaultdict(int))
    seen: set = set()
    for path in glob.glob(str(logs_dir / "actions-*.jsonl")):
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    if entry.get("status") != "success":
                        continue
                    mid = entry.get("message_id", "")
                    action = entry.get("action", "")
                    key = f"{mid}:{action}"
                    if key in seen:
                        continue
                    seen.add(key)
                    ts_raw = entry.get("timestamp", "")
                    if not ts_raw or not action:
                        continue
                    try:
                        utc_dt = datetime.strptime(ts_raw[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                        local_date = utc_dt.astimezone().date().isoformat()
                    except ValueError:
                        local_date = ts_raw[:10]
                    by_date[local_date][action] += 1
        except OSError:
            pass
    dates = sorted(by_date)
    return {
        "dates": dates,
        "archive": [by_date[d].get("archive", 0) for d in dates],
        "label":   [by_date[d].get("label",   0) for d in dates],
        "delete":  [by_date[d].get("delete",  0) for d in dates],
    }


def build_stats(profile: str = None):
    batches = discover_batches(profile)
    rule_reasonings = load_rule_reasonings()

    fetched = 0
    classified_count = 0
    actions = Counter()
    confidence_buckets = {"high": 0, "medium": 0, "low": 0}
    labels: Counter = Counter()  # dynamic — counts whatever labels are in the data
    rule_counts = Counter()
    sender_counts = Counter()
    sender_actions = defaultdict(Counter)
    matched_rule = 0
    fell_through = 0
    seen_message_ids: set = set()  # deduplicate across batches

    for bid in batches:
        csv_rows = load_csv(bid, profile)
        # Count only unique message_ids for fetched total
        new_ids = set(csv_rows.keys()) - seen_message_ids
        fetched += len(new_ids)
        for entry in load_classified(bid, profile):
            mid = entry.get("message_id")
            if mid in seen_message_ids:
                continue  # duplicate — already counted in an earlier batch
            meta = csv_rows.get(mid)
            if meta is None:
                continue
            seen_message_ids.add(mid)
            classified_count += 1

            action = entry.get("suggested_action")
            if action:
                actions[action] += 1

            conf = entry.get("confidence")
            if isinstance(conf, (int, float)):
                if conf >= 0.80:
                    confidence_buckets["high"] += 1
                elif conf >= 0.60:
                    confidence_buckets["medium"] += 1
                else:
                    confidence_buckets["low"] += 1

            label = entry.get("label")
            if label:
                labels[label] += 1

            reasoning = entry.get("reasoning") or ""
            if reasoning:
                rule_counts[reasoning] += 1

            if reasoning in rule_reasonings:
                matched_rule += 1
            else:
                fell_through += 1

            dom = domain_of(meta.get("from_email"))
            if dom:
                sender_counts[dom] += 1
                if action:
                    sender_actions[dom][action] += 1

    executed = count_executed(profile)
    timeline = build_timeline(profile)

    top_rules = [{"reasoning": r, "count": c}
                 for r, c in rule_counts.most_common(10)]

    top_senders = []
    for dom, c in sender_counts.most_common(10):
        top_senders.append({
            "domain": dom,
            "count": c,
            "actions": dict(sender_actions[dom]),
        })

    return {
        "totals": {
            "fetched": fetched,
            "classified": classified_count,
            "executed": executed,
            "remaining": actions.get("keep", 0),  # unexecuted keeps = emails intentionally left in inbox
        },
        "actions": {
            "archive": actions.get("archive", 0),
            "delete": actions.get("delete", 0),
            "label": actions.get("label", 0),
            "keep": actions.get("keep", 0),
        },
        "confidence_buckets": confidence_buckets,
        "labels": labels,
        "top_rules": top_rules,
        "top_senders": top_senders,
        "rule_coverage": {
            "matched_rule": matched_rule,
            "fell_through_heuristic": fell_through,
        },
        "timeline": timeline,
    }


def build_daily_run(profile: str = None):
    """Parse the most recent daily-run log + audit entries from the last 24h."""
    import glob as _glob
    from datetime import timedelta

    data_dir = _profile_data_dir(profile)

    # ── Find most recent daily run log ───────────────────────────────────────
    logs_dir = _profile_logs_dir(profile)
    profile_name = profile or 'personal'
    run_logs = sorted(_glob.glob(str(logs_dir / f'daily-run-{profile_name}-*.log')))
    if not run_logs:
        run_logs = sorted(_glob.glob(str(logs_dir / 'daily-run-*.log')))
    last_run_time = None
    run_summary = {'fetched_batch': None, 'new_actions': 0, 'log_lines': []}

    if run_logs:
        latest = run_logs[-1]
        # Extract timestamp from filename: daily-run-YYYYMMDD-HHMMSS.log
        stem = Path(latest).stem  # daily-run-20260601-183557
        parts = stem.split('-')
        try:
            dt_str = parts[2] + parts[3]  # 20260601183557
            last_run_time = datetime.strptime(dt_str, '%Y%m%d%H%M%S')
        except (IndexError, ValueError):
            last_run_time = None

        with open(latest, encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                run_summary['log_lines'].append(line)
                if 'New safe actions executed:' in line:
                    try:
                        run_summary['new_actions'] = int(line.split(':')[-1].strip())
                    except ValueError:
                        pass
                if 'batch-' in line and 'Fetching' in line:
                    import re as _re
                    m = _re.search(r'batch-(\d+)', line)
                    if m:
                        run_summary['fetched_batch'] = m.group(1)

    # ── Audit entries since last run started ─────────────────────────────────
    if last_run_time is not None:
        cutoff = last_run_time
    else:
        cutoff = datetime.utcnow() - timedelta(hours=24)
    recent = Counter()
    recent_labels: Counter = Counter()
    recent_senders: Counter = Counter()

    for path in _glob.glob(str(logs_dir / 'actions-*.jsonl')):
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
                    if entry.get('status') != 'success':
                        continue
                    ts_str = entry.get('timestamp', '')
                    try:
                        ts = datetime.strptime(ts_str[:19], '%Y-%m-%dT%H:%M:%S')
                    except ValueError:
                        continue
                    if ts < cutoff:
                        continue
                    action = entry.get('action', '')
                    recent[action] += 1
                    if entry.get('label_applied'):
                        recent_labels[entry['label_applied']] += 1
        except OSError:
            pass

    # ── Pending deletes count ─────────────────────────────────────────────────
    done = set()
    for path in _glob.glob(str(logs_dir / 'actions-*.jsonl')):
        try:
            with open(path, encoding='utf-8') as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                        if e.get('status') == 'success' and e.get('message_id'):
                            done.add(e['message_id'])
                    except ValueError:
                        continue
        except OSError:
            pass

    pending_delete_ids = set()
    for path in _glob.glob(str(data_dir / 'batch-*-classified.json')):
        if 'temp' in path or 'janfeb' in path:
            continue
        try:
            with open(path, encoding='utf-8') as fh:
                for e in json.load(fh):
                    if e.get('suggested_action') == 'delete' and e.get('message_id') not in done:
                        pending_delete_ids.add(e['message_id'])
        except (OSError, ValueError):
            pass
    pending_deletes = len(pending_delete_ids)

    # Find most recent batch fetch time (most recently modified batch CSV)
    batch_csvs = _glob.glob(str(data_dir / 'batch-[0-9][0-9][0-9].csv'))
    data_last_fetched = None
    if batch_csvs:
        try:
            latest = max(batch_csvs, key=os.path.getmtime)
            data_last_fetched = datetime.fromtimestamp(os.path.getmtime(latest)).isoformat()
        except OSError:
            pass

    return {
        'last_run_time': last_run_time.isoformat() if last_run_time else None,
        'new_actions': run_summary['new_actions'],
        'fetched_batch': run_summary['fetched_batch'],
        'data_last_fetched': data_last_fetched,
        'last_24h': {
            'archive': recent.get('archive', 0),
            'label': recent.get('label', 0),
            'delete': recent.get('delete', 0),
        },
        'last_24h_labels': dict(recent_labels.most_common(8)),
        'pending_deletes': pending_deletes,
    }


def build_aging_forecast(profile: str = None):
    """For unexecuted 'keep' emails, forecast how many will age out (archive/label)
    if we re-classify at +1d, +3d, +7d, +14d, +30d from now."""
    from datetime import timedelta

    data_dir = _profile_data_dir(profile)
    logs_dir = _profile_logs_dir(profile)

    rules_data = {}
    try:
        with open(RULES_PATH, encoding='utf-8') as fh:
            rules_data = json.load(fh)
    except (OSError, ValueError):
        return {}

    done = set()
    for path in glob.glob(str(logs_dir / 'actions-*.jsonl')):
        try:
            with open(path, encoding='utf-8') as fh:
                for line in fh:
                    line = line.strip()
                    if not line: continue
                    try:
                        e = json.loads(line)
                        if e.get('status') == 'success' and e.get('message_id'):
                            done.add(e['message_id'])
                    except ValueError:
                        continue
        except OSError:
            pass

    seen = set()
    keeps = []  # (message_id, age_days, from_email)
    for batch_path in sorted(glob.glob(str(data_dir / 'batch-*-classified.json'))):
        if 'temp' in batch_path or 'janfeb' in batch_path:
            continue
        batch = os.path.basename(batch_path).replace('batch-','').replace('-classified.json','')
        csv_path = data_dir / f'batch-{batch}.csv'
        try:
            clf_map = {e['message_id']: e for e in json.load(open(batch_path, encoding='utf-8'))}
            with open(csv_path, newline='', encoding='utf-8') as fh:
                for row in csv.DictReader(fh):
                    mid = row.get('message_id','')
                    if mid in seen or mid in done: continue
                    c = clf_map.get(mid,{})
                    if c.get('suggested_action') != 'keep': continue
                    seen.add(mid)
                    age = compute_age_days(row.get('date',''))
                    keeps.append((mid, age or 0, row.get('from_email','')))
        except (OSError, ValueError):
            continue

    sender_rules = rules_data.get('sender_rules', [])

    def would_archive_at(from_email, current_age, future_age):
        """Return True if this email would switch from keep→archive/label at future_age."""
        from_email_lower = from_email.lower()
        for rule in sender_rules:
            if 'action' not in rule: continue
            from_match = rule.get('from_match','').lower()
            if from_match and from_match not in from_email_lower: continue
            min_age = rule.get('min_age_days')
            max_age = rule.get('max_age_days')
            # Would this rule fire at future_age but NOT at current_age?
            if min_age is not None:
                fires_now = current_age >= min_age
                fires_future = future_age >= min_age
                if fires_future and not fires_now and rule['action'] in ('archive','label'):
                    return True
        return False

    horizons = [1, 3, 7, 14, 30, 60]
    forecast = {}
    now_age_map = {mid: age for mid, age, _ in keeps}

    for days_ahead in horizons:
        count = 0
        for mid, age, from_email in keeps:
            future_age = age + days_ahead
            if would_archive_at(from_email, age, future_age):
                count += 1
        forecast[f'+{days_ahead}d'] = count

    return {'keeps_total': len(keeps), 'forecast': forecast}


def build_school_events(days_ahead: int = 60, profile: str = None):
    """Return upcoming school events from data/school-events.json."""
    data_dir = _profile_data_dir(profile)
    events_file = data_dir / 'school-events.json'
    if not events_file.exists():
        return {'events': [], 'total_extracted': 0}

    try:
        data = json.loads(events_file.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'events': [], 'total_extracted': 0}

    today = datetime.now(timezone.utc).date()
    cutoff = today + timedelta(days=days_ahead)

    upcoming = []
    total = 0
    for mid, events in data.items():
        for e in (events or []):
            total += 1
            date_str = e.get('date')
            if not date_str:
                continue
            try:
                from datetime import date as date_cls
                event_date = datetime.strptime(date_str[:10], '%Y-%m-%d').date()
                if today <= event_date <= cutoff:
                    upcoming.append({
                        'date': str(event_date),
                        'event': e.get('event', ''),
                        'urgent': e.get('urgent', False),
                        'category': e.get('category', 'other'),
                        'source_subject': e.get('_subject', ''),
                        'source_date': e.get('_email_date', ''),
                    })
            except (ValueError, TypeError):
                continue

    # Deduplicate: same date + similar event text → keep the most urgent / most specific
    def _normalise(text: str) -> str:
        import re as _re
        t = text.lower().strip()
        # strip all numbers, times, punctuation
        t = _re.sub(r'\d+[:\d]*\s*(am|pm)?', '', t, flags=_re.I)
        t = _re.sub(r'[^a-z ]', ' ', t)
        t = _re.sub(r'\b(at|the|a|an|for|of|and|or|is|in|on|by|pm|am|grades|grade|day)\b', ' ', t)
        t = _re.sub(r'\s+', ' ', t).strip()
        # sort words so "early release dismissal" == "dismissal early release"
        words = sorted(set(t.split()))
        return ' '.join(words[:8])

    def _word_set(text: str) -> set:
        import re as _re
        t = text.lower()
        t = _re.sub(r'\d+[:\d]*\s*(am|pm)?', '', t, flags=_re.I)
        t = _re.sub(r'[^a-z ]', ' ', t)
        stop = {'at','the','a','an','for','of','and','or','is','in','on','by',
                'pm','am','grades','grade'}
        return {w for w in t.split() if len(w) > 2 and w not in stop}

    # Anchor phrases — if both events contain ANY of these on the same date, they're the same event
    _ANCHORS = [
        {'last day', 'school'},
        {'minimum day', 'dismissal'},
        {'early release', 'dismissal'},
        {'field day', 'volunteer'},
        {'summer break', 'begins'},
        {'no school'},
    ]

    def _has_anchor(text: str, anchor: set) -> bool:
        t = text.lower()
        return all(w in t for w in anchor)

    def _similar(a: str, b: str, threshold: float = 0.45) -> bool:
        """True if two event strings share enough words to be considered the same event."""
        wa, wb = _word_set(a.lower()), _word_set(b.lower())
        if not wa or not wb:
            return False
        overlap = len(wa & wb)
        smaller = min(len(wa), len(wb))
        # Very short phrases (1-2 meaningful words): require exact match
        if smaller <= 2:
            return overlap == smaller
        if overlap / smaller >= threshold:
            return True
        # Check anchors — any shared anchor phrase means same event
        for anchor in _ANCHORS:
            if _has_anchor(a, anchor) and _has_anchor(b, anchor):
                return True
        return False

    # First pass: exact-normalised dedup
    seen_norm: set = set()
    pass1 = []
    for e in upcoming:
        norm_key = (e['date'], _normalise(e['event']))
        if norm_key not in seen_norm:
            seen_norm.add(norm_key)
            pass1.append(e)

    # Second pass: semantic dedup within the same date
    deduped = []
    for e in pass1:
        is_dup = False
        for existing in deduped:
            if existing['date'] == e['date'] and _similar(e['event'], existing['event']):
                # Keep whichever is more specific (longer) or more urgent
                if len(e['event']) > len(existing['event']) or (e.get('urgent') and not existing.get('urgent')):
                    deduped[deduped.index(existing)] = e
                is_dup = True
                break
        if not is_dup:
            deduped.append(e)

    deduped.sort(key=lambda x: (x['date'], 0 if x.get('urgent') else 1))
    return {'events': deduped, 'total_extracted': total}


def build_job_leads(profile: str = None):
    """Return structured job leads from data/job-leads.json."""
    data_dir = _profile_data_dir(profile)
    leads_file = data_dir / 'job-leads.json'
    if not leads_file.exists():
        return {'leads': [], 'total': 0}
    try:
        data = json.loads(leads_file.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'leads': [], 'total': 0}

    leads = [v for v in data.values() if v and not v.get('not_job')]
    def _parse_lead_date(lead):
        from email.utils import parsedate_to_datetime as _p
        try:
            return _p(lead.get('_date', '')).timestamp()
        except Exception:
            return 0.0

    leads.sort(key=_parse_lead_date, reverse=True)
    return {'leads': leads, 'total': len(leads)}


def build_digest(profile: str = None):
    """Build today's full inbox digest: what arrived, what was auto-handled,
    what's still waiting. Works for any profile (defaults to personal).

    Returns:
      {
        "profile": "personal",
        "date": "2026-06-08",
        "auto_handled": [ {action, label, subject, from, reasoning, timestamp}, ... ],
        "still_in_inbox": [ {subject, from, age_days, confidence, reasoning}, ... ],
        "pending_review": [ {subject, from, age_days, confidence, reasoning}, ... ],
        "meeting_notes": [ {meeting_title, date, summary}, ... ],
        "summary": { "archived": N, "labeled": N, "kept": N, "pending": N }
      }
    """
    # Determine data directory based on profile
    profile = profile or 'personal'
    profile_data_dir = _profile_data_dir(profile)
    logs_dir = _profile_logs_dir(profile)
    today = datetime.now().date().isoformat()

    # Gather today's executed actions from audit logs
    # Log filenames use local time, so match against local date
    auto_handled = []
    today_prefix = today.replace('-', '')  # 20260608

    for path in sorted(glob.glob(str(logs_dir / f"actions-{today_prefix}*.jsonl"))):
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    if entry.get("status") != "success":
                        continue
                    auto_handled.append({
                        "action": entry.get("action", ""),
                        "label": entry.get("label_applied", ""),
                        "message_id": entry.get("message_id", ""),
                        "reasoning": entry.get("reasoning", ""),
                        "timestamp": entry.get("timestamp", ""),
                    })
        except OSError:
            pass

    # Enrich auto_handled with subject/sender from batch CSVs
    all_csv_rows = {}
    for csv_path in glob.glob(str(profile_data_dir / "batch-*.csv")):
        if 'temp' in csv_path or 'janfeb' in csv_path:
            continue
        try:
            with open(csv_path, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    mid = (row.get("message_id") or "").strip()
                    if mid:
                        all_csv_rows[mid] = row
        except (OSError, csv.Error):
            pass

    for item in auto_handled:
        meta = all_csv_rows.get(item["message_id"], {})
        item["subject"] = meta.get("subject", "")
        item["from_email"] = meta.get("from_email", "")
        item["from_name"] = meta.get("from_name", "")
        item["email_date"] = meta.get("date", "")
        item["age_days"] = compute_age_days(meta.get("date", ""))

    # Gather emails still classified as 'keep' (in inbox) and 'uncertain'
    done_ids = executed_message_ids(profile)
    still_in_inbox = []
    pending_review = []

    for batch_path in sorted(glob.glob(str(profile_data_dir / "batch-*-classified.json"))):
        if 'temp' in batch_path or 'janfeb' in batch_path:
            continue
        try:
            entries = json.load(open(batch_path, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for entry in entries:
            mid = entry.get("message_id")
            if mid in done_ids:
                continue
            if entry.get("suggested_action") != "keep":
                continue
            meta = all_csv_rows.get(mid, {})
            age = compute_age_days(meta.get("date", ""))
            row = {
                "message_id": mid,
                "subject": meta.get("subject", ""),
                "from_email": meta.get("from_email", ""),
                "from_name": meta.get("from_name", ""),
                "age_days": age,
                "confidence": entry.get("confidence", 0),
                "reasoning": entry.get("reasoning", ""),
            }
            if entry.get("confidence", 0) < 0.60:
                pending_review.append(row)
            else:
                still_in_inbox.append(row)

    # Sort: most recent first for inbox, least confident first for review
    still_in_inbox.sort(key=lambda r: r.get("age_days") or 0)
    pending_review.sort(key=lambda r: r.get("confidence", 0))

    # Meeting notes (if extractor has run)
    meeting_notes = []
    digest_file = profile_data_dir / "meeting-digest.json"
    if digest_file.exists():
        try:
            all_notes = json.loads(digest_file.read_text(encoding="utf-8"))
            # Only include today's and yesterday's notes
            for note in all_notes[:10]:
                meeting_notes.append({
                    "meeting_title": note.get("meeting_title", ""),
                    "date": note.get("date", ""),
                    "summary": note.get("summary", "")[:200],
                })
        except (OSError, ValueError):
            pass

    archived_count = sum(1 for h in auto_handled if h["action"] == "archive")
    labeled_count = sum(1 for h in auto_handled if h["action"] == "label")

    return {
        "profile": profile,
        "date": today,
        "auto_handled": auto_handled,
        "still_in_inbox": still_in_inbox[:50],
        "pending_review": pending_review[:20],
        "meeting_notes": meeting_notes,
        "summary": {
            "archived": archived_count,
            "labeled": labeled_count,
            "kept": len(still_in_inbox),
            "pending": len(pending_review),
        },
    }


def build_deletions(profile: str = None):
    batches = discover_batches(profile)
    done = executed_message_ids(profile)  # exclude already-executed (trashed) items
    approved = load_approved_deletes(profile)
    rows = []
    seen_mids = set()
    for bid in batches:
        csv_rows = load_csv(bid, profile)
        for entry in load_classified(bid, profile):
            if entry.get("suggested_action") != "delete":
                continue
            mid = entry.get("message_id")
            if mid in done:
                continue  # already deleted per audit log — not pending
            if mid in seen_mids:
                continue  # duplicate across batches — show only once
            seen_mids.add(mid)
            meta = csv_rows.get(mid)
            if meta is None:
                continue
            date_str = meta.get("date") or ""
            rows.append({
                "message_id": mid,
                "batch": bid,
                "from_email": meta.get("from_email") or "",
                "from_name": meta.get("from_name") or "",
                "subject": meta.get("subject") or "",
                "date": date_str,
                "age_days": compute_age_days(date_str),
                "confidence": entry.get("confidence"),
                "reasoning": entry.get("reasoning") or "",
                "approved": mid in approved,
            })
    rows.sort(key=lambda r: (r["confidence"] if isinstance(r["confidence"], (int, float)) else 0.0))
    return rows


def do_rescue(message_id, batch):
    """Mutate the classified JSON to 'rescue' (keep) an entry.
    Returns (http_status, payload_dict)."""
    if not message_id or not batch:
        return 400, {"ok": False, "error": "message_id and batch are required"}

    json_path = DATA_DIR / f"batch-{batch}-classified.json"
    if not json_path.exists():
        return 404, {"ok": False, "error": f"batch {batch} not found"}

    try:
        with open(json_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return 400, {"ok": False, "error": f"could not read batch file: {exc}"}

    if not isinstance(data, list):
        return 400, {"ok": False, "error": "malformed classified file"}

    found = None
    for entry in data:
        if isinstance(entry, dict) and entry.get("message_id") == message_id:
            found = entry
            break

    if found is None:
        return 404, {"ok": False, "error": "message_id not found in batch"}

    found["suggested_action"] = "keep"
    found["label"] = None
    found["archive_after_label"] = False
    found["confidence"] = 0.99
    found["reasoning"] = "Rescued via dashboard — keep"

    try:
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    except OSError as exc:
        return 400, {"ok": False, "error": f"could not write batch file: {exc}"}

    return 200, {"ok": True, "message_id": message_id}


def _approved_deletes_path(profile: str = None) -> Path:
    """Path to the per-profile approved-deletes snapshot file."""
    return _profile_data_dir(profile) / "delete-approved.json"


def load_approved_deletes(profile: str = None) -> set:
    """Set of message_ids the user has approved for deletion in the next run."""
    path = _approved_deletes_path(profile)
    if not path.exists():
        return set()
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def save_approved_deletes(ids: set, profile: str = None):
    """Persist the approved-deletes snapshot."""
    path = _approved_deletes_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(ids), indent=2), encoding="utf-8")


def do_approve_deletes(message_ids, approve, profile=None):
    """Add or remove message_ids from the approved-deletes snapshot.
    Returns (http_status, payload_dict)."""
    if not isinstance(message_ids, list) or not message_ids:
        return 400, {"ok": False, "error": "message_ids (non-empty list) required"}

    # Only allow approving IDs that are actually pending deletes right now —
    # prevents stale/forged IDs from being armed.
    pending = {r["message_id"] for r in build_deletions(profile)}
    approved = load_approved_deletes(profile)

    applied = []
    for mid in message_ids:
        if approve:
            if mid in pending:
                approved.add(mid)
                applied.append(mid)
        else:
            approved.discard(mid)
            applied.append(mid)

    # Drop any approved IDs that are no longer pending (already deleted/rescued)
    approved &= pending

    save_approved_deletes(approved, profile)
    return 200, {"ok": True, "approved_count": len(approved),
                 "approved": sorted(approved), "applied": applied}


def build_usps(profile: str = None):
    """Read the usps-deliveries.json produced by the extractor."""
    data_dir = _profile_data_dir(profile)
    path = data_dir / "usps-deliveries.json"
    if not path.exists():
        return {"digests": [], "packages": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"digests": [], "packages": []}


def build_proofpoint(profile: str = None):
    """Read the proofpoint-quarantine.json produced by the extractor."""
    data_dir = _profile_data_dir(profile)
    path = data_dir / "proofpoint-quarantine.json"
    if not path.exists():
        return {"digests": [], "total_blocked": 0}
    try:
        digests = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"digests": [], "total_blocked": 0}
    total_blocked = sum(d.get("count", 0) for d in digests)
    return {"digests": digests, "total_blocked": total_blocked}


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "GmailDashboard/1.0"

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html_bytes, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html_bytes)))
        self.end_headers()
        self.wfile.write(html_bytes)

    def _send_text(self, text, status=200):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _parse_qs(self):
        """Parse query string into a dict."""
        params = {}
        try:
            qs = self.path.split("?", 1)[1] if "?" in self.path else ""
            for p in qs.split("&"):
                if "=" in p:
                    k, v = p.split("=", 1)
                    params[k] = v
        except (IndexError, ValueError):
            pass
        return params

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        try:
            params = self._parse_qs()
            profile = params.get("profile") or None

            if path == "/":
                if not TEMPLATE_PATH.exists():
                    self._send_text("dashboard.html not found", 404)
                    return
                self._send_html(TEMPLATE_PATH.read_bytes())
            elif path == "/api/stats":
                self._send_json(build_stats(profile))
            elif path == "/api/deletions":
                self._send_json(build_deletions(profile))
            elif path == "/api/daily":
                self._send_json(build_daily_run(profile))
            elif path == "/api/forecast":
                self._send_json(build_aging_forecast(profile))
            elif path == "/api/job-leads":
                self._send_json(build_job_leads(profile))
            elif path == "/api/digest":
                self._send_json(build_digest(profile))
            elif path == "/api/school-events":
                days = int(params.get("days", 60))
                self._send_json(build_school_events(days, profile))
            elif path == "/api/proofpoint":
                self._send_json(build_proofpoint(profile))
            elif path == "/api/usps":
                self._send_json(build_usps(profile))
            else:
                self._send_json({"error": "not found"}, 404)
        except Exception as exc:  # noqa: BLE001 - never crash the server
            self._send_json({"error": str(exc)}, 500)

    def do_POST(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        try:
            params = self._parse_qs()
            profile = params.get("profile") or None

            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except ValueError:
                self._send_json({"ok": False, "error": "invalid JSON body"}, 400)
                return

            if path == "/api/rescue":
                status, payload = do_rescue(body.get("message_id"), body.get("batch"))
                self._send_json(payload, status)
            elif path == "/api/approve-deletes":
                # body: {message_ids: [...], approve: true|false}
                status, payload = do_approve_deletes(
                    body.get("message_ids"),
                    body.get("approve", True),
                    profile,
                )
                self._send_json(payload, status)
            else:
                self._send_json({"ok": False, "error": "not found"}, 404)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"ok": False, "error": str(exc)}, 500)

    def log_message(self, fmt, *args):  # quieter, single-line logging
        return


def main():
    server = ThreadingHTTPServer((HOST, PORT), DashboardHandler)
    url = f"http://{HOST}:{PORT}"
    print(f"Gmail cleanup dashboard serving at {url}")
    print(f"  Project root: {PROJECT_ROOT}")
    print(f"  Batches: {', '.join(discover_batches()) or '(none found)'}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
