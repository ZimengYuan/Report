#!/usr/bin/env python3
"""Fetch linked pages and derive concise local Chinese summaries for monitor cards."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from urllib import error, request

from synthesize_public_report import (
    BLOCKED_WEB_DOMAINS,
    GENERIC_WEB_NOISE_TERMS,
    LOW_VALUE_EVENT_TERMS,
    TOPIC_RULES,
    clean_text,
    extract_domain,
)


FETCHABLE_SOURCES = {"web", "hn"}
SUMMARY_SENTINEL_IRRELEVANT = "__IRRELEVANT__"
TOPIC_LABELS = {
    "claude-code": "Claude Code",
    "codex": "Codex",
    "large-models": "大模型",
    "obsidian": "Obsidian",
}

OBSIDIAN_CONTEXT_TERMS = (
    "vault",
    "markdown",
    "note",
    "notes",
    "knowledge",
    "plugin",
    "templater",
    "dataview",
    "sync",
    "publish",
    "second brain",
    "zettelkasten",
    "canvas",
    "graph",
)

LARGE_MODEL_CONTEXT_TERMS = (
    "openai",
    "anthropic",
    "gemini",
    "llama",
    "qwen",
    "deepseek",
    "mistral",
    "gpt",
    "claude opus",
    "claude sonnet",
    "reasoning",
    "multimodal",
    "context window",
    "speech",
    "audio",
    "benchmark",
    "inference",
    "api",
    "model release",
)

LOW_SIGNAL_SOURCE_TERMS = (
    "webinar",
    "workshop",
    "register",
    "rsvp",
    "event",
    "tickets",
)


@dataclass
class PageContext:
    url: str
    final_url: str = ""
    title: str = ""
    description: str = ""
    excerpt: str = ""
    content_type: str = ""
    ok: bool = False
    error: str = ""


def should_fetch_url(source: str, url: str) -> bool:
    if source not in FETCHABLE_SOURCES or not url:
        return False
    domain = extract_domain(url)
    return domain not in {"x.com", "twitter.com", "youtube.com", "youtu.be"}


def _extract_title(raw_html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", raw_html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return clean_text(re.sub(r"<[^>]+>", " ", unescape(match.group(1))))


def _extract_meta(raw_html: str, meta_name: str) -> str:
    patterns = [
        rf'<meta[^>]+name=["\']{re.escape(meta_name)}["\'][^>]+content=["\'](.*?)["\']',
        rf'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']{re.escape(meta_name)}["\']',
        rf'<meta[^>]+property=["\']{re.escape(meta_name)}["\'][^>]+content=["\'](.*?)["\']',
        rf'<meta[^>]+content=["\'](.*?)["\'][^>]+property=["\']{re.escape(meta_name)}["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, raw_html, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return clean_text(unescape(match.group(1)))
    return ""


def _strip_tags(fragment: str) -> str:
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return clean_text(unescape(fragment))


def _looks_like_noise(text: str) -> bool:
    lowered = text.lower()
    if len(text) < 28:
        return True
    if lowered.count("|") >= 3:
        return True
    if any(token in lowered for token in ("cookie", "privacy policy", "all rights reserved", "javascript")):
        return True
    return False


def _extract_text_blocks(raw_html: str) -> list[str]:
    cleaned = re.sub(
        r"<(script|style|noscript|svg|iframe)[^>]*>.*?</\1>",
        " ",
        raw_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    blocks: list[str] = []
    for tag in ("h1", "h2", "p", "li"):
        for match in re.finditer(rf"<{tag}\b[^>]*>(.*?)</{tag}>", cleaned, flags=re.IGNORECASE | re.DOTALL):
            text = _strip_tags(match.group(1))
            if text and not _looks_like_noise(text):
                blocks.append(text)

    if not blocks:
        fallback = _strip_tags(cleaned)
        if fallback:
            blocks = re.split(r"(?<=[。！？.!?])\s+", fallback)

    deduped: list[str] = []
    seen: set[str] = set()
    for block in blocks:
        normalized = block.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(block)
        if len(deduped) >= 8:
            break
    return deduped


def fetch_page_context(url: str, timeout: int = 18, max_bytes: int = 280_000) -> PageContext:
    context = PageContext(url=url)
    req = request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) ReportMonitor/1.0",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(max_bytes)
            context.final_url = resp.geturl()
            context.content_type = resp.headers.get("Content-Type", "")
    except error.HTTPError as exc:
        context.error = f"http {exc.code}"
        return context
    except Exception as exc:  # pragma: no cover - network failure path
        context.error = clean_text(str(exc))
        return context

    content_type = context.content_type.lower()
    text = ""
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if not text:
        context.error = "decode failed"
        return context

    if "html" in content_type or "<html" in text.lower() or "<body" in text.lower():
        context.title = _extract_title(text)
        context.description = (
            _extract_meta(text, "description")
            or _extract_meta(text, "og:description")
            or _extract_meta(text, "twitter:description")
        )
        blocks = _extract_text_blocks(text)
        context.excerpt = clean_text(" ".join(blocks))[:2400]
    else:
        plain = clean_text(text)
        context.excerpt = plain[:2400]

    if not any([context.title, context.description, context.excerpt]):
        context.error = "empty body"
        return context

    context.ok = True
    return context


def page_relevance_score(topic_key: str, context: PageContext) -> int:
    haystack = clean_text(" ".join([context.title, context.description, context.excerpt])).lower()
    if not haystack:
        return 0

    rules = TOPIC_RULES[topic_key]
    score = 0
    for kw in rules["must"]:
        if kw in haystack:
            score += 2
    for kw in rules["bonus"]:
        if kw in haystack:
            score += 1
    for kw in rules["noise"]:
        if kw in haystack:
            score -= 3
    domain = extract_domain(context.final_url or context.url)
    if domain and any(domain == hint or domain.endswith(f".{hint}") for hint in (
        "openai.com",
        "anthropic.com",
        "obsidian.md",
        "docs.obsidian.md",
        "ai.google.dev",
        "huggingface.co",
        "mistral.ai",
        "deepseek.com",
    )):
        score += 2
    return score


def fallback_summary_from_page(topic_title: str, topic_key: str, item, context: PageContext | None, fetched_relevance: int) -> tuple[str, bool]:
    summary, is_irrelevant = heuristic_candidate_summary(
        topic_title,
        topic_key,
        item,
        context,
        fetched_relevance,
    )
    return summary, is_irrelevant


def _joined_candidate_text(item, context: PageContext | None) -> str:
    parts = [
        context.title if context else "",
        context.description if context else "",
        context.excerpt if context else "",
        clean_text(getattr(item, "summary", "") or ""),
        clean_text(getattr(item, "why_relevant", "") or ""),
        clean_text(getattr(item, "byline", "") or ""),
        clean_text(getattr(item, "identifier", "") or ""),
    ]
    return clean_text(" ".join(part for part in parts if part))


def _extract_first_sentence(text: str, limit: int = 120) -> str:
    text = clean_text(text)
    if not text:
        return ""
    sentence = re.split(r"(?<=[。！？.!?])\s+", text)[0]
    sentence = sentence.strip(" -|:;,.，。；：")
    if len(sentence) > limit:
        sentence = sentence[: limit - 1].rstrip(" ,;:") + "…"
    return sentence


def _contains_any(text: str, *tokens: str) -> bool:
    return any(token in text for token in tokens)


def _normalized_page_title(title: str) -> str:
    title = clean_text(title)
    if not title:
        return ""
    parts = [part.strip() for part in re.split(r"\s+[|\-–—]\s+", title) if clean_text(part)]
    if parts:
        return parts[0]
    return title


def _quoted_title(title: str, limit: int = 60) -> str:
    title = clean_text(title)
    if not title:
        return ""
    if len(title) > limit:
        title = title[: limit - 1].rstrip(" ,;:") + "…"
    return f"《{title}》"


def _present_labels(lowered: str, mapping: list[tuple[str, str]], limit: int = 3) -> list[str]:
    labels: list[str] = []
    for token, label in mapping:
        if token in lowered and label not in labels:
            labels.append(label)
        if len(labels) >= limit:
            break
    return labels


def _join_labels(labels: list[str]) -> str:
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} 和 {labels[1]}"
    return "、".join(labels[:-1]) + f" 和 {labels[-1]}"


def _summary_from_title(topic_label: str, page_title: str, lowered: str) -> str:
    quoted = _quoted_title(_normalized_page_title(page_title))
    if not quoted:
        return ""
    if _contains_any(lowered, "compare", "comparison", "vs ", "versus", "benchmark"):
        return f"{quoted} 主要在做方案对比，重点是不同工具或模型在真实任务里的表现差异、适用边界和取舍。"
    if _contains_any(lowered, "guide", "tutorial", "how to", "docs", "documentation", "integration", "plugin", "template"):
        return f"{quoted} 更像一份 {topic_label} 上手文档，重点交代具体接入步骤、配置方法和可复用的工作流。"
    if _contains_any(lowered, "release", "launch", "introduces", "announces", "new feature", "update"):
        return f"{quoted} 记录了一次版本或功能更新，重点是新增能力、适用场景以及这次变化对实际使用的影响。"
    if _contains_any(lowered, "security", "vulnerability", "prompt injection", "zero trust", "leak"):
        return f"{quoted} 聚焦 {topic_label} 的安全问题，重点在攻击路径、暴露面和可执行的缓解思路。"
    return f"{quoted} 提供了一个可复查的 {topic_label} 具体案例，关键信息主要落在标题涉及的功能、流程或争议点上。"


def _low_value_page(topic_key: str, item, context: PageContext | None, lowered: str) -> bool:
    domain = extract_domain((context.final_url if context else "") or (context.url if context else "") or getattr(item, "url", ""))
    if domain in BLOCKED_WEB_DOMAINS:
        return True

    if any(term in lowered for term in GENERIC_WEB_NOISE_TERMS):
        return True

    if item.source in FETCHABLE_SOURCES and any(term in lowered for term in LOW_VALUE_EVENT_TERMS):
        if not any(term in lowered for term in ("takeaways", "notes", "recording", "slides", "transcript", "write-up", "总结")):
            return True

    if topic_key == "obsidian" and not any(term in lowered for term in OBSIDIAN_CONTEXT_TERMS):
        return True

    # For X posts, skip strict topic keyword filtering since tweet content is limited
    # The heuristic rules below will handle X post summarization
    if item.source not in FETCHABLE_SOURCES:
        return False

    if topic_key == "claude-code" and not any(
        term in lowered
        for term in (
            "claude code",
            "anthropic",
            "sourcemap",
            "source code",
            "terminal",
            "tmux",
            "review",
            "plugin",
            "agent skill",
            "ollama",
            "quota",
        )
    ):
        return True

    if topic_key == "codex" and not any(
        term in lowered
        for term in (
            "codex",
            "openai",
            "codex cli",
            "mcp",
            "gmail",
            "figma",
            "notion",
            "slack",
            "prompt injection",
            "benchmark",
            "coding agent",
        )
    ):
        return True

    if topic_key == "large-models" and not any(term in lowered for term in LARGE_MODEL_CONTEXT_TERMS):
        return True

    if topic_key == "large-models" and _contains_any(lowered, "claude code", "cursor", "copilot", "coding agent"):
        if not _contains_any(lowered, "gpt", "gemini", "llama", "qwen", "deepseek", "mistral", "opus", "sonnet", "reasoning", "multimodal", "model"):
            return True

    return False


def heuristic_candidate_summary(
    topic_title: str,
    topic_key: str,
    item,
    context: PageContext | None,
    fetched_relevance: int,
) -> tuple[str, bool]:
    if context and context.ok and item.source in FETCHABLE_SOURCES and fetched_relevance <= 0:
        return "", True

    haystack = _joined_candidate_text(item, context)
    lowered = haystack.lower()
    page_title = clean_text(context.title if context else "")
    page_desc = clean_text(context.description if context else "")
    normalized_title = _normalized_page_title(page_title)
    best_sentence = _extract_first_sentence(page_desc or (context.excerpt if context else "") or clean_text(getattr(item, "summary", "") or ""))
    topic_label = TOPIC_LABELS.get(topic_key, topic_title)
    domain = extract_domain((context.final_url if context else "") or (context.url if context else "") or getattr(item, "url", ""))

    def has(*tokens: str) -> bool:
        return any(token in lowered for token in tokens)

    if _low_value_page(topic_key, item, context, lowered):
        return "", True

    if topic_key == "claude-code":
        if has("published claude code", "published claude codes source code", "source code") and has("claude code", "anthropic"):
            return "文章讨论 Anthropic 意外公开 Claude Code 源码后的连锁影响，重点在泄露细节和社区对风险的再解读。", False
        if has("hype", "overblown") and has("leak", "source code", "claude code"):
            return "这条讨论认为 Claude Code 源码泄露被过度渲染，争论焦点在事件真实危害是否像舆论说得那么严重。", False
        if has("sourcemap", "source map", "source code leaked", "leaked source", "typescript source"):
            return "文章围绕 Claude Code 源码暴露事件展开，重点在 sourcemap 导致源码被抓取后的安全风险。", False
        if has("can someone share", "share claude source code", "share source code"):
            return "源码泄露话题还在扩散，社区已经出现直接索要 Claude Code 源码镜像的讨论，说明事件热度并未消退。", False
        if has("promptfoo", "agent skill", "eval", "evals"):
            return "这篇文档介绍 Promptfoo 的 agent skill 集成方式，核心是把外部 agent 接进评测流程，自动生成和执行更复杂的 eval。", False
        if has("同僚", "100万トークン", "voice", "1m token") or ("同僚" in haystack):
            return "这条讨论把 Claude Code 放进“AI 从工具走向同事”的叙事里，重点是语音交互、超长上下文和 agent 自主性。", False
        if has("cursor", "copilot", "compare", "comparison", "vs "):
            rivals = _present_labels(lowered, [("cursor", "Cursor"), ("copilot", "GitHub Copilot"), ("codex", "Codex")])
            rival_text = _join_labels(rivals) or "其他 AI 编码工具"
            return f"这篇内容把 Claude Code 与 {rival_text} 放在一起比较，重点看复杂任务处理、自主执行深度和团队协作场景里的差异。", False
        if has("plugin", "extension", "integration", "review", "terminal", "tmux", "workflow"):
            return "文章聚焦 Claude Code 的终端工作流与插件整合，重点在 review、tmux、多步骤执行和团队协作链路里的实际落地方式。", False
        if has("ollama", "local model", "self-hosted", "quota", "billing", "cost"):
            return "讨论集中在 Claude Code 的本地接入、模型切换、配额限制与成本控制，反映出一线使用里最现实的工程约束。", False

    if topic_key == "codex":
        if has("prompt injection", "vulnerability", "security flaw", "read access"):
            return "这条内容聚焦 Codex 的 prompt injection 风险，担心 agent 在拥有代码和文档读取权限时被恶意指令带偏。", False
        if has("figma", "notion", "gmail", "slack", "jira", "linear"):
            tools = _present_labels(lowered, [("figma", "Figma"), ("notion", "Notion"), ("gmail", "Gmail"), ("slack", "Slack"), ("jira", "Jira"), ("linear", "Linear")])
            tool_text = _join_labels(tools) or "外部工具"
            return f"文章讨论 Codex 与 {tool_text} 的连接能力，重点是把编码 agent 从单纯写码扩展到设计、沟通和项目管理流程。", False
        if has("mcp", "agents v2", "hooks", "codex cli", "cli", "plugin"):
            return "内容聚焦 Codex CLI、MCP、hooks 或插件能力的演进，说明它正从代码补全工具转向可编排的 agent 工作流平台。", False
        if has("benchmark", "live against both", "vs claude", "vs cursor", "qwen", "grok"):
            rivals = _present_labels(lowered, [("claude", "Claude"), ("cursor", "Cursor"), ("grok", "Grok"), ("qwen", "Qwen")])
            rival_text = _join_labels(rivals) or "其他工具"
            return f"讨论重点是拿 Codex 与 {rival_text} 做实战对比，关注真实任务完成率、稳定性以及失败时的恢复能力。", False
        if has("benchmark", "swe-bench", "evaluation", "compare", "cursor", "copilot", "claude"):
            return "文章比较了 Codex 与其他 coding agent 的能力差异，重点在任务完成质量、稳定性、调试成本和开发体验。", False
        if has("automation", "agentic", "workflow", "end-to-end"):
            return "讨论焦点是 Codex 如何承担端到端自动化任务，例如串起检索、改码、验证和交付，而不再局限于单点代码生成。", False

    if topic_key == "large-models":
        if has("gemini 2.5", "gpt-5", "claude 4", "qwen3", "llama 4", "deepseek", "mistral", "model release"):
            vendors = _present_labels(lowered, [("openai", "OpenAI"), ("anthropic", "Anthropic"), ("gemini", "Gemini"), ("qwen", "Qwen"), ("llama", "Llama"), ("deepseek", "DeepSeek"), ("mistral", "Mistral")])
            vendor_text = _join_labels(vendors) or "主流模型厂商"
            return f"这条内容关注 {vendor_text} 的新版本变化，重点在推理能力、上下文长度、多模态支持和产品化速度上的直接竞争。", False
        if has("reasoning", "chain-of-thought", "thinking budget", "cot"):
            return "内容围绕大模型推理能力展开，重点讨论长思维链、thinking budget 与成本、时延和稳定性之间的权衡。", False
        if has("whisper", "transcribe", "speech", "voice", "audio"):
            return "文章聚焦语音方向的大模型更新，核心看点是转写准确率、语音理解能力、实时交互体验和开源替代方案。", False
        if has("openai", "anthropic", "gemini", "llama", "qwen", "deepseek", "mistral"):
            vendors = _present_labels(lowered, [("openai", "OpenAI"), ("anthropic", "Anthropic"), ("gemini", "Gemini"), ("llama", "Llama"), ("qwen", "Qwen"), ("deepseek", "DeepSeek"), ("mistral", "Mistral")])
            vendor_text = _join_labels(vendors) or "主流大模型厂商"
            return f"内容关注 {vendor_text} 的能力比较，重点在推理、多模态、价格和开发者可用性上的差异。", False
        if has("multimodal", "vision", "video", "image"):
            return "文章讨论大模型的多模态进展，重点在图像、视频或音频理解能力的增强，以及这些能力如何进入真实产品场景。", False
        if has("pricing", "price", "cost", "cheaper", "降价"):
            return "内容聚焦大模型价格与成本变化，反映出 API 性价比竞争已经开始明显影响应用层的模型选型和部署策略。", False

    if topic_key == "obsidian":
        if has("from zero", "desde cero", "markdown", "notion") and has("obsidian"):
            return "作者预告一场从零开始的 Obsidian 教学，并解释自己为何从 Notion 转向基于 Markdown 的笔记工作流。", False
        if has("second brain", "shared context layer", "actual productivity stack"):
            return "这条分享把 Obsidian 当作第二大脑和共享上下文层，用来给其他 AI 工具持续提供可调用的个人知识背景。", False
        if has("graveyard for good ideas", "sprouted", "second brain"):
            return "这条内容反思 Obsidian/Notion 式第二大脑容易只收藏不回看，问题不在工具本身，而在缺少回顾和执行闭环。", False
        if has("obsidian sync"):
            return "这条讨论虽然很短，但再次说明同步体验仍是 Obsidian 用户最敏感、最常被提起的核心需求之一。", False
        if has("markdown notes", "ai directly", "manage everything for you", "forget notion", "forget obsidian"):
            return "讨论把 AI 直接接进 Markdown 笔记库，目标是让代理替你整理和调用知识，而不是手动维护 Obsidian/Notion。", False
        if has("sync", "vault", "icloud", "onedrive", "git sync"):
            sync_tools = _present_labels(lowered, [("icloud", "iCloud"), ("onedrive", "OneDrive"), ("git sync", "Git Sync"), ("obsidian sync", "Obsidian Sync")])
            sync_text = _join_labels(sync_tools) or "不同同步方案"
            return f"文章围绕 Obsidian 的 vault 同步与跨端管理展开，重点比较 {sync_text} 在稳定性、成本和维护复杂度上的差异。", False
        if has("plugin", "community plugin", "dataview", "templater", "publish"):
            plugins = _present_labels(lowered, [("dataview", "Dataview"), ("templater", "Templater"), ("publish", "Publish")])
            plugin_text = _join_labels(plugins) or "插件生态"
            return f"内容聚焦 Obsidian 的 {plugin_text} 或发布工作流，重点在如何把知识库进一步自动化、结构化并复用到日常写作。", False
        if has("zettelkasten", "atomic note", "digital garden", "public garden", "双向链接"):
            return "文章讨论 Obsidian 的知识组织方法，重点在卡片盒、双向链接与数字花园式的内容沉淀。", False
        if has("ai", "rag", "retrieval", "assistant", "second brain"):
            return "内容关注 Obsidian 与 AI 检索或写作能力的结合，重点在知识库如何从静态笔记演变成可调用、可检索的工作台。", False

    if context and context.ok:
        if re.search(r"[\u4e00-\u9fff]", best_sentence):
            return best_sentence, False
        if normalized_title:
            return _summary_from_title(topic_label, normalized_title, lowered), False

    raw_hint = clean_text(" ".join(filter(None, [getattr(item, "summary", ""), getattr(item, "why_relevant", ""), getattr(item, "byline", ""), getattr(item, "identifier", "")])))
    raw_sentence = _extract_first_sentence(raw_hint)
    if raw_sentence:
        if re.search(r"[\u4e00-\u9fff]", raw_sentence):
            return raw_sentence, False
        if has("compare", "vs ", "benchmark"):
            return f"这条内容围绕 {topic_label} 的横向对比展开，重点是不同工具或方案在真实任务、效果和使用成本上的差异。", False
        if has("security", "leak", "vulnerability", "prompt injection"):
            return f"这条内容聚焦 {topic_label} 的安全或风险问题，重点在暴露面、攻击路径以及可操作的防护思路。", False
        if has("plugin", "integration", "workflow", "template", "sync", "publish"):
            return f"这条内容更偏向 {topic_label} 的工作流或集成实践，顺着原文通常能看到更具体的配置方法和落地步骤。", False
        if domain:
            return f"这条内容来自 {domain}，围绕 {topic_label} 的具体案例展开，建议顺着原文核对其中提到的方法、结论和限制条件。", False

    if normalized_title:
        return _summary_from_title(topic_label, normalized_title, lowered), False

    if domain:
        return f"这条内容来自 {domain}，属于 {topic_label} 方向里的具体信号，最好结合原文判断它对应的是教程、案例还是风险提醒。", False

    return f"这条内容提供了 {topic_label} 方向的新信号，但公开信息仍偏有限，建议打开原文确认具体结论、证据和适用场景。", False


def summarize_candidates(topic_title: str, topic_key: str, candidates: list[dict]) -> dict[int, dict]:
    if not candidates:
        return {}

    localized: dict[int, dict] = {}
    for candidate in candidates:
        summary, is_irrelevant = heuristic_candidate_summary(
            topic_title,
            topic_key,
            candidate["item"],
            candidate.get("page_context"),
            candidate.get("page_relevance", 0),
        )
        if summary or is_irrelevant:
            localized[candidate["index"]] = {
                "summary_zh": SUMMARY_SENTINEL_IRRELEVANT if is_irrelevant else summary,
                "is_irrelevant": is_irrelevant,
            }
    return localized
