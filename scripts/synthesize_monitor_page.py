#!/usr/bin/env python3
"""Combine multiple topic briefs into one monitoring page."""

from __future__ import annotations

import argparse
import datetime
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
            score = combined_heat_score(primary, topic_key)
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


def select_global_items(sections: list[TopicSection], max_items: int = 40) -> dict[str, list]:
    """
    收集所有候选按热度排序选入，去重留到 _merge_similar_items 统一处理。
    每 topic 最多 15 条，总上限 40 条。
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
    max_per_topic = 15

    all_candidates: list[tuple[int, str, object]] = []
    for section in sections:
        for item in ranked[section.topic_key]:
            all_candidates.append((combined_heat_score(item, section.topic_key), section.topic_key, item))

    all_candidates.sort(key=lambda entry: entry[0], reverse=True)

    chosen_count = 0
    for _heat, topic_key, item in all_candidates:
        if chosen_count >= max_items:
            break
        if len(selected[topic_key]) >= max_per_topic:
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
    """各主题的配色方案：左侧边、标题背景、图标。"""
    return {
        "claude-code": {
            "border": "#6366f1",
            "header_bg": "linear-gradient(135deg,#6366f1,#818cf8)",
            "icon": "🤖",
            "label": "Claude Code",
        },
        "codex": {
            "border": "#f59e0b",
            "header_bg": "linear-gradient(135deg,#f59e0b,#fbbf24)",
            "icon": "⚡",
            "label": "Codex",
        },
        "large-models": {
            "border": "#10b981",
            "header_bg": "linear-gradient(135deg,#10b981,#34d399)",
            "icon": "🧠",
            "label": "大模型",
        },
        "obsidian": {
            "border": "#8b5cf6",
            "header_bg": "linear-gradient(135deg,#8b5cf6,#a78bfa)",
            "icon": "📎",
            "label": "Obsidian",
        },
    }


def _heat_html(score: int) -> str:
    if score >= 130:
        return '<span style="background:#ef4444;color:#fff;padding:2px 10px;border-radius:12px;font-size:12px;font-weight:bold">🔥 爆热</span>'
    elif score >= 90:
        return '<span style="background:#f97316;color:#fff;padding:2px 10px;border-radius:12px;font-size:12px;font-weight:bold">🔶 高热</span>'
    elif score >= 60:
        return '<span style="background:#3b82f6;color:#fff;padding:2px 10px;border-radius:12px;font-size:12px;font-weight:bold">📊 中热</span>'
    else:
        return '<span style="background:#6b7280;color:#fff;padding:2px 10px;border-radius:12px;font-size:12px;font-weight:bold">📉 平热</span>'


def _source_html(source: str) -> str:
    colors = {
        "x": ("#1d9bf0", "#fff"),
        "youtube": ("#ff0000", "#fff"),
        "hn": ("#ff6600", "#fff"),
        "web": ("#0d9488", "#fff"),
        "reddit": ("#ff4500", "#fff"),
    }
    bg, fg = colors.get(source, ("#6b7280", "#fff"))
    label = SOURCE_LABELS.get(source, source)
    return f'<span style="background:{bg};color:{fg};padding:2px 9px;border-radius:10px;font-size:11px;font-weight:600">{label}</span>'


def _engagement_html(item) -> str:
    if item.engagement:
        return f'<span style="color:#9ca3af;font-size:12px;margin-left:6px">{item.engagement}</span>'
    return ""


def _merged_item_card(index: int, m: MergedItem, section: TopicSection) -> str:
    """渲染一条 MergedItem（多条目合并），含多条链接。"""
    topic_key = m.topic_key
    score = m.score
    heat = _heat_html(score)
    primary = m.primary_item

    # 来源 + 日期（用 primary 的）
    source = _source_html(primary.source)
    date_str = f'<span style="color:#9ca3af;font-size:12px">{primary.date or "未知日期"}</span>'

    # 互动量（取 engagement 最多的那条）
    best_eng = max((item.engagement for item in m.linked_items), key=lambda e: int(e.split()[0]) if e and e.split()[0].isdigit() else 0, default="")
    eng_html = f'<span style="color:#9ca3af;font-size:12px;margin-left:6px">{best_eng}</span>' if best_eng else ""

    # 链接按钮：多条时垂直堆叠
    if len(m.linked_items) == 1:
        link_btns = f'<a href="{primary.url}" target="_blank" style="display:inline-block;margin-top:8px;padding:5px 14px;background:#1d9bf0;color:#fff;border-radius:8px;font-size:12px;text-decoration:none;font-weight:600">🔗 打开原文</a>'
    else:
        link_btns = "<div style=\"margin-top:8px;display:flex;flex-wrap:wrap;gap:6px\">"
        for li, item in enumerate(m.linked_items, 1):
            if item.url:
                label = f"🔗 链接{len(m.linked_items)>2 and chr(0x2460+li-1) or ''} · {item.source.capitalize()}"
                link_btns += f'<a href="{item.url}" target="_blank" style="display:inline-block;padding:4px 10px;background:#1d9bf0;color:#fff;border-radius:8px;font-size:11px;text-decoration:none;font-weight:600">{label}</a>'
        link_btns += "</div>"

    row_bg = "#f8fafc" if index % 2 == 0 else "#ffffff"
    border_colors = ["#6366f1", "#f59e0b", "#10b981", "#8b5cf6"]
    border_color = border_colors[index % len(border_colors)]
    summary_escaped = markdown_safe_text(m.summary_zh) if m.summary_zh else markdown_safe_text(fallback_summary_zh(topic_key, primary))

    return f"""
