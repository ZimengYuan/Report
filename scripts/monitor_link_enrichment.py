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


def fallback_summary_from_page(topic_title: str, item, context: PageContext | None, fetched_relevance: int) -> tuple[str, bool]:
    if context and context.ok and item.source in FETCHABLE_SOURCES and fetched_relevance <= 0:
        return "这是一条与当前主题关联度较低的网页结果，建议移除。", True

    if context and context.ok:
        detail = clean_text(context.description or context.excerpt or context.title)
        detail = re.split(r"(?<=[。！？.!?])\s+", detail)[0]
        detail = detail[:58].rstrip(" ,;:")
        if detail:
            if re.search(r"[\u4e00-\u9fff]", detail):
                return detail, False
            title = clean_text(context.title)[:40]
            if title:
                return f"网页主要讨论：{title}", False
            return f"网页主要内容：{detail[:38]}", False

    return "", False


def summarize_candidates(topic_title: str, topic_key: str, candidates: list[dict]) -> dict[int, dict]:
    if not candidates:
        return {}

    env = load_runtime_env()
    api_key = env.get("OPENAI_API_KEY")
    if not api_key:
        return {}

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

    return {}
