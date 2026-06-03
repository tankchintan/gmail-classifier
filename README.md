# Gmail Classifier

Rule-based Gmail inbox cleanup with Claude Haiku for uncertain emails, daily automation, and a local dashboard. Built to clear a personal inbox with 5,000+ unread emails — now runs daily via launchd.

**No agents, no cloud infra, no subscription.** Runs entirely locally. Core features are free; AI features cost ~$0.04/run.

## What it does

- **Fetches** unread email metadata from Gmail API in batches
- **Classifies** each email using 550+ sender rules with confidence scores
- **Executes** safe actions automatically (archive, label) at ≥ 0.75 confidence
- **Holds deletes** for manual review — never auto-deletes without explicit approval
- **Extracts** structured data from newsletters (school calendar events) and recruiter emails (job leads) via Claude Haiku
- **Tracks everything** in a local dashboard at `http://localhost:5001`

## How it works

```
fetch_unread.py  →  classify.py  →  execute_actions.py
                         ↓
                   Claude Haiku           (optional, ~$0.04/run)
                   uncertain emails only
                         ↓
         extract_school_events.py / extract_job_leads.py
                         ↓
                   dashboard/dashboard.py
```

**Two-pass execution model:**
- Pass 1 (automatic): archive + label at confidence ≥ 0.75
- Pass 2 (manual only): deletes after you review and explicitly approve

## Quick start

### 1. Google Cloud OAuth setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project, enable Gmail API
3. Create OAuth 2.0 credentials → Desktop app → download as `credentials.json` in this directory
4. Scopes needed: `gmail.readonly` (fetch) + `gmail.modify` (execute actions)

### 2. Python environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. First auth + fetch

```bash
# Opens browser for OAuth, then fetches your first 50 emails
venv/bin/python scripts/fetch_unread.py --batch 001 --limit 50
```

### 4. Classify

```bash
venv/bin/python scripts/classify.py \
  --input data/batch-001.csv \
  --output data/batch-001-classified.json
```

### 5. Execute safe actions (no deletes)

```bash
venv/bin/python scripts/execute_actions.py \
  --input data/batch-001-classified.json \
  --confidence-threshold 0.75
```

### 6. Dashboard

```bash
venv/bin/python dashboard/dashboard.py
# → http://localhost:5001
```

## Daily automation

`daily_run.sh` runs the full pipeline: fetch new emails → classify → re-classify all batches (age gates fire on older emails) → execute safe actions → extract AI insights.

```bash
./daily_run.sh            # rule-based only
./daily_run.sh --with-ai  # also send uncertain emails to Claude Haiku
```

Schedule on macOS via launchd — see `com.YOURNAME.gmail-classifier.plist.example` for a template.

## Classification rules

Rules live in two files:

| File | Committed | Purpose |
|---|---|---|
| `scripts/classification_rules.base.json` | ✅ | 550+ domain/pattern rules — the shared core |
| `scripts/classification_rules.personal.json` | ❌ gitignored | Your personal sender overrides |

Copy the example to get started:
```bash
cp scripts/classification_rules.personal.example.json \
   scripts/classification_rules.personal.json
```

Personal rules are checked first (first-match-wins), then base rules. The base ruleset covers newsletters, receipts, recruiters, notifications, travel, finance, shopping, and more.

### Rule format

```json
{
  "from_match": "@github.com",
  "action": "label",
  "label": "Dev",
  "reasoning": "GitHub notifications"
}
```

Optional fields: `subject_match`, `min_age_days`, `max_age_days`, `label`, `archive_after_label`.

Actions: `keep` · `archive` · `label` · `delete`

### Age gates

Rules can fire differently based on email age:

```json
[
  { "from_match": "@newsletter.com", "action": "keep", "max_age_days": 7 },
  { "from_match": "@newsletter.com", "action": "archive", "min_age_days": 7 }
]
```

Re-classifying all batches daily means emails automatically age into archive as they get older — no manual work needed.

## AI features (optional)

Set `ANTHROPIC_API_KEY` in your environment, or configure `apiKeyHelper` in `~/.claude/settings.json`.

| Feature | Flag/Script | What it does |
|---|---|---|
| Uncertain email review | `--with-ai` on `classify.py` | Sends emails < 0.75 confidence to Claude Haiku |
| School calendar | `extract_school_events.py` | Extracts upcoming events from school newsletters |
| Job leads pipeline | `extract_job_leads.py` | Extracts role/company/comp from recruiter emails |

Both extractors are idempotent — already-processed message IDs are skipped.

## Dashboard

**Daily Run tab**: actions this run, inbox aging forecast, labels applied, AI insights (school calendar + job pipeline).

**All-time Stats tab**: action breakdown, confidence distribution, top rules, top senders, timeline chart.

## Safety

- Deletes **never** auto-execute — require explicit `--only-deletes` flag
- Every action logged to `logs/actions-TIMESTAMP.jsonl` with full metadata
- Execution is idempotent — re-running any batch skips already-executed emails
- Confidence thresholds are configurable; default is conservative (0.75 safe actions, 0.97 deletes)

## File structure

```
gmail-classifier/
├── scripts/
│   ├── fetch_unread.py                       # Gmail API fetcher
│   ├── classify.py                           # Rule engine + optional AI pass
│   ├── execute_actions.py                    # Executor with audit log
│   ├── extract_school_events.py              # School calendar extraction
│   ├── extract_job_leads.py                  # Job leads extraction
│   ├── classification_rules.base.json        # Committed: shared rules
│   ├── classification_rules.personal.json    # Gitignored: your rules
│   └── classification_rules.personal.example.json
├── dashboard/
│   ├── dashboard.py                          # Local HTTP server
│   └── templates/dashboard.html
├── daily_run.sh                              # Full pipeline script
├── requirements.txt
└── .gitignore
```

## Requirements

- Python 3.10+
- Google Cloud project with Gmail API enabled
- `anthropic` Python package + API key (only for AI features)

## License

MIT