<div style="background:{row_bg};border-radius:10px;padding:14px 16px;margin-bottom:10px;border-left:4px solid {border_color};box-shadow:0 1px 3px rgba(0,0,0,0.06)">
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap">
    <span style="background:#1e293b;color:#fff;width:26px;height:26px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:13px;font-weight:bold;flex-shrink:0">{index}</span>
    {heat}
    <span style="color:#6b7280;font-size:12px">热度: {score}</span>
    {eng_html}
    {source}
    {date_str}
    <span style="margin-left:auto;font-size:12px;color:#94a3b8">{len(m.linked_items)} 条相关</span>
  </div>
  <div style="font-size:15px;color:#1e293b;line-height:1.65;margin-bottom:10px">{summary_escaped}</div>
  {link_btns}
</div>"""


def render_topic_section(section: TopicSection, merged_items: list[MergedItem]) -> list[str]:
    colors = _topic_colors()
    c = colors.get(section.topic_key, colors["claude-code"])

    if not merged_items:
        return [
            f'<div style="background:#f1f5f9;border-radius:12px;padding:20px;text-align:center;color:#64748b">'
            f'{c["icon"]} {c["label"]} · 本轮暂无高质量条目</div>'
        ]

    bullets = render_section_bullets(section)
    bullet_html = ""
    for b in bullets:
        clean_b = markdown_safe_text(b.lstrip("- ").rstrip("。").strip())
        bullet_html += f'<li style="margin-bottom:4px">{clean_b}</li>'

    total_raw = sum(len(m.linked_items) for m in merged_items)
    header = f"""
<div style="background:{c['header_bg']};border-radius:12px 12px 0 0;padding:14px 20px;display:flex;align-items:center;gap:10px">
  <span style="font-size:22px">{c['icon']}</span>
  <div>
    <div style="color:#fff;font-size:17px;font-weight:bold">{c['label']}</div>
    <div style="color:rgba(255,255,255,0.8);font-size:12px">{section.source_summary_text}</div>
  </div>
  <div style="margin-left:auto;text-align:right">
    <div style="background:rgba(255,255,255,0.25);border-radius:8px;padding:4px 10px;color:#fff;font-size:13px;font-weight:bold">{len(merged_items)} 条精品 · {total_raw} 条原始</div>
  </div>
</div>
<div style="background:#f8fafc;border-radius:0 0 12px 12px;padding:14px 16px;margin-bottom:20px;border:1px solid #e2e8f0;border-top:none">
  <ul style="margin:0;padding-left:18px;color:#475569;font-size:13px;line-height:1.7">{bullet_html}</ul>
</div>"""

    items_html = ""
    for idx, m in enumerate(merged_items, start=1):
        items_html += _merged_item_card(idx, m, section)

    return [f'<div style="max-width:820px;margin-bottom:30px">{header}{items_html}</div>\n']


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
    """生成视觉美观、各主题并排的趋势总结（基于 merged items）。"""
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
            '<div style="text-align:center;color:#9ca3af;padding:30px;font-size:15px">'
            '本轮暂无足够的可用数据进行趋势判断，建议等待下一轮数据积累后再做分析。</div>'
        ]

    colors = _topic_colors()
    cards = []
    sorted_topics = sorted(topic_stats.items(), key=lambda x: x[1]["max"], reverse=True)

    for topic_key, stats in sorted_topics:
        c = colors.get(topic_key, colors["claude-code"])
        bar_len = min(stats["max"] // 13, 10)
        bar = "▓" * bar_len + "░" * (10 - bar_len)
        cards.append(f"""
<div style="flex:1;min-width:200px;background:#f8fafc;border-radius:12px;padding:16px;border-top:4px solid {c['border']};box-shadow:0 1px 4px rgba(0,0,0,0.06)">
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
    <span style="font-size:20px">{c['icon']}</span>
    <span style="font-size:15px;font-weight:bold;color:#1e293b">{c['label']}</span>
  </div>
  <div style="font-size:28px;font-weight:bold;color:{c['border']};margin-bottom:4px">{stats['max']}</div>
  <div style="font-size:13px;color:#64748b;margin-bottom:10px">{bar} 均值 {stats['avg']} · {stats['count']} 条精品（{stats['raw_count']} 条原始）</div>
  <div style="font-size:12px;color:#94a3b8;margin-bottom:8px">主要来源：{stats['top_source']}</div>
  <div style="font-size:13px;color:#475569;line-height:1.6">{stats['top_summary']}</div>
