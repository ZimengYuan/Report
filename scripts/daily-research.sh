#!/bin/bash
# Daily Research Script - Runs last30days research and deploys to GitHub Pages
# Schedule: 10:00 (morning) and 20:00 (evening) Beijing time

set -e

# Configuration
REPO_DIR="/home/nie/Claude/info/Report"
LOG_FILE="$REPO_DIR/logs/research.log"
DATE=$(date +%Y-%m-%d)
TIME=$(date +%H%M)

# Create logs directory if not exists
mkdir -p "$REPO_DIR/logs"

# Determine research type based on hour
HOUR=$(date +%H)
if [ "$HOUR" -eq "10" ]; then
    RESEARCH_TYPE="morning"
    TOPIC="Claude Code && Codex"
else
    RESEARCH_TYPE="evening"
    TOPIC="AI Development Overview"
fi

TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")
echo "[$TIMESTAMP] Starting $RESEARCH_TYPE research for: $TOPIC" >> "$LOG_FILE"

# Change to repo directory
cd "$REPO_DIR"

# Run last30days research
echo "[$TIMESTAMP] Running last30days research..." >> "$LOG_FILE"

# Check if last30days is available
if command -v last30days &> /dev/null; then
    # Create output directory for this research
    OUTPUT_DIR="$REPO_DIR/_research/$RESEARCH_TYPE/$DATE"
    mkdir -p "$OUTPUT_DIR"

    # Run research with output
    last30days "$TOPIC" --output "$OUTPUT_DIR/research-$TIME.md" --format markdown >> "$LOG_FILE" 2>&1

    if [ $? -eq 0 ]; then
        echo "[$TIMESTAMP] Research completed successfully" >> "$LOG_FILE"
    else
        echo "[$TIMESTAMP] Research failed with error" >> "$LOG_FILE"
        exit 1
    fi
else
    echo "[$TIMESTAMP] last30days not found. Please install first." >> "$LOG_FILE"
    echo "[$TIMESTAMP] Run: npm install -g last30days" >> "$LOG_FILE"
    exit 1
fi

# Update research index
INDEX_FILE="$REPO_DIR/_research/$RESEARCH_TYPE/README.md"
echo "# $RESEARCH_TYPE Research - $DATE" >> "$INDEX_FILE"
echo "- [$TIME - $TOPIC](./$DATE/research-$TIME.md)" >> "$INDEX_FILE"

# Git operations (if git is configured)
if git rev-parse --git-dir > /dev/null 2>&1; then
    echo "[$TIMESTAMP] Committing changes..." >> "$LOG_FILE"

    git add -A
    git commit -m "Research update: $RESEARCH_TYPE $DATE $TIME" || true
    git push origin main || echo "[$TIMESTAMP] Git push failed - token may not be configured" >> "$LOG_FILE"
else
    echo "[$TIMESTAMP] Not a git repository, skipping commit" >> "$LOG_FILE"
fi

TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")
echo "[$TIMESTAMP] Daily research completed" >> "$LOG_FILE"
