#!/usr/bin/env python3
"""Combine multiple topic briefs into one monitoring page."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib import error, request

from synthesize_public_report import (
    TOPIC_RULES,
    SOURCE_LABELS,
    assign_theme,
    clean_text,
    combined_heat_score,
    error_summary,
    heat_label,
    item_within_window,
    markdown_safe_text,
    parse_compact_report,
    pick_curated_items,
    source_summary,
)


TOPIC_ORDER = ["claude-code", "codex", "large-models", "obsidian"]
OPENAI_CHAT_MODELS = ["gpt-5-mini", "gpt-4.1-mini"]


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
    localized_summaries: dict[int, str]


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
    return top_heat >= 52 or trusted_hit or len(section.curated_items) >= 2


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
        if len(selected[topic_key]) >= 5:
            continue
        selected[topic_key].append(item)
        chosen_count += 1

    for section in sections:
        selected[section.topic_key].sort(
            key=lambda item: combined_heat_score(item, section.topic_key),
            reverse=True,
        )

    return selected


def load_runtime_env() -> dict[str, str]:
    env = dict(os.environ)
    env_path = Path.home() / ".config" / "last30days" / ".env"
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return env


def parse_json_object(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


def localize_item_summaries(topic_title: str, items: list) -> dict[int, str]:
    env = load_runtime_env()
    api_key = env.get("OPENAI_API_KEY")
    if not api_key or not items:
        return {}

    payload_items = []
    for idx, item in enumerate(items, start=1):
        payload_items.append(
            {
                "index": idx,
                "source": SOURCE_LABELS.get(item.source, item.source),
                "date": item.date or "日期未识别",
                "text": clean_text(item.summary or item.why_relevant or item.byline or item.identifier),
            }
        )

    system_prompt = (
        "你是中文技术编辑。请把输入条目重写成清晰、克制、信息密度高的中文摘要。"
        "要求：1. 每条只输出一句中文。2. 保留产品名、公司名、专有名词。"
        "3. 不要空话，不要营销腔，不要编造。4. 尽量在 28 到 60 个汉字内。"
        "5. 如果原文是推文，就提炼事件与看点；如果是视频或博客，也要写成中文摘要。"
        "只返回 JSON，格式为 {\"items\":[{\"index\":1,\"summary_zh\":\"...\"}]}。"
    )
    user_payload = json.dumps({"topic": topic_title, "items": payload_items}, ensure_ascii=False)

    for model in OPENAI_CHAT_MODELS:
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_payload},
            ],
            "response_format": {"type": "json_object"},
        }
        req = request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=40) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
            content = raw["choices"][0]["message"]["content"]
            parsed = parse_json_object(content)
            items_payload = parsed.get("items", [])
            localized = {}
            for entry in items_payload:
                try:
                    index = int(entry["index"])
                except Exception:
                    continue
                summary = clean_text(str(entry.get("summary_zh", "")))
                if summary:
                    localized[index] = summary
            if localized:
                return localized
        except Exception:
            continue

    return {}


def render_section_bullets(section: TopicSection) -> list[str]:
    if not section.curated_items:
        return ["- 本轮没有筛到足够稳的高质量内容，建议继续观察下一轮。"]

    grouped: dict[str, list] = {}
    for item in section.curated_items:
        grouped.setdefault(assign_theme(item, section.topic_key), []).append(item)

    theme_summaries = {label: summary for label, _keywords, summary in TOPIC_RULES[section.topic_key]["themes"]}
    bullets = []
    for label, items in sorted(grouped.items(), key=lambda pair: len(pair[1]), reverse=True)[:2]:
        bullets.append(f"- {label}：{theme_summaries.get(label, '本轮有一些可参考但仍需继续观察的信号。')} 当前保留 {len(items)} 条较强样本。")
    return bullets


def fallback_summary_zh(topic_key: str, item) -> str:
    haystack = clean_text(item.summary or item.why_relevant or item.byline or item.identifier).lower()

    if topic_key == "claude-code":
        if any(token in haystack for token in ["source map", "sourcemap", ".map", "leaked", "leak", "source code", "typescript"]):
            return "多条讨论集中在 Claude Code CLI 源码因 sourcemap 暴露而外泄，这件事正在被当作安全与工程事故反复讨论。"
        if any(token in haystack for token in ["ollama", "local", "locally", "no api fees", "free"]):
            return "这条内容强调 Claude Code 可结合本地模型或低成本方案使用，核心卖点是终端体验与部署门槛。"
        if any(token in haystack for token in ["terminal", "workflow", "agent", "review", "plugin"]):
            return "这条内容主要在谈 Claude Code 如何进入真实开发工作流，重点是终端协作、agent 体验和日常使用反馈。"
        return "这条内容围绕 Claude Code 的最新动态展开，重点是开发工作流、终端体验和安全边界。"

    if topic_key == "codex":
        if any(token in haystack for token in ["figma", "notion", "gmail", "slack", "plugin", "plugins"]):
            return "这条内容聚焦 Codex 的插件能力，说明它已经开始接入 Figma、Notion、Gmail、Slack 等外部工具。"
        if any(token in haystack for token in ["0.117", "agents v2", "mcp", "hooks", "cli"]):
            return "这条内容在讲 Codex CLI 新版本引入插件系统、Agents v2 和 MCP 安装能力，工具链扩展性明显增强。"
        if any(token in haystack for token in ["benchmark", "best-performing", "assessment pipeline", "mean score"]):
            return "这条内容把 Codex 放进 coding agent 对比评测里，核心信息是它在任务完成质量上表现靠前。"
        if any(token in haystack for token in ["tool", "connects", "automation", "agent"]):
            return "这条内容强调 Codex 正从单一写码工具转向可连外部系统、可编排任务的通用 coding agent。"
        return "这条内容围绕 Codex 的产品能力、CLI 入口和开发者工作流展开，重点在可交付能力而不是口号。"

    if topic_key == "large-models":
        if any(token in haystack for token in ["openai", "anthropic", "gemini", "llama", "qwen", "deepseek"]):
            return "这条内容在比较主流大模型阵营的版本、能力或路线差异，重点看谁在推理与产品化上更快。"
        if any(token in haystack for token in ["reasoning", "inference", "context", "latency", "token"]):
            return "这条内容主要在讨论大模型的推理能力、上下文长度与成本速度之间的取舍。"
        return "这条内容围绕大模型的版本竞争、推理能力和应用外溢展开，值得继续观察后续跟进。"

    if topic_key == "obsidian":
        if any(token in haystack for token in ["remarkable", "handwritten", "handwritten notes", "images converted", "markdown"]):
            return "这条内容展示了把手写笔记或图片同步进 Obsidian，再进一步整理成 Markdown 知识库的完整流程。"
        if any(token in haystack for token in ["vault", "synced", "phone", "obsidian app"]):
            return "这条内容在讨论 Obsidian vault 的同步方式，以及是否需要把手机端和统一入口一起纳入工作流。"
        if any(token in haystack for token in ["plugin", "template", "workflow"]):
            return "这条内容聚焦 Obsidian 插件与模板工作流，核心是如何把外部内容更顺畅地收进知识库。"
        return "这条内容围绕 Obsidian 的知识库组织、同步方式或插件工作流展开，偏向真实经验分享。"

    return "这条内容主要在讨论相关主题的最新动态，建议点开原文确认具体细节。"


def render_topic_section(section: TopicSection, chosen_items: list) -> list[str]:
    lines = [
        f"## {section.title}",
        "",
    ]
    lines.extend(render_section_bullets(section))
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
        summary_zh = section.localized_summaries.get(index) or fallback_summary_zh(section.topic_key, item)
        lines.extend(
            [
                f"#### {index}. {source_label} · {item.date or '日期未识别'}",
                "",
                f"- 热度：{heat_label(item, section.topic_key)}",
                f"- 总结：{markdown_safe_text(summary_zh)}",
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
            localized_summaries={},
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
    for section in sections:
        section.localized_summaries = localize_item_summaries(section.title, selected[section.topic_key])
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
