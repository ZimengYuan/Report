# Project Memory

## System Overview
Automated research system that runs twice daily (10am, 8pm Beijing time) using last30days to generate a public report and deploy it to GitHub Pages.

## last30days Configuration
- **Status**: Installed and configured
- **X/Twitter Data Source**: Temporarily unavailable (Bearer token expired)
- **ScrapeCreators API**: Configured
- **xAI API Key**: Configured (network connectivity issues)
- **Configured / Conditional Sources**: Reddit, X, Hacker News, Polymarket, YouTube, TikTok, Instagram, Bluesky, Truth Social, Xiaohongshu, native web
- **Public Automation Policy**: Both time slots run both standard topics and attempt all configured data sources.

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
└── scripts/             # Automation scripts
```

## Cron Jobs
- `0 10 * * *` - Morning research (10:00 Beijing)
- `0 20 * * *` - Evening research (20:00 Beijing)

## Data Flow
1. Cron triggers daily-research.sh
2. Script selects the active time slot
3. last30days runs both standard topics for that slot
4. Public Markdown reports are written to `_research/<slot>/<date>/`
5. Raw compact output is stored under `artifacts/raw-research/`
6. Public indexes are regenerated
7. Only `_research/` is committed and pushed
8. GitHub Pages serves the updated site
