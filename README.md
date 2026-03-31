# Report

Automated research reports published to GitHub Pages.

## What This Repo Does

- `10:00` Beijing time: generate the morning public reports for both topics
- `20:00` Beijing time: generate the evening public reports for both topics
- publish only the curated Markdown reports under `_research/`

Raw compact outputs are saved under `artifacts/raw-research/` for local debugging and are excluded from both git and the public site.

## Repo Layout

- `_research/`: public reports and per-section indexes
- `artifacts/raw-research/`: local-only raw captures
- `scripts/daily-research.sh`: automation entrypoint
- `memory/`: local operating notes

## Source Policy

The default public automation attempts every configured source made available by `last30days`, including:

- `Reddit`
- `X`
- `Hacker News`
- `YouTube`
- `TikTok`
- `Instagram`
- `Polymarket`
- `Bluesky`
- `Truth Social`
- `Xiaohongshu`
- native web search when a backend is configured

Availability still depends on local credentials and upstream service health, but the script no longer trims the source set by policy.

## Running Locally

```bash
bash scripts/daily-research.sh
```

The script:

1. infers the active slot from the current hour
2. runs `last30days` for both standard topics
3. writes public reports into `_research/<slot>/01-claude-code-codex.md` and `_research/<slot>/02-ai-overview.md`
4. refreshes the slot-level index files under `_research/morning/` and `_research/evening/`
5. commits only `_research/` when there are public changes

## Safety Notes

- do not store token values in repo files
- keep `~/.config/last30days/.env` permission-restricted
- `.codex/`, `logs/`, and `artifacts/` should stay out of commits
