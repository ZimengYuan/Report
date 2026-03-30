#!/bin/bash
# Daily Research Script - Runs last30days research for ALL topics and deploys to GitHub Pages
# Schedule: 10:00 (morning) and 20:00 (evening) Beijing time
# Both research sessions cover ALL topics

set -e

# Configuration
REPO_DIR="/home/nie/Claude/info/Report"
LOG_FILE="$REPO_DIR/logs/research.log"
DATE=$(date +%Y-%m-%d)
TIME=$(date +%H%M)

# Research topics - both topics every time
TOPICS=(
    "Claude Code && Codex 技术发展、使用方法"
    "AI 发展总览"
)

# Create logs directory if not exists
mkdir -p "$REPO_DIR/logs"

# Determine research type based on hour
HOUR=$(date +%H)
if [ "$HOUR" -eq "10" ]; then
    RESEARCH_TYPE="morning"
else
    RESEARCH_TYPE="evening"
fi

TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")
echo "[$TIMESTAMP] Starting $RESEARCH_TYPE research for all topics" >> "$LOG_FILE"

# Change to repo directory
cd "$REPO_DIR"

# Find skill root
for dir in \
  "." \
  "${CLAUDE_PLUGIN_ROOT:-}" \
  "${GEMINI_EXTENSION_DIR:-}" \
  "$HOME/.claude/plugins/marketplaces/last30days-skill" \
  "$HOME/.gemini/extensions/last30days-skill" \
  "$HOME/.gemini/extensions/last30days" \
  "$HOME/.claude/skills/last30days" \
  "$HOME/.agents/skills/last30days" \
  "$HOME/.codex/skills/last30days"; do
  [ -n "$dir" ] && [ -f "$dir/scripts/last30days.py" ] && SKILL_ROOT="$dir" && break
done

if [ -z "${SKILL_ROOT:-}" ]; then
    echo "[$TIMESTAMP] ERROR: Could not find last30days.py" >> "$LOG_FILE"
    exit 1
fi

# Run research for each topic
for TOPIC in "${TOPICS[@]}"; do
    echo "[$TIMESTAMP] Running research for: $TOPIC" >> "$LOG_FILE"

    # Create output directory
    OUTPUT_DIR="$REPO_DIR/_research/$RESEARCH_TYPE/$DATE"
    mkdir -p "$OUTPUT_DIR"

    # Run last30days
    python3 "${SKILL_ROOT}/scripts/last30days.py" "$TOPIC" --emit=compact --no-native-web --save-dir="$OUTPUT_DIR" >> "$LOG_FILE" 2>&1

    if [ $? -eq 0 ]; then
        echo "[$TIMESTAMP] Research completed: $TOPIC" >> "$LOG_FILE"
    else
        echo "[$TIMESTAMP] Research failed: $TOPIC" >> "$LOG_FILE"
    fi
done

# Update research index
INDEX_FILE="$REPO_DIR/_research/$RESEARCH_TYPE/README.md"
{
    echo ""
    echo "## $DATE $TIME"
    for TOPIC in "${TOPICS[@]}"; do
        echo "- $TOPIC"
    done
} >> "$INDEX_FILE"

# Git operations
if git rev-parse --git-dir > /dev/null 2>&1; then
    echo "[$TIMESTAMP] Committing and pushing..." >> "$LOG_FILE"

    git add -A
    git commit -m "Research update: $RESEARCH_TYPE $DATE $TIME" || true
    git push origin main || echo "[$TIMESTAMP] Git push failed" >> "$LOG_FILE"
fi

TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")
echo "[$TIMESTAMP] Daily research completed - all topics covered" >> "$LOG_FILE"
