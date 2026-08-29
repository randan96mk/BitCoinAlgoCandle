#!/bin/bash
# BTC Trading Alerts — One-command launcher
# Usage: ./start.sh

set -e

cd "$(dirname "$0")"

MIN_MAJOR=3
MIN_MINOR=10

version_ok() {
    "$1" -c "import sys; sys.exit(0 if sys.version_info >= ($MIN_MAJOR, $MIN_MINOR) else 1)" 2>/dev/null
}

# Find the newest suitable Python (macOS system python3 is often 3.9 — too old)
PY=""
for cand in python3.14 python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$cand" &>/dev/null && version_ok "$cand"; then
        PY="$cand"
        break
    fi
done

if [ -z "$PY" ]; then
    echo "❌ Python ${MIN_MAJOR}.${MIN_MINOR}+ not found (system python3 is $(python3 --version 2>/dev/null || echo 'missing'))."
    echo "   Install a newer Python:"
    echo "   brew install python@3.12"
    exit 1
fi

echo "Using: $($PY --version) ($(command -v $PY))"

# Recreate venv if it was built with an old Python
if [ -d ".venv" ] && ! version_ok ".venv/bin/python"; then
    echo "♻️  Existing venv uses an old Python — recreating..."
    rm -rf .venv
fi

if [ ! -d ".venv" ]; then
    echo "📦 Creating virtual environment..."
    "$PY" -m venv .venv
fi

# Activate
source .venv/bin/activate

# Install/update deps
echo "📦 Installing dependencies..."
pip install --quiet --upgrade pip
pip install --quiet --upgrade -r requirements.txt

# Create required dirs
mkdir -p logs database

# Start server
echo ""
echo "🚀 Starting BTC Trading Alerts..."
echo "   Dashboard: http://localhost:8000"
echo "   Press Ctrl+C to stop"
echo ""

# Open browser once server is ready
(
    while ! curl -s http://localhost:8000 >/dev/null 2>&1; do
        sleep 1
    done
    open http://localhost:8000
) &

python run.py
