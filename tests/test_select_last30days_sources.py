import importlib.util
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


select_module = _load_module("select_last30days_sources", "select_last30days_sources.py")
choose_sources = select_module.choose_sources


class TestChooseSources(TestCase):
    @patch.object(select_module, "probe_scrapecreators", return_value=(True, "ScrapeCreators ok"))
    @patch.object(select_module, "probe_polymarket", return_value=(True, "Polymarket ok"))
    def test_prefers_healthy_sources_in_defined_order(self, _pm, _sc):
        diag = {
            "openai": True,
            "reddit_public": True,
            "x_source": "xai",
            "xai": True,
            "youtube": True,
            "hackernews": True,
            "web_search_backend": "openai-web",
            "polymarket": True,
            "tiktok": True,
            "instagram": True,
            "bluesky": False,
            "truthsocial": False,
            "xiaohongshu": False,
        }
        env_file = {"SCRAPECREATORS_API_KEY": "token"}

        selected, reasons = choose_sources(diag, env_file)

        self.assertEqual(selected, ["reddit", "x", "web", "youtube", "hn", "tiktok", "instagram", "polymarket"])
        self.assertIn("Using xAI-backed X search", reasons["x"])

    def test_records_skip_reason_for_missing_x_source(self):
        diag = {
            "openai": False,
            "reddit_public": False,
            "x_source": "none",
            "youtube": True,
            "hackernews": True,
            "web_search_backend": None,
            "polymarket": False,
            "bluesky": False,
            "truthsocial": False,
            "xiaohongshu": False,
        }

        selected, reasons = choose_sources(diag, {})

        self.assertEqual(selected, ["youtube", "hn"])
        self.assertEqual(reasons["x"], "No working X source detected")

    @patch.object(select_module, "probe_scrapecreators", return_value=(False, "ScrapeCreators credits exhausted"))
    @patch.object(select_module, "probe_polymarket", return_value=(None, "CERTIFICATE_VERIFY_FAILED"))
    def test_reddit_public_and_polymarket_ssl_fallback(self, _pm, _sc):
        diag = {
            "openai": True,
            "reddit_public": True,
            "x_source": "none",
            "youtube": False,
            "hackernews": True,
            "web_search_backend": "exa",
            "polymarket": True,
            "tiktok": True,
            "instagram": True,
            "bluesky": False,
            "truthsocial": False,
            "xiaohongshu": False,
        }

        selected, reasons = choose_sources(diag, {"SCRAPECREATORS_API_KEY": "token"})

        self.assertIn("reddit", selected)
        self.assertIn("fallback to Reddit public", reasons["reddit"])
        self.assertIn("polymarket", selected)

    @patch.object(select_module, "probe_scrapecreators", return_value=(False, "ScrapeCreators credits exhausted"))
    def test_apify_fallback_for_tiktok_instagram(self, _sc):
        diag = {
            "openai": True,
            "reddit_public": True,
            "x_source": "none",
            "youtube": False,
            "hackernews": False,
            "web_search_backend": None,
            "polymarket": False,
            "tiktok": True,
            "instagram": True,
            "bluesky": False,
            "truthsocial": False,
            "xiaohongshu": False,
        }

        selected, _reasons = choose_sources(
            diag,
            {
                "SCRAPECREATORS_API_KEY": "token",
                "APIFY_API_TOKEN": "apify-token",
            },
        )

        self.assertIn("tiktok", selected)
        self.assertIn("instagram", selected)
