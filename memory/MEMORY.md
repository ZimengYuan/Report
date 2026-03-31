# Project Memory

## System Overview
Automated research system that runs twice daily (10am, 8pm Beijing time) using last30days to generate a concise trend brief and deploy it to GitHub Pages.

## last30days Configuration
- **Status**: Installed and configured
- **X/Twitter Data Source**: Working through Bird-authenticated search
- **ScrapeCreators API**: Configured but currently credit-limited (`402` on live probe)
- **YouTube**: `yt-dlp` installed and working, but slower in default-depth runs
- **Configured / Conditional Sources**: Reddit, X, Hacker News, Polymarket, YouTube, TikTok, Instagram, Bluesky, Truth Social, Xiaohongshu, native web
- **Public Automation Policy**: Both time slots run both standard topics, auto-select healthy sources before each run, and use a rolling window from the previous official slot to now.
- **Blog Priority**: When native web search is configured, blog/docs/official-post results should be treated as higher-value references.

## GitHub Integration
- **Repository**: https://github.com/ZimengYuan/Report.git
- **Branch**: main (for GitHub Pages)
- **Token**: Configured locally (not documented in repo files)

## Project Structure
```
Report/
├── README.md            # Operating notes
├── memory/              # Configuration and state
├── _research/           # Published trend briefs served by GitHub Pages
├── artifacts/           # Raw local-only outputs (excluded from the site)
├── _layouts/            # Jekyll layouts
├── assets/              # Static assets
└── scripts/             # Automation + second-stage synthesis
```

## Cron Jobs
- `0 10 * * *` - Morning research (10:00 Beijing)
- `0 20 * * *` - Evening research (20:00 Beijing)
- Canonical file: `cron/research.cron`
- Installer: `scripts/install-cron.sh`

## Data Flow
1. Cron triggers daily-research.sh
2. Script selects the active time slot
3. Script computes the rolling window from the previous official slot to now
4. `scripts/select_last30days_sources.py` chooses healthy sources for the current machine
5. last30days runs both standard topics for that slot in compact mode
6. Raw compact output is stored under `artifacts/raw-research/<slot>/<date>/`
7. `scripts/synthesize_public_report.py` converts the raw capture into a concise trend brief with at most 15 curated items
8. Published Markdown briefs are written to `_research/<slot>/01-claude-code-codex.md` and `_research/<slot>/02-ai-overview.md`
9. Each page front matter records `updated_at`, `window_start`, `window_end`, and cron trigger metadata
10. Public indexes are regenerated
11. Only `_research/` is committed and pushed during automated runs
12. GitHub Pages serves the updated site
