#!/bin/bash
# JARVIS Startup Script — Start Ollama + JARVIS
set -e

JARVIS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OLLAMA_DIR="$HOME/ollama"

echo "🚀 Starting JARVIS ecosystem..."

# 1. Start Ollama (if not running)
if ! pgrep -f "ollama serve" > /dev/null 2>&1; then
    echo "  → Starting Ollama server..."
    export OLLAMA_MODELS="$OLLAMA_DIR/models"
    nohup "$OLLAMA_DIR/bin/ollama" serve > "$OLLAMA_DIR/ollama.log" 2>&1 & disown
    sleep 3

    # Check GPU detection
    if grep -q "NVIDIA" "$OLLAMA_DIR/ollama.log" 2>/dev/null; then
        echo "  ✅ Ollama running with GPU"
    else
        echo "  ⚠️  Ollama running (CPU only)"
    fi
else
    echo "  ✅ Ollama already running"
fi

# 2. Start JARVIS
if pgrep -f "python -m src.main" > /dev/null 2>&1; then
    echo "  ⚠️  JARVIS already running, restarting..."
    pkill -f "python -m src.main" 2>/dev/null
    sleep 2
fi

echo "  → Starting JARVIS..."
cd "$JARVIS_DIR"
source .venv/bin/activate
nohup python -m src.main > jarvis.log 2>&1 & disown

sleep 3
if pgrep -f "python -m src.main" > /dev/null 2>&1; then
    echo "  ✅ JARVIS running (PID: $(pgrep -f 'python -m src.main'))"
else
    echo "  ❌ JARVIS failed to start. Check jarvis.log"
    exit 1
fi

echo ""
echo "🤖 JARVIS is ready! Chat on Telegram."
echo "   Logs: $JARVIS_DIR/jarvis.log"
echo "   Ollama: $OLLAMA_DIR/ollama.log"
