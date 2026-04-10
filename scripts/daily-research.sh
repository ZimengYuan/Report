#!/bin/bash
# Daily Research Script
# Schedule:
#   - 10:00 Beijing time -> run one unified monitoring page (deep)
# Strategy:
#   - Each topic fans out into multiple focused queries.
#   - Query outputs are merged into one normalized compact artifact per topic.

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
RESEARCH_DEPTH="${LAST30DAYS_RESEARCH_DEPTH:-deep}"
RESEARCH_TIMEOUT="${LAST30DAYS_TIMEOUT:-420}"
TIME_WINDOWS_DAYS_CSV="${LAST30DAYS_TIME_WINDOWS:-1,7}"
TIME_WINDOWS_DAYS=()
WINDOW_START=""
WINDOW_END=""
RESEARCH_TYPE=""
SECTION_TITLE=""
SCHEDULE_LABEL=""
TRIGGER_MODE="manual"
TRIGGER_SCHEDULE="manual"
PUBLISH_REPORT=0
PUSH_AFTER_COMMIT=1
MANUAL_PROFILE="${LAST30DAYS_MANUAL_PROFILE:-full}"
APPLIED_MANUAL_PROFILE=""
TIME_WINDOWS_EXPLICIT=0
SLOT_OVERRIDE=""
WINDOW_START_OVERRIDE=""
WINDOW_END_OVERRIDE=""
LOCK_FILE="$LOG_DIR/daily-research.lock"
TEMP_FILES=()

mkdir -p "$LOG_DIR"
cd "$REPO_DIR"

usage() {
    cat <<'EOF'
Usage: bash scripts/daily-research.sh [options]

Options:
    --slot <morning>                 Force slot selection (single daily slot).
  --window-start <datetime>        Override window start (parsed by GNU date).
  --window-end <datetime>          Override window end (parsed by GNU date).
    --time-windows <csv>             last30days --days windows, e.g. 1,7.
  --trigger-mode <manual|cron>     Annotate run mode in front matter/logs.
  --manual-profile <auto|fast|full>
                                    Manual mode defaults. auto resolves to full.
  --publish                        Commit/push _research/ changes after run.
  --no-push                        Commit only; do not push (requires --publish).
  --date <YYYY-MM-DD>              Override report date folder/front matter date.
  --help                           Show this help.
EOF
}

append_path_if_node_dir() {
    local node_dir="$1"
    if [ -d "$node_dir" ] && [ -x "$node_dir/node" ]; then
        export PATH="$node_dir:$PATH"
    fi
}

configure_node_runtime_path() {
    local nvm_candidate

    if [ -n "${LAST30DAYS_NODE_BIN:-}" ]; then
        append_path_if_node_dir "$LAST30DAYS_NODE_BIN"
    fi

    append_path_if_node_dir "$HOME/miniconda3/envs/node22/bin"

    for nvm_candidate in "$HOME"/.nvm/versions/node/v*/bin; do
        append_path_if_node_dir "$nvm_candidate"
    done
}

log_msg() {
    local message="$1"
    TIMESTAMP="$(date +"%Y-%m-%d %H:%M:%S")"
    echo "[$TIMESTAMP] $message" >> "$LOG_FILE"
}

register_temp_file() {
    local file_path="$1"
    TEMP_FILES+=("$file_path")
}

cleanup_temp_files() {
    local file_path
    for file_path in "${TEMP_FILES[@]:-}"; do
        [ -n "$file_path" ] || continue
        if [ -e "$file_path" ]; then
            rm -f "$file_path"
        fi
    done
}

on_exit() {
    cleanup_temp_files
}

