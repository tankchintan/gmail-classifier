# Using Claude Code (or any AI assistant) with Gmail Classifier

This project is designed to be driven conversationally — you don't need to memorize commands. Open a Claude Code session (or any AI coding assistant) in this directory and just talk to it.

## How it works

The scripts do the actual work. The AI session is your interactive layer — it reads files, runs commands, explains results, and asks before doing anything destructive.

```
You (natural language)  →  AI session  →  Python scripts  →  Gmail API
```

## Getting started

Open Claude Code in this directory:
```bash
cd gmail-classifier
claude   # or your preferred AI coding tool
```

Then just ask:
> "Read the CLAUDE.md and get me oriented"

The AI will read the project structure and be ready to help.

## Multi-profile support

The project supports multiple email accounts via profiles. Each profile has its own data, logs, rules, and OAuth tokens.

```bash
./daily_run_profile.sh personal --with-ai   # personal Gmail
./daily_run_profile.sh work --with-ai       # work email
```

All scripts accept `--profile <name>` (e.g. `--profile work`). Profiles are configured in `profiles/{name}.json` (gitignored). See `profiles/personal.example.json` for the format.

## Conversational workflow examples

### Process a new batch
> "Fetch and classify a new batch of emails"

Runs `fetch_unread.py` then `classify.py` and reports back what it found.

### Review what was classified
> "Show me what's in the latest batch — how many archives, labels, deletes?"

The AI reads the classified JSON and summarizes for you.

### Execute safe actions
> "Execute archives and labels from the latest batch, skip deletes"

Runs `execute_actions.py` with appropriate flags. Deletes are always skipped unless you explicitly say otherwise.

### Review pending deletes
> "Show me what's queued for deletion"

Reads all classified batches, filters delete suggestions, shows subjects/senders/reasoning before anything executes.

### Approve specific deletes
> "Delete the LinkedIn marketing emails older than 30 days"

The AI shows you the exact list first, waits for confirmation, then executes with `--only-deletes`.

### Run the full daily pipeline
> "Run the daily pipeline"

Executes `./daily_run_profile.sh personal` and reports the summary.

### Add a classification rule
> "All emails from @newsletter.substack.com should be archived after 14 days"

The AI adds the rule to `classification_rules.personal.json` and confirms.

### Check AI insights
> "What school events are coming up?"
> "Show me the job leads pipeline"

Reads `data/school-events.json` and `data/job-leads.json` directly and formats the results.

## AI model for classification (optional)

The scripts use Claude Haiku by default (via `ANTHROPIC_API_KEY`) for:
- Classifying uncertain emails (`--with-ai` flag on `classify.py`)
- Extracting school events from newsletters (`extract_school_events.py`)
- Extracting job leads from recruiter emails (`extract_job_leads.py`)

You can swap the model by changing the `model=` parameter in those scripts. Any Anthropic model works — Haiku is cheapest (~$0.04/full run), Sonnet is more accurate for ambiguous cases.

The interactive AI session (Claude Code etc.) is separate — it's just reading files and running shell commands on your behalf, using whatever model you've configured in your editor.

## Safety rules (non-negotiable)

Remind your AI session of these if it ever forgets:

1. **Never auto-execute deletes** — always show the full list first, wait for explicit approval
2. **Show actual emails before deleting** — subjects + senders, not just a pattern summary
3. **Read files directly** — never ask the user to paste log or JSON contents
4. **When uncertain, ask** — don't guess at rule changes or batch boundaries
5. **Whitelist entries in `classification_rules.personal.json` are sacred** — never suggest actions on those senders

## Useful questions to ask your AI session

```
"What happened in today's run?"
"How many emails have we processed total?"
"Show me emails from [domain] that were archived"
"Are there any emails about to auto-archive that I should review?"
"What labels are being applied most?"
"Add [sender] to the whitelist"
"Why was this email classified as delete?"
"Re-run classification on batch 012 — I updated the rules"
"Show me the school calendar for the next 30 days"
"Any new job leads this week?"
```

## Key files the AI reads most often

| File | Purpose |
|---|---|
| `data/{profile}/batch-NNN.csv` | Raw fetched email metadata |
| `data/{profile}/batch-NNN-classified.json` | Classifications with confidence + reasoning |
| `logs/{profile}/actions-*.jsonl` | Audit trail of every executed action |
| `logs/{profile}/daily-run-*.log` | Full daily run output |
| `data/personal/school-events.json` | Extracted school calendar events |
| `data/personal/job-leads.json` | Extracted job leads pipeline |
| `profiles/{profile}.json` | Profile config (tokens, data dir, rules — gitignored) |
| `scripts/classification_rules.{profile}.json` | Profile-specific sender rules (gitignored) |
| `scripts/classification_rules.base.json` | Shared domain/pattern rules |
