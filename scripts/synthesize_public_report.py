#!/usr/bin/env python3
"""Convert last30days compact output into a concise trend brief."""

from __future__ import annotations

import argparse
import html
import re
from dataclasses import dataclass, field
from pathlib import Path


SECTION_TO_SOURCE = {
    "Reddit Threads": "reddit",
    "X Posts": "x",
    "YouTube Videos": "youtube",
    "TikTok Videos": "tiktok",
    "Instagram Reels": "instagram",
    "Hacker News Stories": "hn",
    "Bluesky Posts": "bluesky",
    "Truth Social Posts": "truthsocial",
    "Prediction Markets (Polymarket)": "polymarket",
    "Web Results": "web",
}

SOURCE_LABELS = {
    "reddit": "Reddit",
    "x": "X",
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "instagram": "Instagram",
    "hn": "Hacker News",
    "bluesky": "Bluesky",
    "truthsocial": "Truth Social",
    "polymarket": "Polymarket",
    "web": "Web",
}

SOURCE_PRIORITY = {
    "reddit": 6,
    "hn": 6,
    "youtube": 5,
    "x": 4,
    "web": 3,
    "polymarket": 2,
    "bluesky": 2,
    "truthsocial": 1,
    "tiktok": 1,
    "instagram": 1,
}

TOPIC_RULES = {
    "claude-code-codex": {
        "title": "Claude Code && Codex",
        "must": [
            "claude code",
            "codex",
            "openclaw",
            "copilot",
            "cursor",
            "agent",
            "code review",
            "terminal",
            "workflow",
            "编程",
            "代码",
            "插件",
        ],
        "bonus": [
            "developer",
            "tmux",
            "review",
            "delegate",
            "task",
            "test",
            "安全",
            "risk",
            "security",
            "supply chain",
            "quota",
            "容量",
        ],
        "noise": [
            "tron",
            "defi",
            "stablecoin",
            "rwa",
            "ethcc",
            "token",
            "chain",
            "nft",
            "dao",
            "candbomb",
            "$trx",
        ],
        "themes": [
            (
                "工具整合与生态",
                ["plugin", "插件", "review", "delegate", "openclaw", "copilot", "cursor", "cloudcli", "astro", "proofshot"],
                "讨论重点落在把多种 coding agent 串起来使用，更多是在比拼集成能力，而不是单点模型参数。",
            ),
            (
                "工作流与一线体验",
                ["workflow", "terminal", "tmux", "容量", "quota", "weekly", "pair", "coding", "use", "使用"],
                "可用样本主要反映真实开发体验，包括终端工作流、配额消耗和人与 agent 的协作方式。",
            ),
            (
                "风险与稳定性争议",
                ["risk", "security", "supply chain", "edge case", "worst of both worlds", "privacy", "halluc", "失控", "安全"],
                "除了效率讨论，也能看到一部分样本在担心安全边界、供应链攻击和 agent 失误成本。",
            ),
        ],
        "editor_note": "这个主题更适合看成“AI 编程 agent 工具链动态”，不适合把每条社交媒体吐槽都当成趋势结论。",
    },
    "ai-overview": {
        "title": "AI 发展总览",
        "must": [
            "ai",
            "人工智能",
            "openai",
            "anthropic",
            "claude",
            "codex",
            "gemini",
            "llm",
            "agent",
            "model",
            "模型",
            "robot",
            "stitch",
            "figma",
        ],
        "bonus": [
            "design",
            "developer",
            "coding",
            "browser",
            "product",
            "tool",
            "多模态",
            "平台",
            "os",
            "系统",
            "automation",
        ],
        "noise": [
            "tron",
            "defi",
            "stablecoin",
            "rwa",
            "ethcc",
            "token",
            "chain",
            "nft",
            "dao",
            "social-fi",
            "bnb",
            "$trx",
            "liquidity",
            "mining",
        ],
        "themes": [
            (
                "产品与发布动态",
                ["openai", "anthropic", "claude", "codex", "gemini", "stitch", "figma", "tool", "产品", "发布"],
                "更可靠的样本集中在新工具、平台入口和产品级更新，而不是宏观行业判断。",
            ),
            (
                "Agent 与开发流程",
                ["agent", "coding", "developer", "workflow", "编程", "代码", "design", "ui"],
                "本轮有效信号主要落在 AI 如何改变设计和开发流程，尤其是 agent 化工作方式。",
            ),
            (
                "宏观趋势判断",
                ["robot", "browser", "os", "系统", "平台", "多模态", "model", "模型"],
                "能提炼的宏观信息不多，更多只能作为局部产品动态的拼图，而不是全行业总览。",
            ),
        ],
        "editor_note": "“AI 发展总览”这个主题天然过宽，抓取时很容易混入加密、会务宣传和泛科技噪声，因此正式报告必须二次筛选。",
    },
}


