#!/usr/bin/env python3
"""Fetch linked pages and derive concise Chinese summaries for monitor cards."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from urllib import error, request

from synthesize_public_report import TOPIC_RULES, clean_text, extract_domain


FETCHABLE_SOURCES = {"web", "hn"}
OPENAI_CHAT_MODELS = ["gpt-5-mini", "gpt-4.1-mini"]
SUMMARY_SENTINEL_IRRELEVANT = "__IRRELEVANT__"
TOPIC_LABELS = {
    "claude-code": "Claude Code",
    "codex": "Codex",
    "large-models": "大模型",
    "obsidian": "Obsidian",
}


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


def _extract_first_sentence(text: str, limit: int = 48) -> str:
    text = clean_text(text)
    if not text:
        return ""
    sentence = re.split(r"(?<=[。！？.!?])\s+", text)[0]
    sentence = sentence.strip(" -|:;,.，。；：")
    if len(sentence) > limit:
        sentence = sentence[: limit - 1].rstrip(" ,;:") + "…"
    return sentence


def _quoted_title(title: str, limit: int = 32) -> str:
    title = clean_text(title)
    if not title:
        return ""
    if len(title) > limit:
        title = title[: limit - 1].rstrip(" ,;:") + "…"
    return f"《{title}》"


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
    best_sentence = _extract_first_sentence(page_desc or (context.excerpt if context else "") or clean_text(getattr(item, "summary", "") or ""))
    topic_label = TOPIC_LABELS.get(topic_key, topic_title)

    def has(*tokens: str) -> bool:
        return any(token in lowered for token in tokens)

    if topic_key == "claude-code":
        if has("sourcemap", "source map", "source code leaked", "leaked source", "typescript source"):
            return "文章围绕 Claude Code 源码暴露事件展开，重点在 sourcemap 导致源码被抓取后的安全风险。", False
        if has("cursor", "copilot", "compare", "comparison", "vs "):
            return "内容比较了 Claude Code 与其他 AI 编码工具的定位差异，重点在复杂任务、自主性和团队协作场景。", False
        if has("plugin", "extension", "integration", "review", "terminal", "tmux", "workflow"):
            return "文章聚焦 Claude Code 的终端工作流与插件整合，强调它如何进入真实开发、review 与协作链路。", False
        if has("ollama", "local model", "self-hosted", "quota", "billing", "cost"):
            return "讨论集中在 Claude Code 的本地接入、配额与成本控制，反映出一线使用中的工程现实问题。", False

    if topic_key == "codex":
        if has("figma", "notion", "gmail", "slack", "jira", "linear"):
            return "文章讨论 Codex 与外部工具的连接能力，重点是把编码 agent 扩展到设计、协作和项目管理流程。", False
        if has("mcp", "agents v2", "hooks", "codex cli", "cli", "plugin"):
            return "内容聚焦 Codex CLI 与插件能力演进，说明它正从写码工具转向更完整的 agent 工作流平台。", False
        if has("benchmark", "swe-bench", "evaluation", "compare", "cursor", "copilot", "claude"):
            return "文章比较了 Codex 与其他 coding agent 的能力差异，重点在任务完成质量、稳定性和开发体验。", False
        if has("automation", "agentic", "workflow", "end-to-end"):
            return "讨论焦点是 Codex 如何承担端到端自动化任务，而不再局限于单点代码生成。", False

    if topic_key == "large-models":
        if has("reasoning", "chain-of-thought", "thinking budget", "cot"):
            return "内容围绕大模型推理能力展开，重点讨论长思维链、thinking budget 与成本延迟之间的权衡。", False
        if has("whisper", "transcribe", "speech", "voice", "audio"):
            return "文章聚焦语音方向的大模型更新，核心看点是转写准确率、语音理解能力和开源替代方案。", False
        if has("openai", "anthropic", "gemini", "llama", "qwen", "deepseek", "mistral"):
            return "内容关注主流大模型厂商的新版本与能力比较，重点在推理、多模态和产品化速度的差异。", False
        if has("multimodal", "vision", "video", "image"):
            return "文章讨论大模型的多模态进展，重点在图像、视频或音频理解能力的增强及其应用场景。", False
        if has("pricing", "price", "cost", "cheaper", "降价"):
            return "内容聚焦大模型价格与成本变化，反映出 API 性价比竞争正在影响应用层的选型。", False

    if topic_key == "obsidian":
        if has("sync", "vault", "icloud", "onedrive", "git sync"):
            return "文章围绕 Obsidian 的 vault 同步与跨端管理展开，重点比较不同同步方案的稳定性和维护成本。", False
        if has("plugin", "community plugin", "dataview", "templater", "publish"):
            return "内容聚焦 Obsidian 插件生态或发布工作流，重点在如何把知识库进一步自动化和结构化。", False
        if has("zettelkasten", "atomic note", "digital garden", "public garden", "双向链接"):
            return "文章讨论 Obsidian 的知识组织方法，重点在卡片盒、双向链接与数字花园式的内容沉淀。", False
        if has("ai", "rag", "retrieval", "assistant", "second brain"):
            return "内容关注 Obsidian 与 AI 检索或写作能力的结合，重点在知识库如何成为更可调用的工作台。", False

    if context and context.ok:
        if re.search(r"[\u4e00-\u9fff]", best_sentence):
            return best_sentence, False
        if page_title:
            return f"{topic_label} 相关网页 { _quoted_title(page_title) }，主要围绕实践经验、产品更新或使用方法展开。", False

    raw_hint = clean_text(" ".join(filter(None, [getattr(item, "summary", ""), getattr(item, "why_relevant", ""), getattr(item, "byline", ""), getattr(item, "identifier", "")])))
    raw_sentence = _extract_first_sentence(raw_hint)
    if raw_sentence:
        if re.search(r"[\u4e00-\u9fff]", raw_sentence):
            return raw_sentence, False
        return f"这条内容与 {topic_label} 相关，主要围绕一线实践、工具对比或产品动态展开。", False

    return f"这条内容与 {topic_label} 相关，建议点开原文查看具体细节。", False


def summarize_candidates(topic_title: str, topic_key: str, candidates: list[dict]) -> dict[int, dict]:
    if not candidates:
        return {}

    env = load_runtime_env()
    api_key = env.get("OPENAI_API_KEY")
    if not api_key:
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

    payload_items = []
    for candidate in candidates:
        item = candidate["item"]
        context: PageContext | None = candidate.get("page_context")
        payload_items.append(
            {
                "index": candidate["index"],
                "source": item.source,
                "date": item.date or "未知日期",
                "url": item.url or "",
                "raw_hint": clean_text(" | ".join(filter(None, [item.summary, item.why_relevant, item.byline, item.identifier])))[:260],
                "page_title": context.title if context else "",
                "page_description": context.description if context else "",
                "page_excerpt": context.excerpt[:900] if context else "",
                "page_relevance": candidate.get("page_relevance", 0),
            }
        )

    system_prompt = (
        "你是中文技术编辑，负责把抓取候选写成真正可读的监控卡片摘要。\n"
        "请优先依据 page_title / page_description / page_excerpt，也就是实际网页正文信息来写摘要，不要只复述 raw_hint。\n"
        "输出要求：\n"
        "1. 每条只写一句中文摘要，18 到 50 个汉字左右。\n"
        "2. 先说发生了什么，再点出最值得看的信息，不要写空话。\n"
        "3. 如果网页内容与主题明显无关，summary_zh 必须输出 __IRRELEVANT__。\n"
        "4. 不要输出 Markdown，不要编号，不要附加解释。\n"
        "只返回 JSON：{\"items\":[{\"index\":1,\"summary_zh\":\"...\"}]}"
    )
    body = {
        "topic": topic_title,
        "topic_key": topic_key,
        "items": payload_items,
    }

    for model in OPENAI_CHAT_MODELS:
        req = request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(
                {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": json.dumps(body, ensure_ascii=False)},
                    ],
                    "response_format": {"type": "json_object"},
                }
            ).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=45) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
            content = raw["choices"][0]["message"]["content"]
            parsed = parse_json_object(content)
        except Exception:
            continue

        localized: dict[int, dict] = {}
        for entry in parsed.get("items", []):
            try:
                index = int(entry["index"])
            except Exception:
                continue
            summary = clean_text(str(entry.get("summary_zh", "")))
            if summary:
                localized[index] = {
                    "summary_zh": summary,
                    "is_irrelevant": summary == SUMMARY_SENTINEL_IRRELEVANT,
                }
        if localized:
            return localized

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