</div>""")

    cards_html = f'<div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:24px">{"".join(cards)}</div>'

    tip = """
<div style="background:linear-gradient(135deg,#1e293b,#334155);border-radius:12px;padding:16px 20px;color:#e2e8f0;font-size:13px;line-height:1.8">
  <div style="font-weight:bold;margin-bottom:6px;font-size:14px">💡 阅读建议</div>
  优先查看 🔥 <b>爆热</b> 条目，信息密度最高；博客 / 黑客新闻内容通常比推文更深入；
  若某主题本轮空白，并不代表无讨论，往往是数据源未抓取到够强的信号。
  <b>多条相似内容已合并展示</b>，每条精品条目底部列出了所有相关链接。
</div>"""

    return [cards_html + "\n" + tip]


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

    # ---- 合并相似条目 ----
    merged = _merge_similar_items(selected)
    # 用 AI 摘要填充 merged.summary_zh
    for topic_key, items_list in selected.items():
        section = next((s for s in sections if s.topic_key == topic_key), None)
        if not section:
            continue
        for mi in merged[topic_key]:
            # 找 primary_item 在 selected 列表中的位置（用于取 localized_summaries）
            try:
                idx = items_list.index(mi.primary_item) + 1
            except ValueError:
                idx = 1
            ai_summary = section.localized_summaries.get(idx)
            if ai_summary:
                mi.summary_zh = ai_summary

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
<div style="background:linear-gradient(135deg,#0f172a,#1e3a5f);border-radius:16px;padding:24px 28px;margin-bottom:28px;box-shadow:0 4px 20px rgba(0,0,0,0.15)">
  <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px">
    <div>
      <div style="color:#e2e8f0;font-size:11px;letter-spacing:2px;text-transform:uppercase;margin-bottom:4px">四主题监控简报</div>
      <div style="color:#fff;font-size:22px;font-weight:bold">{slot_icon} {slot_label}版 · {report_date}</div>
      <div style="color:#94a3b8;font-size:13px;margin-top:4px">🕐 {window_date_str} · 约 {window_hours}h</div>
    </div>
    <div style="display:flex;gap:16px;flex-wrap:wrap">
      <div style="text-align:center">
        <div style="font-size:32px;font-weight:bold;color:#38bdf8">{total_merged}</div>
        <div style="font-size:12px;color:#94a3b8">精品条数</div>
      </div>
      <div style="width:1px;background:#334155;margin:4px 0"></div>
      <div style="text-align:center">
        <div style="font-size:32px;font-weight:bold;color:#1d9bf0">{x_hits}</div>
        <div style="font-size:12px;color:#94a3b8">X</div>
      </div>
      <div style="text-align:center">
        <div style="font-size:32px;font-weight:bold;color:#ff0000">{youtube_hits}</div>
        <div style="font-size:12px;color:#94a3b8">YouTube</div>
      </div>
      <div style="text-align:center">
        <div style="font-size:32px;font-weight:bold;color:#f97316">{hn_hits}</div>
        <div style="font-size:12px;color:#94a3b8">HN</div>
      </div>
      <div style="text-align:center">
        <div style="font-size:32px;font-weight:bold;color:#0d9488">{blog_hits}</div>
        <div style="font-size:12px;color:#94a3b8">博客</div>
      </div>
    </div>
  </div>
  <div style="margin-top:14px;padding-top:14px;border-top:1px solid #334155;display:flex;gap:20px;flex-wrap:wrap">
    <div style="font-size:12px;color:#94a3b8">📡 数据来源：<span style="color:#e2e8f0">{search_sources or '未记录'}</span></div>
    <div style="font-size:12px;color:#94a3b8">🔍 原始候选：{total_raw} 条 → 合并去重后 {total_merged} 条精品</div>
  </div>
</div>"""

    topic_cards = ""
    for section in sections:
        topic_cards += "\n".join(render_topic_section(section, merged[section.topic_key]))

    trend_section = """
<div style="max-width:820px;margin-top:10px;margin-bottom:10px">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:16px">
    <span style="font-size:20px">🔮</span>
    <span style="font-size:17px;font-weight:bold;color:#1e293b">当前整体趋势</span>
  </div>
"""
    trend_content = "\n".join(_build_trend_summary(sections, merged))
    trend_section += trend_content + "\n</div>"

    lines = [
        "",
        overview_card,
        "",
        topic_cards,
        "",
        trend_section,
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
