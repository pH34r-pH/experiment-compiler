import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_pages_site import render_detail

from experiment_compiler.catalog import resolve_article_reference
from experiment_compiler.core import PackageError, compile_package


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

    def test_detail_page_exposes_exact_protocol_when_summaries_are_absent(self):
        page = render_detail({
            "id": "real-protocol-shape", "title": "Illustrative study", "question": None,
            "method": None, "source": {"repository": "owner/research", "commit": "b" * 40},
            "protocol": {"record": "experiment/protocol.md", "text": "## Endpoints\n<script>alert(1)</script>"},
        })
        self.assertIn("Full authoritative protocol", page)
        self.assertIn("experiment/protocol.md", page)
        self.assertIn("## Endpoints\n&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("Not declared.", page)
        self.assertIn("completed execution does not establish scientific acceptance", page)
        self.assertIn("Interpretation is source-authored evidence, not approval", page)
        self.assertIn("Independent reproduction requires its own observed run and comparison", page)

    def test_unrelated_experiment_gets_no_inferred_article_link(self):
        page = render_detail({
            "id": "standalone-example",
            "title": "Standalone example",
            "source": {"repository": "owner/research", "commit": "b" * 40},
            "package": {"sha256": "a" * 64, "size": 12},
        })
        self.assertNotIn("Related research articles", page)
        self.assertNotIn("article-backlink", page)

    def contract_fixture(self):
        return json.loads((ROOT / "tests/fixtures/article-reference-v1.json").read_text())

    def test_exact_reference_fixture_matches_real_package_without_qualification(self):
        import tempfile
        fixture = self.contract_fixture()
        record = resolve_article_reference(fixture["projection"], fixture["compiled_experiment"])
        with tempfile.TemporaryDirectory() as directory:
            report = compile_package(ROOT / "examples/linear-regression-v1/experiment.json",
                                     Path(directory) / "package.zip")
        self.assertEqual(record["package"]["sha256"], report["packageSha256"])
        self.assertEqual(record["package"]["size"], report["packageSizeBytes"])
        for field in ("qualification", "publication", "independentlyReproducible"):
            self.assertNotIn(field, record)
        self.assertEqual(record["backlinks"], [])

    def test_article_relation_round_trip_preserves_package_and_ignores_title_revision(self):
        fixture = self.contract_fixture()
        record = fixture["projection"]["experiments"][0]
        original_package = copy.deepcopy(record["package"])
        with self.assertRaises(PackageError):
            resolve_article_reference(fixture["projection"], fixture["compiled_experiment"],
                                      article=fixture["article"])
        record["backlinks"].append(fixture["article"])
        revised_article = {**fixture["article"], "title": "Revised explanatory prose"}
        self.assertIs(resolve_article_reference(fixture["projection"], fixture["compiled_experiment"],
                                               article=revised_article), record)
        self.assertEqual(record["package"], original_package)
        for field, value in (("url", "https://tyharbin.com/articles/different/"),
                             ("sourceCommit", "d" * 40)):
            with self.subTest(field=field), self.assertRaises(PackageError):
                resolve_article_reference(fixture["projection"], fixture["compiled_experiment"],
                                          article={**fixture["article"], field: value})

    def test_exact_reference_negative_identity_contract(self):
        fixture = self.contract_fixture()
        projection, reference = fixture["projection"], fixture["compiled_experiment"]
        record = projection["experiments"][0]
        expected = {"sha256": record["package"]["sha256"], "profile": record["profile"],
                    "source": record["source"]}
        self.assertIs(resolve_article_reference(projection, {**reference, "expected": expected}), record)
        for ref in ("latest", "sample-latest-v1", "*", "unknown", "https://example.com/x"):
            with self.subTest(ref=ref), self.assertRaises(PackageError):
                resolve_article_reference(projection, {"ref": ref})
        for field, value in (("sha256", "e" * 64), ("profile", "compiled-experiment-lifecycle-v1"),
                             ("source", {**record["source"], "commit": "f" * 40}),
                             ("source", {"commit": record["source"]["commit"]})):
            with self.subTest(field=field, value=value), self.assertRaises(PackageError):
                resolve_article_reference(projection, {**reference, "expected": {field: value}})
        duplicate = copy.deepcopy(projection)
        duplicate["experiments"].append(copy.deepcopy(record))
        with self.assertRaises(PackageError):
            resolve_article_reference(duplicate, reference)
        for detail in ("/experiments/different/", "javascript:alert(1)"):
            malformed = copy.deepcopy(projection)
            malformed["experiments"][0]["detailUrl"] = detail
            with self.assertRaises(PackageError):
                resolve_article_reference(malformed, reference)

    def test_reference_schema_and_runtime_reject_adversarial_types(self):
        try:
            import jsonschema
        except ImportError:
            jsonschema = None
        fixture = self.contract_fixture()
        schema = json.loads((ROOT / "site/data/article-reference-v1.schema.json").read_text())
        invalid = [
            {"ref": 123},
            {**fixture["compiled_experiment"], "expected": {"sha256": int("1" * 64)}},
            {**fixture["compiled_experiment"], "expected": {"source": {
                "repository": "owner/repo", "commit": int("1" * 40)}}},
            {**fixture["compiled_experiment"], "expected": {"source": {
                "repository": "https://evil.test/repo", "commit": "a" * 40}}},
        ]
        for reference in invalid:
            with self.subTest(reference=reference):
                if jsonschema is not None:
                    with self.assertRaises(jsonschema.ValidationError):
                        jsonschema.validate(reference, schema)
                with self.assertRaises(PackageError):
                    resolve_article_reference(fixture["projection"], reference)
        for version in (2.0, True, "2", None):
            with self.subTest(version=version), self.assertRaises(PackageError):
                resolve_article_reference({**fixture["projection"], "schemaVersion": version},
                                          fixture["compiled_experiment"])

    def test_unsafe_backlinks_fail_resolution_and_rendering(self):
        fixture = self.contract_fixture()
        for url in ("javascript:alert(1)", "//tyharbin.com/articles/x/", "http://tyharbin.com/articles/x/",
                    "https://user@tyharbin.com/articles/x/", "https://tyharbin.com/articles/x/?q=1",
                    "https://tyharbin.com/articles/x/#fragment", "https://evil.test/articles/x/",
                    "https://tyharbin.com/artic\tles/x/", "https://tyharbin.com/articles/x/\n",
                    "\nhttps://tyharbin.com/articles/x/"):
            projection = copy.deepcopy(fixture["projection"])
            record = projection["experiments"][0]
            record["backlinks"] = [{**fixture["article"], "url": url}]
            with self.subTest(url=url):
                with self.assertRaises(PackageError):
                    resolve_article_reference(projection, fixture["compiled_experiment"])
                with self.assertRaises(PackageError):
                    render_detail(record)

    def test_falsey_malformed_backlinks_fail_resolution_and_rendering(self):
        fixture = self.contract_fixture()
        for backlinks in ({}, "", 0, False, None):
            record = copy.deepcopy(fixture["projection"]["experiments"][0])
            record["backlinks"] = backlinks
            projection = {**fixture["projection"], "experiments": [record]}
            with self.subTest(backlinks=backlinks):
                with self.assertRaises(PackageError):
                    resolve_article_reference(projection, fixture["compiled_experiment"])
                with self.assertRaises(PackageError):
                    render_detail(record)

    def test_projection_authority_must_match_public_contract(self):
        fixture = self.contract_fixture()
        for project in (None, {}, {"name": "Other compiler", "repository": "https://evil.test"},
                        {**fixture["projection"]["project"], "extra": True}):
            with self.subTest(project=project), self.assertRaises(PackageError):
                resolve_article_reference({**fixture["projection"], "project": project},
                                          fixture["compiled_experiment"])
        missing = copy.deepcopy(fixture["projection"])
        del missing["project"]
        with self.assertRaises(PackageError):
            resolve_article_reference(missing, fixture["compiled_experiment"])

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
        self.assertIn(".details > div, .details > details { min-width: 0;", css)
        self.assertIn(".details pre { min-width: 0; max-width: 100%;", css)
        self.assertIn(".detail-page summary, .details summary { min-height: 48px;", css)


if __name__ == "__main__":
    unittest.main()
