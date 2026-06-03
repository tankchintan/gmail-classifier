# Gmail Classifier - Project Overview

**Created**: 2026-05-29  
**Goal**: Automate Gmail classification, cleanup, and archiving for 5K+ unread Primary inbox emails using Claude Unleashed with full auditability.

## ✅ What's Been Built

### 1. Claude Unleashed Agent Definitions (`.claude-unleashed/agents/`)

Four specialized agents, each with clear responsibilities:

| Agent | Role | Permissions | Key Features |
|-------|------|-------------|--------------|
| **gmail-fetcher** | Fetch email metadata | Read-only (gmail.metadata) | • Batch requests<br>• Thread deduplication<br>• Progress tracking |
| **gmail-classifier** | Analyze & suggest actions | Read-only | • Confidence scoring<br>• Pattern recognition<br>• Conservative bias |
| **gmail-actor** | Execute approved actions | Write (gmail.modify) | • Confidence threshold filtering<br>• JSONL audit logging<br>• Error handling |
| **gmail-auditor** | Review action quality | Read-only | • Good/Bad/Ugly analysis<br>• Rule improvement suggestions<br>• Learning loop |

### 2. CU Workflows (`.claude-unleashed/workflows/`)

Two workflows for different phases:

**`gmail-initial-batch.yaml`** (Day 0)
- Conservative batch processing (500 emails at a time)
- Manual review checkpoint before execution
- Designed for parallel execution across batches

**`gmail-daily-process.yaml`** (Ongoing)
- End-to-end automated workflow
- High-confidence auto-execution (≥0.81)
- Low-confidence manual review
- Built-in auditing

### 3. Python Scripts (`scripts/`)

Core Gmail API integration:

| Script | Purpose | OAuth Scope |
|--------|---------|-------------|
| `fetch_unread.py` | Fetch email metadata | gmail.metadata |
| `classify.py` | Pattern-based classification | None (local) |
| `execute_actions.py` | Execute actions with logging | gmail.modify |
| `test_auth.py` | Verify OAuth setup | gmail.metadata |
| `classification_rules.json` | Explicit classification patterns | N/A |

### 4. Documentation

- **README.md**: Comprehensive setup and usage guide
- **QUICKSTART.md**: Step-by-step Day 0 walkthrough
- **PROJECT_OVERVIEW.md**: This file - architecture and design decisions

## 🎯 Design Decisions

### Auditability First

Every action is logged in **multiple layers**:

1. **Action logs** (`logs/actions-*.jsonl`): Structured JSONL with:
   - Timestamp, message_id, thread_id
   - Action taken, confidence score
   - Reasoning, rule applied
   - Success/error status

2. **CU session logs**: Built-in CU observability:
   - Full transcript of agent decisions
   - Token usage, cost tracking
   - Post-mortem summaries

3. **Audit reports** (`audit-reports/`): Periodic quality reviews:
   - Good/Bad/Ugly pattern analysis
   - Rule improvement suggestions
   - Week-over-week comparisons

### Conservative Execution

Safety thresholds built into every layer:

- **Confidence threshold**: 0.81 (configurable, started high)
- **Delete threshold**: 0.95 (extra safety for destructive actions)
- **Manual review gates**: Initial batches require human approval
- **Gradual relaxation**: Lower thresholds as system proves reliable

### Parallel Processing

Day 0 designed for **fan-out parallelism**:
- 10 parallel workflows can process 5K emails in ~same time as 500
- Each batch independent (500 emails × 10 batches)
- CU Mac Command Center provides fleet visualization

### Scope Progression

OAuth scopes requested progressively:

1. **Start**: `gmail.metadata` (most restrictive, no email bodies)
2. **When executing**: Upgrade to `gmail.modify` (labels, archive, trash)
3. **Never**: `gmail.readonly` or full access (principle of least privilege)

## 📊 Architecture Flow

```
Day 0 (Initial Processing):
┌─────────────────────────────────────────────────────────┐
│  Human: Launch parallel batch workflows                │
└─────────────────┬───────────────────────────────────────┘
                  │
                  ▼
    ┌─────────────────────────────┐
    │  gmail-fetcher agent        │
    │  • Fetch 500 emails         │
    │  • Dedupe by thread         │
    │  • Output CSV               │
    └─────────────┬───────────────┘
                  │
                  ▼
    ┌─────────────────────────────┐
    │  gmail-classifier agent     │
    │  • Analyze patterns         │
    │  • Suggest actions          │
    │  • Assign confidence        │
    │  • Output JSON              │
    └─────────────┬───────────────┘
                  │
                  ▼
    ┌─────────────────────────────┐
    │  HUMAN REVIEW CHECKPOINT    │
    │  • Check classifications    │
    │  • Review all deletes       │
    │  • Approve or tune rules    │
    └─────────────┬───────────────┘
                  │
                  ▼
    ┌─────────────────────────────┐
    │  gmail-actor agent          │
    │  • Filter by threshold      │
    │  • Execute actions          │
    │  • Log everything (JSONL)   │
    └─────────────┬───────────────┘
                  │
                  ▼
    ┌─────────────────────────────┐
    │  gmail-auditor agent        │
    │  • Review action quality    │
    │  • Identify mistakes        │
    │  • Suggest improvements     │
    └─────────────────────────────┘

Ongoing (Daily Workflow):
┌─────────────────────────────────────────────────────────┐
│  cu workflow run gmail-daily-process                    │
└─────────────────┬───────────────────────────────────────┘
                  │
                  ▼
    ┌─────────────────────────────────────────────────────┐
    │  Fetch → Classify → Execute (≥0.81) → Audit         │
    │                  ↓                                   │
    │        Generate low-confidence review               │
    │        (for human spot-checking)                    │
    └─────────────────────────────────────────────────────┘
```