compute_window_days_from_bounds() {
    local start_text="$1"
    local end_text="$2"
    local start_epoch end_epoch

    start_epoch="$(date -d "$start_text" +%s)"
    end_epoch="$(date -d "$end_text" +%s)"

    if [ "$end_epoch" -le "$start_epoch" ]; then
        echo "0"
        return
    fi

    echo $(( (end_epoch - start_epoch + 86399) / 86400 ))
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --slot)
                SLOT_OVERRIDE="${2:-}"
                shift 2
                ;;
            --window-start)
                WINDOW_START_OVERRIDE="${2:-}"
                shift 2
                ;;
            --window-end)
                WINDOW_END_OVERRIDE="${2:-}"
                shift 2
                ;;
            --time-windows)
                TIME_WINDOWS_DAYS_CSV="${2:-}"
                TIME_WINDOWS_EXPLICIT=1
                shift 2
                ;;
            --trigger-mode)
                TRIGGER_MODE="${2:-}"
                shift 2
                ;;
            --manual-profile)
                MANUAL_PROFILE="${2:-}"
                shift 2
                ;;
            --publish)
                PUBLISH_REPORT=1
                shift
                ;;
            --no-push)
                PUSH_AFTER_COMMIT=0
                shift
                ;;
            --date)
                DATE="${2:-}"
                shift 2
                ;;
            --help|-h)
                usage
                exit 0
                ;;
            *)
                echo "Unknown argument: $1" >&2
                usage >&2
                exit 2
                ;;
        esac
    done

    case "$SLOT_OVERRIDE" in
        ""|morning)
            ;;
        *)
            echo "Invalid --slot value: $SLOT_OVERRIDE" >&2
            exit 2
            ;;
    esac

    case "$TRIGGER_MODE" in
        manual|cron)
            ;;
        *)
            echo "Invalid --trigger-mode value: $TRIGGER_MODE" >&2
            exit 2
            ;;
    esac

    case "$MANUAL_PROFILE" in
        auto|fast|full)
            ;;
        *)
            echo "Invalid --manual-profile value: $MANUAL_PROFILE" >&2
            exit 2
            ;;
    esac

    if { [ -n "$WINDOW_START_OVERRIDE" ] && [ -z "$WINDOW_END_OVERRIDE" ]; } || \
       { [ -z "$WINDOW_START_OVERRIDE" ] && [ -n "$WINDOW_END_OVERRIDE" ]; }; then
        echo "--window-start and --window-end must be provided together" >&2
        exit 2
    fi

    TIME_WINDOWS_DAYS=()
    IFS=',' read -ra _windows <<< "$TIME_WINDOWS_DAYS_CSV"
    for _days in "${_windows[@]}"; do
        _days="$(echo "$_days" | xargs)"
        if [[ ! "$_days" =~ ^[0-9]+$ ]] || [ "$_days" -le 0 ]; then
            echo "Invalid --time-windows value: $TIME_WINDOWS_DAYS_CSV" >&2
            exit 2
        fi
        TIME_WINDOWS_DAYS+=("$_days")
    done

    if [ "${#TIME_WINDOWS_DAYS[@]}" -eq 0 ]; then
        echo "--time-windows cannot be empty" >&2
        exit 2
    fi

    if [ "$PUBLISH_REPORT" -eq 0 ] && [ "$PUSH_AFTER_COMMIT" -eq 0 ]; then
        echo "Ignoring --no-push because --publish is not enabled" >&2
    fi
}

apply_manual_profile_defaults() {
    if [ "$TRIGGER_MODE" != "manual" ]; then
        APPLIED_MANUAL_PROFILE="cron"
        return
    fi

    if [ "$MANUAL_PROFILE" = "auto" ]; then
        APPLIED_MANUAL_PROFILE="full"
    else
        APPLIED_MANUAL_PROFILE="$MANUAL_PROFILE"
    fi

    if [ "$APPLIED_MANUAL_PROFILE" != "fast" ]; then
        return
    fi

    if [ "$TIME_WINDOWS_EXPLICIT" -eq 0 ] && [ -z "${LAST30DAYS_TIME_WINDOWS+x}" ]; then
        TIME_WINDOWS_DAYS_CSV="1"
        TIME_WINDOWS_DAYS=("1")
    fi

    if [ -z "${LAST30DAYS_RESEARCH_DEPTH+x}" ]; then
        RESEARCH_DEPTH="default"
    fi

    if [ -z "${LAST30DAYS_TIMEOUT+x}" ]; then
        RESEARCH_TIMEOUT="240"
    fi

    if [ -z "${REPORT_MONITOR_DISABLE_LLM+x}" ]; then
        export REPORT_MONITOR_DISABLE_LLM=1
    fi
}

