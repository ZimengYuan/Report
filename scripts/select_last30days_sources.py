#!/usr/bin/env python3
"""Select the healthiest last30days sources for automated public runs."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib import error, parse, request


USER_AGENT = "ReportSourceSelector/1.0"
SOURCE_ORDER = [
    "reddit",
    "x",
    "youtube",
    "hn",
    "bluesky",
    "truthsocial",
    "tiktok",
    "instagram",
    "polymarket",
    "web",
    "xiaohongshu",
]


def load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def run_diagnose(skill_root: Path) -> dict[str, Any]:
    cmd = ["python3", str(skill_root / "scripts" / "last30days.py"), "--diagnose"]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "last30days --diagnose failed")

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse diagnose output: {exc}") from exc


def http_probe(url: str, headers: dict[str, str] | None = None, timeout: int = 10) -> tuple[int | None, str]:
    req = request.Request(url, headers=headers or {"User-Agent": USER_AGENT})
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(400).decode("utf-8", "ignore")
            return resp.getcode(), body
    except error.HTTPError as exc:
        body = exc.read(400).decode("utf-8", "ignore")
        return exc.code, body
    except Exception as exc:  # pragma: no cover - network/environment specific
        return None, str(exc)


def probe_scrapecreators(token: str | None) -> tuple[bool, str]:
    if not token:
        return False, "SCRAPECREATORS_API_KEY missing"

    params = parse.urlencode({"query": "ai", "sort": "relevance", "timeframe": "week"})
    url = f"https://api.scrapecreators.com/v1/reddit/search?{params}"
    status, body = http_probe(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "x-api-key": token,
        },
        timeout=12,
    )

    if status == 200:
        return True, "ScrapeCreators responding normally"
    if status == 402:
        return False, "ScrapeCreators credits exhausted"
    if status in (401, 403):
        return False, "ScrapeCreators key rejected"
    if status is None:
        return False, f"ScrapeCreators probe failed: {body}"
    return False, f"ScrapeCreators probe returned HTTP {status}"


def probe_polymarket() -> tuple[bool, str]:
    params = parse.urlencode(
        {
            "q": "ai",
            "page": "1",
            "events_status": "active",
            "keep_closed_markets": "0",
        }
    )
    url = f"https://gamma-api.polymarket.com/public-search?{params}"
    status, body = http_probe(url, timeout=12)
    if status == 200:
        return True, "Polymarket public API reachable"
    if status is None:
        return False, f"Polymarket probe failed: {body}"
    return False, f"Polymarket probe returned HTTP {status}"


def choose_sources(diag: dict[str, Any], env_file: dict[str, str]) -> tuple[list[str], dict[str, str]]:
    selected: list[str] = []
    reasons: dict[str, str] = {}

    def enable(source: str, reason: str) -> None:
        if source not in selected:
            selected.append(source)
        reasons[source] = reason

    if diag.get("openai") or diag.get("reddit_public"):
        if env_file.get("SCRAPECREATORS_API_KEY"):
            sc_ok, sc_reason = probe_scrapecreators(env_file.get("SCRAPECREATORS_API_KEY"))
            if sc_ok:
                enable("reddit", sc_reason)
                if diag.get("tiktok"):
                    enable("tiktok", sc_reason)
                if diag.get("instagram"):
                    enable("instagram", sc_reason)
            else:
                reasons["reddit"] = sc_reason
                if diag.get("tiktok"):
                    reasons["tiktok"] = sc_reason
                if diag.get("instagram"):
                    reasons["instagram"] = sc_reason
        else:
            enable("reddit", "Using non-ScrapeCreators Reddit fallback")

    x_source = diag.get("x_source")
    if x_source == "xai" and diag.get("xai"):
        enable("x", "Using xAI-backed X search")
    elif diag.get("bird_authenticated"):
        enable("x", "Using Bird-authenticated X search")
    elif x_source not in (None, "none"):
        enable("x", f"Using X source: {x_source}")
    else:
        reasons["x"] = "No working X source detected"

    if diag.get("youtube"):
        enable("youtube", "yt-dlp available")
    else:
        reasons["youtube"] = "yt-dlp not installed"

    if diag.get("hackernews"):
        enable("hn", "Hacker News is public")

    if diag.get("bluesky"):
        enable("bluesky", "Bluesky credentials present")
    else:
        reasons["bluesky"] = "Bluesky credentials missing"

    if diag.get("truthsocial"):
        enable("truthsocial", "Truth Social token present")
    else:
        reasons["truthsocial"] = "Truth Social token missing"

    if diag.get("xiaohongshu"):
        enable("xiaohongshu", "Xiaohongshu API configured")
    else:
        reasons["xiaohongshu"] = "Xiaohongshu API unavailable"

    if diag.get("web_search_backend"):
        enable("web", f"Native web backend: {diag['web_search_backend']}")
    else:
        reasons["web"] = "No native web backend configured"

    if diag.get("polymarket"):
        pm_ok, pm_reason = probe_polymarket()
        if pm_ok:
            enable("polymarket", pm_reason)
        else:
            reasons["polymarket"] = pm_reason

    selected = [source for source in SOURCE_ORDER if source in selected]
    return selected, reasons


def format_output(selected: list[str], reasons: dict[str, str], fmt: str) -> str:
    if fmt == "csv":
        return ",".join(selected)
    if fmt == "json":
        return json.dumps({"selected_sources": selected, "reasons": reasons}, ensure_ascii=False, indent=2)
    raise ValueError(f"Unsupported format: {fmt}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Choose healthy last30days sources for automation")
    parser.add_argument("--skill-root", required=True, help="Path to last30days skill root")
    parser.add_argument("--env-file", default=str(Path.home() / ".config" / "last30days" / ".env"))
    parser.add_argument("--format", choices=["csv", "json"], default="json")
    args = parser.parse_args()

    skill_root = Path(args.skill_root).expanduser().resolve()
    env_file = load_env_file(Path(args.env_file).expanduser())

    diag = run_diagnose(skill_root)
    selected, reasons = choose_sources(diag, env_file)

    if not selected:
        selected = ["hn"]
        reasons["hn"] = "Fallback to public HN only"

    print(format_output(selected, reasons, args.format))
    enabled_summary = ", ".join(selected)
    sys.stderr.write(f"[source-select] enabled: {enabled_summary}\n")
    for source in SOURCE_ORDER:
        if source in selected:
            sys.stderr.write(f"[source-select] {source}: {reasons.get(source, 'enabled')}\n")
        else:
            reason = reasons.get(source)
            if reason:
                sys.stderr.write(f"[source-select] {source}: skipped ({reason})\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
