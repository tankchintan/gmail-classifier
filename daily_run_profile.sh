#!/usr/bin/env bash
# Gmail Classifier — Profile-aware Daily Run
# Supports multiple email accounts via profiles.
#
# Usage:
#   ./daily_run_profile.sh personal            # run personal profile (default)
#   ./daily_run_profile.sh work                # run work profile
#   ./daily_run_profile.sh work --with-ai      # work profile with AI pass
#
# The profile name maps to profiles/{name}.json for configuration.
# Falls back to legacy daily_run.sh behavior when called without arguments.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$PROJECT_ROOT/venv/bin/activate"

# Parse arguments
PROFILE="${1:-personal}"
shift 2>/dev/null || true
WITH_AI="${1:-}"

LOG_DIR="$PROJECT_ROOT/logs/$PROFILE"
LOG_FILE="$LOG_DIR/daily-run-${PROFILE}-$(date +%Y%m%d-%H%M%S).log"

mkdir -p "$LOG_DIR"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

log "=== Gmail Classifier Daily Run (profile: $PROFILE) ==="
log "Project: $PROJECT_ROOT"
log "Log: $LOG_FILE"
[ -n "$WITH_AI" ] && log "AI pass: enabled"

source "$VENV"

# Read profile config for data_dir
PROFILE_FILE="$PROJECT_ROOT/profiles/${PROFILE}.json"
if [ -f "$PROFILE_FILE" ]; then
  DATA_DIR_REL=$(python3 -c "import json; print(json.load(open('$PROFILE_FILE')).get('data_dir', 'data'))")
  FETCH_LIMIT=$(python3 -c "import json; print(json.load(open('$PROFILE_FILE')).get('fetch', {}).get('limit', 300))")
else
  DATA_DIR_REL="data"
  FETCH_LIMIT=300
fi

DATA_DIR="$PROJECT_ROOT/$DATA_DIR_REL"
mkdir -p "$DATA_DIR"

PROFILE_FLAG="--profile $PROFILE"

# ── Step 1: Fetch new arrivals ────────────────────────────────────────────────
BATCH_NUM=$(ls "$DATA_DIR/batch-"[0-9][0-9][0-9]".csv" 2>/dev/null \
  | sed 's/.*batch-\([0-9]*\)\.csv/\1/' | sort -n | tail -1)
