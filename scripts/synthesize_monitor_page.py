#!/usr/bin/env python3
"""Combine multiple topic briefs into one monitoring page."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from synthesize_public_report import (
    TOPIC_RULES,
    SOURCE_LABELS,
    clean_text,
    combined_heat_score,
    error_summary,
    heat_label,
    item_within_window,
    markdown_safe_text,
    parse_compact_report,
    pick_curated_items,
    render_theme_bullets,
    short_snippet,
    source_summary,
)


TOPIC_ORDER = ["claude-code", "codex", "large-models", "obsidian"]


@dataclass
class TopicPayload:
    topic_key: str
    title: str
    input_path: Path


@dataclass
class TopicSection:
    topic_key: str
    title: str
    report_date_range: str
    source_summary_text: str
    error_summary_text: str
    quality_line: str
    curated_items: list
    stats: dict[str, int]


def parse_topic_payload(raw_spec: str) -> TopicPayload:
    parts = raw_spec.split("|", 2)
    if len(parts) != 3:
        raise ValueError(f"Invalid --topic spec: {raw_spec}")
    topic_key, title, input_path = parts
    return TopicPayload(topic_key=topic_key, title=title, input_path=Path(input_path))


def is_publishable(section: TopicSection) -> bool:
    if not section.curated_items:
        return False

    top_heat = max(combined_heat_score(item, section.topic_key) for item in section.curated_items)
    trusted_hit = any(
        item.source in {"web", "hn"} and combined_heat_score(item, section.topic_key) >= 58
        for item in section.curated_items
    )
    return top_heat >= 65 or trusted_hit


def select_global_items(sections: list[TopicSection], max_items: int = 15) -> dict[str, list]:
    ranked = {
        section.topic_key: sorted(
            section.curated_items,
            key=lambda item: combined_heat_score(item, section.topic_key),
            reverse=True,
        )
        for section in sections
    }

    selected: dict[str, list] = {section.topic_key: [] for section in sections}
    chosen_count = 0

    for section in sections:
        if ranked[section.topic_key] and chosen_count < max_items:
            selected[section.topic_key].append(ranked[section.topic_key].pop(0))
            chosen_count += 1

    remaining: list[tuple[int, str, object]] = []
    for section in sections:
        for item in ranked[section.topic_key][:5]:
            remaining.append((combined_heat_score(item, section.topic_key), section.topic_key, item))

    remaining.sort(key=lambda entry: entry[0], reverse=True)

    for _heat, topic_key, item in remaining:
        if chosen_count >= max_items:
            break
        if item in selected[topic_key]:
            continue
        if len(selected[topic_key]) >= 4:
            continue
        selected[topic_key].append(item)
        chosen_count += 1

    return selected


def render_topic_section(section: TopicSection, chosen_items: list) -> list[str]:
    lines = [
        f"## {section.title}",
        "",
    ]
    lines.extend(render_theme_bullets(section.curated_items, section.topic_key)[:2])
    lines.extend(
        [
            f"- 本轮有效来源：{section.source_summary_text}",
        ]
    )
    if section.quality_line:
        lines.append(f"- 抓取质量提示：{section.quality_line}")
    if section.error_summary_text:
        lines.append(f"- 异常来源：{section.error_summary_text}")
    lines.extend(
        [
            "",
            "### 精华条目",
            "",
        ]
    )

    for index, item in enumerate(chosen_items, start=1):
        source_label = SOURCE_LABELS.get(item.source, item.source)
        lines.extend(
            [
                f"#### {index}. {source_label} · {item.date or '日期未识别'}",
                "",
                f"- 热度：{heat_label(item, section.topic_key)}",
                f"- 总结：{short_snippet(item, 180)}",
                f"- 链接：{'[打开原文](' + item.url + ')' if item.url else '无链接'}",
                "",
            ]
        )

    return lines


def build_sections(payloads: list[TopicPayload]) -> list[TopicSection]:
    sections: list[TopicSection] = []
    for payload in payloads:
        report = parse_compact_report(payload.input_path)
        curated_items, stats = pick_curated_items(report, payload.topic_key)
        curated_items = [item for item in curated_items if item_within_window(item, report)]
        section = TopicSection(
            topic_key=payload.topic_key,
            title=payload.title,
            report_date_range=report.date_range,
            source_summary_text=source_summary(report),
            error_summary_text=error_summary(report),
            quality_line=clean_text(report.quality_line),
            curated_items=curated_items,
            stats=stats,
        )
        if is_publishable(section):
            sections.append(section)
    sections.sort(key=lambda section: TOPIC_ORDER.index(section.topic_key))
    return sections


def render_page(
    sections: list[TopicSection],
    slot: str,
    report_date: str,
    window_start: str,
    window_end: str,
    search_sources: str,
) -> str:
    if not sections:
        raise ValueError("No publishable sections available")

    selected = select_global_items(sections)
    total_items = sum(len(items) for items in selected.values())
    active_titles = "、".join(section.title for section in sections)
    source_set = sorted(
        {SOURCE_LABELS.get(item.source, item.source) for section in sections for item in selected[section.topic_key]}
    )
    source_text = "、".join(source_set) if source_set else "暂无稳定来源"
    slot_label = "早间" if slot == "morning" else "晚间"
    blog_hits = sum(1 for section in sections for item in selected[section.topic_key] if item.source == "web")
    hn_hits = sum(1 for section in sections for item in selected[section.topic_key] if item.source == "hn")
    x_hits = sum(1 for section in sections for item in selected[section.topic_key] if item.source == "x")

    lines = [
        f"# 四主题监控简报",
        "",
        f"**时段：** {slot_label}",
        f"**日期：** {report_date}",
        f"**抓取窗口：** {window_start} 至 {window_end}",
        f"**启用数据源：** {search_sources or '未记录'}",
        "",
        "## 当前总览",
        "",
        f"- 本轮监控的主题是：{active_titles}。",
        f"- 本页最终只保留了 {total_items} 条精华内容，全部来自当前窗口里更值得看的条目。",
        f"- 当前最值得优先看的来源是：{source_text}。",
    ]

    if blog_hits > 0:
        lines.append(f"- 本轮拿到了 {blog_hits} 条博客/网页内容，博客与技术文章仍然是判断长期趋势最稳的参考。")
    else:
        lines.append("- 本轮没有拿到可用的博客/网页结果；如果要更稳定抓博客，需要补上原生 web 搜索后端。")

    if x_hits > 0:
        lines.append(f"- 推文侧本轮保留了 {x_hits} 条高相关内容，适合看一线使用反馈和即时观点。")
    if hn_hits > 0:
        lines.append(f"- Hacker News 本轮保留了 {hn_hits} 条内容，适合补足博客与开发者讨论。")

    lines.extend(
        [
            "",
            "## 分主题监控",
            "",
        ]
    )

    for section in sections:
        lines.extend(render_topic_section(section, selected[section.topic_key]))

    lines.extend(
        [
            "## 当前整体趋势",
            "",
            f"- 过去这个时段里，更强的公共信号集中在 {active_titles} 这几条线上，而不是泛 AI 新闻。",
            "- 如果某个主题本轮没有进入页面，通常不是完全没有讨论，而是没有筛到足够强、足够干净的优质条目。",
            "- 这页会优先保留博客、技术文章、Hacker News 与高信噪比推文；泛广告、低质量转发和弱相关内容会被直接过滤。",
        ]
    )

    return "\n".join(lines).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a single monitoring page from multiple compact reports.")
    parser.add_argument("--topic", action="append", required=True, help="topic_key|title|input_path")
    parser.add_argument("--slot", required=True, choices=["morning", "evening"])
    parser.add_argument("--date", required=True)
    parser.add_argument("--window-start", default="未记录")
    parser.add_argument("--window-end", default="未记录")
    parser.add_argument("--search-sources", default="未记录")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payloads = [parse_topic_payload(raw) for raw in args.topic]
    sections = build_sections(payloads)
    if not sections:
        return 2
    print(
        render_page(
            sections,
            args.slot,
            args.date,
            args.window_start,
            args.window_end,
            args.search_sources,
        ),
        end="",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