parse_args "$@"
apply_manual_profile_defaults
configure_node_runtime_path
trap on_exit EXIT

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log_msg "Another daily-research.sh run is in progress; exiting without changes"
    exit 0
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
    local research_type="$1"
    local now_epoch today_10 yesterday_10 last_slot_epoch

    if [ -n "$WINDOW_START_OVERRIDE" ] && [ -n "$WINDOW_END_OVERRIDE" ]; then
        WINDOW_START="$(date -d "$WINDOW_START_OVERRIDE" +"%Y-%m-%d %H:%M:%S %z")"
        WINDOW_END="$(date -d "$WINDOW_END_OVERRIDE" +"%Y-%m-%d %H:%M:%S %z")"
        return
    fi

    now_epoch="$(date +%s)"
    today_10="$(date -d "$(date +%F) 10:00:00" +%s)"
    yesterday_10="$(date -d "yesterday 10:00:00" +%s)"

    if [ "$TRIGGER_MODE" = "cron" ]; then
        # 每日 10:00 定时：抓上一官方时间点（昨天 10:00）到当前
        last_slot_epoch="$yesterday_10"
    else
        # 手动运行：10:00 前看昨天窗口；10:00 后看今天窗口
        if [ "$now_epoch" -lt "$today_10" ]; then
            last_slot_epoch="$yesterday_10"
        else
            last_slot_epoch="$today_10"
        fi
    fi

    WINDOW_START="$(date -d "@$last_slot_epoch" +"%Y-%m-%d %H:%M:%S %z")"
    WINDOW_END="$(date +"%Y-%m-%d %H:%M:%S %z")"
}

