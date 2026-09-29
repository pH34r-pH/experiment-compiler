import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_pages_site import render_detail


class SiteProjectionTest(unittest.TestCase):
    def test_catalog_schema_versions_exact_identity_and_optional_article_backlinks(self):
        schema = json.loads((ROOT / "site/data/experiments.schema.json").read_text())
        self.assertEqual(schema["properties"]["schemaVersion"]["const"], 2)
        experiment = schema["properties"]["experiments"]["items"]
        self.assertIn("detailUrl", experiment["required"])
        self.assertIn("backlinks", experiment["required"])
        self.assertIn("source", experiment["required"])
        self.assertIn("package", experiment["required"])

    def test_detail_page_escapes_source_text_and_keeps_digest_scoped_actions(self):
        digest = "a" * 64
        commit = "b" * 40
        page = render_detail({
            "id": "sample-experiment",
            "title": "A <careful> experiment",
            "hypothesis": "Signal & control",
            "question": "Which readout?",
            "method": "Frozen model",
            "profile": "compiled-experiment-v1",
            "source": {"repository": "owner/research", "commit": commit},
            "package": {"sha256": digest, "size": 1234},
            "backlinks": [{"title": "A reviewed article", "url": "https://tyharbin.com/articles/sample/",
                           "sourceCommit": commit}],
            "acceptance": {"passed": True},
        })
        self.assertIn("A &lt;careful&gt; experiment", page)
        self.assertNotIn("A <careful> experiment", page)
        self.assertIn(f"/packages/{digest}.zip", page)
        self.assertIn(commit, page)
        self.assertIn("https://tyharbin.com/articles/sample/", page)
        self.assertIn("Return to the compiled experiment catalog", page)
        self.assertIn('class="brand"', page)
        self.assertIn("TJHG <span>/ experiments</span>", page)
        self.assertIn('class="cross-site-link" href="https://tyharbin.com/research/"', page)
        self.assertIn('class="article-backlink"', page)
        self.assertIn("Read A reviewed article →", page)

    def test_unrelated_experiment_gets_no_inferred_article_link(self):
        page = render_detail({
            "id": "standalone-example",
            "title": "Standalone example",
            "source": {"repository": "owner/research", "commit": "b" * 40},
            "package": {"sha256": "a" * 64, "size": 12},
        })
        self.assertNotIn("Related research articles", page)
        self.assertNotIn("article-backlink", page)

    def test_compiler_style_uses_the_shared_publication_tokens_and_controls(self):
        css = (ROOT / "site/assets/site.css").read_text()
        for token in ("--bg: #e8e3d8", "--panel: #f2eee5", "--ink: #17191a",
                      "--muted: #62615c", "--line: #aaa497", "--accent: #765466"):
            self.assertIn(token, css)
        self.assertIn("height: 58px", css)
        self.assertIn("min-height: 44px", css)
        self.assertIn("min-height: 48px", css)
        self.assertIn("@media (prefers-reduced-motion: reduce)", css)
        self.assertIn("@media (forced-colors: active)", css)


if __name__ == "__main__":
    unittest.main()
