# Setup Checklist

Complete these steps before running your first batch.

## ☐ 1. Google Cloud Console Setup

### Create OAuth Credentials

1. Go to: https://console.cloud.google.com/
2. **Create project** (or select existing):
   - Project name: `gmail-classifier`
   - Click "Create"
3. **Enable Gmail API**:
   - Search for "Gmail API"
   - Click "Enable"
4. **Create OAuth credentials**:
   - Go to "Credentials" → "Create Credentials" → "OAuth client ID"
   - Application type: **Desktop app**
   - Name: `gmail-classifier-local`
   - Click "Create"
5. **Download credentials**:
   - Click the download button (⬇️) next to your credential
   - Save as: `~/projects/gmail-classifier/credentials.json`

**Verification**:
```bash
ls ~/projects/gmail-classifier/credentials.json
# Should show the file exists
```

---

## ☐ 2. Python Environment Setup

### Create Virtual Environment

```bash
cd ~/projects/gmail-classifier
python3 -m venv venv
```

### Activate Virtual Environment

```bash
source venv/bin/activate
# Your prompt should show (venv) prefix
```

### Install Dependencies

```bash
pip install -r requirements.txt
```

**Expected output**:
```
Successfully installed google-api-python-client-2.122.0 ...
```

**Verification**:
```bash
pip list | grep google
# Should show google-api-python-client, google-auth, etc.
```

---

## ☐ 3. Test Gmail API Authentication

### Run Test Script

```bash
# Make sure venv is activated
source venv/bin/activate

./scripts/test_auth.py
```

**What will happen**:
1. Browser will open with Google OAuth consent screen
2. Choose your **personal Gmail account** (not work)
3. Click "Allow" to grant permissions
4. Browser will show "The authentication flow has completed"
5. Script will show ✅ success messages

**Expected output**:
```
Testing Gmail API authentication...
============================================================
✅ Authentication successful!

Testing basic API access...
✅ Connected to Gmail account:
   Email: your-email@gmail.com
   Total messages: XXXX
   Total threads: XXXX

Testing query for unread Primary inbox...
✅ Found XX unread emails in first batch (showing max 10)

============================================================
✅ All tests passed! You're ready to use CU agents.
```

**Troubleshooting**:
- ❌ "credentials.json not found" → Go back to step 1
- ❌ "Gmail API not enabled" → Enable Gmail API in Google Cloud Console
- ❌ "Access denied" → Make sure you're using your personal Gmail account

---

## ☐ 4. Verify CU Daemon

### Check Daemon Status

```bash
cu daemon status --json
```

**Expected output** (status should be "running"):
```json
{
  "status": "running",
  "version": "...",
  ...
}
```

### Start Daemon (if not running)

```bash
cu daemon start
```

---

## ☐ 5. Verify CU Agents

### List Available Agents

```bash
cd ~/projects/gmail-classifier
cu agents ls --json
```

**Expected output**: Should include these 4 agents:
```json
[
  {"name": "gmail-fetcher", "archetype": "other", ...},
  {"name": "gmail-classifier", "archetype": "other", ...},
  {"name": "gmail-actor", "archetype": "other", ...},
  {"name": "gmail-auditor", "archetype": "reviewer", ...}
]
```

### Inspect an Agent

```bash
cu agents show gmail-fetcher
```

**Troubleshooting**:
- ❌ Agents not found → Make sure you're in `~/projects/gmail-classifier` directory
- ❌ Empty list → Check `.claude-unleashed/agents/*.yaml` files exist

---

## ☐ 6. Verify CU Workflows

### List Available Workflows

```bash
cd ~/projects/gmail-classifier
cu workflow ls --json
```

**Expected output**: Should include these 2 workflows:
```json
[
  {"name": "gmail-initial-batch", ...},
  {"name": "gmail-daily-process", ...}
]
```

---

## ✅ Ready to Go!

If all checkboxes are complete, you're ready to process your first batch.

**Next steps**:
1. Open **QUICKSTART.md**
2. Follow "Day 0: First Batch" section
3. Start with batch 001 (500 emails)

**Quick test run** (dry run - won't modify Gmail):
```bash
cu run \
  --agent gmail-fetcher \
  --repo ~/projects/gmail-classifier \
  --prompt "Fetch metadata for first 10 unread Primary inbox emails. Just print summary, don't save CSV."

cu tail <session-short-name>
```

---

## 📋 Pre-Flight Checklist Summary

Before running your first batch, verify:

- [ ] ✅ `credentials.json` exists in project root
- [ ] ✅ Virtual environment activated (`venv/`)
- [ ] ✅ Python dependencies installed
- [ ] ✅ `test_auth.py` completed successfully
- [ ] ✅ CU daemon is running
- [ ] ✅ 4 CU agents are available
- [ ] ✅ 2 CU workflows are available
- [ ] ✅ You've read QUICKSTART.md

**All checked?** → Proceed to QUICKSTART.md! 🚀
