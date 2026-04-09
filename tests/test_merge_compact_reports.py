import importlib.util
import sys
from pathlib import Path
from unittest import TestCase

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
merge_module = _load_module("merge_compact_reports", "merge_compact_reports.py")

merge_date_range = merge_module.merge_date_range
merge_items = merge_module.merge_items
merge_reports = merge_module.merge_reports
Item = public_module.Item
ParsedCompactReport = public_module.ParsedCompactReport


class TestMergeCompactReports(TestCase):
    def test_merge_items_deduplicates_and_keeps_strongest(self):
        first = Item(
            source="web",
            identifier="Release note",
            score=62,
            byline="Vendor update",
            date="2026-04-08",
            summary="A short summary",
            url="https://example.com/a",
            highlights=["A", "B"],
        )
        second = Item(
            source="web",
            identifier="Release note",
            score=79,
            byline="Vendor update",
            date="2026-04-08",
            summary="A better summary",
            url="https://example.com/a",
            highlights=["B", "C"],
        )

        merged = merge_items([first, second])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].score, 79)
        self.assertIn("C", merged[0].highlights)

    def test_merge_reports_expands_date_range(self):
        report_a = ParsedCompactReport(
            topic="Codex",
            date_range="2026-04-07 to 2026-04-08",
            mode="both",
            model="gpt-x",
            items_by_source={"web": []},
        )
        report_b = ParsedCompactReport(
            topic="Codex",
            date_range="2026-04-08 to 2026-04-09",
            mode="both",
            model="gpt-x",
            items_by_source={"web": []},
        )

        merged = merge_reports("Codex", [report_a, report_b])

        self.assertEqual(merge_date_range([report_a, report_b]), "2026-04-07 to 2026-04-09")
        self.assertEqual(merged.topic, "Codex")
        self.assertIn("focused queries", merged.quality_line)
