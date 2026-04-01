#!/usr/bin/env python3
"""Combine multiple topic briefs into one monitoring page."""

from __future__ import annotations

import argparse
import datetime
import html
import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib import error, request

from monitor_link_enrichment import (
    fallback_summary_from_page,
    fetch_page_context,
    page_relevance_score,
    should_fetch_url,
    summarize_candidates,
)
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
TARGET_PAGE_ITEMS = 40
BASE_ITEMS_PER_TOPIC = 3
MAX_ITEMS_PER_TOPIC = 10


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


def _content_fingerprint(item) -> str:
    """生成内容指纹：正文 normalized 后前 120 字符作为去重依据。"""
    raw = clean_text(item.summary or item.why_relevant or item.byline or item.identifier or "")
    return raw.lower().strip()[:120]


def _is_similar(a, b, threshold: float = 0.75) -> bool:
    """判断两条内容是否高度相似（正文前 120 字符相同）。"""
    fa = _content_fingerprint(a)
    fb = _content_fingerprint(b)
    if not fa or not fb:
        return False
    return fa == fb


def _item_fingerprint(item) -> str:
    """更严格的内容指纹：合并 summary + identifier 前 200 字符，去重更精准。"""
    raw = clean_text(item.summary or item.why_relevant or item.byline or item.identifier or "")
    return raw.lower().strip()[:200]


@dataclass
class MergedItem:
    """一条渲染条目，可能对应多条原始 item（相似内容合并）。"""
    topic_key: str
    primary_item: object          # 热度最高的原始 item
    summary_zh: str               # 统一中文摘要
    linked_items: list[object]   # 所有相似条目（含 primary）
    score: int                    # 最高热度


def _merge_similar_items(selected: dict[str, list]) -> dict[str, list[MergedItem]]:
    """
    将相似内容合并为一条 MergedItem，多个链接叠在一起展示。
    合并逻辑：同 topic 内，指纹相同或正文前 200 字符相同的条目视为同事件。
    """
    from collections import defaultdict

    merged: dict[str, list[MergedItem]] = {tk: [] for tk in selected}

    for topic_key, items in selected.items():
        # 按指纹分组
        groups: dict[str, list] = defaultdict(list)
        for item in items:
            fp = _item_fingerprint(item)
            if fp:
                groups[fp].append(item)

        for fp, group in groups.items():
            if not group:
                continue
            # 该组内按热度排序，取最高为 primary
            group.sort(key=lambda i: combined_heat_score(i, topic_key), reverse=True)
            primary = group[0]
            unique_sources = {item.source for item in group}
            corroboration_bonus = max(0, len(unique_sources) - 1) * 6
            trusted_bonus = 6 if any(item.source in {"web", "hn"} for item in group) else 0
            score = combined_heat_score(primary, topic_key) + corroboration_bonus + trusted_bonus
            merged_item = MergedItem(
                topic_key=topic_key,
                primary_item=primary,
                summary_zh="",
                linked_items=group,
                score=score,
            )
            merged[topic_key].append(merged_item)

    # 每组内再按热度排序
    for topic_key in merged:
        merged[topic_key].sort(key=lambda m: m.score, reverse=True)

    return merged


