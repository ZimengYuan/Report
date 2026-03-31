# Project Memory

## System Overview
Automated research system that runs twice daily (10am, 8pm Beijing time) using last30days to generate a public report and deploy it to GitHub Pages.

## last30days Configuration
- **Status**: Installed and configured
- **X/Twitter Data Source**: Working through Bird-authenticated search
- **ScrapeCreators API**: Configured but currently credit-limited (`402` on live probe)
- **YouTube**: `yt-dlp` installed and working, but slower in default-depth runs
- **Configured / Conditional Sources**: Reddit, X, Hacker News, Polymarket, YouTube, TikTok, Instagram, Bluesky, Truth Social, Xiaohongshu, native web
- **Public Automation Policy**: Both time slots run both standard topics and auto-select healthy sources before each run.

## GitHub Integration
- **Repository**: https://github.com/ZimengYuan/Report.git
- **Branch**: main (for GitHub Pages)
- **Token**: Configured locally (not documented in repo files)

## Project Structure
```
Report/
├── README.md            # Operating notes
├── memory/              # Configuration and state
├── _research/           # Public reports served by GitHub Pages
├── artifacts/           # Raw local-only outputs (excluded from the site)
├── _layouts/            # Jekyll layouts
├── assets/              # Static assets
└── scripts/             # Automation + second-stage synthesis
```

## Cron Jobs
- `0 10 * * *` - Morning research (10:00 Beijing)
- `0 20 * * *` - Evening research (20:00 Beijing)

## Data Flow
1. Cron triggers daily-research.sh
2. Script selects the active time slot
3. `scripts/select_last30days_sources.py` chooses healthy sources for the current machine
4. last30days runs both standard topics for that slot in compact mode
5. Raw compact output is stored under `artifacts/raw-research/<slot>/<date>/`
6. `scripts/synthesize_public_report.py` converts the raw capture into a curated public report
7. Public Markdown reports are written to `_research/<slot>/01-claude-code-codex.md` and `_research/<slot>/02-ai-overview.md`
8. Public indexes are regenerated
9. Only `_research/` is committed and pushed
10. GitHub Pages serves the updated site
