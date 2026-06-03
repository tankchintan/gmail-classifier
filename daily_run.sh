#!/usr/bin/env bash
# Gmail Classifier — Daily Run
# Fetches new mail, re-classifies all batches (age gates fire), executes safe actions.
# Deletes are NEVER auto-executed — review the dashboard delete queue manually.
#
# Usage:
#   ./daily_run.sh              # standard run (keyword body detection)
#   ./daily_run.sh --with-ai    # also use Claude Haiku for uncertain emails (~$0.04)
#
# Schedule via launchd (every 3 days at 7am) — see com.ctank.gmail-classifier.plist
#
# Requires ANTHROPIC_API_KEY in environment or in .env file (for --with-ai)

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$PROJECT_ROOT/venv/bin/activate"
LOG_DIR="$PROJECT_ROOT/logs"
LOG_FILE="$LOG_DIR/daily-run-$(date +%Y%m%d-%H%M%S).log"
WITH_AI="${1:-}"

mkdir -p "$LOG_DIR"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

log "=== Gmail Classifier Daily Run ==="
log "Project: $PROJECT_ROOT"
log "Log: $LOG_FILE"
[ -n "$WITH_AI" ] && log "AI pass: enabled (Claude Haiku)"

source "$VENV"

# ── Step 1: Fetch new arrivals ────────────────────────────────────────────────
BATCH_NUM=$(ls "$PROJECT_ROOT/data/batch-"[0-9][0-9][0-9]".csv" 2>/dev/null \
  | sed 's/.*batch-\([0-9]*\)\.csv/\1/' | sort -n | tail -1)
NEXT_BATCH=$(printf "%03d" $((10#${BATCH_NUM:-0} + 1)))

log "Step 1: Fetching new mail → batch-$NEXT_BATCH (limit 300)"
python "$PROJECT_ROOT/scripts/fetch_unread.py" \
  --batch "$NEXT_BATCH" --limit 300 \
  >> "$LOG_FILE" 2>&1 && log "  ✅ Fetch done" || log "  ⚠️  Fetch failed (continuing)"

# ── Step 2: Classify new batch ────────────────────────────────────────────────
NEW_CSV="$PROJECT_ROOT/data/batch-$NEXT_BATCH.csv"
NEW_JSON="$PROJECT_ROOT/data/batch-$NEXT_BATCH-classified.json"

if [ -f "$NEW_CSV" ]; then
  log "Step 2: Classifying new batch"
  BODY_FLAGS="--with-body"
  [ "$WITH_AI" = "--with-ai" ] && BODY_FLAGS="--with-body --with-ai"
  python "$PROJECT_ROOT/scripts/classify.py" \
    --input "$NEW_CSV" --output "$NEW_JSON" $BODY_FLAGS \
    >> "$LOG_FILE" 2>&1 && log "  ✅ Classify done" || log "  ⚠️  Classify failed"
fi

# ── Step 3: Re-classify all existing batches (age gates fire) ─────────────────
log "Step 3: Re-classifying all existing batches (age gates)"
RECLASSIFIED=0
for CSV in "$PROJECT_ROOT/data/batch-"[0-9][0-9][0-9]".csv"; do
  [ -f "$CSV" ] || continue
  BATCH=$(basename "$CSV" .csv | sed 's/batch-//')
  # Skip the batch we just classified with AI — re-running without AI would lose those results
  [ "$BATCH" = "$NEXT_BATCH" ] && continue
  JSON="$PROJECT_ROOT/data/batch-$BATCH-classified.json"
  python "$PROJECT_ROOT/scripts/classify.py" \
    --input "$CSV" --output "$JSON" \
    >> "$LOG_FILE" 2>&1
  RECLASSIFIED=$((RECLASSIFIED + 1))
done
log "  ✅ Re-classified $RECLASSIFIED batches"

# ── Step 4: Execute safe actions (archive + label only, NO deletes) ────────────
log "Step 4: Executing safe actions (confidence >= 0.75, deletes skipped)"
TOTAL_NEW=0
for JSON in "$PROJECT_ROOT/data/batch-"[0-9][0-9][0-9]"-classified.json"; do
  [ -f "$JSON" ] || continue
  RESULT=$(python "$PROJECT_ROOT/scripts/execute_actions.py" \
    --input "$JSON" --confidence-threshold 0.75 2>&1)
  echo "$RESULT" >> "$LOG_FILE"
  NEW=$(echo "$RESULT" | grep "^  Successful:" | grep -v ": 0$" | grep -oE "[0-9]+$" || true)
  [ -n "$NEW" ] && TOTAL_NEW=$((TOTAL_NEW + NEW))
done
log "  ✅ Executed $TOTAL_NEW new safe actions"

# ── Step 5: Extract school events from newsletters ────────────────────────────
log "Step 5: Extracting school events from newsletters"
python "$PROJECT_ROOT/scripts/extract_school_events.py" \
  >> "$LOG_FILE" 2>&1 && log "  ✅ School events extracted" || log "  ⚠️  School events extraction failed (continuing)"

log "Step 5b: Extracting job leads from recruiter emails"
python "$PROJECT_ROOT/scripts/extract_job_leads.py" \
  >> "$LOG_FILE" 2>&1 && log "  ✅ Job leads extracted" || log "  ⚠️  Job leads extraction failed (continuing)"

# ── Step 6: Summary ───────────────────────────────────────────────────────────
PENDING_DELETES=$(python3 -c "
import json, glob
done = set()
for p in glob.glob('$LOG_DIR/actions-*.jsonl'):
    for line in open(p):
        line = line.strip()
        if not line: continue
        try:
            e = json.loads(line)
            if e.get('status') == 'success' and e.get('message_id'):
                done.add(e['message_id'])
        except: pass
pending = 0
for p in glob.glob('$PROJECT_ROOT/data/batch-*-classified.json'):
    if 'temp' in p or 'janfeb' in p: continue
    try:
        for e in json.load(open(p)):
            if e.get('suggested_action') == 'delete' and e.get('message_id') not in done:
                pending += 1
    except: pass
print(pending)
" 2>/dev/null || echo "?")

log ""
log "=== Daily Run Complete ==="
log "  New safe actions executed: $TOTAL_NEW"
log "  Pending deletes in dashboard: $PENDING_DELETES (review at http://localhost:5001)"
log "  Log: $LOG_FILE"