NEXT_BATCH=$(printf "%03d" $((10#${BATCH_NUM:-0} + 1)))

log "Step 1: Fetching new mail → batch-$NEXT_BATCH (limit $FETCH_LIMIT)"
FETCH_OUTPUT=$(python "$PROJECT_ROOT/scripts/fetch_unread.py" \
  $PROFILE_FLAG --batch "$NEXT_BATCH" --limit "$FETCH_LIMIT" 2>&1)
FETCH_EXIT=$?
echo "$FETCH_OUTPUT" >> "$LOG_FILE"
if [ $FETCH_EXIT -eq 0 ]; then
  log "  ✅ Fetch done"
else
  FETCH_ERROR=$(echo "$FETCH_OUTPUT" | grep -E "Error|error|Exception|Traceback" | tail -3)
  log "  ⚠️  Fetch failed (exit $FETCH_EXIT): ${FETCH_ERROR:-unknown error}"
fi

# ── Step 2: Classify new batch ────────────────────────────────────────────────
NEW_CSV="$DATA_DIR/batch-$NEXT_BATCH.csv"
NEW_JSON="$DATA_DIR/batch-$NEXT_BATCH-classified.json"

if [ -f "$NEW_CSV" ]; then
  log "Step 2: Classifying new batch"
  BODY_FLAGS="--with-body"
  [ "$WITH_AI" = "--with-ai" ] && BODY_FLAGS="--with-body --with-ai"
  python "$PROJECT_ROOT/scripts/classify.py" \
    $PROFILE_FLAG --input "$NEW_CSV" --output "$NEW_JSON" $BODY_FLAGS \
    >> "$LOG_FILE" 2>&1 && log "  ✅ Classify done" || log "  ⚠️  Classify failed"
fi

# ── Step 3: Re-classify existing batches (age gates fire on older mail) ───────
log "Step 3: Re-classifying existing batches (age gates)"
RECLASSIFIED=0
for CSV in "$DATA_DIR/batch-"[0-9][0-9][0-9]".csv"; do
  [ -f "$CSV" ] || continue
  BATCH=$(basename "$CSV" .csv | sed 's/batch-//')
  [ "$BATCH" = "$NEXT_BATCH" ] && continue
  JSON="$DATA_DIR/batch-$BATCH-classified.json"
  python "$PROJECT_ROOT/scripts/classify.py" \
    $PROFILE_FLAG --input "$CSV" --output "$JSON" \
    >> "$LOG_FILE" 2>&1
  RECLASSIFIED=$((RECLASSIFIED + 1))
done
log "  ✅ Re-classified $RECLASSIFIED batches"

# ── Step 4: Execute safe actions (archive + label only, NO deletes) ────────────
log "Step 4: Executing safe actions (confidence >= 0.75, deletes skipped)"
TOTAL_NEW=0
for JSON in "$DATA_DIR/batch-"[0-9][0-9][0-9]"-classified.json"; do
  [ -f "$JSON" ] || continue
  RESULT=$(python "$PROJECT_ROOT/scripts/execute_actions.py" \
    $PROFILE_FLAG --input "$JSON" --confidence-threshold 0.75 2>&1)
  echo "$RESULT" >> "$LOG_FILE"
  NEW=$(echo "$RESULT" | grep "^  Successful:" | grep -v ": 0$" | grep -oE "[0-9]+$" || true)
  [ -n "$NEW" ] && TOTAL_NEW=$((TOTAL_NEW + NEW))
done
log "  ✅ Executed $TOTAL_NEW new safe actions"

# ── Step 4.5: Execute APPROVED deletes (snapshot safety gate) ──────────────────
# Only message_ids the user explicitly approved in the dashboard are deleted.
# The approved snapshot lives at $DATA_DIR/delete-approved.json.
APPROVED_FILE="$DATA_DIR/delete-approved.json"
if [ -f "$APPROVED_FILE" ]; then
  APPROVED_COUNT=$(python3 -c "import json; print(len(json.load(open('$APPROVED_FILE'))))" 2>/dev/null || echo 0)
  if [ "${APPROVED_COUNT:-0}" -gt 0 ]; then
    log "Step 4.5: Executing $APPROVED_COUNT approved delete(s)"
    TOTAL_DEL=0
    for JSON in "$DATA_DIR/batch-"[0-9][0-9][0-9]"-classified.json"; do
      [ -f "$JSON" ] || continue
      RESULT=$(python "$PROJECT_ROOT/scripts/execute_actions.py" \
        $PROFILE_FLAG --input "$JSON" --only-deletes --delete-threshold 0.90 \
        --approved-deletes-file "$APPROVED_FILE" 2>&1)
      echo "$RESULT" >> "$LOG_FILE"
      DEL=$(echo "$RESULT" | grep "^    delete:" | grep -oE "[0-9]+$" || true)
      [ -n "$DEL" ] && TOTAL_DEL=$((TOTAL_DEL + DEL))
    done
    log "  ✅ Executed $TOTAL_DEL approved delete(s)"
    # Clear the snapshot — approvals are one-shot. Already-deleted IDs are
    # idempotent no-ops anyway, but clearing keeps the queue honest.
    echo "[]" > "$APPROVED_FILE"
    log "  ✅ Cleared approved-deletes snapshot"
  fi
fi

# ── Step 5: Run profile-specific extractors ───────────────────────────────────
if [ -f "$PROFILE_FILE" ]; then
  EXTRACTORS=$(python3 -c "import json; exts=json.load(open('$PROFILE_FILE')).get('extractors',[]); print(' '.join(exts))")
else
  EXTRACTORS="school_events job_leads"
fi

for EXT in $EXTRACTORS; do
  SCRIPT="$PROJECT_ROOT/scripts/extractors/${EXT}.py"
  if [ ! -f "$SCRIPT" ]; then
    # Legacy location (before extractors/ subdir)
    SCRIPT="$PROJECT_ROOT/scripts/extract_${EXT}.py"
  fi
  if [ -f "$SCRIPT" ]; then
    log "Step 5: Running extractor: $EXT"
    python "$SCRIPT" $PROFILE_FLAG \
      >> "$LOG_FILE" 2>&1 && log "  ✅ $EXT done" || log "  ⚠️  $EXT failed (continuing)"
  fi
done

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
for p in glob.glob('$DATA_DIR/batch-*-classified.json'):
    if 'temp' in p or 'janfeb' in p: continue
    try:
        for e in json.load(open(p)):
            if e.get('suggested_action') == 'delete' and e.get('message_id') not in done:
                pending += 1
    except: pass
print(pending)
" 2>/dev/null || echo "?")

log ""
log "=== Daily Run Complete ($PROFILE) ==="
log "  New safe actions executed: $TOTAL_NEW"
log "  Pending deletes: $PENDING_DELETES"
log "  Log: $LOG_FILE"
