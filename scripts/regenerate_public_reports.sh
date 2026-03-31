#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"

render_report() {
    local raw_input="$1"
    local report_title="$2"
    local slot="$3"
    local date="$4"
    local permalink="$5"
    local topic_key="$6"
    local output_file="$7"
    local temp_body

    temp_body="$(mktemp)"
    python3 "$REPO_DIR/scripts/synthesize_public_report.py" \
        --input "$raw_input" \
        --topic-key "$topic_key" \
        --report-title "$report_title" \
        --slot "$slot" \
        --date "$date" > "$temp_body"

    {
        cat <<EOF
---
layout: research
title: "$report_title"
type: "$slot"
public_report: true
date: $date
permalink: $permalink
---

EOF
        cat "$temp_body"
    } > "$output_file"

    rm -f "$temp_body"
}

render_report \
    "$REPO_DIR/artifacts/raw-research/morning/2026-03-31/claude-code-codex-raw.md" \
    "Claude Code && Codex" \
    "morning" \
    "2026-03-31" \
    "/research/morning/claude-code-codex/" \
    "claude-code-codex" \
    "$REPO_DIR/_research/morning/01-claude-code-codex.md"

render_report \
    "$REPO_DIR/artifacts/raw-research/morning/2026-03-31/ai-raw.md" \
    "AI 发展总览" \
    "morning" \
    "2026-03-31" \
    "/research/morning/ai-overview/" \
    "ai-overview" \
    "$REPO_DIR/_research/morning/02-ai-overview.md"
