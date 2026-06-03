# Quick Start Guide

**Goal**: Process 5K unread emails using Claude Unleashed with full auditability.

## Prerequisites

1. **Google Cloud OAuth setup** (one-time)
   - Go to https://console.cloud.google.com/
   - Create project: `gmail-classifier`
   - Enable Gmail API
   - Create OAuth Desktop credentials
   - Download `credentials.json` → save to this directory

2. **Python environment** (one-time)
   ```bash
   cd ~/projects/gmail-classifier
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. **CU daemon running**
   ```bash
   cu daemon status --json
   # If not running:
   cu daemon start
   ```

## Day 0: First Batch (Conservative Approach)

### Step 1: Fetch first 500 emails

```bash
cd ~/projects/gmail-classifier

# Run the fetcher agent
cu run \
  --agent gmail-fetcher \
  --repo ~/projects/gmail-classifier \
  --prompt "Fetch metadata for first 500 unread Primary inbox emails (batch 001). Output CSV to data/batch-001.csv. Use gmail.metadata scope only."

# Watch progress
cu sessions ls
cu tail <session-short-name>
```

**Expected output**: `data/batch-001.csv` with ~500 emails (deduplicated by thread)

### Step 2: Classify the batch

```bash
cu run \
  --agent gmail-classifier \
  --repo ~/projects/gmail-classifier \
  --prompt "Classify emails from data/batch-001.csv. Output JSON with suggested actions and confidence scores to data/batch-001-classified.json. Be conservative - use 0.81+ confidence only when very certain."

# Check the session
cu sessions get <session-id> --json
```

**Expected output**: `data/batch-001-classified.json`

### Step 3: Human review (CRITICAL!)

```bash
# Review high-confidence actions (≥0.81)
cat data/batch-001-classified.json | jq '.[] | select(.confidence >= 0.81)'

# Review ALL delete suggestions (should be very few)
cat data/batch-001-classified.json | jq '.[] | select(.suggested_action == "delete")'

# Count by action type
cat data/batch-001-classified.json | jq 'group_by(.suggested_action) | map({action: .[0].suggested_action, count: length})'
```

**Decision point**: Do the suggestions make sense? If yes, proceed. If no, adjust classification rules in `scripts/classification_rules.json` and re-run Step 2.

### Step 4: Execute high-confidence actions

⚠️ **This modifies your Gmail** - only run after review!

```bash
cu run \
  --agent gmail-actor \
  --repo ~/projects/gmail-classifier \
  --prompt "Execute actions from data/batch-001-classified.json where confidence >= 0.81. For deletes, require confidence >= 0.95. Log every action to logs/actions-$(date +%Y%m%d-%H%M%S).jsonl. Print summary at completion."

# Watch it run
cu tail <session-short-name>

# Check what happened
cat logs/actions-*.jsonl | tail -20
```

**Expected output**: Action log in `logs/` directory

### Step 5: Audit the results

```bash
cu run \
  --agent gmail-auditor \
  --repo ~/projects/gmail-classifier \
  --prompt "Review actions from the most recent log file in logs/. Generate markdown audit report to audit-reports/audit-batch-001.md. Focus on: were high-confidence actions correct? Any patterns of mistakes? Suggest rule improvements."

# Read the audit report
cat audit-reports/audit-batch-001.md
```

**Expected output**: Audit report with Good/Bad/Ugly analysis

---

## Day 0: Scaling Up (Parallel Batches)

Once you're confident in the first batch, process the remaining ~4.5K emails in parallel:

```bash
# Launch 9 workflows in parallel (batches 002-010)
for i in {2..10}; do
  batch=$(printf "%03d" $i)
  offset=$((($i - 1) * 500))
  
  cu workflow run gmail-initial-batch \
    --repo ~/projects/gmail-classifier \
    --input "{\"batch_number\": \"$batch\", \"batch_size\": 500, \"offset\": $offset}" &
done

# Watch the fleet
cu sessions ls

# Or use Mac Command Center app to visualize
```

**This will run 9 parallel workflows**, each fetching + classifying 500 emails. Review each batch's output before executing actions.

---

## Ongoing: Daily Processing

Once Day 0 is complete and you trust the system:

```bash
# Manual trigger (not scheduled yet)
cu workflow run gmail-daily-process \
  --repo ~/projects/gmail-classifier \
  --input '{"confidence_threshold": 0.81}'

# Watch progress
cu tail <workflow-session-name>
```

This workflow will:
1. Fetch all new unread emails
2. Classify them
3. Execute high-confidence actions (≥0.81)
4. Generate low-confidence review report for manual handling
5. Audit the actions taken

---

## Troubleshooting

### "Insufficient authentication scopes"

The scripts start with `gmail.metadata` (most restrictive), but executing actions requires `gmail.modify`.

**Fix**: Delete `token.pickle` and re-authenticate:
```bash
rm ~/projects/gmail-classifier/token.pickle
# Next script run will prompt for re-auth with new scope
```

### CU agent not found

```bash
# List available agents
cu agents ls --json

# Should see: gmail-fetcher, gmail-classifier, gmail-actor, gmail-auditor
# If not, check you're in the right directory:
cd ~/projects/gmail-classifier
cu agents ls --json  # Repo-local agents
```

### Session stuck

```bash
cu sessions ls  # Find stuck session
cu sessions unstick <session-id>
# Or kill and restart:
cu sessions kill <session-id>
```

---

## Next Steps

1. ✅ Complete Google OAuth setup
2. ✅ Process first batch (001)
3. ✅ Review and audit
4. 🔄 Process remaining batches (002-010) in parallel
5. 🔄 Tune classification rules based on audit feedback
6. 🔄 Lower confidence threshold as system improves (0.81 → 0.75 → 0.70)
7. 🔄 Add scheduling for daily runs

---

## Useful Commands

```bash
# View all gmail-actor sessions
cu sessions ls --json | jq '.[] | select(.agent == "gmail-actor")'

# View recent action logs
find ~/projects/gmail-classifier/logs -name "actions-*.jsonl" -mtime -7 -exec cat {} \;

# Count actions by type
cat ~/projects/gmail-classifier/logs/actions-*.jsonl | jq -s 'group_by(.action) | map({action: .[0].action, count: length})'

# Generate post-mortem for a session
cu sessions post-mortem <session-id> > audit-reports/session-<id>.md
```
