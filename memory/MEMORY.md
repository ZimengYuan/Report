# Project Memory

## System Overview
Automated research system that runs twice daily (10am, 8pm Beijing time) using last30days to research specified topics and deploy results to GitHub Pages.

## last30days Configuration
- **Status**: Installed and configured
- **X/Twitter Data Source**: Temporarily unavailable (Bearer token expired)
- **ScrapeCreators API**: Configured
- **xAI API Key**: Configured (network connectivity issues)
- **Active Data Sources**: Reddit, Hacker News, Polymarket, YouTube, TikTok, Instagram

## GitHub Integration
- **Repository**: https://github.com/ZimengYuan/Report.git
- **Branch**: main (for GitHub Pages)
- **Token**: Pending user configuration

## Project Structure
```
Report/
├── memory/              # Configuration and state
├── _research/           # Research outputs (morning/evening)
├── _includes/           # Jekyll includes
├── _layouts/            # Jekyll layouts
├── assets/              # Static assets
└── scripts/             # Automation scripts
```

## Cron Jobs
- `0 10 * * *` - Morning research (10:00 Beijing)
- `0 20 * * *` - Evening research (20:00 Beijing)

## Data Flow
1. Cron triggers daily-research.sh
2. Script runs last30days for each topic
3. Results saved to _research/morning/ or _research/evening/
4. Jekyll site regenerated
5. Committed and pushed to GitHub
6. GitHub Pages serves the updated site