resolve_slot_metadata() {
    if [ -n "$SLOT_OVERRIDE" ]; then
        RESEARCH_TYPE="$SLOT_OVERRIDE"
    else
        RESEARCH_TYPE="morning"
    fi

    SECTION_TITLE="Daily Research"
    SCHEDULE_LABEL="10:00 Beijing time"

    if [ "$TRIGGER_MODE" = "cron" ]; then
        TRIGGER_SCHEDULE="0 10 * * * Asia/Shanghai"
    fi
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

prune_monitor_snapshots() {
    local section_dir="$1"
    local keep_count="$2"
    local snapshots=()
    local idx=0

    while IFS= read -r snapshot; do
        snapshots+=("$snapshot")
    done < <(find "$section_dir" -mindepth 1 -maxdepth 1 -type f -name 'monitor-*.md' | sort -r)

    for snapshot in "${snapshots[@]}"; do
        idx=$((idx + 1))
        if [ "$idx" -le "$keep_count" ]; then
            continue
        fi
        rm -f "$snapshot"
    done
}

write_archive_page() {
    local research_type="$1"
    local keep_count="$2"
    local section_dir="$PUBLIC_ROOT/$research_type"
    local archive_file="$section_dir/archive.md"
    local snapshot_path
    local snapshot_name
    local snapshot_date
    local display_title
    local count=0

    mkdir -p "$section_dir"

    {
        echo "# 最近7天监控存档"
        echo
        echo "此页面保留最近 ${keep_count} 天的公开监控简报。"
        echo
        echo "## 存档列表"
        echo
        echo "| 日期 | 报告 |"
        echo "|------|------|"

        while IFS= read -r snapshot_path; do
            count=$((count + 1))
            if [ "$count" -gt "$keep_count" ]; then
                break
            fi
            snapshot_name="$(basename "$snapshot_path")"
            snapshot_date="${snapshot_name#monitor-}"
            snapshot_date="${snapshot_date%.md}"
            display_title="$(awk -F'"' '/^title: / {print $2; exit}' "$snapshot_path")"
            if [ -z "$display_title" ]; then
                display_title="四主题监控简报"
            fi
            printf '| %s | [%s](./%s) |\n' "$snapshot_date" "$display_title" "$snapshot_name"
        done < <(find "$section_dir" -mindepth 1 -maxdepth 1 -type f -name 'monitor-*.md' | sort -r)
    } > "$archive_file"
}

filter_monitor_sources() {
    local raw_sources="$1"
    local preferred=()
    local source

    IFS=',' read -ra source <<< "$raw_sources"
    for source in "${source[@]}"; do
        case "$source" in
            web|hn|x|youtube|reddit)
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

apply_manual_source_policy() {
    local raw_sources="$1"
    local preferred=()
    local source

    if [ "$APPLIED_MANUAL_PROFILE" != "fast" ] || [ -n "${LAST30DAYS_FORCE_SEARCH_SOURCES:-}" ]; then
        printf '%s' "$raw_sources"
        return
    fi

    IFS=',' read -ra source <<< "$raw_sources"
    for source in "${source[@]}"; do
        case "$source" in
            x|web|hn)
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

topic_queries_for_key() {
    local topic_key="$1"

    case "$topic_key" in
        claude-code)
            printf '%s\n' \
                "Anthropic Claude Code release notes changelog coding agent" \
                "Claude Code terminal workflow plugin review delegate tmux" \
                "Claude Code security policy quota incident postmortem"
            ;;
        codex)
            printf '%s\n' \
                "OpenAI Codex launch release notes codex cli chatgpt codex" \
                "Codex CLI MCP integration github figma notion slack" \
                "OpenAI Codex benchmark latency eval developer workflow"
            ;;
        large-models)
            printf '%s\n' \
                "OpenAI Anthropic Google DeepMind Meta Llama Qwen DeepSeek model release" \
                "reasoning model benchmark context window inference latency pricing" \
                "official blog model card api changelog release notes"
            ;;
        obsidian)
            printf '%s\n' \
                "Obsidian official release notes changelog plugin api" \
                "Obsidian dataview templater sync publish community plugin workflow" \
                "Obsidian vault markdown zettelkasten second brain knowledge workflow"
            ;;
        *)
            return 1
            ;;
    esac
}