@dataclass
class Item:
    source: str
    identifier: str
    score: int
    byline: str
    date: str = ""
    engagement: str = ""
    summary: str = ""
    url: str = ""
    why_relevant: str = ""
    highlights: list[str] = field(default_factory=list)
    raw_text: str = ""


@dataclass
class ParsedCompactReport:
    topic: str = ""
    date_range: str = ""
    mode: str = ""
    model: str = ""
    limited_recent: bool = False
    items_by_source: dict[str, list[Item]] = field(default_factory=dict)
    errors_by_source: dict[str, str] = field(default_factory=dict)
    source_status_lines: list[str] = field(default_factory=list)
    quality_line: str = ""


def clean_text(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def strip_markdown(text: str) -> str:
    text = clean_text(text)
    return text.replace("**", "").replace("*", "")


def markdown_safe_text(text: str) -> str:
    text = strip_markdown(text)
    return text.replace("|", "\\|")


def parse_item_block(source: str, header_line: str, block_lines: list[str]) -> Item:
    header_match = re.match(r"^\*\*(?P<identifier>[^*]+)\*\* \(score:(?P<score>\d+)\) (?P<rest>.+)$", header_line.strip())
    if not header_match:
        raise ValueError(f"Unrecognized item header: {header_line}")

    rest = header_match.group("rest").strip()
    engagement = ""
    date = ""

    engagement_match = re.search(r"\[(?P<eng>[^\]]+)\]$", rest)
    if engagement_match:
        engagement = engagement_match.group("eng").strip()
        rest = rest[: engagement_match.start()].strip()

    date_match = re.search(r"\((?P<date>\d{4}-\d{2}-\d{2}|Unknown|date unknown)\)$", rest)
    if date_match:
        date = date_match.group("date").strip()
        rest = rest[: date_match.start()].strip()

    summary_lines: list[str] = []
    highlights: list[str] = []
    why_relevant = ""
    url = ""
    in_highlights = False

    for raw_line in block_lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            in_highlights = False
            continue
        if stripped == "**":
            continue
        if stripped == "Highlights:":
            in_highlights = True
            continue
        if stripped.startswith("<details>") or stripped.startswith("</details>") or stripped.startswith("Full transcript"):
            in_highlights = False
            continue
        if stripped.startswith("- "):
            if in_highlights:
                highlights.append(clean_text(stripped[2:]))
            continue
        if stripped.startswith("http://") or stripped.startswith("https://"):
            url = stripped
            in_highlights = False
            continue
        if stripped.startswith("*") and stripped.endswith("*") and stripped != "*":
            why_relevant = strip_markdown(stripped)
            in_highlights = False
            continue
        if not url:
            summary_lines.append(clean_text(stripped))

    summary = clean_text(" ".join(summary_lines))
    raw_text = " ".join(part for part in [rest, summary, why_relevant, " ".join(highlights)] if part)

    return Item(
        source=source,
        identifier=header_match.group("identifier").strip(),
        score=int(header_match.group("score")),
        byline=clean_text(rest),
        date=date,
        engagement=engagement,
        summary=summary,
        url=url,
        why_relevant=why_relevant,
        highlights=highlights,
        raw_text=clean_text(raw_text),
    )


def parse_compact_report(path: Path) -> ParsedCompactReport:
    lines = path.read_text(encoding="utf-8").splitlines()
    report = ParsedCompactReport(items_by_source={source: [] for source in SECTION_TO_SOURCE.values()})
    current_source = None
    i = 0

    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()

        if stripped.startswith("## Research Results:"):
            report.topic = clean_text(stripped.removeprefix("## Research Results:"))
        elif stripped.startswith("**⚠️ LIMITED RECENT DATA**"):
            report.limited_recent = True
        elif stripped.startswith("**Date Range:**"):
            report.date_range = clean_text(stripped.removeprefix("**Date Range:**"))
        elif stripped.startswith("**Mode:**"):
            report.mode = clean_text(stripped.removeprefix("**Mode:**"))
        elif stripped.startswith("**OpenAI Model:**"):
            report.model = clean_text(stripped.removeprefix("**OpenAI Model:**"))
        elif stripped.startswith("### "):
            current_source = SECTION_TO_SOURCE.get(stripped[4:])
        elif stripped == "---":
            if i + 1 < len(lines) and lines[i + 1].strip().startswith("**🔍 Research Coverage:"):
                report.quality_line = strip_markdown(lines[i + 1].strip())
            if i + 1 < len(lines) and lines[i + 1].strip() == "**Sources:**":
                i += 2
                while i < len(lines):
                    status_line = clean_text(lines[i])
                    if status_line:
                        report.source_status_lines.append(status_line)
                    i += 1
                break
        elif current_source and stripped.startswith("**ERROR:**"):
            report.errors_by_source[current_source] = strip_markdown(stripped.split(":", 1)[1])
        elif current_source and stripped.startswith("**") and "(score:" in stripped:
            header_line = stripped
            block_lines: list[str] = []
            i += 1
            while i < len(lines):
                candidate = lines[i].rstrip()
                candidate_stripped = candidate.strip()
                if candidate_stripped.startswith("### ") or candidate_stripped == "---":
                    i -= 1
                    break
                if candidate_stripped.startswith("**") and "(score:" in candidate_stripped:
                    i -= 1
                    break
                block_lines.append(candidate)
                i += 1
            report.items_by_source[current_source].append(parse_item_block(current_source, header_line, block_lines))
        i += 1

    return report


def score_item_for_topic(item: Item, topic_key: str) -> tuple[int, list[str], list[str]]:
    rules = TOPIC_RULES[topic_key]
    haystack = item.raw_text.lower()
    positive_hits: list[str] = []
    noise_hits: list[str] = []
    score = 0

    for kw in rules["must"]:
        if kw in haystack:
            score += 2
            positive_hits.append(kw)
    for kw in rules["bonus"]:
        if kw in haystack:
            score += 1
            positive_hits.append(kw)
    for kw in rules["noise"]:
        if kw in haystack:
            score -= 3
            noise_hits.append(kw)

    if item.source in {"reddit", "hn", "youtube"}:
        score += 1
    if item.source == "x" and item.engagement:
        score += 1

    return score, positive_hits, noise_hits


def pick_curated_items(report: ParsedCompactReport, topic_key: str) -> tuple[list[Item], dict[str, int]]:
    candidates: list[tuple[int, Item]] = []
    stats = {"kept": 0, "filtered_noise": 0, "filtered_weak": 0}

    for source, items in report.items_by_source.items():
        for item in items:
            topic_score, positive_hits, noise_hits = score_item_for_topic(item, topic_key)
            overall = item.score + topic_score * 8 + SOURCE_PRIORITY.get(source, 0)
            if noise_hits:
                stats["filtered_noise"] += 1
                continue
            if topic_score <= 0 or not positive_hits:
                stats["filtered_weak"] += 1
                continue
            candidates.append((overall, item))

    candidates.sort(key=lambda pair: pair[0], reverse=True)

    selected: list[Item] = []
    per_source_kept: dict[str, int] = {}

    for _overall, item in candidates:
        if per_source_kept.get(item.source, 0) >= 6:
            continue
        selected.append(item)
        per_source_kept[item.source] = per_source_kept.get(item.source, 0) + 1
        if len(selected) >= 15:
            break

    stats["kept"] = len(selected)
    return selected, stats


def assign_theme(item: Item, topic_key: str) -> str:
    rules = TOPIC_RULES[topic_key]
    haystack = item.raw_text.lower()
    best_label = "其他有效信号"
    best_hits = 0

    for label, keywords, _summary in rules["themes"]:
        hits = sum(1 for kw in keywords if kw in haystack)
        if hits > best_hits:
            best_hits = hits
            best_label = label

    return best_label


def short_snippet(item: Item, limit: int = 58) -> str:
    text = item.summary or item.why_relevant or item.byline
    text = markdown_safe_text(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def source_summary(report: ParsedCompactReport) -> str:
    parts = []
    for source, items in report.items_by_source.items():
        if items:
            parts.append(f"{SOURCE_LABELS[source]} {len(items)} 条")
    return "、".join(parts) if parts else "无稳定可用来源"


def error_summary(report: ParsedCompactReport) -> str:
    parts = []
    for source, error in report.errors_by_source.items():
        trimmed = clean_text(error)
        if len(trimmed) > 72:
            trimmed = trimmed[:71].rstrip() + "…"
        parts.append(f"{SOURCE_LABELS[source]}：{trimmed}")
    return "；".join(parts)


def combined_heat_score(item: Item, topic_key: str) -> int:
    topic_score, _positive_hits, _noise_hits = score_item_for_topic(item, topic_key)
    return item.score + topic_score * 8 + SOURCE_PRIORITY.get(item.source, 0)


def heat_label(item: Item, topic_key: str) -> str:
    heat = combined_heat_score(item, topic_key)
    if heat >= 95:
        label = "爆热"
    elif heat >= 80:
        label = "高热"
    elif heat >= 65:
        label = "中热"
    else:
        label = "观察"

    engagement = f"；{markdown_safe_text(item.engagement)}" if item.engagement else ""
    return f"{label}（{heat}{engagement}）"


def render_theme_bullets(curated_items: list[Item], topic_key: str) -> list[str]:
    if not curated_items:
        return ["- 本轮没有筛出足够可靠的高相关样本，当前只适合继续观察，不适合下明确趋势结论。"]

    grouped: dict[str, list[Item]] = {}
    for item in curated_items:
        grouped.setdefault(assign_theme(item, topic_key), []).append(item)

    bullets = []
    theme_summaries = {label: summary for label, _keywords, summary in TOPIC_RULES[topic_key]["themes"]}

    for label, items in sorted(grouped.items(), key=lambda pair: len(pair[1]), reverse=True)[:3]:
        examples = "、".join(f"“{short_snippet(item, 36)}”" for item in items[:2])
        base = theme_summaries.get(label, "本轮有一些零散但仍可用的样本。")
        bullets.append(f"- {label}：{base} 代表样本包括 {examples}。")

    return bullets


def render_item_sections(curated_items: list[Item], topic_key: str) -> list[str]:
    sections: list[str] = []
    for index, item in enumerate(curated_items[:15], start=1):
        source_label = SOURCE_LABELS.get(item.source, item.source)
        date_part = item.date or "日期未识别"
        summary = short_snippet(item, 180)
        link = f"[打开原文]({item.url})" if item.url else "无链接"
        sections.extend(
            [
                f"### {index}. {source_label} · {date_part}",
                "",
                f"- 热度：{heat_label(item, topic_key)}",
                f"- 总结：{summary}",
                f"- 链接：{link}",
                "",
            ]
        )
    return sections


def render_overall_summary(report: ParsedCompactReport, curated_items: list[Item], stats: dict[str, int], topic_key: str) -> list[str]:
    active_sources = sorted({SOURCE_LABELS[item.source] for item in curated_items})
    source_text = "、".join(active_sources) if active_sources else "单一弱来源"
    lines: list[str] = []

    if curated_items:
        lines.append(f"- 当前更值得关注的是 {source_text} 里反复出现的共识信号，而不是零散单条爆点；这轮最终保留了 {len(curated_items)} 条精华条目。")
    else:
        lines.append("- 当前没有形成足够稳的跨来源趋势，建议先观察下一轮数据。")

    if report.limited_recent:
        lines.append("- 抓取阶段已经提示最近有效数据偏少，所以这次结论强度需要下调。")
    if stats["filtered_noise"] > 0 or stats["filtered_weak"] > 0:
        lines.append(f"- 本轮过滤了 {stats['filtered_weak']} 条弱相关样本和 {stats['filtered_noise']} 条明显噪声，列表只保留更能代表趋势的条目。")

    if topic_key == "claude-code-codex":
        lines.append("- 整体上，这个主题的真实热度还在“agent 工作流、工具整合、开发体验”上，不在单个模型宣传口径上。")
    else:
        lines.append("- 整体上，这个主题的有效信号仍偏产品发布和 agent 工作流，宏观行业判断的可信度明显低于具体产品动态。")

    return lines


def render_report(report: ParsedCompactReport, topic_key: str, report_title: str, slot: str, report_date: str) -> str:
    curated_items, stats = pick_curated_items(report, topic_key)
    slot_label = "早间" if slot == "morning" else "晚间"
    errors = error_summary(report)

    lines = [
        f"# {report_title} 趋势简报",
        "",
        f"**时段：** {slot_label}",
        f"**日期：** {report_date}",
        f"**时间范围：** {report.date_range or '未识别'}",
        f"**生成模型：** {report.model or '未识别'}",
        f"**有效来源：** {source_summary(report)}",
        "",
        "## 当前趋势",
        "",
    ]
    lines.extend(render_theme_bullets(curated_items, topic_key))
    lines.extend(
        [
            "",
            "## 精华条目（最多15条）",
            "",
        ]
    )
    lines.extend(render_item_sections(curated_items, topic_key) or ["- 本轮没有保留到足够可靠的精华条目。"])
    lines.extend(
        [
            "## 当前趋势总结",
            "",
        ]
    )
    lines.extend(render_overall_summary(report, curated_items, stats, topic_key))
    if report.quality_line:
        lines.append(f"- {report.quality_line}。")
    if errors:
        lines.append(f"- 本轮异常来源：{errors}。")
    lines.append(f"- 备注：{TOPIC_RULES[topic_key]['editor_note']}")
    return "\n".join(lines).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a curated public report from last30days compact output.")
    parser.add_argument("--input", required=True, help="Path to compact markdown input")
    parser.add_argument("--topic-key", required=True, choices=sorted(TOPIC_RULES.keys()))
    parser.add_argument("--report-title", required=True)
    parser.add_argument("--slot", required=True, choices=["morning", "evening"])
    parser.add_argument("--date", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = parse_compact_report(Path(args.input))
    print(render_report(report, args.topic_key, args.report_title, args.slot, args.date), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
