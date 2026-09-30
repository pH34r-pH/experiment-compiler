#!/usr/bin/env python3
"""Build the static Experiment Compiler site from authoritative artifacts."""
from __future__ import annotations

import argparse
import html
import json
import shutil
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from experiment_compiler.catalog import describe_catalog, discover_recipes
from experiment_compiler.core import (
    _validate_related_articles,
    canonical,
    compile_package,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "_site")
    args = parser.parse_args()

    destination = args.output
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    shutil.copytree(ROOT / "site/assets", destination / "assets")
    shutil.copy2(ROOT / "site/index.html", destination / "index.html")
    (destination / "data").mkdir()
    shutil.copy2(ROOT / "site/data/experiments.schema.json", destination / "data/experiments.schema.json")

    shutil.copy2(ROOT / "site/data/article-reference-v1.schema.json",
                 destination / "data/article-reference-v1.schema.json")

    recipes_root = ROOT / "examples"
    catalog = describe_catalog(recipes_root)
    package_dir = destination / "packages"
    package_dir.mkdir(parents=True)
    details_dir = destination / "experiments"
    details_dir.mkdir()
    for recipe_path, descriptor in zip(discover_recipes(recipes_root), catalog["experiments"], strict=True):
        report = compile_package(recipe_path, package_dir / f"{descriptor['id']}.zip")
        (package_dir / f"{descriptor['id']}.zip").rename(
            package_dir / f"{report['packageSha256']}.zip")
        descriptor["package"] = {"sha256": report["packageSha256"], "size": report["packageSizeBytes"]}
        descriptor["detailUrl"] = f"/experiments/{quote(descriptor['id'])}/"
        descriptor["backlinks"] = descriptor.get("backlinks", [])
        detail = details_dir / descriptor["id"]
        detail.mkdir()
        (detail / "index.html").write_text(render_detail(descriptor), encoding="utf-8")

    data = {
        "schemaVersion": 2,
        "project": {
            "name": "Experiment Compiler",
            "repository": "https://github.com/pH34r-pH/experiment-compiler",
        },
        "experiments": catalog["experiments"],
    }
    data_dir = destination / "data"
    (data_dir / "experiments.json").write_bytes(canonical(data))
    (destination / ".nojekyll").write_text("")
    print(json.dumps({"experimentCount": len(catalog["experiments"]), "site": str(destination)}, sort_keys=True))
    return 0


def render_protocol(protocol: object) -> str:
    """Show exact authoritative source text without interpreting its headings."""
    if not isinstance(protocol, dict) or not isinstance(protocol.get("text"), str):
        return ""
    return ("<section><details><summary>Full authoritative protocol</summary>"
            f"<p>Package member: {html.escape(protocol['record'])}</p>"
            f"<pre>{html.escape(protocol['text'])}</pre></details></section>")


def render_detail(experiment: dict) -> str:
    """Render a static package detail route from Compiler-derived metadata."""
    title = html.escape(experiment["title"])
    identifier = html.escape(experiment["id"])
    source = experiment.get("source") or {}
    source_repository = source.get("repository", "")
    source_commit = source.get("commit", "")
    source_url = f"https://github.com/{quote(source_repository, safe='/')}/tree/{quote(source_commit)}"
    package = experiment.get("package") or {}
    package_hash = package.get("sha256", "")
    package_url = f"/packages/{quote(package_hash)}.zip" if package_hash else ""
    package_action = (f'<a class="primary" href="{package_url}" download>Download this exact package</a>'
                      if package_url else '<span class="not-available">No compiled ZIP is published.</span>')
    summary = html.escape(experiment.get("hypothesis") or experiment.get("question") or "")
    question = html.escape(experiment.get("question") or "Not declared.")
    method = html.escape(experiment.get("method") or "Not declared.")
    protocol_html = render_protocol(experiment.get("protocol"))
    profile = html.escape(experiment.get("profile", ""))
    package_identity = (f"SHA-256 {html.escape(package_hash)} · {package.get('size', 0):,} bytes"
                        if package_hash else "Package identity not declared")
    provenance = html.escape(f"{source_repository}@{source_commit}")
    records = html.escape(json.dumps({key: experiment.get(key) for key in
        ("lifecycle", "executionAttempts", "acceptance", "result", "scientificInterpretation", "environment", "resources", "contents")
        if experiment.get(key) is not None}, indent=2, sort_keys=True))
    backlinks = experiment.get("backlinks", [])
    _validate_related_articles(backlinks)
    backlink_html = ("<section><h2>Related research articles</h2><ul>" + "".join(
        f'<li><a class="article-backlink" href="{html.escape(item["url"], quote=True)}">Read {html.escape(item["title"])} →</a></li>'
        for item in backlinks
    ) + "</ul></section>") if backlinks else ""
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="Exact package identity, provenance, and lifecycle projection for {title}.">
<title>{title} — Experiment Compiler</title><link rel="stylesheet" href="/assets/site.css"></head>
<body><header class="topbar"><a class="brand" href="/">TJHG <span>/ experiments</span></a><nav aria-label="Primary"><a class="cross-site-link" href="https://tyharbin.com/research/">Research ↗</a><a href="/#experiments">Catalog</a><a href="https://github.com/pH34r-pH/experiment-compiler">GitHub ↗</a></nav><button class="theme-toggle" type="button" data-theme-toggle aria-pressed="false">Dark</button></header>
<main class="detail-page"><p class="eyebrow">COMPILED EXPERIMENT · {identifier}</p><h1>{title}</h1><p class="lede">{summary}</p>
<section class="identity"><h2>Package identity</h2><dl><dt>Profile</dt><dd>{profile}</dd><dt>Package</dt><dd class="digest">{package_identity}</dd><dt>Source</dt><dd><a href="{source_url}" target="_blank" rel="noreferrer">{provenance}</a></dd></dl><div class="actions">{package_action}</div></section>
<section><h2>Research question</h2><p>{question}</p><h2>Method</h2><p>{method}</p></section>
{protocol_html}
<section><p>Package integrity checks establish byte consistency. Execution status records an attempt; completed execution does not establish scientific acceptance. Interpretation is source-authored evidence, not approval. Independent reproduction requires its own observed run and comparison.</p><details><summary>Lifecycle, evidence, and declared environment</summary><pre>{records}</pre></details></section>
{backlink_html}<p class="return-link"><a href="/#experiments">← Return to the compiled experiment catalog</a></p></main>
<footer><span>Apache-2.0 · Tyler J.H.G.</span><a href="https://github.com/pH34r-pH/experiment-compiler">Source on GitHub ↗</a></footer><script src="/assets/instrument.js"></script></body></html>\n'''


if __name__ == "__main__":
    raise SystemExit(main())
