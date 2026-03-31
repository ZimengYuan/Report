#!/bin/bash
# Daily Research Script
# Schedule:
#   - 10:00 Beijing time -> run both public topics
#   - 20:00 Beijing time -> run both public topics

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$REPO_DIR/logs"
LOG_FILE="$LOG_DIR/research.log"
PUBLIC_ROOT="$REPO_DIR/_research"
ARTIFACT_ROOT="$REPO_DIR/artifacts/raw-research"
DATE="$(date +%Y-%m-%d)"
TIME="$(date +%H%M)"
HOUR="$(date +%H)"
TIMESTAMP="$(date +"%Y-%m-%d %H:%M:%S")"
RESEARCH_DEPTH="${LAST30DAYS_RESEARCH_DEPTH:-quick}"
RESEARCH_TIMEOUT="${LAST30DAYS_TIMEOUT:-180}"

mkdir -p "$LOG_DIR"
cd "$REPO_DIR"

if [ -x "$HOME/miniconda3/envs/node22/bin/node" ]; then
    export PATH="$HOME/miniconda3/envs/node22/bin:$PATH"
fi

find_skill_root() {
    local dir

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
        if [ -n "$dir" ] && [ -f "$dir/scripts/last30days.py" ]; then
            printf '%s\n' "$dir"
            return 0
        fi
    done

    return 1
}

write_index() {
    local research_type="$1"
    local section_title="$2"
    local schedule_label="$3"
    local section_dir="$PUBLIC_ROOT/$research_type"
    local index_file="$section_dir/README.md"
    local file_path
    local file_name
    local title

    mkdir -p "$section_dir"

    {
        echo "# $section_title"
        echo
        echo "Research conducted at $schedule_label."
        echo
        echo "This index lists public reports only. Raw captures are written to \`artifacts/raw-research/\` and are excluded from the site."
        echo
        echo "## Reports"
        echo
        echo "| Topic | File |"
        echo "|-------|------|"

        while IFS= read -r file_path; do
            file_name="$(basename "$file_path")"
            title="$(awk -F'"' '/^title: / {print $2; exit}' "$file_path")"
            if [ -z "$title" ]; then
                title="$file_name"
            fi
            printf '| %s | [%s](./%s) |\n' "$title" "$file_name" "$file_name"
        done < <(find "$section_dir" -mindepth 1 -maxdepth 1 -type f -name '*.md' ! -name 'README.md' ! -name '*-raw*.md' ! -path '*/raw/*' | sort)
    } > "$index_file"
}

if ! SKILL_ROOT="$(find_skill_root)"; then
    echo "[$TIMESTAMP] ERROR: Could not find last30days.py" >> "$LOG_FILE"
    exit 1
fi

if [ "$HOUR" -lt "15" ]; then
    RESEARCH_TYPE="morning"
    SECTION_TITLE="Morning Research"
    SCHEDULE_LABEL="10:00 Beijing time"
else
    RESEARCH_TYPE="evening"
    SECTION_TITLE="Evening Research"
    SCHEDULE_LABEL="20:00 Beijing time"
fi

PUBLIC_DIR="$PUBLIC_ROOT/$RESEARCH_TYPE"
ARTIFACT_DIR="$ARTIFACT_ROOT/$RESEARCH_TYPE/$DATE"

TOPIC_SPECS=(
    "01|Claude Code && Codex|Claude Code Codex AI coding agent|01-claude-code-codex|claude-code-codex|claude-code-codex-raw|claude-code-codex"
    "02|AI 发展总览|OpenAI Anthropic Gemini Claude AI agents|02-ai-overview|ai-overview|ai-raw|ai-overview"
)

mkdir -p "$PUBLIC_DIR" "$ARTIFACT_DIR"

echo "[$TIMESTAMP] Starting $RESEARCH_TYPE research for both topics" >> "$LOG_FILE"
echo "[$TIMESTAMP] Raw artifacts -> $ARTIFACT_DIR" >> "$LOG_FILE"
echo "[$TIMESTAMP] Node runtime: $(command -v node 2>/dev/null || echo unavailable) ($(node -v 2>/dev/null || echo unavailable))" >> "$LOG_FILE"

if [ -n "${LAST30DAYS_FORCE_SEARCH_SOURCES:-}" ]; then
    ALL_SEARCH_SOURCES="$LAST30DAYS_FORCE_SEARCH_SOURCES"
    echo "[$TIMESTAMP] Source selection forced by LAST30DAYS_FORCE_SEARCH_SOURCES=$ALL_SEARCH_SOURCES" >> "$LOG_FILE"
