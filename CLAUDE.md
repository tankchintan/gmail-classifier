# Gmail Classifier Project

**Owner**: Chintan  
**Created**: 2026-05-29  
**Purpose**: Automate Gmail inbox cleanup for 5K+ unread emails using Claude Unleashed agents with full auditability

---

## Project Overview

This project uses **Claude Unleashed (CU)** to classify, clean up, and audit personal Gmail inbox emails. The system is designed with conservative defaults and comprehensive logging so the user can review every action.

### Architecture

**4 CU Agents** (`.claude-unleashed/agents/`):
- `gmail-fetcher` - Fetches email metadata (read-only, gmail.metadata scope)
- `gmail-classifier` - Analyzes patterns, suggests actions with confidence scores
- `gmail-actor` - Executes approved actions, logs everything in JSONL
- `gmail-auditor` - Reviews action quality, suggests rule improvements

**2 CU Workflows** (`.claude-unleashed/workflows/`):
- `gmail-conservative-batch` - Day 0 processing (fetch + classify only, no execution)
- `gmail-daily-process` - Ongoing automated workflow (after trust is established)

**Python Scripts** (`scripts/`):
- `fetch_unread.py` - Gmail API fetcher (OAuth, batch requests, deduplication)
- `classify.py` - Pattern-based classification engine
- `execute_actions.py` - Action executor with audit logging
- `test_auth.py` - OAuth verification script

---

## User Preferences (CRITICAL)

### Conservative Approach

1. **Suggest deletes freely, but NEVER execute without explicit approval**
   - Classifier should honestly suggest "delete" for spam, unwanted emails
   - Actor agent NEVER executes deletes unless user explicitly says "execute deletes for X pattern"
   - This allows building up a corpus of delete suggestions for review

2. **Start with one batch at a time (500 emails)**
   - No parallel processing until user says they trust the system
   - Manual review after each classification
   - Explicit approval before any execution

3. **Confidence thresholds**
   - General actions: ≥0.81 (conservative)
   - Deletes: ≥0.97 AND explicit user approval (extra safety)
   - Gradually lower as system proves reliable

### Conversational Workflow

The user prefers **natural language interaction** over formal commands:

**User says**: "Process batch 001"  
**You do**: Run `cu workflow run gmail-conservative-batch`, monitor, report back

**User says**: "Review the classifications"  
**You do**: Read `data/batch-001-classified.json`, analyze, summarize in plain English

**User says**: "Execute archives and labels, skip deletes"  
**You do**: Run `cu run --agent gmail-actor` with appropriate prompt, monitor, report results

**User says**: "How did it go?"  
**You do**: Read `logs/actions-*.jsonl`, summarize what happened

### Never Require Manual Copy-Paste

- ❌ "Paste the log contents here" 
- ✅ Just read the file directly and answer the question

The user has access to the project files - you should read them directly when asked questions.

---

## File Organization

```
~/projects/gmail-classifier/
├── .claude-unleashed/
│   ├── agents/           # CU agent definitions
│   └── workflows/        # CU workflow orchestrations
├── scripts/              # Python Gmail API logic
├── data/                 # CSV and JSON outputs (gitignored)
│   ├── batch-XXX.csv                  # Fetched email metadata
│   ├── batch-XXX-classified.json      # Classifications with confidence
│   └── batch-XXX-summary.md           # Human-readable summaries
├── logs/                 # JSONL action logs (gitignored)
│   └── actions-TIMESTAMP.jsonl        # Audit trail
├── audit-reports/        # Quality reviews (gitignored)
├── credentials.json      # Google OAuth (gitignored, user provides)
└── token.pickle          # OAuth token cache (gitignored)
```

---

## Typical User Workflow

### Phase 1: Setup (One-time)
```bash
./setup.sh  # Automated setup (venv, deps, auth check)
```

### Phase 2: Process Each Batch (Conservative)

**User says**: "Process batch 001"

**You do**:
1. Run: `cu workflow run gmail-conservative-batch --input '{"batch_number": "001"}'`
2. Monitor with: `cu tail <session-id>`
3. When done, read `data/batch-001-classified.json` and `data/batch-001-summary.md`
4. Report back: "Batch 001 classified. X archives, Y labels, Z deletes suggested (not executed). Want to review?"

**User says**: "Show me the delete suggestions"

**You do**:
1. Read `data/batch-001-classified.json`
2. Filter: `suggested_action == "delete"`
3. Show list with subject, sender, confidence, reasoning
4. Ask: "Do any of these look safe to execute?"

**User says**: "Execute archives and labels only"

**You do**:
1. Run: `cu run --agent gmail-actor --prompt "Execute batch-001-classified.json, confidence ≥0.81. Skip ALL deletes."`
2. Monitor execution
3. When done, read `logs/actions-*.jsonl`
4. Report: "Executed X archives, Y labels. Skipped Z deletes. Any issues: [list]"