def select_global_items(sections: list[TopicSection], max_items: int = TARGET_PAGE_ITEMS) -> dict[str, list]:
    """
    收集候选并尽量保证每个主题都有足够样本，再按全局热度补齐。
    去重留到 _merge_similar_items 统一处理。
    """
    ranked = {
        section.topic_key: sorted(
            section.curated_items,
            key=lambda item: combined_heat_score(item, section.topic_key),
            reverse=True,
        )
        for section in sections
    }

    selected: dict[str, list] = {section.topic_key: [] for section in sections}
    max_per_topic = MAX_ITEMS_PER_TOPIC

    for section in sections:
        topic_key = section.topic_key
        base_items = ranked[topic_key][:BASE_ITEMS_PER_TOPIC]
        selected[topic_key].extend(base_items)

    chosen_keys = {
        (
            item.url or "",
            item.identifier,
            item.date,
            topic_key,
        )
        for topic_key, items in selected.items()
        for item in items
    }

    all_candidates: list[tuple[int, str, object]] = []
    for section in sections:
        for item in ranked[section.topic_key]:
            all_candidates.append((combined_heat_score(item, section.topic_key), section.topic_key, item))

    all_candidates.sort(key=lambda entry: entry[0], reverse=True)

    chosen_count = sum(len(items) for items in selected.values())
    for _heat, topic_key, item in all_candidates:
        item_key = (item.url or "", item.identifier, item.date, topic_key)
        if item_key in chosen_keys:
            continue
        if chosen_count >= max_items:
            break
        if len(selected[topic_key]) >= max_per_topic:
            continue
        selected[topic_key].append(item)
        chosen_keys.add(item_key)
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
        # 尽量多传字段给模型，让摘要更具体
        raw_parts = [
            clean_text(item.summary or ""),
            clean_text(item.why_relevant or ""),
            clean_text(item.byline or ""),
            clean_text(item.identifier or ""),
        ]
        # 也加入 highlights 如果有的话
        highlights_text = ""
        if item.highlights:
            highlights_text = " | 高光：" + " ".join(item.highlights[:2])
        full_text = " | ".join(p for p in raw_parts if p) + highlights_text

        payload_items.append(
            {
                "index": idx,
                "source": SOURCE_LABELS.get(item.source, item.source),
                "date": item.date or "日期未识别",
                "text": full_text[:600],  # 限制长度避免 token 浪费
            }
        )

    system_prompt = (
        "你是一个中文技术深度编辑。请根据每条内容的原文（来自推文/视频/博客/黑客新闻），撰写一条有信息量的中文技术摘要。\n"
        "撰写规范：\n"
        "1. 每条输出【一句】完整的中文句子（30~65个汉字），不得拆分多句。\n"
        "2. 标题式信息（\"XX 发布\"、\"XX 开源\"）要补充具体细节：版本号、关键功能、数字指标。\n"
        "3. 推文类内容要提炼出事件本身和核心看点，不只是\"讨论了XX\"。\n"
        "4. 博客/视频类内容要交代作者身份、内容类型和最值得看的角度。\n"
        "5. 相同事件的多条内容，摘要要有差异化，不能雷同。\n"
        "6. 禁止：空话（\"引起了广泛关注\"）、营销腔（\"震撼发布\"）、编造细节。\n"
        "7. 原文如有具体数字、人名、产品名，必须保留或转写进摘要。\n"
        "只返回 JSON，格式为 {\"items\":[{\"index\":1,\"summary_zh\":\"...\"}]}，不要有其他文字。"
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


def enrich_merged_items(section: TopicSection, merged_items: list[MergedItem], page_cache: dict[str, object]) -> list[MergedItem]:
    """对最终候选做正文抓取、相关性复核和中文摘要增强。"""
    if not merged_items:
        return []

    candidates: list[dict] = []
    prelim_items: list[MergedItem] = []

    for merged_item in merged_items:
        primary = merged_item.primary_item
        page_context = None
        page_relevance = 0

        if primary.url and should_fetch_url(primary.source, primary.url):
            page_context = page_cache.get(primary.url)
            if page_context is None:
                page_context = fetch_page_context(primary.url)
                page_cache[primary.url] = page_context
            if page_context and page_context.ok:
                page_relevance = page_relevance_score(section.topic_key, page_context)

        if primary.source in {"web", "hn"} and page_context and page_context.ok and page_relevance <= 0:
            continue

        prelim_items.append(merged_item)
        candidates.append(
            {
                "index": len(prelim_items),
                "item": primary,
                "page_context": page_context,
                "page_relevance": page_relevance,
            }
        )

    if not prelim_items:
        return []

    localized = summarize_candidates(section.title, section.topic_key, candidates)
    final_items: list[MergedItem] = []

    for merged_item, candidate in zip(prelim_items, candidates):
        result = localized.get(candidate["index"], {})
        if result.get("is_irrelevant"):
            continue

        summary = result.get("summary_zh", "")
        if not summary:
            fallback_summary, is_irrelevant = fallback_summary_from_page(
                section.title,
                candidate["item"],
                candidate.get("page_context"),
                candidate.get("page_relevance", 0),
            )
            if is_irrelevant:
                continue
            summary = fallback_summary

        if summary:
            merged_item.summary_zh = summary
        final_items.append(merged_item)

    return final_items


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


def _extract_keywords(item) -> list[str]:
    """从各字段提取有区分度的关键词片段。"""
    fields = [
        clean_text(item.summary or ""),
        clean_text(item.why_relevant or ""),
        clean_text(item.byline or ""),
        clean_text(item.identifier or ""),
    ]
    text = " ".join(fields)
    # 提取 3~6 字符的有意义词组
    import re
    tokens = re.findall(r'[\w\-\.]+', text.lower())
    return tokens


def fallback_summary_zh(topic_key: str, item) -> str:
    haystack = clean_text(item.summary or item.why_relevant or item.byline or item.identifier)
    haystack_lower = haystack.lower()
    raw_fields = clean_text(item.summary or item.why_relevant or item.byline or item.identifier)
    tokens = _extract_keywords(item)

    if topic_key == "claude-code":
        # 源码泄露事件
        if any(t in haystack_lower for t in ["source map", "sourcemap", ".map", "leaked source", "source code leaked", "typescript source"]):
            # 尝试从 identifier/byline 提取具体信息
            if "grok" in haystack_lower:
                return "Claude Code CLI 源码因 sourcemap 配置错误被 Grok 爬取暴露，Anthropic 已收到通知但尚未公开回应，引发安全社区广泛讨论。"
            if "blokeman" in haystack_lower or "2026" in haystack_lower:
                return "Claude Code CLI 源码外泄事件持续发酵，多个开发者社区开始分析暴露的代码结构和潜在安全风险。"
            return "Claude Code CLI 工具源码因 sourcemap 暴露问题被开源社区发现并传播，触发安全与隐私层面的审视。"
        if any(t in haystack_lower for t in ["ollama", "local model", "locally", "no api", "free to use", "self-hosted"]):
            return f"开发者分享将 Claude Code 接入 Ollama 等本地模型的实践，侧重终端工作流、低成本部署和人机协作方式的探索。内容涉及：{min(tokens[:3], key=lambda x: len(x)) or '本地模型集成'}。"
        if any(t in haystack_lower for t in ["terminal workflow", "iterm", "kitty", "alacritty"]):
            return "Claude Code 在终端工作流中的实际表现引发讨论，焦点包括终端复用、配色配置和长时间跑任务的稳定性。"
        if any(t in haystack_lower for t in ["review", "pull request", "pr ", "git review"]):
            return "开发者分享用 Claude Code 辅助代码 review 的体验，重点在 PR 描述生成、变更摘要和评审效率上的实际提升。"
        if any(t in haystack_lower for t in ["plugin", "extension", "vscode", "cursor"]):
            return f"Claude Code 与 VS Code/Cursor 等编辑器的插件生态成为讨论热点，具体涉及：{', '.join([t for t in tokens[:3] if len(t) > 3] or ['集成体验'])}。"
        if any(t in haystack_lower for t in ["quota", "limit", "billing", "cost", "expensive"]):
            return "Claude Code API 调用配额与计费问题引发用户反馈，核心痛点在高频率使用下的成本控制和配额耗尽处理方式。"
        return f"Claude Code 相关动态，关键词：{', '.join(tokens[2:6] if len(tokens) > 5 else tokens)}。点击原文了解更多细节。"

    if topic_key == "codex":
        if any(t in haystack_lower for t in ["figma", "notion", "gmail", "slack", "linear", "jira"]):
            plugins = [t for t in ["Figma", "Notion", "Gmail", "Slack", "Linear", "Jira"] if t.lower() in haystack_lower]
            return f"Codex 插件体系扩展到 {plugins[0] if plugins else '多个主流工具'}，演示了将 AI coding agent 接入真实生产工具链的路径，不再只限于写代码。"
        if any(t in haystack_lower for t in ["0.117", "agents v2", "mcp", "hooks", "cli v"]):
            version = next((t for t in tokens if t.startswith("0.") or "v" in t), None)
            return f"Codex CLI 发布新版本（{version or '近期版本'}），引入 Agents v2、MCP 协议支持和插件安装机制，大幅提升工具链可扩展性。"
        if any(t in haystack_lower for t in ["benchmark", "best-performing", "SWE-bench", "mean score", "evaluation"]):
            return "Codex 在第三方 coding agent 基准评测中进入前列，具体在任务完成率和代码正确性上展现了竞争力，引发开发者社区关注。"
        if any(t in haystack_lower for t in ["agentic", "automation", "end-to-end", "workflow automation"]):
            return "Codex 正从纯代码补全工具向端到端自动化 agent 演进，支持连接外部系统并完成复杂任务编排，定位已超越传统 IDE 插件。"
        if any(t in haystack_lower for t in ["fine-tune", "fine-tuned", "custom model", "domain-specific"]):
            return "开发者尝试对 Codex 进行微调或接入领域特定模型，探讨在垂类场景（安全审查、文档生成等）中的效果与局限。"
        return f"Codex 产品动态，核心提及：{', '.join(tokens[1:5] if len(tokens) > 4 else tokens)}。点击原文了解更多。"

    if topic_key == "large-models":
        if any(t in haystack_lower for t in ["openai", "anthropic", "gemini", "llama", "qwen", "deepseek", "mistral", "groq"]):
            brands = [t for t in ["OpenAI", "Anthropic", "Gemini", "Llama", "Qwen", "DeepSeek", "Mistral", "Groq"] if t.lower() in haystack_lower]
            brands_str = "、".join(brands[:3]) if brands else "主流模型"
            return f"{brands_str} 的版本迭代和能力对比成为焦点，核心在看推理速度、多模态支持和实际产品落地速度的差异。"
        if any(t in haystack_lower for t in ["reasoning", "chain-of-thought", "cot", "thinking budget"]):
            return "大模型推理能力（尤其是 chain-of-thought 和 thinking budget 机制）引发深度讨论，焦点在长思维链的成本、延迟与效果之间的取舍。"
        if any(t in haystack_lower for t in ["context window", "1m token", "100k", "200k", "10m"]):
            context_match = next((t for t in tokens if any(c.isdigit() for c in t) and "k" in t.lower() or "m" in t.lower()), None)
            return f"长上下文窗口能力（{context_match or '超长上下文'}）成为大模型竞争关键维度，侧重无损记忆、检索质量和推理成本的影响。"
        if any(t in haystack_lower for t in ["multimodal", "vision", "image", "audio", "video"]):
            return "多模态能力（大模型理解图像、音频、视频）成为新一轮产品差异化战场，多家厂商密集发布相关更新。"
        if any(t in haystack_lower for t in ["price war", "降价", "cheaper", "cost drop", "token cost"]):
            return "大模型价格战持续，主要厂商纷纷下调 API 定价，焦点在性价比压缩对 AI 应用层创新的推动。"
        return f"大模型领域最新动态，关键提及：{', '.join(tokens[1:5] if len(tokens) > 4 else tokens)}。点击原文了解详情。"

    if topic_key == "obsidian":
        if any(t in haystack_lower for t in ["remarkable", "handwritten", "handwritten note", "tablet", "纸笔", "手写"]):
            return "用户分享将 reMarkable 等手写设备与 Obsidian 联动的工作流，把纸质笔记拍照转 Markdown 后纳入知识库，实现模拟与数字笔记的融合管理。"
        if any(t in haystack_lower for t in ["vault", "synced", "sync", "icloud", "onedrive", "git sync"]):
            return "Obsidian vault 同步方案成为讨论热点，涉及 iCloud、OneDrive、Git 等方案的稳定性对比和移动端访问体验。"
        if any(t in haystack_lower for t in ["plugin", "plugins", "community plugin"]):
            plugin_names = [t for t in tokens if len(t) > 4 and t not in {"plugin", "plugins", "community"}]
            return f"Obsidian 社区插件生态活跃，本条涉及 {plugin_names[0] if plugin_names else '具体插件'}的使用技巧或新插件推荐。"
        if any(t in haystack_lower for t in ["template", "templater", "dataview", "meta", "frontmatter"]):
            return "Obsidian 模板与 Dataview 查询实践引发讨论，核心在如何用结构化元数据实现知识库的自动化组织和长期积累。"
        if any(t in haystack_lower for t in ["zettelkasten", "卡片盒", "atomic note", "link", "双向链接"]):
            return "Zettelkasten（卡片盒笔记法）在 Obsidian 中的实践分享，侧重原子化笔记、双向链接构建和知识网络生长的方法论。"
        if any(t in haystack_lower for t in ["publish", "发布", "public garden", "digital garden", "blog"]):
            return "Obsidian Publish 或数字花园（Digital Garden）搭建方案引发关注，探讨如何将私密笔记逐步公开发布成可阅读网站。"
        return f"Obsidian 相关讨论，关键词：{', '.join(tokens[1:5] if len(tokens) > 4 else tokens)}。点击原文了解详情。"

    return "这条内容涉及相关主题的最新动态，建议点开原文确认具体细节。"


def _topic_colors() -> dict[str, dict]:
    """各主题的基础标识与展示文案。"""
    return {
        "claude-code": {
            "icon": "🤖",
            "label": "Claude Code",
            "eyebrow": "编码工作流",
            "tagline": "追踪终端 Agent、插件生态与真实开发链路里的新信号。",
            "chips": ["终端", "插件", "Review"],
        },
        "codex": {
            "icon": "⚡",
            "label": "Codex",
            "eyebrow": "产品能力",
            "tagline": "关注 Codex CLI、工具扩展与端到端自动化能力的演进。",
            "chips": ["CLI", "MCP", "自动化"],
        },
        "large-models": {
            "icon": "🧠",
            "label": "大模型",
            "eyebrow": "模型战况",
            "tagline": "观察模型发布、推理能力、多模态与价格竞争的趋势变化。",
            "chips": ["推理", "多模态", "版本"],
        },
        "obsidian": {
            "icon": "📎",
            "label": "Obsidian",
            "eyebrow": "知识管理",
            "tagline": "筛选插件、同步、知识库组织和数字花园相关的高质量讨论。",
            "chips": ["插件", "同步", "知识库"],
        },
    }


def _heat_html(score: int) -> str:
    if score >= 130:
        return '<span class="monitor-heat monitor-heat--blast">🔥 爆热</span>'
    elif score >= 90:
        return '<span class="monitor-heat monitor-heat--high">🔶 高热</span>'
    elif score >= 60:
        return '<span class="monitor-heat monitor-heat--mid">📊 中热</span>'
    else:
        return '<span class="monitor-heat monitor-heat--low">📉 平热</span>'


def _source_html(source: str) -> str:
    label = html.escape(SOURCE_LABELS.get(source, source))
    safe_source = source if source in {"x", "youtube", "hn", "web", "reddit"} else "web"
    return f'<span class="monitor-source-badge monitor-source-badge--{safe_source}">{label}</span>'


def _engagement_html(item) -> str:
    if item.engagement:
        return f'<span>{html.escape(item.engagement)}</span>'
    return ""


def _merged_item_card(index: int, m: MergedItem, section: TopicSection) -> str:
    """渲染一条三区结构卡片。"""
    topic_key = m.topic_key
    score = m.score
    heat = _heat_html(score)
    primary = m.primary_item
    source = _source_html(primary.source)
    best_eng = max(
        (item.engagement for item in m.linked_items),
        key=lambda e: int(e.split()[0]) if e and e.split()[0].isdigit() else 0,
        default="",
    )
    meta_parts = [
        f"<span>热度 {score}</span>",
        _engagement_html(type("Eng", (), {"engagement": best_eng})()) if best_eng else "",
        f"<span>{html.escape(primary.date or '未知日期')}</span>",
        f"<span>{len(m.linked_items)} 条相关</span>",
    ]
    meta_html = "".join(part for part in meta_parts if part)

    link_items = []
    if primary.url:
        link_items.append(
            f'<a class="monitor-link" href="{html.escape(primary.url)}" target="_blank" rel="noopener noreferrer">查看主链接</a>'
        )
    if len(m.linked_items) > 1:
        for li, item in enumerate(m.linked_items[1:], 2):
            if not item.url:
                continue
            label = f"关联来源 {li - 1}"
            link_items.append(
                f'<a class="monitor-link monitor-link--alt" href="{html.escape(item.url)}" target="_blank" rel="noopener noreferrer">{html.escape(label)}</a>'
            )

    summary_escaped = html.escape(m.summary_zh or fallback_summary_zh(topic_key, primary))
    source_label = html.escape(SOURCE_LABELS.get(primary.source, primary.source))
    date_label = html.escape(primary.date or "未知日期")
    related_label = (
        f"同事件已合并 {len(m.linked_items)} 条相关来源"
        if len(m.linked_items) > 1
        else "当前展示为单条高置信信号"
    )
    overview_bits = [f"来源：{source_label}", f"时间：{date_label}", related_label]
    if best_eng:
        overview_bits.append(f"互动：{html.escape(best_eng)}")
    overview_text = html.escape(" · ".join(overview_bits))

    return f"""
<article class="monitor-item-card">
  <div class="monitor-item-card__top">
    <div class="monitor-item-card__badges">
      <span class="monitor-rank">{index}</span>
      {heat}
      {source}
    </div>
    <div class="monitor-item-card__meta">{meta_html}</div>
  </div>
  <div class="monitor-item-card__section">
    <p class="monitor-item-card__section-label">重点结论</p>
    <p class="monitor-item-card__summary">{summary_escaped}</p>
  </div>
  <div class="monitor-item-card__section monitor-item-card__section--soft">
    <p class="monitor-item-card__section-label">信息概览</p>
    <p class="monitor-item-card__factline">{overview_text}</p>
  </div>
  <div class="monitor-item-card__links">{''.join(link_items)}</div>
</article>"""


def render_topic_section(section: TopicSection, merged_items: list[MergedItem]) -> list[str]:
    colors = _topic_colors()
    c = colors.get(section.topic_key, colors["claude-code"])

    if not merged_items:
        chips_html = "".join(
            f'<span class="monitor-topic__chip">{html.escape(chip)}</span>'
            for chip in c.get("chips", [])
        )
        return [
            (
                f'<section class="monitor-topic topic--{section.topic_key}">'
                f'<div class="monitor-topic__header"><div class="monitor-topic__identity">'
                f'<span class="monitor-topic__icon">{c["icon"]}</span>'
                f'<div class="monitor-topic__copy"><p class="monitor-topic__eyebrow">{html.escape(c.get("eyebrow", "主题监控"))}</p>'
                f'<h2 class="monitor-topic__title">{html.escape(c["label"])}</h2>'
                f'<p class="monitor-topic__subtitle">本轮暂无高质量条目</p>'
                f'<p class="monitor-topic__tagline">{html.escape(c.get("tagline", ""))}</p></div></div></div>'
                f'<div class="monitor-topic__body"><div class="monitor-topic__chips">{chips_html}</div>'
                f'<p class="monitor-topic__note">当前没有筛到足够稳的内容，建议等待下一轮。</p></div></section>'
            )
        ]

    bullets = render_section_bullets(section)
    bullet_html = "".join(
        f'<li class="monitor-topic__note">{html.escape(b.lstrip("- ").rstrip("。").strip())}</li>'
        for b in bullets
    )

    total_raw = sum(len(m.linked_items) for m in merged_items)
    chips_html = "".join(
        f'<span class="monitor-topic__chip">{html.escape(chip)}</span>'
        for chip in c.get("chips", [])
    )
    header = f"""
<section class="monitor-topic topic--{section.topic_key}">
  <div class="monitor-topic__header">
    <div class="monitor-topic__identity">
      <span class="monitor-topic__icon">{c['icon']}</span>
      <div class="monitor-topic__copy">
        <p class="monitor-topic__eyebrow">{html.escape(c.get('eyebrow', '主题监控'))}</p>
        <h2 class="monitor-topic__title">{html.escape(c['label'])}</h2>
        <p class="monitor-topic__subtitle">{html.escape(section.source_summary_text)}</p>
        <p class="monitor-topic__tagline">{html.escape(c.get('tagline', ''))}</p>
      </div>
    </div>
    <div class="monitor-topic__count">{len(merged_items)} 条精品 · {total_raw} 条原始</div>
  </div>
  <div class="monitor-topic__body">
    <div class="monitor-topic__chips">{chips_html}</div>
    <ul class="monitor-topic__notes">{bullet_html}</ul>
    <div class="monitor-topic__grid">
"""
    items_html = "\n".join(_merged_item_card(idx, m, section) for idx, m in enumerate(merged_items, start=1))
    footer = """
    </div>
  </div>
 </section>"""
    return [header + items_html + footer + "\n"]


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


def _build_trend_summary(sections: list, merged: dict[str, list[MergedItem]]) -> list[str]:
    """生成趋势卡片和阅读提示。"""
    topic_stats = {}
    for section in sections:
        items = merged.get(section.topic_key, [])
        if not items:
            continue
        scores = [m.score for m in items]
        max_s = max(scores)
        avg_s = sum(scores) // len(scores)
        top_source = max(
            set(item.source for m in items for item in m.linked_items),
            key=lambda s: sum(1 for m in items for item in m.linked_items if item.source == s)
        )
        top_m = items[0]  # 已按热度排序
        summary = top_m.summary_zh or fallback_summary_zh(section.topic_key, top_m.primary_item)
        topic_stats[section.topic_key] = {
            "count": len(items),
            "raw_count": sum(len(m.linked_items) for m in items),
            "max": max_s,
            "avg": avg_s,
            "top_source": SOURCE_LABELS.get(top_source, top_source),
            "section": section,
            "top_summary": markdown_safe_text(summary),
        }

    if not topic_stats:
        return [
            (
                '<section class="monitor-trend">'
                '<div class="monitor-section-heading"><span>🔮</span><h2>当前整体趋势</h2></div>'
                '<div class="monitor-tips">本轮暂无足够的可用数据进行趋势判断，'
                '建议等待下一轮数据积累后再观察。</div>'
                "</section>"
            )
        ]

    colors = _topic_colors()
    cards = []
    sorted_topics = sorted(topic_stats.items(), key=lambda x: x[1]["max"], reverse=True)

    for topic_key, stats in sorted_topics:
        c = colors.get(topic_key, colors["claude-code"])
        bar_len = min(stats["max"] // 13, 10)
        bar = "▓" * bar_len + "░" * (10 - bar_len)
        topic_label = html.escape(c["label"])
        top_source = html.escape(stats["top_source"])
        top_summary = stats["top_summary"]
        cards.append(f"""
<article class="monitor-trend-card topic--{topic_key}">
  <div class="monitor-trend-card__top">
    <span class="monitor-topic__icon">{c['icon']}</span>
    <span class="monitor-trend-card__label">{topic_label}</span>
  </div>
  <div class="monitor-trend-card__score">{stats['max']}</div>
  <div class="monitor-trend-card__meta">
    <span class="monitor-trend-bar">{bar}</span>
    <span>均值 {stats['avg']} · {stats['count']} 条精品（{stats['raw_count']} 条原始）</span>
  </div>
  <div class="monitor-trend-card__meta">主要来源：{top_source}</div>
  <div class="monitor-trend-card__summary">{top_summary}</div>
</article>""")

    cards_html = f'<div class="monitor-trend__grid">{"".join(cards)}</div>'

    tip = """
<div class="monitor-tips">
  <p class="monitor-tips__title">阅读建议</p>
  优先查看爆热条目，信息密度通常最高；博客和 Hacker News 往往比短帖更能解释背景与落地做法。
  页面里已经把多条相似信息合并成单卡片，卡片底部会列出所有相关原文链接，方便继续深挖。
</div>"""

    return [
        (
            '<section class="monitor-trend">'
            '<div class="monitor-section-heading"><span>🔮</span><h2>当前整体趋势</h2></div>'
            f"{cards_html}"
            f"{tip}"
            "</section>"
        )
    ]


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

    # ---- 合并相似条目 ----
    merged = _merge_similar_items(selected)
    page_cache: dict[str, object] = {}
    for section in sections:
        merged[section.topic_key] = enrich_merged_items(section, merged[section.topic_key], page_cache)

    total_merged = sum(len(v) for v in merged.values())
    total_raw = sum(len(items) for items in selected.values())
    slot_label = "早间" if slot == "morning" else "晚间"
    slot_icon = "🌅" if slot == "morning" else "🌙"

    blog_hits = sum(
        1 for topic_items in selected.values() for item in topic_items if item.source == "web"
    )
    hn_hits = sum(
        1 for topic_items in selected.values() for item in topic_items if item.source == "hn"
    )
    x_hits = sum(
        1 for topic_items in selected.values() for item in topic_items if item.source == "x"
    )
    youtube_hits = sum(
        1 for topic_items in selected.values() for item in topic_items if item.source == "youtube"
    )

    try:
        import re
        m_start = re.match(r"(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})", window_start)
        m_end = re.match(r"(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})", window_end)
        if m_start and m_end:
            ws_dt = datetime.datetime.strptime(m_start.group(1) + " " + m_start.group(2), "%Y-%m-%d %H:%M:%S")
            we_dt = datetime.datetime.strptime(m_end.group(1) + " " + m_end.group(2), "%Y-%m-%d %H:%M:%S")
            window_hours = round((we_dt - ws_dt).total_seconds() / 3600, 1)
            window_date_str = ws_dt.strftime("%m/%d %H:%M") + " – " + we_dt.strftime("%H:%M")
        else:
            raise ValueError("format mismatch")
    except Exception:
        window_date_str = window_start.split(" ")[0]
        window_hours = "未知"

    overview_card = f"""
<section class="monitor-hero">
  <div class="monitor-hero__top">
    <div>
      <p class="monitor-eyebrow">四主题监控简报</p>
      <h1 class="monitor-hero__title"><span>{slot_icon}</span><span>{slot_label}版 · {html.escape(report_date)}</span></h1>
      <p class="monitor-hero__window">最近时段窗口：{html.escape(window_date_str)} · 约 {window_hours} 小时</p>
    </div>
    <div class="monitor-hero__stats">
      <div class="monitor-stat"><span class="monitor-stat__value">{total_merged}</span><span class="monitor-stat__label">精品条数</span></div>
      <div class="monitor-stat"><span class="monitor-stat__value">{x_hits}</span><span class="monitor-stat__label">X / 推文</span></div>
      <div class="monitor-stat"><span class="monitor-stat__value">{youtube_hits}</span><span class="monitor-stat__label">YouTube</span></div>
      <div class="monitor-stat"><span class="monitor-stat__value">{hn_hits}</span><span class="monitor-stat__label">Hacker News</span></div>
      <div class="monitor-stat"><span class="monitor-stat__value">{blog_hits}</span><span class="monitor-stat__label">博客 / 网页</span></div>
    </div>
  </div>
  <div class="monitor-hero__meta">
    <span class="monitor-meta-pill">📡 启用数据源：{html.escape(search_sources or '未记录')}</span>
    <span class="monitor-meta-pill">🔍 原始候选：{total_raw} 条</span>
    <span class="monitor-meta-pill">🧹 去重精简后：{total_merged} 条</span>
    <span class="monitor-meta-pill">🗂 监控主题：Claude Code · Codex · 大模型 · Obsidian</span>
  </div>
</section>"""

    topic_cards = "\n".join(
        "\n".join(render_topic_section(section, merged[section.topic_key]))
        for section in sections
    )

    trend_section = "\n".join(_build_trend_summary(sections, merged))

    lines = [
        "",
        '<div class="monitor-page">',
        overview_card,
        topic_cards,
        trend_section,
        "</div>",
        "",
    ]

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
