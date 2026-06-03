#!/bin/bash
# Automated setup script for gmail-classifier project

set -e  # Exit on error

PROJECT_ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$PROJECT_ROOT"

echo "🚀 Gmail Classifier Setup"
echo "========================="
echo ""

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed. Please install it first."
    exit 1
fi

echo "✅ Python 3 found: $(python3 --version)"

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo ""
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
    echo "✅ Virtual environment created"
else
    echo "✅ Virtual environment already exists"
fi

# Activate virtual environment
echo ""
echo "📦 Installing dependencies..."
source venv/bin/activate

# Install requirements
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

echo "✅ Dependencies installed"

# Check for credentials.json
echo ""
if [ ! -f "credentials.json" ]; then
    echo "⚠️  credentials.json not found!"
    echo ""
    echo "Next steps:"
    echo "1. Go to: https://console.cloud.google.com/"
    echo "2. Create a project and enable Gmail API"
    echo "3. Create OAuth Desktop credentials"
    echo "4. Download credentials.json to: $PROJECT_ROOT/credentials.json"
    echo ""
    echo "Then run this script again."
    exit 1
else
    echo "✅ credentials.json found"
fi

# Test authentication
echo ""
echo "🔐 Testing Gmail API authentication..."
if python3 scripts/test_auth.py; then
    echo ""
    echo "✅ Setup complete!"
else
    echo ""
    echo "❌ Authentication test failed. Check the error above."
    exit 1
fi

# Check CU daemon
echo ""
echo "🔍 Checking CU daemon..."
if cu daemon status --json &> /dev/null; then
    echo "✅ CU daemon is running"
else
    echo "⚠️  CU daemon is not running"
    echo "Starting daemon..."
    cu daemon start
fi

# List agents
echo ""
echo "🤖 Available CU agents:"
cu agents ls --json | jq -r '.[].name' | sed 's/^/  - /'

echo ""
echo "========================="
echo "✅ All set! Ready to process emails."
echo ""
echo "Next steps:"
echo "  1. Review QUICKSTART.md"
echo "  2. Run your first batch:"
echo "     cu run --agent gmail-fetcher --repo . --prompt 'Fetch batch 001'"
echo ""
