# Report Agent Notes

## Purpose

This repo publishes a daily four-topic monitoring page to GitHub Pages.
Timezone is `Asia/Shanghai`. Canonical run slot is `10:00`.

## Fixed Topics

- Claude Code
- Codex
- 大模型
- Obsidian

## Core Flow

1. `scripts/daily-research.sh`
   Chooses `morning` or `evening`, computes the rolling time window, selects healthy `last30days` sources, runs multiple focused queries per topic, merges per-topic compact outputs, synthesizes the final public page, rebuilds slot indexes. Publishing (`git commit/push`) happens only when run with `--publish` (cron template already passes it).
2. `scripts/select_last30days_sources.py`
   Selects healthy search sources for the current machine. The public monitor flow prefers `web`, `hn`, `x`, and `youtube` when available.
3. `scripts/merge_compact_reports.py`
   Deduplicates and merges multiple compact reports for one topic.
4. `scripts/synthesize_monitor_page.py`
   Selects publishable items, merges similar events, enriches `web` and `hn` links, generates Chinese summaries and topic trend summaries, then renders the final monitor page.

## Rolling Window Rules

- Daily run: previous official slot -> current run time (canonical schedule at `10:00`)
- Collection time windows: `1 day` and `7 days` are both queried then merged

## Important Directories

- `README.md`: high-level operating notes
- `memory/`: local runbooks and environment notes
- `_research/`: published public reports served by GitHub Pages
- `_research/morning/archive.md`: recent 7-day public monitor archive page
- `artifacts/raw-research/`: local-only raw compact captures
- `logs/`: run logs
- `cron/research.cron`: canonical schedule
- `scripts/`: automation and synthesis logic

## Output Rules

- Public pages should be synthesized monitor pages, not raw `last30days` dumps.
- Final pages should be in Chinese.
- Summaries should be concrete: explain what happened, which tools/models/vendors are involved, and the risk or value.
- Strategy is `AI-first, heuristic-fallback`.
- Do not hardcode model names. Parse them from compact raw files and show the actual model used on the page.
- If different topics were generated with different models, reflect that in the page.
- `scripts/daily-research.sh` writes `monitor-YYYY-MM-DD.md` snapshots and keeps only the latest 7 for archive browsing.

## Common Commands

- Full run: `bash scripts/daily-research.sh`
- Full run + publish: `bash scripts/daily-research.sh --trigger-mode cron --publish --time-windows 1,7`
- Install cron: `bash scripts/install-cron.sh`
- Check working tree first: `git status --short`

If only rendering or summarization logic changed, it is valid to regenerate from existing raw artifacts instead of re-running collection. Use `scripts/synthesize_monitor_page.py` with the files under `artifacts/raw-research/...`.

## Safety Notes

- Only `_research/` should be auto-committed by the scheduled run.
- `artifacts/`, `logs/`, and local agent notes should stay out of the published site.
- Keep secrets out of repo files. Local notes mention `~/.config/last30days/.env` should remain permission-restricted.
- Do not overwrite unrelated user changes when preparing commits.

## Environment Notes

- `last30days` is installed locally.
- Repo memory says X / Bird-authenticated search has worked before, but another memory note says the bearer token may be expired. Verify current source health through the selector and logs instead of assuming.
- `yt-dlp` and a working Node runtime are expected for YouTube-related work.
