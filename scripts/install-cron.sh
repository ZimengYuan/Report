#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
CRON_FILE="$REPO_DIR/cron/research.cron"

mkdir -p "$REPO_DIR/logs"
crontab "$CRON_FILE"

echo "Installed cron schedule from $CRON_FILE"
echo "Runs at 10:00 and 20:00 Asia/Shanghai"