### Phase 3: Approve Delete Patterns (Later)

**User says**: "Show me all delete suggestions from batches 001-010"

**You do**:
1. Read all `data/batch-*-classified.json` files
2. Filter deletes, group by pattern (domain, subject keywords, age)
3. Present patterns: "Newsletters >90 days: 45 emails. Spam from X domain: 23 emails."

**User says**: "Newsletters >90 days are safe to auto-delete"

**You do**:
1. Update `scripts/classification_rules.json` or create a new rule
2. Update `gmail-actor` prompts to allow deletes for this pattern
3. Confirm: "Rule added. Future batches will auto-delete newsletters >90 days with ≥0.97 confidence."

---

## Working with CU Commands

When the user asks you to do something, **run the CU commands directly** (don't just suggest them):

### Running Workflows
```bash
cu workflow run gmail-conservative-batch \
  --repo ~/projects/gmail-classifier \
  --input '{"batch_number": "001", "batch_size": 500}'
```

### Running Individual Agents
```bash
cu run \
  --agent gmail-actor \
  --repo ~/projects/gmail-classifier \
  --prompt "Execute actions from data/batch-001-classified.json, confidence ≥0.81. Skip deletes."
```

### Monitoring Sessions
```bash
cu sessions ls --json                    # List all sessions
cu sessions get <session-id> --json      # Get session details
cu tail <session-short-name>             # Live stream events
cu sessions post-mortem <session-id>     # Generate summary
```

### Checking Status
Always check session status when user asks "is it done?" or "what's happening?":
```bash
cu sessions ls --json | jq '.[] | select(.status == "running")'
```

---

## Auditability Requirements

The user cares deeply about auditability. Every action must be logged with:
- ✅ Timestamp
- ✅ Email subject, sender, message_id
- ✅ Action taken (archive/delete/label/keep)
- ✅ Confidence score
- ✅ Reasoning
- ✅ Success/failure status

**Logs are in**: `logs/actions-TIMESTAMP.jsonl` (one JSON object per line)

When reviewing logs, **always read the actual files** - don't ask the user to paste contents.

---

## Safety Rules (CRITICAL)

1. **NEVER auto-execute deletes** unless user explicitly approves the pattern
2. **Start conservative**: High confidence thresholds, manual review gates
3. **Gradually relax**: Lower thresholds as system proves accurate
4. **When uncertain**: Always ask the user rather than guessing
5. **Read files directly**: Never ask user to copy-paste when you can read the file

---

## Classification Patterns (Learn Over Time)

Initial heuristics in `scripts/classify.py`:
- Newsletters >30 days → archive (confidence 0.85-0.92)
- Receipts → label "receipts" (confidence 0.87)
- Obvious spam → suggest delete (confidence 0.97+)
- Questions from real people → keep (confidence 0.75)
- Very old emails (>90 days) → archive (confidence 0.75)

As the user reviews batches, update `scripts/classification_rules.json` with:
- Whitelist domains (never auto-archive)
- Blacklist domains (safe to delete)
- Custom patterns learned from audit feedback

---

## Common Questions & Answers

**Q: "What happened in batch X?"**  
A: Read `logs/actions-*.jsonl` or `data/batch-X-summary.md`, summarize in plain English

**Q: "Review the classifications"**  
A: Read `data/batch-X-classified.json`, analyze confidence distribution, flag any concerns

**Q: "Is the session done?"**  
A: Check `cu sessions get <id> --json`, report status

**Q: "Show me emails from domain X"**  
A: Read logs/data files, filter by from_email, present results

**Q: "How many emails have we processed total?"**  
A: Count lines across all `logs/actions-*.jsonl` files, report total + breakdown

---

## Documentation Files

- **PROJECT_OVERVIEW.md** - Architecture, design decisions, learning loop
- **QUICKSTART.md** - Step-by-step Day 0 walkthrough
- **SETUP_CHECKLIST.md** - Pre-flight checklist before first run
- **CONVERSATIONAL_USAGE.md** - How to interact naturally (no copy-paste)
- **README.md** - Comprehensive reference guide

When the user asks "how do I X?", reference the appropriate doc.

---

## Next Steps (Current State)

**Setup not yet run**:
1. User needs to add `credentials.json` from Google Cloud Console
2. Run `./setup.sh` to configure environment
3. Process first batch (001) conservatively
4. Review classifications together
5. Execute approved actions only (skip deletes initially)

**After first few batches**:
- Tune classification rules based on audit feedback
- Approve safe deletion patterns
- Gradually lower confidence thresholds
- Eventually enable daily automated workflow

---

## Remember

- **Be conversational** - user doesn't want formal commands, they want natural interaction
- **Run things for them** - don't just suggest commands, execute them
- **Read files directly** - never ask to paste when you can read
- **Be conservative** - when uncertain, ask rather than assume
- **Log everything** - auditability is paramount

This is a learning system. The classifier will improve as patterns emerge from audit feedback.
