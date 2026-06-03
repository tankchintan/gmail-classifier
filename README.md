# Gmail Classifier - CU Edition

Automated Gmail classification, cleanup, and auditing using Claude Unleashed.

## Project Goals
- **Classify** emails using learned patterns
- **Automate** archiving, deleting, labeling actions
- **Audit** every action for quality review
- **Clean up** 5K+ unread Primary inbox emails

## Architecture

### CU Agents
- **gmail-fetcher**: Read-only, fetches email metadata
- **gmail-classifier**: Analyzes patterns, suggests actions with confidence scores
- **gmail-actor**: Executes approved actions (≥81% confidence), logs everything
- **gmail-auditor**: Reviews action logs, learns from patterns

### Workflows
- **gmail-initial-batch**: Day 0 processing (500 emails at a time, manual)
- **gmail-daily-process**: Ongoing daily processing

## Setup

### 1. Google Cloud OAuth Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project: `gmail-classifier`
3. Enable Gmail API
4. Create OAuth 2.0 credentials:
   - Application type: **Desktop app**
   - Name: `gmail-classifier-local`
5. Download `credentials.json` → save to this directory
6. First run will open browser for OAuth consent

**Scopes requested**: `gmail.metadata` (most restrictive - no email bodies)

### 2. Python Environment

```bash
cd ~/projects/gmail-classifier
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Verify CU Daemon

```bash
cu daemon status --json
# If not running:
cu daemon start
```

### 4. List Available Agents

```bash
cd ~/projects/gmail-classifier
cu agents ls --json
# Should see: gmail-fetcher, gmail-classifier, gmail-actor, gmail-auditor
```

## Day 0: Initial 5K Email Processing

### Phase 1: Fetch All (Conservative)

```bash
# Single session, output CSV only (no classification yet)
cu run \
  --agent gmail-fetcher \
  --repo ~/projects/gmail-classifier \
  --prompt "Fetch metadata for first 500 unread Primary inbox emails. Output CSV to data/batch-001.csv"

# Check progress
cu sessions ls
cu tail <session-short-name>
```

**Repeat in batches**: 001 (0-500), 002 (500-1000), etc.

### Phase 2: Manual Classification Review

```bash
# Classify one batch to establish patterns
cu run \
  --agent gmail-classifier \
  --repo ~/projects/gmail-classifier \
  --prompt "Classify emails in data/batch-001.csv. Output actions with confidence scores to data/batch-001-classified.json"

# Review the output
cat data/batch-001-classified.json | jq '.[] | select(.confidence >= 0.81)'
```

**Human review step**: Check if suggested actions make sense before any execution.

### Phase 3: Execute High-Confidence Actions

```bash
# Only run after manual review!
cu run \
  --agent gmail-actor \
  --repo ~/projects/gmail-classifier \
  --prompt "Execute actions from data/batch-001-classified.json where confidence >= 0.81. Log every action to logs/actions-$(date +%Y%m%d-%H%M%S).jsonl"

# Check what happened
cu sessions get <session-id> --json
cat logs/actions-*.jsonl | tail -20
```

## Ongoing: Daily Workflow

Once confident in the system:

```bash
# Manual trigger (not scheduled yet)
cu workflow run gmail-daily-process \
  --repo ~/projects/gmail-classifier \
  --input '{"confidence_threshold": 0.81}'

# Watch progress
cu sessions ls
cu tail <workflow-session-name>

# Review audit after completion
cu sessions post-mortem <session-id>
```

## Auditability

Every action is logged in multiple places:

### 1. Action Logs (structured)
```bash
# All actions taken by gmail-actor
cat logs/actions-*.jsonl | jq '.'

# Filter by action type
cat logs/actions-*.jsonl | jq 'select(.action == "archive")'

# Actions in last 7 days
find logs -name "actions-*.jsonl" -mtime -7 -exec cat {} \;
```

### 2. CU Session Logs
```bash
# All gmail-actor sessions
cu sessions ls --json | jq '.[] | select(.agent == "gmail-actor")'

# Post-mortem for specific session
cu sessions post-mortem <session-id> > audit-reports/session-<id>.md
```

### 3. Periodic Audit Review
```bash
cu run \
  --agent gmail-auditor \
  --repo ~/projects/gmail-classifier \
  --prompt "Review all actions from the last 7 days in logs/. Generate markdown report: were actions good/bad/ugly? Suggest rule improvements."
```

## Tuning the System

### Adjust Confidence Threshold

Edit `.claude-unleashed/workflows/gmail-daily-process.yaml`:
```yaml
high-confidence-actions:
  prompt: |
    Execute ONLY actions with confidence >= 0.85  # Changed from 0.81
```

### Add New Classification Rules

The `gmail-classifier` agent learns from:
- Audit feedback (gmail-auditor reports)
- Manual corrections (when you override low-confidence decisions)
- Patterns you explicitly add to `scripts/classification_rules.json`

## Troubleshooting

### OAuth Token Expired
```bash
rm token.pickle
python scripts/fetch_unread.py  # Re-authenticates
```

### CU Daemon Issues
```bash
cu daemon status --json
cu daemon restart
cu plugins doctor --json  # Check MCP plugin health
```

### Session Stuck
```bash
cu sessions ls  # Find stuck session
cu sessions unstick <session-id>
# Or kill and restart:
cu sessions kill <session-id>
```

## File Structure

```
gmail-classifier/
├── README.md                          # This file
├── requirements.txt                   # Python dependencies
├── credentials.json                   # Google OAuth (gitignored)
├── token.pickle                       # OAuth token cache (gitignored)
├── .claude-unleashed/
│   ├── agents/
│   │   ├── gmail-fetcher.yaml
│   │   ├── gmail-classifier.yaml
│   │   ├── gmail-actor.yaml
│   │   └── gmail-auditor.yaml
│   └── workflows/
│       ├── gmail-initial-batch.yaml
│       └── gmail-daily-process.yaml
├── scripts/
│   ├── fetch_unread.py               # Core Gmail API logic
│   ├── classify.py                    # Classification engine
│   ├── execute_actions.py             # Action executor with logging
│   └── classification_rules.json      # Explicit rules
├── data/                              # CSV outputs, classifications
│   ├── batch-001.csv
│   ├── batch-001-classified.json
│   └── ...
├── logs/                              # Action logs (JSONL)
│   ├── actions-20260529-090000.jsonl
│   └── ...
└── audit-reports/                     # Generated audit reports
    └── session-<id>.md
```

## Next Steps

1. ✅ Complete Google Cloud OAuth setup
2. ✅ Install Python dependencies
3. ✅ Fetch first batch of 500 emails
4. ✅ Manually review classifications
5. ✅ Execute high-confidence actions (≥81%)
6. ✅ Run auditor to review quality
7. 🔄 Iterate on classification rules
8. 🔄 Gradually increase batch size
9. 🔄 Lower confidence threshold as system improves
10. 🔄 Eventually add scheduling