count_query_runs() {
    local total=0
    local spec
    local topic_key
    local _unused1 _unused2 _unused3
    local search_topic

    for spec in "${TOPIC_SPECS[@]}"; do
        IFS='|' read -r _unused1 _unused2 _unused3 topic_key <<< "$spec"
        while IFS= read -r search_topic; do
            [ -n "$search_topic" ] || continue
            total=$((total + ${#TIME_WINDOWS_DAYS[@]}))
        done < <(topic_queries_for_key "$topic_key")
    done

    printf '%s\n' "$total"
}

if ! SKILL_ROOT="$(find_skill_root)"; then
    log_msg "ERROR: Could not find last30days.py"
    exit 1
fi

resolve_slot_metadata

PUBLIC_DIR="$PUBLIC_ROOT/$RESEARCH_TYPE"
ARTIFACT_DIR="$ARTIFACT_ROOT/$RESEARCH_TYPE/$DATE"

TOPIC_SPECS=(
    "Claude Code|Claude Code|claude-code-raw|claude-code"
    "Codex|Codex|codex-raw|codex"
    "大模型|大模型|large-models-raw|large-models"
    "Obsidian|Obsidian|obsidian-raw|obsidian"
)

mkdir -p "$PUBLIC_DIR" "$ARTIFACT_DIR"
compute_recent_window "$RESEARCH_TYPE"

if [ -n "${LAST30DAYS_FORCE_SEARCH_SOURCES:-}" ]; then
    ALL_SEARCH_SOURCES="$LAST30DAYS_FORCE_SEARCH_SOURCES"
    log_msg "Source selection forced by LAST30DAYS_FORCE_SEARCH_SOURCES=$ALL_SEARCH_SOURCES"
else
    if ALL_SEARCH_SOURCES="$(python3 "$REPO_DIR/scripts/select_last30days_sources.py" --skill-root "$SKILL_ROOT" --format csv 2>>"$LOG_FILE")"; then
        if [ -z "$ALL_SEARCH_SOURCES" ]; then
            ALL_SEARCH_SOURCES="hn"
            log_msg "Source selector returned empty set, falling back to HN only"
        fi
    else
        ALL_SEARCH_SOURCES="x,hn"
        log_msg "Source selector failed, falling back to $ALL_SEARCH_SOURCES"
    fi
fi

ALL_SEARCH_SOURCES="$(filter_monitor_sources "$ALL_SEARCH_SOURCES")"
SOURCE_SELECTION_BEFORE_MANUAL_POLICY="$ALL_SEARCH_SOURCES"
ALL_SEARCH_SOURCES="$(apply_manual_source_policy "$ALL_SEARCH_SOURCES")"
PLANNED_QUERY_RUNS="$(count_query_runs)"

log_msg "Starting $RESEARCH_TYPE unified monitoring run (trigger_mode=$TRIGGER_MODE, publish=$PUBLISH_REPORT)"
if [ "$TRIGGER_MODE" = "manual" ]; then
    log_msg "Manual profile: $APPLIED_MANUAL_PROFILE"
    if [ "$APPLIED_MANUAL_PROFILE" = "fast" ]; then
        log_msg "Manual fast defaults -> depth=$RESEARCH_DEPTH timeout=$RESEARCH_TIMEOUT time_windows=$TIME_WINDOWS_DAYS_CSV local_llm_disabled=${REPORT_MONITOR_DISABLE_LLM:-0}"
        if [ "$SOURCE_SELECTION_BEFORE_MANUAL_POLICY" != "$ALL_SEARCH_SOURCES" ]; then
            log_msg "Manual fast source policy reduced sources: $SOURCE_SELECTION_BEFORE_MANUAL_POLICY -> $ALL_SEARCH_SOURCES"
        fi
    fi
fi
log_msg "Raw artifacts -> $ARTIFACT_DIR"
log_msg "Node runtime: $(command -v node 2>/dev/null || echo unavailable) ($(node -v 2>/dev/null || echo unavailable))"
log_msg "Rolling window -> $WINDOW_START to $WINDOW_END"
log_msg "Using search sources: $ALL_SEARCH_SOURCES"
log_msg "Time windows (days): $TIME_WINDOWS_DAYS_CSV"
log_msg "Research depth: $RESEARCH_DEPTH"
log_msg "Global timeout: $RESEARCH_TIMEOUT"
log_msg "Planned last30days runs: $PLANNED_QUERY_RUNS"

overall_status=0
completed_topics=0
topic_args=()
monitor_body="$(mktemp)"
monitor_report="$(mktemp)"
register_temp_file "$monitor_body"
register_temp_file "$monitor_report"
public_report="$PUBLIC_DIR/01-monitor.md"

for spec in "${TOPIC_SPECS[@]}"; do
    IFS='|' read -r display_title report_title raw_slug topic_key <<< "$spec"
    raw_capture="$ARTIFACT_DIR/$raw_slug.md"
    successful_query_outputs=()
    query_index=0

    log_msg "Running research for topic: $display_title"
    log_msg "Raw capture -> $raw_capture"

    while IFS= read -r search_topic; do
        [ -n "$search_topic" ] || continue
        query_index=$((query_index + 1))
        for window_days in "${TIME_WINDOWS_DAYS[@]}"; do
            temp_capture="$(mktemp)"
            register_temp_file "$temp_capture"

            log_msg "Query $query_index for $display_title (days=$window_days): $search_topic"

            LAST30DAYS_CMD=(
                python3
                "${SKILL_ROOT}/scripts/last30days.py"
                "$search_topic"
                --emit=compact
                --search="$ALL_SEARCH_SOURCES"
                --days "$window_days"
                "--$RESEARCH_DEPTH"
                --timeout "$RESEARCH_TIMEOUT"
            )

            if "${LAST30DAYS_CMD[@]}" > "$temp_capture" 2>>"$LOG_FILE"; then
                successful_query_outputs+=("$temp_capture")
                log_msg "Query $query_index succeeded for $display_title (days=$window_days)"
            else
                status=$?
                rm -f "$temp_capture"
                log_msg "Query $query_index failed for $display_title (days=$window_days, exit $status)"
            fi
        done
    done < <(topic_queries_for_key "$topic_key")

    if [ "${#successful_query_outputs[@]}" -eq 0 ]; then
        overall_status=1
        log_msg "All focused queries failed for $display_title"
        continue
    fi

    if [ "${#successful_query_outputs[@]}" -eq 1 ]; then
        mv "${successful_query_outputs[0]}" "$raw_capture"
    else
        MERGE_CMD=(
            python3
            "$REPO_DIR/scripts/merge_compact_reports.py"
            --topic "$report_title"
        )
        for merged_input in "${successful_query_outputs[@]}"; do
            MERGE_CMD+=(--input "$merged_input")
        done

        if "${MERGE_CMD[@]}" > "$raw_capture" 2>>"$LOG_FILE"; then
            :
        else
            status=$?
            overall_status=1
            log_msg "Compact merge failed for $display_title (exit $status)"
            rm -f "${successful_query_outputs[@]}"
            continue
        fi
        rm -f "${successful_query_outputs[@]}"
    fi

    topic_args+=("--topic" "${topic_key}|${report_title}|${raw_capture}")
    completed_topics=$((completed_topics + 1))
    log_msg "Research completed: $display_title (${#successful_query_outputs[@]} focused queries merged)"
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
trigger_mode: "$TRIGGER_MODE"
trigger_schedule: "$TRIGGER_SCHEDULE"
time_windows_days: "$TIME_WINDOWS_DAYS_CSV"
window_start: "$WINDOW_START"
window_end: "$WINDOW_END"
search_sources: "$ALL_SEARCH_SOURCES"
permalink: /research/$RESEARCH_TYPE/monitor/
---

EOF
            cat "$monitor_body"
        } > "$monitor_report"

        mv "$monitor_report" "$public_report"
        cp "$public_report" "$PUBLIC_DIR/monitor-$DATE.md"
        prune_monitor_snapshots "$PUBLIC_DIR" 7
        write_archive_page "$RESEARCH_TYPE" 7
        log_msg "Unified monitoring page generated: $public_report"
    else
        status=$?
        rm -f "$monitor_report"
        if [ "$status" -eq 2 ]; then
            log_msg "No publishable high-quality topics found; keeping previous public page unchanged"
        else
            overall_status=1
            log_msg "Unified page synthesis failed (exit $status)"
        fi
    fi
else
    log_msg "No topic runs succeeded; skipping unified page generation"
fi

write_index "morning" "Daily Research" "10:00 Beijing time"

if [ "$PUBLISH_REPORT" -eq 1 ] && git rev-parse --git-dir > /dev/null 2>&1; then
    log_msg "Publishing public research updates..."
    git add _research

    if git diff --cached --quiet; then
        log_msg "No public report changes to commit"
    else
        if git commit -m "Research update: $RESEARCH_TYPE $DATE $TIME" >> "$LOG_FILE" 2>&1; then
            if [ "$PUSH_AFTER_COMMIT" -eq 1 ]; then
                git push origin main >> "$LOG_FILE" 2>&1 || log_msg "Git push failed"
            else
                log_msg "Skipping git push (--no-push enabled)"
            fi
        else
            log_msg "Git commit failed"
            overall_status=1
        fi
    fi
else
    log_msg "Publish step disabled; generated reports remain in working tree"
fi

log_msg "Daily research completed for $RESEARCH_TYPE ($completed_topics/${#TOPIC_SPECS[@]} topic captures succeeded)"

exit "$overall_status"
