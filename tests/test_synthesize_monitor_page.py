import importlib.util
import os
import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


def _load_module(module_name: str, file_name: str):
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_DIR / file_name)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module {module_name} from {file_name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


public_module = _load_module("synthesize_public_report", "synthesize_public_report.py")
monitor_module = _load_module("synthesize_monitor_page", "synthesize_monitor_page.py")

TopicSection = monitor_module.TopicSection
select_global_items = monitor_module.select_global_items
Item = public_module.Item


def make_item(source: str, score: int, idx: int) -> Item:
    return Item(
        source=source,
        identifier=f"id-{idx}",
        score=score,
        byline="sample",
        date="2026-04-09",
        summary=f"summary-{idx}",
        url=f"https://example.com/{idx}",
    )


class TestSelectGlobalItems(TestCase):
    def test_keeps_base_items_per_topic(self):
        section_a = TopicSection(
            topic_key="codex",
            title="Codex",
            report_date_range="2026-04-09",
            model="gpt",
            source_summary_text="",
            error_summary_text="",
            quality_line="",
            curated_items=[make_item("web", 80, 1), make_item("x", 70, 2), make_item("hn", 60, 3), make_item("web", 50, 4)],
            stats={},
            localized_summaries={},
        )
        section_b = TopicSection(
            topic_key="claude-code",
            title="Claude Code",
            report_date_range="2026-04-09",
            model="gpt",
            source_summary_text="",
            error_summary_text="",
            quality_line="",
            curated_items=[make_item("web", 81, 5), make_item("x", 71, 6), make_item("hn", 61, 7), make_item("web", 51, 8)],
            stats={},
            localized_summaries={},
        )

        selected = select_global_items([section_a, section_b], max_items=6)

        self.assertGreaterEqual(len(selected["codex"]), 3)
        self.assertGreaterEqual(len(selected["claude-code"]), 3)


class TestLocalLlmControls(TestCase):
    def test_call_local_llm_json_short_circuits_when_disabled(self):
        with patch.dict(os.environ, {"REPORT_MONITOR_DISABLE_LLM": "1"}, clear=False):
            with patch.object(monitor_module, "_available_local_llm_providers", side_effect=AssertionError("should not inspect providers")):
                result = monitor_module._call_local_llm_json("system", {"items": []})

        self.assertEqual(result, {})


class TestRenderPageLabels(TestCase):
    def test_morning_slot_uses_daily_label_in_monitor_page(self):
        section = TopicSection(
            topic_key="codex",
            title="Codex",
            report_date_range="2026-04-09",
            model="gpt",
            source_summary_text="",
            error_summary_text="",
            quality_line="",
            curated_items=[make_item("web", 80, 1)],
            stats={},
            localized_summaries={},
        )

        with patch.object(monitor_module, "_build_archive_section", return_value=""), \
             patch.object(monitor_module, "enrich_merged_items", side_effect=lambda _section, items, _cache: items), \
             patch.object(monitor_module, "localize_topic_trends", return_value=[]):
            rendered = monitor_module.render_page(
                [section],
                "morning",
                "2026-04-09",
                "2026-04-09 10:00:00 +0800",
                "2026-04-09 17:47:14 +0800",
                "web,x",
            )

        self.assertIn("每日版 · 2026-04-09", rendered)
        self.assertNotIn("早间版", rendered)
