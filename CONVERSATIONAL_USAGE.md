# Conversational Usage Guide

This is how to use the Gmail Classifier with natural language - no pasting, no formal workflows, just ask questions.

## 🗣️ How to Ask Me Questions

### About Logs

Just ask me directly - I'll read the files and answer:

**You say:**
> "What happened in the last batch?"

**I do:**
```python
Read("/Users/ctank/projects/gmail-classifier/logs/actions-latest.jsonl")
# Then tell you what happened
```

**More examples:**
- "Show me all emails that were archived today"
- "Were there any mistakes in batch 001?"
- "What actions were taken on emails from amazon.com?"
- "How many emails did we process yesterday?"
- "Show me the 5 lowest confidence actions that were executed"

### About Classifications

**You say:**
> "Review the classifications for batch 001 - do they look good?"

**I do:**
```python
Read("/Users/ctank/projects/gmail-classifier/data/batch-001-classified.json")
# Analyze and tell you if anything looks suspicious
```

**More examples:**
- "Are we being too aggressive with deletes?"
- "What's the confidence distribution for batch 002?"
- "Show me the top 10 most confident suggestions"
- "Are there any emails from real people being auto-archived?"

### During Active Processing

While a CU agent is running:

**You say:**
> "What's the classifier agent doing right now?"

**I do:**
```bash
cu sessions ls --json  # Find the running session
cu sessions get <id> --json  # Check its status
# Tell you what it's working on
```

**More examples:**
- "Is the fetcher agent stuck?"
- "How many emails has it processed so far?"
- "What's the current batch looking like?"

## 🚫 What You DON'T Need to Do

❌ Copy-paste log contents  
❌ Run separate auditor agents for simple questions  
❌ Generate formal reports for ad-hoc queries  
❌ Use `cat` or `jq` commands yourself  

Just ask me in plain English!

## ✅ Conservative Workflow (Updated)

### Step 1: Fetch & Classify (No Execution)

```bash
cu workflow run gmail-conservative-batch \
  --repo ~/projects/gmail-classifier \
  --input '{"batch_number": "001", "batch_size": 500}'
```

This will:
- Fetch 500 emails
- Classify them (ultra-conservative)
- Generate a summary markdown
- **NOT execute anything**

### Step 2: Ask Me to Review

**You say here in AI Suite:**
> "Review batch 001 classifications - are they safe to execute?"

**I'll:**
- Read `data/batch-001-classified.json`
- Read `data/batch-001-summary.md`
- Tell you if anything looks risky
- Highlight any delete suggestions for your approval

### Step 3: Execute (Only After You Approve)

If you're satisfied:

```bash
cu run \
  --agent gmail-actor \
  --repo ~/projects/gmail-classifier \
  --prompt "Execute actions from data/batch-001-classified.json. Confidence threshold: 0.81. Skip ALL delete actions - I haven't approved deletion patterns yet."
```

**Or if you want to approve specific deletes:**

```bash
cu run \
  --agent gmail-actor \
  --repo ~/projects/gmail-classifier \
  --prompt "Execute actions from data/batch-001-classified.json. Confidence threshold: 0.81. You may execute deletes ONLY for: obvious spam from known spam domains with confidence >= 0.97."
```

### Step 4: Ask Me How It Went

**You say:**
> "How did batch 001 execution go? Any issues?"

**I'll:**
- Read the latest log file
- Summarize what happened
- Flag any errors or concerning patterns

## 🎯 Typical Conversational Flow

```
You: "Let's process the first batch"
Me: [Generates cu workflow run command]

You: [Runs command in terminal]

You: "Is it done?"
Me: [Checks cu sessions ls, tells you status]

You: "Review the classifications"
Me: [Reads JSON, analyzes, gives summary]

You: "Looks good, execute archive and label only"
Me: [Generates cu run command with those constraints]

You: [Runs it]

You: "What happened?"
Me: [Reads logs, gives natural language summary]

You: "Show me emails from newsletters"
Me: [Filters logs, shows you the newsletter actions]

You: "Are we handling those well?"
Me: [Analyzes pattern, gives assessment]
```

## 🤖 When to Use the Auditor Agent

Use the auditor for **scheduled/automated reviews**, not ad-hoc questions:

**Good use cases:**
- Weekly quality reports
- End-of-month pattern analysis
- Automated checks in workflows
- Learning loop (feed improvements back to classifier)

**For everything else, just ask me!**

## 🎨 Mac Command Center (If Available)

If you have the CU Mac app:

1. Open "Claude Unleashed" app
2. Go to Command Center
3. You'll see tiles for each running session
4. Click a tile to watch the live feed
5. Come back here to AI Suite to ask questions

**Best of both worlds:**
- Visual monitoring in Mac app
- Natural language Q&A here with me

## 💡 Pro Tips

1. **Ask broad questions first**: "How's batch 001 looking?" → I'll read files and summarize
2. **Then drill down**: "Show me the delete suggestions" → I'll filter and show details
3. **Always review before executing**: Let me read classifications before you run actor
4. **Use me for sanity checks**: "Does this look right?" with any file path

---

**Bottom line**: You never need to paste file contents or run extra commands. Just talk to me like I have access to your entire project (because I do!).
