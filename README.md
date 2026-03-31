# Report

Automated research briefs published to GitHub Pages.

## What This Repo Does

- `10:00` Beijing time: generate the morning briefs for both topics
- `20:00` Beijing time: generate the evening briefs for both topics
- publish only the curated Markdown briefs under `_research/`
- keep raw compact captures under `artifacts/raw-research/`, then synthesize them into trend pages with at most 15 条精选条目

Raw compact outputs are saved under `artifacts/raw-research/` for local debugging and are excluded from both git and the public site.

## Repo Layout

- `_research/`: published trend briefs and per-section indexes
- `artifacts/raw-research/`: local-only raw captures
- `scripts/daily-research.sh`: automation entrypoint
- `scripts/synthesize_public_report.py`: second-stage formatter for the published briefs
- `cron/research.cron`: canonical cron schedule for 10:00 / 20:00 Beijing runs
- `scripts/install-cron.sh`: installs the repo cron file into local crontab
- `memory/`: local operating notes

## Source Policy

The public automation now auto-selects the healthiest currently available `last30days` sources before each run.

- always prefers healthy configured sources such as `X`, `YouTube`, `Hacker News`, and native web search when available
- probes `ScrapeCreators` before enabling `Reddit`, `TikTok`, and `Instagram`
- skips sources that are configured but currently unhealthy, such as `402` credit exhaustion or unreachable public APIs
- can still be overridden manually with `LAST30DAYS_FORCE_SEARCH_SOURCES=x,youtube,hn`

## Triggering With Cron

Canonical crontab:

```cron
0 10 * * * cd /home/nie/Claude/info/Report && bash scripts/daily-research.sh >> /home/nie/Claude/info/Report/logs/cron.log 2>&1
0 20 * * * cd /home/nie/Claude/info/Report && bash scripts/daily-research.sh >> /home/nie/Claude/info/Report/logs/cron.log 2>&1
```

Install it with:

```bash
bash scripts/install-cron.sh
```

## Running Locally

```bash
bash scripts/daily-research.sh
```

The script:

1. infers the active slot from the current hour
2. runs `scripts/select_last30days_sources.py` to pick healthy sources for the current machine
3. runs `last30days --emit=compact --quick` for both standard topics using focused search queries
4. stores raw captures under `artifacts/raw-research/<slot>/<date>/`
5. synthesizes curated briefs into `_research/<slot>/01-claude-code-codex.md` and `_research/<slot>/02-ai-overview.md`
6. refreshes the slot-level index files under `_research/morning/` and `_research/evening/`
7. writes `updated_at` with second-level precision into each page front matter
8. commits only `_research/` when there are published changes

Useful overrides:

- `LAST30DAYS_FORCE_SEARCH_SOURCES=reddit,x,youtube,hn`
- `LAST30DAYS_RESEARCH_DEPTH=default`

## Safety Notes

- do not store token values in repo files
- keep `~/.config/last30days/.env` permission-restricted
- `.codex/`, `logs/`, and `artifacts/` should stay out of commits
