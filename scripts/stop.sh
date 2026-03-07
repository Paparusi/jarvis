#!/bin/bash
# JARVIS Stop Script
echo "🛑 Stopping JARVIS ecosystem..."

if pgrep -f "python -m src.main" > /dev/null 2>&1; then
    pkill -f "python -m src.main" 2>/dev/null
    echo "  ✅ JARVIS stopped"
else
    echo "  ⚠️  JARVIS not running"
fi

if pgrep -f "ollama serve" > /dev/null 2>&1; then
    pkill -f "ollama serve" 2>/dev/null
    pkill -f "ollama runner" 2>/dev/null
    echo "  ✅ Ollama stopped"
else
    echo "  ⚠️  Ollama not running"
fi

echo "Done."