## 🔄 Learning Loop

The system improves over time through feedback:

1. **gmail-actor** logs every action with reasoning
2. **gmail-auditor** reviews logs, identifies mistakes
3. **Human** periodically reviews audit reports
4. **Update** `classification_rules.json` with new patterns
5. **gmail-classifier** learns from rules + audit feedback
6. **Gradually** lower confidence thresholds as accuracy improves

## 🚀 Getting Started

See **QUICKSTART.md** for step-by-step instructions.

**TL;DR**:
```bash
# 1. Setup (one-time)
cd ~/projects/gmail-classifier
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Add credentials.json from Google Cloud Console

# 2. Test auth
./scripts/test_auth.py

# 3. Run first batch
cu run --agent gmail-fetcher --repo . --prompt "Fetch batch 001 (500 emails)"
cu run --agent gmail-classifier --repo . --prompt "Classify batch 001"

# 4. Review, then execute
cat data/batch-001-classified.json | jq '.[] | select(.confidence >= 0.81)'
cu run --agent gmail-actor --repo . --prompt "Execute batch 001 (≥0.81 confidence)"

# 5. Audit
cu run --agent gmail-auditor --repo . --prompt "Review latest actions"
```

## 🔧 Configuration

### Tunable Parameters

| Parameter | Default | Location | Purpose |
|-----------|---------|----------|---------|
| Confidence threshold | 0.81 | Workflow input / CLI arg | Minimum confidence for execution |
| Delete threshold | 0.95 | `gmail-actor` agent / CLI arg | Extra safety for deletes |
| Batch size | 500 | Workflow input | Emails per batch |
| Classification rules | See file | `scripts/classification_rules.json` | Explicit patterns |

### Adding Classification Rules

Edit `scripts/classification_rules.json`:

```json
{
  "whitelist_domains": ["important-client.com", "team.mycompany.com"],
  "blacklist_domains": ["known-spam.com"],
  "newsletter_patterns": ["unsubscribe", "newsletter"],
  "receipt_patterns": ["receipt", "invoice"],
  "spam_patterns": ["you won", "claim your prize"]
}
```

## 📈 Success Metrics

Track these over time:

- **Accuracy**: % of audited actions rated "Good"
- **Coverage**: % of inbox processed automatically (high-confidence)
- **False positives**: Important emails incorrectly archived/deleted
- **Time savings**: Minutes saved vs. manual processing

## 🔮 Future Enhancements

Once core system is proven:

1. **Scheduling**: Add `cu schedules add` for daily runs
2. **Lower thresholds**: 0.81 → 0.75 → 0.70 as accuracy improves
3. **More labels**: Beyond "receipts" - add "travel", "newsletters", etc.
4. **Sender learning**: Auto-whitelist frequent contacts
5. **Thread intelligence**: Better handling of ongoing conversations
6. **Dashboard**: Visualize inbox health over time

## 🐛 Known Limitations

- **No email body access**: Using `gmail.metadata` scope for privacy/speed
  - Trade-off: Some classifications may be less accurate
  - Mitigation: Can upgrade to `gmail.readonly` if needed
  
- **Date parsing**: Rough parser for email dates
  - May mis-calculate age for some emails
  - Mitigation: Enhance parser in `classify.py`

- **Pattern-based only**: No ML/AI classification (yet)
  - Relies on heuristics + explicit rules
  - Mitigation: Audit loop helps refine rules

## 📝 Maintenance

### Weekly Tasks
- Review audit reports: `cat audit-reports/audit-*.md | less`
- Check for recurring mistakes in action logs
- Update `classification_rules.json` if patterns emerge

### Monthly Tasks
- Compare week-over-week accuracy trends
- Consider lowering confidence thresholds
- Clean up old CSV/JSON files in `data/`

### As Needed
- Add sender domains to whitelist/blacklist
- Adjust classification heuristics in `classify.py`
- Re-classify old batches with new rules

## 🆘 Support

If you encounter issues:

1. Check **QUICKSTART.md** troubleshooting section
2. Review CU session logs: `cu sessions get <id> --json`
3. Check action logs: `cat logs/actions-*.jsonl | jq .`
4. Verify daemon health: `cu daemon status --json`

## 📜 License & Privacy

- **Code**: Personal use only (not licensed for distribution)
- **Data**: All email metadata and logs stay local
- **OAuth tokens**: Never commit `credentials.json` or `token.pickle` to git
- **Gmail API**: Subject to Google's terms of service

---

**Happy classifying! 📧✨**
