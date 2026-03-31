#!/bin/bash
# Daily Research Script
# Schedule:
#   - 10:00 Beijing time -> run one unified monitoring page
#   - 20:00 Beijing time -> run one unified monitoring page

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
REPORT_UPDATED_AT="$(date +"%Y-%m-%d %H:%M:%S %z")"
RESEARCH_DEPTH="${LAST30DAYS_RESEARCH_DEPTH:-quick}"
RESEARCH_TIMEOUT="${LAST30DAYS_TIMEOUT:-180}"
WINDOW_START=""
WINDOW_END=""
WINDOW_DAYS=1

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

compute_recent_window() {
    local now_epoch today_10 today_20 yesterday_20 last_slot_epoch

    now_epoch="$(date +%s)"
    today_10="$(date -d "$(date +%F) 10:00:00" +%s)"
    today_20="$(date -d "$(date +%F) 20:00:00" +%s)"
    yesterday_20="$(date -d "yesterday 20:00:00" +%s)"

    if [ "$now_epoch" -le "$today_10" ]; then
        # 早间 -> 抓昨晚20点到今早10点
        last_slot_epoch="$yesterday_20"
        WINDOW_DAYS=1
    elif [ "$now_epoch" -le "$today_20" ]; then
        # 晚间 -> 抓今早10点到当前（而非只抓20:00后的2小时）
        last_slot_epoch="$today_10"
        WINDOW_DAYS=0
    else
        # 夜间（>20:00）-> 抓今早10点到当前，与晚间同窗口
        last_slot_epoch="$today_10"
        WINDOW_DAYS=0
    fi

    WINDOW_START="$(date -d "@$last_slot_epoch" +"%Y-%m-%d %H:%M:%S %z")"
    WINDOW_END="$(date +"%Y-%m-%d %H:%M:%S %z")"
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

filter_monitor_sources() {
    local raw_sources="$1"
    local preferred=()
    local source

    IFS=',' read -ra source <<< "$raw_sources"
    for source in "${source[@]}"; do
        case "$source" in
            web|hn|x|youtube)
                preferred+=("$source")
                ;;
        esac
    done

    if [ "${#preferred[@]}" -gt 0 ]; then
        (IFS=','; printf '%s' "${preferred[*]}")
    else
        printf '%s' "$raw_sources"
    fi
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
    "Claude Code|Claude Code|claude code anthropic coding agent terminal workflow|claude-code-raw|claude-code"
    "Codex|Codex|OpenAI Codex coding agent codex cli chatgpt codex|codex-raw|codex"
    "大模型|大模型|OpenAI Anthropic Gemini Llama Qwen DeepSeek LLM model reasoning|large-models-raw|large-models"
    "Obsidian|Obsidian|Obsidian markdown notes plugin vault knowledge management|obsidian-raw|obsidian"
)

mkdir -p "$PUBLIC_DIR" "$ARTIFACT_DIR"
compute_recent_window

echo "[$TIMESTAMP] Starting $RESEARCH_TYPE unified monitoring run" >> "$LOG_FILE"
echo "[$TIMESTAMP] Raw artifacts -> $ARTIFACT_DIR" >> "$LOG_FILE"
echo "[$TIMESTAMP] Node runtime: $(command -v node 2>/dev/null || echo unavailable) ($(node -v 2>/dev/null || echo unavailable))" >> "$LOG_FILE"
echo "[$TIMESTAMP] Rolling window -> $WINDOW_START to $WINDOW_END" >> "$LOG_FILE"

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

ALL_SEARCH_SOURCES="$(filter_monitor_sources "$ALL_SEARCH_SOURCES")"

echo "[$TIMESTAMP] Using search sources: $ALL_SEARCH_SOURCES" >> "$LOG_FILE"
echo "[$TIMESTAMP] Research depth: $RESEARCH_DEPTH" >> "$LOG_FILE"
echo "[$TIMESTAMP] Global timeout: $RESEARCH_TIMEOUT" >> "$LOG_FILE"

