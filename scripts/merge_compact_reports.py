#!/usr/bin/env python3
"""Merge multiple last30days compact reports into one normalized compact report."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date
from pathlib import Path

from synthesize_public_report import (
    ParsedCompactReport,
    SECTION_TO_SOURCE,
    SOURCE_LABELS,
    Item,
    clean_text,
    parse_compact_report,
    report_window,
)


def merge_date_range(reports: list[ParsedCompactReport]) -> str:
    starts: list[date] = []
    ends: list[date] = []
    for report in reports:
        start, end = report_window(report)
        if start:
            starts.append(start)
        if end:
            ends.append(end)
    if not starts or not ends:
        return reports[0].date_range if reports else ""
    return f"{min(starts).isoformat()} to {max(ends).isoformat()}"


def item_key(item: Item) -> str:
    if item.url:
        return f"url:{item.url.strip()}"
    fingerprint = "|".join(
        [
            item.source,
            clean_text(item.identifier).lower(),
            clean_text(item.date).lower(),
            clean_text(item.summary or item.byline).lower()[:160],
        ]
    )
    return f"fp:{fingerprint}"


def merge_items(items: list[Item]) -> list[Item]:
    merged: dict[str, Item] = {}
    for item in items:
        key = item_key(item)
        existing = merged.get(key)
        if existing is None or item.score > existing.score:
            merged[key] = item
            continue

        if not existing.url and item.url:
            existing.url = item.url
        if not existing.summary and item.summary:
            existing.summary = item.summary
        if not existing.why_relevant and item.why_relevant:
            existing.why_relevant = item.why_relevant
        if not existing.engagement and item.engagement:
            existing.engagement = item.engagement
        if not existing.date and item.date:
            existing.date = item.date
        if item.highlights:
            seen = {clean_text(h) for h in existing.highlights}
            for highlight in item.highlights:
                normalized = clean_text(highlight)
                if normalized and normalized not in seen:
                    existing.highlights.append(highlight)
                    seen.add(normalized)

    return sorted(merged.values(), key=lambda item: item.score, reverse=True)


def render_item(item: Item) -> list[str]:
    header = f"**{item.identifier}** (score:{item.score}) {item.byline}"
    if item.date:
        header += f" ({item.date})"
    if item.engagement:
        header += f" [{item.engagement}]"

    lines = [header]
    if item.summary:
        lines.append(f"  {item.summary}")
        lines.append("")
    if item.url:
        lines.append(f"  {item.url}")
    if item.highlights:
        lines.append("  Highlights:")
        for highlight in item.highlights[:5]:
            lines.append(f"    - {highlight}")
    if item.why_relevant:
        lines.append(f"  *{item.why_relevant}*")
    lines.append("  **")
    lines.append("")
    return lines


def render_status_lines(report: ParsedCompactReport) -> list[str]:
    lines: list[str] = []
    for source in SECTION_TO_SOURCE.values():
        items = report.items_by_source.get(source, [])
        if items:
            noun = {
                "x": "posts",
                "youtube": "videos",
                "hn": "stories",
                "web": "results",
                "reddit": "threads",
                "tiktok": "videos",
                "instagram": "reels",
                "bluesky": "posts",
                "truthsocial": "posts",
                "polymarket": "markets",
            }.get(source, "items")
            lines.append(f"  ✅ {SOURCE_LABELS.get(source, source)}: {len(items)} {noun}")
            continue

        error_text = report.errors_by_source.get(source)
        if error_text:
            lines.append(f"  ⚡ {SOURCE_LABELS.get(source, source)}: {error_text}")
    return lines


def merge_reports(topic: str, reports: list[ParsedCompactReport]) -> ParsedCompactReport:
    merged = ParsedCompactReport(items_by_source={source: [] for source in SECTION_TO_SOURCE.values()})
    merged.topic = topic or next((report.topic for report in reports if report.topic), "")
    merged.date_range = merge_date_range(reports)
    merged.mode = next((report.mode for report in reports if report.mode), "")
    merged.model = next((report.model for report in reports if report.model), "")
    merged.limited_recent = any(report.limited_recent for report in reports)
    merged.quality_line = f"合并 {len(reports)} 组 focused queries 后重新去重输出"

    source_items: dict[str, list[Item]] = defaultdict(list)
    source_errors: dict[str, list[str]] = defaultdict(list)

    for report in reports:
        for source, items in report.items_by_source.items():
            source_items[source].extend(items)
        for source, error_text in report.errors_by_source.items():
            if error_text:
                source_errors[source].append(error_text)

    for source in SECTION_TO_SOURCE.values():
        merged.items_by_source[source] = merge_items(source_items.get(source, []))
        if not merged.items_by_source[source] and source_errors.get(source):
            unique_errors: list[str] = []
            for error_text in source_errors[source]:
                normalized = clean_text(error_text)
                if normalized and normalized not in unique_errors:
                    unique_errors.append(normalized)
            if unique_errors:
                merged.errors_by_source[source] = " | ".join(unique_errors)

    merged.source_status_lines = []
    return merged


def render_compact_report(report: ParsedCompactReport) -> str:
    lines: list[str] = [
        f"## Research Results: {report.topic}",
        "",
        f"**Date Range:** {report.date_range or '未识别'}",
        f"**Mode:** {report.mode or 'both'}",
        f"**OpenAI Model:** {report.model or 'unknown'}",
        "",
    ]

    for section_name, source in SECTION_TO_SOURCE.items():
        lines.append(f"### {section_name}")
        lines.append("")
        items = report.items_by_source.get(source, [])
        if items:
            for item in items:
                lines.extend(render_item(item))
        elif source in report.errors_by_source:
            lines.append(f"**ERROR:** {report.errors_by_source[source]}")
            lines.append("")
        else:
            lines.append(f"*No relevant {SOURCE_LABELS.get(source, source)} found for this topic.*")
            lines.append("")

    lines.append("---")
    lines.append("**Sources:**")
    status_lines = report.source_status_lines or render_status_lines(report)
    lines.extend(status_lines)
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Merge multiple compact reports into one normalized compact report.")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--input", action="append", required=True, help="Compact report path; repeat for multiple inputs")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    reports = [parse_compact_report(Path(path)) for path in args.input]
    merged = merge_reports(args.topic, reports)
    print(render_compact_report(merged), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
