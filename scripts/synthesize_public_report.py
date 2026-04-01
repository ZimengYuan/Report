#!/usr/bin/env python3
"""Convert last30days compact output into a concise trend brief."""

from __future__ import annotations

import argparse
import html
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import urlparse


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
    "hn": "黑客新闻",
    "bluesky": "Bluesky",
    "truthsocial": "Truth Social",
    "polymarket": "Polymarket",
    "web": "博客/网页",
}

SOURCE_PRIORITY = {
    "reddit": 6,
    "hn": 8,
    "youtube": 6,
    "x": 4,
    "web": 12,
    "polymarket": 2,
    "bluesky": 2,
    "truthsocial": 1,
    "tiktok": 1,
    "instagram": 1,
}

OFFICIAL_DOMAIN_HINTS = (
    "openai.com",
    "anthropic.com",
    "obsidian.md",
    "docs.obsidian.md",
    "ai.google.dev",
    "blog.google",
    "googleblog.com",
    "huggingface.co",
    "mistral.ai",
    "deepseek.com",
    "qwenlm.ai",
    "meta.com",
    "about.fb.com",
)

MAX_CURATION_POOL = 80
MAX_CURATION_PER_SOURCE = 30

TOPIC_RULES = {
    "claude-code": {
        "title": "Claude Code",
        "must": [
            "claude code",
            "anthropic",
            "claude",
            "agent",
            "terminal",
            "workflow",
            "编程",
            "代码",
            "插件",
            "cli",
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
                ["plugin", "插件", "review", "delegate", "extension", "ide", "cli", "terminal"],
                "讨论重点落在 Claude Code 如何接入真实开发链路，包括插件、命令行、IDE 和 review 流程。",
            ),
            (
                "工作流与一线体验",
                ["workflow", "terminal", "tmux", "容量", "quota", "weekly", "pair", "coding", "use", "使用", "agent"],
                "可用样本主要反映真实开发体验，包括终端工作流、配额消耗和人与 agent 的协作方式。",
            ),
            (
                "风险与稳定性争议",
                ["risk", "security", "supply chain", "edge case", "worst of both worlds", "privacy", "halluc", "失控", "安全"],
                "除了效率讨论，也能看到一部分样本在担心安全边界、供应链攻击和 agent 失误成本。",
            ),
        ],
        "editor_note": "Claude Code 更适合观察真实开发工作流、插件生态和终端体验，零散营销口径价值不高。",
    },
    "codex": {
        "title": "Codex",
        "must": [
            "openai",
            "codex",
            "chatgpt codex",
            "codex cli",
            "agent",
            "coding",
            "代码",
            "terminal",
        ],
        "bonus": [
            "developer",
            "code review",
            "workflow",
            "cli",
            "benchmark",
            "latency",
            "tool",
            "automation",
            "open source",
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
                "产品能力与入口",
                ["openai", "codex", "chatgpt", "tool", "cli", "agent", "terminal", "产品", "发布"],
                "可靠信号通常集中在 Codex 的产品入口、命令行能力和可交付的 agent 体验，而不是泛泛提及。",
            ),
            (
                "开发者工作流",
                ["agent", "coding", "developer", "workflow", "编程", "代码", "review", "task"],
                "本轮更有价值的样本通常直接描述 Codex 如何进入开发者的任务拆解、写码和 review 过程。",
            ),
            (
                "稳定性与对比讨论",
                ["latency", "quota", "benchmark", "compare", "cursor", "claude", "copilot"],
                "可用信息常常出现在与其他 coding agent 的对比里，重点是稳定性、速度和任务完成质量。",
            ),
        ],
        "editor_note": "Codex 这个词历史包袱很重，抓取时要优先保留明确指向 OpenAI 最新产品形态的内容。",
    },
    "large-models": {
        "title": "大模型",
        "must": [
            "llm",
            "large language model",
            "foundation model",
            "模型",
            "大模型",
            "openai",
            "anthropic",
            "gemini",
            "llama",
            "qwen",
            "deepseek",
            "推理",
        ],
        "bonus": [
            "agent",
            "benchmark",
            "inference",
            "context",
            "multimodal",
            "多模态",
            "reasoning",
            "训练",
            "蒸馏",
            "开源",
        ],
        "noise": [
            "tron",
            "defi",
            "stablecoin",
            "token",
            "chain",
            "nft",
            "dao",
            "social-fi",
            "bnb",
            "$trx",
        ],
        "themes": [
            (
                "模型发布与版本竞争",
                ["openai", "anthropic", "gemini", "llama", "qwen", "deepseek", "release", "版本", "模型"],
                "更可靠的信号往往来自模型版本更新、能力对比和官方技术说明，而不是泛行业口号。",
            ),
            (
                "推理与成本取舍",
                ["reasoning", "inference", "latency", "token", "context", "成本", "速度", "推理"],
                "本轮更值得看的通常是推理能力、上下文长度和推理成本之间的取舍讨论。",
            ),
            (
                "开源与应用外溢",
                ["open source", "开源", "agent", "multimodal", "多模态", "browser", "robot"],
                "有效样本里经常能看到大模型从底层能力外溢到 agent、浏览器和多模态产品形态。",
            ),
        ],
        "editor_note": "大模型主题最容易混入宏观空话，优先看技术博客、发布文档和高质量讨论串。",
    },
    "obsidian": {
        "title": "Obsidian",
        "must": [
            "obsidian",
            "vault",
            "markdown",
            "knowledge management",
            "知识库",
            "笔记",
            "双链",
            "plugin",
            "插件",
        ],
        "bonus": [
            "canvas",
            "community plugin",
            "sync",
            "publish",
            "template",
            "zettelkasten",
            "second brain",
            "graph",
            "mcp",
            "ai",
        ],
        "noise": [
            "minecraft",
            "valorant",
            "destiny",
            "entertainment",
            "obsidian energy",
            "obsidian theater",
        ],
        "themes": [
            (
                "插件与工作流",
                ["plugin", "插件", "community plugin", "template", "workflow", "mcp"],
                "值得保留的信号通常来自插件发布、模板实践和知识管理工作流分享。",
            ),
            (
                "知识库组织方式",
                ["vault", "markdown", "zettelkasten", "second brain", "graph", "双链", "知识库"],
                "本轮更有价值的样本通常在讨论 Obsidian 如何组织知识、卡片和长期积累。",
            ),
            (
                "与 AI 结合",
                ["ai", "llm", "agent", "copilot", "embedding", "搜索"],
                "如果有高质量内容，往往体现在 Obsidian 与 AI 检索、写作和自动化能力的结合方式。",
            ),
        ],
        "editor_note": "Obsidian 主题适合重点看博客、官方论坛和高质量经验贴，低质量截图式分享意义不大。",
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
    header_match = re.match(r"^\*\*(?P<identifier>[^*]+)\*\* (?:\[WEB\]\s*)?\(score:(?P<score>\d+)\) (?P<rest>.+)$", header_line.strip())
    if not header_match:
        raise ValueError(f"Unrecognized item header: {header_line}")

    rest = header_match.group("rest").strip()
    engagement = ""
    date = ""

    engagement_match = re.search(r"\[(?P<eng>[^\]]+)\]$", rest)
    if engagement_match:
        engagement = engagement_match.group("eng").strip()
        rest = rest[: engagement_match.start()].strip()
        if engagement.startswith("date:"):
            engagement = ""

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


def extract_domain(url: str) -> str:
    if not url:
        return ""
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except ValueError:
        return ""


def source_depth_bonus(item: Item) -> int:
    bonus = 0

    if item.source == "web":
        bonus += 10
    elif item.source == "hn":
        bonus += 7
    elif item.source == "youtube":
        bonus += 4
    elif item.source == "reddit":
        bonus += 3

    if item.highlights:
        bonus += min(len(item.highlights), 3)

    domain = extract_domain(item.url)
    if domain and any(domain == hint or domain.endswith(f".{hint}") for hint in OFFICIAL_DOMAIN_HINTS):
        bonus += 8

    if item.source == "x" and item.engagement:
        counts = [int(token) for token in re.findall(r"\d+", item.engagement)]
        if counts:
            bonus += min(sum(counts) // 20, 4)

    return bonus


def pick_curated_items(report: ParsedCompactReport, topic_key: str) -> tuple[list[Item], dict[str, int]]:
    candidates: list[tuple[int, Item]] = []
    stats = {"kept": 0, "filtered_noise": 0, "filtered_weak": 0}

    for source, items in report.items_by_source.items():
        for item in items:
            # 时间窗口过滤已移除：保留所有时间范围内的条目以扩大候选池
            topic_score, positive_hits, noise_hits = score_item_for_topic(item, topic_key)
            overall = item.score + topic_score * 8 + SOURCE_PRIORITY.get(source, 0) + source_depth_bonus(item)
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
        if per_source_kept.get(item.source, 0) >= MAX_CURATION_PER_SOURCE:
            continue
        selected.append(item)
        per_source_kept[item.source] = per_source_kept.get(item.source, 0) + 1
        if len(selected) >= MAX_CURATION_POOL:
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


def parse_iso_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def report_window(report: ParsedCompactReport) -> tuple[date | None, date | None]:
    if " to " not in report.date_range:
        return None, None
    start_raw, end_raw = report.date_range.split(" to ", 1)
    return parse_iso_date(start_raw.strip()), parse_iso_date(end_raw.strip())


def item_within_window(item: Item, report: ParsedCompactReport) -> bool:
    item_date = parse_iso_date(item.date)
    start_date, end_date = report_window(report)
    if not item_date or not start_date or not end_date:
        return True
    return start_date <= item_date <= end_date


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


def render_overall_summary(
    report: ParsedCompactReport,
    curated_items: list[Item],
    stats: dict[str, int],
    topic_key: str,
    window_start: str,
    window_end: str,
    search_sources: str,
) -> list[str]:
    active_sources = sorted({SOURCE_LABELS[item.source] for item in curated_items})
    source_text = "、".join(active_sources) if active_sources else "单一弱来源"
    lines: list[str] = []
    blog_items = [item for item in curated_items if item.source == "web"]

    if curated_items:
        lines.append(f"- 当前更值得关注的是 {source_text} 里反复出现的共识信号，而不是零散单条爆点；这轮最终保留了 {len(curated_items)} 条精华条目。")
    else:
        lines.append("- 当前没有形成足够稳的跨来源趋势，建议先观察下一轮数据。")

    lines.append(f"- 本轮抓取窗口按“最近一个时段”处理：{window_start} 至 {window_end}。")
    lines.append(f"- 本轮启用的数据源：{search_sources or '未记录'}。")

    if blog_items:
        lines.append(f"- 本轮命中了 {len(blog_items)} 条博客/网页信号，博客与官方文章会被优先视作更稳的参考。")
    else:
        lines.append("- 本轮没有拿到可用的博客/网页结果；如果要稳定抓博客，需要在本机补上原生 web 搜索后端。")

    if report.limited_recent:
        lines.append("- 抓取阶段已经提示最近有效数据偏少，所以这次结论强度需要下调。")
    if stats["filtered_noise"] > 0 or stats["filtered_weak"] > 0:
        lines.append(f"- 本轮过滤了 {stats['filtered_weak']} 条弱相关样本和 {stats['filtered_noise']} 条明显噪声，列表只保留更能代表趋势的条目。")

    theme_labels = [assign_theme(item, topic_key) for item in curated_items]
    dominant_theme = max(set(theme_labels), key=theme_labels.count) if theme_labels else "持续观察"
    lines.append(f"- 整体上，这个主题当前最强的公共信号集中在“{dominant_theme}”，比零散营销和单条刷屏内容更值得看。")

    return lines


def render_report(
    report: ParsedCompactReport,
    topic_key: str,
    report_title: str,
    slot: str,
    report_date: str,
    window_start: str,
    window_end: str,
    search_sources: str,
) -> str:
    curated_items, stats = pick_curated_items(report, topic_key)
    slot_label = "早间" if slot == "morning" else "晚间"
    errors = error_summary(report)

    lines = [
        f"# {report_title} 趋势简报",
        "",
        f"**时段：** {slot_label}",
        f"**日期：** {report_date}",
        f"**抓取窗口：** {window_start} 至 {window_end}",
        f"**底层检索日期范围：** {report.date_range or '未识别'}",
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
    lines.extend(render_overall_summary(report, curated_items, stats, topic_key, window_start, window_end, search_sources))
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
    parser.add_argument("--window-start", default="未记录")
    parser.add_argument("--window-end", default="未记录")
    parser.add_argument("--search-sources", default="未记录")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = parse_compact_report(Path(args.input))
    print(
        render_report(
            report,
            args.topic_key,
            args.report_title,
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