else
    if ALL_SEARCH_SOURCES="$(python3 "$REPO_DIR/scripts/select_last30days_sources.py" --skill-root "$SKILL_ROOT" --format csv 2>>"$LOG_FILE")"; then
        if [ -z "$ALL_SEARCH_SOURCES" ]; then
            ALL_SEARCH_SOURCES="hn"
            echo "[$TIMESTAMP] Source selector returned empty set, falling back to HN only" >> "$LOG_FILE"
        fi
    else
        ALL_SEARCH_SOURCES="x,hn"
        echo "[$TIMESTAMP] Source selector failed, falling back to $ALL_SEARCH_SOURCES" >> "$LOG_FILE"
    fi
fi

echo "[$TIMESTAMP] Using search sources: $ALL_SEARCH_SOURCES" >> "$LOG_FILE"
echo "[$TIMESTAMP] Research depth: $RESEARCH_DEPTH" >> "$LOG_FILE"
echo "[$TIMESTAMP] Global timeout: $RESEARCH_TIMEOUT" >> "$LOG_FILE"

overall_status=0
completed_reports=0

for spec in "${TOPIC_SPECS[@]}"; do
    IFS='|' read -r order report_title search_topic report_slug permalink_slug raw_slug topic_key <<< "$spec"
    public_report="$PUBLIC_DIR/$report_slug.md"
    raw_capture="$ARTIFACT_DIR/$raw_slug.md"
    temp_body="$(mktemp)"
    temp_capture="$(mktemp)"
    temp_report="$(mktemp)"

    echo "[$TIMESTAMP] Running research for: $search_topic" >> "$LOG_FILE"
    echo "[$TIMESTAMP] Public report -> $public_report" >> "$LOG_FILE"
    echo "[$TIMESTAMP] Raw capture -> $raw_capture" >> "$LOG_FILE"

    LAST30DAYS_CMD=(
        python3
        "${SKILL_ROOT}/scripts/last30days.py"
        "$search_topic"
        --emit=compact
        --search="$ALL_SEARCH_SOURCES"
        "--$RESEARCH_DEPTH"
        --timeout "$RESEARCH_TIMEOUT"
    )

    if "${LAST30DAYS_CMD[@]}" > "$temp_capture" 2>>"$LOG_FILE"; then
        mv "$temp_capture" "$raw_capture"
    else
        status=$?
        rm -f "$temp_capture" "$temp_body" "$temp_report"
        overall_status=1
        echo "[$TIMESTAMP] Research failed: $search_topic (exit $status)" >> "$LOG_FILE"
        continue
    fi

    if python3 "$REPO_DIR/scripts/synthesize_public_report.py" \
        --input "$raw_capture" \
        --topic-key "$topic_key" \
        --report-title "$report_title" \
        --slot "$RESEARCH_TYPE" \
        --date "$DATE" > "$temp_body" 2>>"$LOG_FILE"; then
        {
            cat <<EOF
---
layout: research
title: "$report_title"
type: "$RESEARCH_TYPE"
public_report: true
date: $DATE
permalink: /research/$RESEARCH_TYPE/$permalink_slug/
---

EOF
            cat "$temp_body"
        } > "$temp_report"
        mv "$temp_report" "$public_report"
        completed_reports=$((completed_reports + 1))
        echo "[$TIMESTAMP] Research completed: $search_topic" >> "$LOG_FILE"
    else
        status=$?
        rm -f "$temp_body" "$temp_report"
        overall_status=1
        echo "[$TIMESTAMP] Report synthesis failed: $report_title (exit $status)" >> "$LOG_FILE"
    fi

    rm -f "$temp_body"
done

write_index "morning" "Morning Research" "10:00 Beijing time"
write_index "evening" "Evening Research" "20:00 Beijing time"

if git rev-parse --git-dir > /dev/null 2>&1; then
    echo "[$TIMESTAMP] Committing public research updates..." >> "$LOG_FILE"
    git add _research

    if git diff --cached --quiet; then
        echo "[$TIMESTAMP] No public report changes to commit" >> "$LOG_FILE"
    else
        git commit -m "Research update: $RESEARCH_TYPE $DATE $TIME" >> "$LOG_FILE" 2>&1 || true
        git push origin main >> "$LOG_FILE" 2>&1 || echo "[$TIMESTAMP] Git push failed" >> "$LOG_FILE"
    fi
fi

TIMESTAMP="$(date +"%Y-%m-%d %H:%M:%S")"
echo "[$TIMESTAMP] Daily research completed for $RESEARCH_TYPE ($completed_reports/${#TOPIC_SPECS[@]} reports succeeded)" >> "$LOG_FILE"

exit "$overall_status"