overall_status=0
completed_topics=0
topic_args=()
monitor_body="$(mktemp)"
monitor_report="$(mktemp)"
public_report="$PUBLIC_DIR/01-monitor.md"

for spec in "${TOPIC_SPECS[@]}"; do
    IFS='|' read -r display_title report_title search_topic raw_slug topic_key <<< "$spec"
    raw_capture="$ARTIFACT_DIR/$raw_slug.md"
    temp_capture="$(mktemp)"

    echo "[$TIMESTAMP] Running research for: $search_topic" >> "$LOG_FILE"
    echo "[$TIMESTAMP] Raw capture -> $raw_capture" >> "$LOG_FILE"

    LAST30DAYS_CMD=(
        python3
        "${SKILL_ROOT}/scripts/last30days.py"
        "$search_topic"
        --emit=compact
        --search="$ALL_SEARCH_SOURCES"
        --days "$WINDOW_DAYS"
        "--$RESEARCH_DEPTH"
        --timeout "$RESEARCH_TIMEOUT"
    )

    if "${LAST30DAYS_CMD[@]}" > "$temp_capture" 2>>"$LOG_FILE"; then
        mv "$temp_capture" "$raw_capture"
        topic_args+=("--topic" "${topic_key}|${report_title}|${raw_capture}")
        completed_topics=$((completed_topics + 1))
    else
        status=$?
        rm -f "$temp_capture"
        overall_status=1
        echo "[$TIMESTAMP] Research failed: $search_topic (exit $status)" >> "$LOG_FILE"
        continue
    fi
    echo "[$TIMESTAMP] Research completed: $display_title" >> "$LOG_FILE"
done

if [ "${#topic_args[@]}" -gt 0 ]; then
    if python3 "$REPO_DIR/scripts/synthesize_monitor_page.py" \
        "${topic_args[@]}" \
        --slot "$RESEARCH_TYPE" \
        --date "$DATE" \
        --window-start "$WINDOW_START" \
        --window-end "$WINDOW_END" \
        --search-sources "$ALL_SEARCH_SOURCES" > "$monitor_body" 2>>"$LOG_FILE"; then
        {
            cat <<EOF
---
layout: research
title: "四主题监控简报"
type: "$RESEARCH_TYPE"
public_report: true
date: $DATE
updated_at: "$REPORT_UPDATED_AT"
trigger_mode: "cron"
trigger_schedule: "0 10,20 * * * Asia/Shanghai"
window_start: "$WINDOW_START"
window_end: "$WINDOW_END"
search_sources: "$ALL_SEARCH_SOURCES"
permalink: /research/$RESEARCH_TYPE/monitor/
---

EOF
            cat "$monitor_body"
        } > "$monitor_report"

        find "$PUBLIC_DIR" -mindepth 1 -maxdepth 1 -type f -name '*.md' ! -name 'README.md' ! -name 'index.md' -delete
        mv "$monitor_report" "$public_report"
        echo "[$TIMESTAMP] Unified monitoring page generated: $public_report" >> "$LOG_FILE"
    else
        status=$?
        rm -f "$monitor_report"
        if [ "$status" -eq 2 ]; then
            echo "[$TIMESTAMP] No publishable high-quality topics found; keeping previous public page unchanged" >> "$LOG_FILE"
        else
            overall_status=1
            echo "[$TIMESTAMP] Unified page synthesis failed (exit $status)" >> "$LOG_FILE"
        fi
    fi
else
    echo "[$TIMESTAMP] No topic runs succeeded; skipping unified page generation" >> "$LOG_FILE"
fi

rm -f "$monitor_body" "$monitor_report"

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
echo "[$TIMESTAMP] Daily research completed for $RESEARCH_TYPE ($completed_topics/${#TOPIC_SPECS[@]} topic captures succeeded)" >> "$LOG_FILE"

exit "$overall_status"
