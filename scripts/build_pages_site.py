#!/usr/bin/env python3
"""Build the static Experiment Compiler site from authoritative artifacts."""
from __future__ import annotations

import argparse
import html
import json
import math
import shutil
import sys
from decimal import Decimal, localcontext
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
    index = (ROOT / "site/index.html").read_text(encoding="utf-8")
    marker = "<!-- GENERATED_CATALOG -->"
    if index.count(marker) != 1:
        raise ValueError("site/index.html must contain one generated catalog marker")
    index = index.replace(marker, render_catalog(catalog["experiments"]))
    (destination / "index.html").write_text(index, encoding="utf-8")
    data_dir = destination / "data"
    (data_dir / "experiments.json").write_bytes(canonical(data))
    (destination / ".nojekyll").write_text("")
    print(json.dumps({"experimentCount": len(catalog["experiments"]), "site": str(destination)}, sort_keys=True))
    return 0


def _family_groups(experiments: list[dict]) -> list[tuple[dict | None, list[dict]]]:
    """Group only by the exact source-owned isPartOf @id; keep undeclared rows apart."""
    declared: dict[str, tuple[dict, list[dict]]] = {}
    standalone: list[tuple[dict | None, list[dict]]] = []
    for experiment in experiments:
        family = experiment.get("isPartOf")
        if family is None:
            standalone.append((None, [experiment]))
            continue
        key = family["@id"]
        if key not in declared:
            declared[key] = (family, [])
        elif declared[key][0]["name"] != family["name"]:
            raise ValueError(f"Conflicting names for family {key}")
        declared[key][1].append(experiment)
    groups = sorted(declared.values(), key=lambda item: (item[0]["name"].casefold(), item[0]["@id"]))
    for family, records in groups:
        records.sort(key=lambda record: record["id"])
    standalone.sort(key=lambda item: item[1][0]["id"])
    return [*groups, *standalone]


def _evaluation_mse(experiment: dict) -> tuple[str | None, str]:
    value = (experiment.get("result") or {}).get("metrics", {}).get("evalMse")
    if isinstance(value, dict):
        numerator, denominator = value.get("numerator"), value.get("denominator")
        if (type(numerator) is int and type(denominator) is int and denominator != 0):
            exact = f"{numerator} / {denominator}"
            try:
                with localcontext() as context:
                    context.prec = 8
                    approximation = Decimal(numerator) / Decimal(denominator)
                display = f"{approximation:.4g} ({exact})" if approximation.is_finite() else exact
            except (ArithmeticError, ValueError):
                display = exact
            return f"{numerator}/{denominator}", display
    if type(value) is int:
        return f"{value}/1", str(value)
    if isinstance(value, float) and math.isfinite(value):
        return format(value, ".17g"), f"{value:.4g}"
    return None, "Not reported"


def _catalog_search_text(experiment: dict) -> str:
    family = experiment.get("isPartOf") or {}
    source = experiment.get("source") or {}
    package = experiment.get("package") or {}
    lifecycle = experiment.get("lifecycle") or {}
    attempts = experiment.get("executionAttempts") or []
    result_text = json.dumps(experiment.get("result") or {}, sort_keys=True)
    interpretation_text = " ".join(
        item.get("summary", "") for item in experiment.get("scientificInterpretation") or []
    )
    return " ".join(str(value) for value in (
        experiment.get("id", ""), experiment.get("title", ""), family.get("@id", ""),
        family.get("name", ""), experiment.get("profile", ""),
        source.get("repository", ""), source.get("commit", ""),
        package.get("sha256", ""), lifecycle.get("creativeWorkStatus", ""),
        " ".join(item.get("actionStatus", "") for item in attempts),
        result_text, interpretation_text,
    )).casefold()


def _catalog_title_cell(experiment: dict) -> str:
    identifier = html.escape(experiment["id"])
    title = html.escape(experiment["title"])
    return f'<th scope="row"><span class="record-title">{title}</span><code>{identifier}</code></th>'


def _catalog_source_link(experiment: dict) -> str:
    source = experiment.get("source") or {}
    source_repository = source.get("repository", "Not reported")
    source_commit = source.get("commit", "")
    source_text = html.escape(f"{source_repository}@{source_commit}")
    source_url = f"https://github.com/{quote(source_repository, safe='/')}/tree/{quote(source_commit)}" if source_commit else ""
    return (f'<a href="{html.escape(source_url, quote=True)}">{source_text}</a>'
            if source_url else source_text)


def _catalog_package_values(experiment: dict) -> tuple[str, str, str]:
    package = experiment.get("package") or {}
    package_hash = package.get("sha256")
    package_size = package.get("size") if type(package.get("size")) is int else None
    package_size_value = html.escape(f"{package_size:,} bytes") if package_size is not None else "Not compiled"
    package_hash_value = f"<code>{html.escape(package_hash)}</code>" if package_hash else "Not compiled"
    size_sort = "" if package_size is None else str(package_size)
    return package_size_value, package_hash_value, size_sort


def _catalog_result_html(experiment: dict) -> tuple[str, str | None, str]:
    result = experiment.get("result") or {}
    eval_value, eval_display = _evaluation_mse(experiment)
    if result:
        acceptance = result.get("acceptancePassed")
        acceptance_text = ("passed" if acceptance is True else "not passed" if acceptance is False
                           else "not reported")
        result_html = ("<span>Reference result</span><br>Eval MSE " + html.escape(eval_display) +
                       "<br><span>Recorded acceptance: " + acceptance_text + "</span>")
        return result_html, eval_value, eval_display

    interpretations = experiment.get("scientificInterpretation") or []
    summaries = [item.get("summary") for item in interpretations if isinstance(item.get("summary"), str)]
    if summaries:
        result_html = "".join(
            "<details class=\"row-interpretation\"><summary>Source interpretation on record</summary>"
            f"<p>{html.escape(summary)}</p></details>" for summary in summaries
        )
        return result_html, eval_value, eval_display
    return "<span>Not reported</span>", eval_value, eval_display


def _catalog_status_values(experiment: dict) -> tuple[str, str, str]:
    lifecycle = experiment.get("lifecycle")
    attempts = experiment.get("executionAttempts") or []
    if not lifecycle:
        return "No lifecycle record", "", "Not applicable"

    status_text = html.escape(lifecycle.get("creativeWorkStatus") or "Not declared")
    action_states = ", ".join(
        html.escape(item.get("actionStatus", "Not declared").rsplit("/", 1)[-1])
        for item in attempts
    )
    if action_states:
        status_text += "<br><span>Action status: " + action_states + "</span>"
    attempt_count = lifecycle.get("attemptCount")
    attempt_display = html.escape(str(attempt_count)) if type(attempt_count) is int else "Not reported"
    attempts_sort = "" if attempt_count is None else str(attempt_count)
    return status_text, attempts_sort, attempt_display


def _catalog_detail_links(experiment: dict) -> tuple[str, str]:
    detail_url = experiment.get("detailUrl", "")
    detail_link = (f'<a href="{html.escape(detail_url, quote=True)}">Details</a>'
                   if detail_url else "Details unavailable")
    package_hash = (experiment.get("package") or {}).get("sha256")
    download_link = (f'<a class="download-link" href="/packages/{quote(package_hash)}.zip" download>Download ZIP</a>'
                     if package_hash else "Download unavailable")
    return detail_link, download_link


def _catalog_row(experiment: dict) -> str:
    identifier = experiment["id"]
    record_id = html.escape("record-" + identifier, quote=True)
    escaped_id = html.escape(identifier, quote=True)
    search = html.escape(_catalog_search_text(experiment), quote=True)
    package_size_value, package_hash_value, size_sort = _catalog_package_values(experiment)
    result_html, eval_value, eval_display = _catalog_result_html(experiment)
    status_text, attempts_sort, attempt_display = _catalog_status_values(experiment)
    detail_link, download_link = _catalog_detail_links(experiment)
    source_link = _catalog_source_link(experiment)
    title_cell = _catalog_title_cell(experiment)
    eval_sort = "" if eval_value is None else eval_value
    return (
        f'<tr class="catalog-record" id="{record_id}" '
        f'data-record-id="{escaped_id}" data-search="{search}" '
        f'data-sort-id="{escaped_id}" '
        f'data-sort-package-bytes="{size_sort}" data-sort-attempts="{attempts_sort}" '
        f'data-sort-eval-mse="{eval_sort}">'
        f'{title_cell}'
        f'<td>{result_html}</td><td>{status_text}</td>'
        f'<td data-value="{size_sort}">{package_size_value}</td>'
        f'<td data-value="{attempts_sort}">{attempt_display}</td>'
        f'<td data-value="{eval_sort}">{html.escape(eval_display)}</td>'
        f'<td class="provenance">{source_link}</td><td class="package-id">{package_hash_value}</td>'
        f'<td class="catalog-links">{detail_link}<br>{download_link}</td></tr>'
    )


def render_catalog(experiments: list[dict]) -> str:
    """Render a no-JavaScript catalog; site.js enhances this table in place."""
    column_count = 9
    parts = [
        '<table id="catalog-table" class="catalog-table">',
        '<caption>Each row is one compiled experiment record. A declared family is navigation metadata, not a provenance or scientific verdict.</caption>',
        '<thead><tr>',
    ]
    for label, key in (("Experiment record", "id"), ("Result / interpretation", None),
                       ("Status", None), ("Package size", "package-bytes"),
                       ("Attempts", "attempts"), ("Eval MSE", "eval-mse"),
                       ("Provenance", None), ("Package SHA-256", None), ("Links", None)):
        sortable = f' data-sort-key="{key}"' if key else ""
        parts.append(f'<th scope="col"{sortable}>{label}</th>')
    parts.append('</tr></thead>')
    for family, records in _family_groups(experiments):
        if family is not None:
            anchor = family["@id"].split("#", 1)[1]
            header = (f'<tr class="family-heading"><th scope="rowgroup" colspan="{column_count}" '
                      f'id="{html.escape(anchor, quote=True)}"><span class="family-label">'
                      f'{html.escape(family["name"])}</span><span class="family-id">'
                      f'{html.escape(anchor)}</span><a class="family-permalink" '
                      f'href="#{html.escape(anchor, quote=True)}" aria-label="Link to '
                      f'{html.escape(family["name"], quote=True)} family">#</a></th></tr>')
            group_attrs = f'data-family-id="{html.escape(family["@id"], quote=True)}"'
        else:
            identifier = records[0]["id"]
            header = (f'<tr class="family-heading"><th scope="rowgroup" colspan="{column_count}" '
                      f'id="record-family-{html.escape(identifier, quote=True)}">Family not declared'
                      '<span class="family-id">This record is shown separately</span></th></tr>')
            group_attrs = f'data-record-group="{html.escape(identifier, quote=True)}"'
        parts.append(f'<tbody class="catalog-group" {group_attrs}>')
        parts.append(header)
        parts.extend(_catalog_row(record) for record in records)
        parts.append('</tbody>')
    parts.append('<tfoot hidden><tr><td colspan="9">No experiments match this filter.</td></tr></tfoot>')
    parts.append('</table>')
    return "\n".join(parts)


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
<meta name="theme-color" content="#f4f9fd">
<title>{title} — Experiment Compiler</title><link rel="preload" href="/assets/fonts/long-measure/NimbusSansNarrow-Regular.woff2" as="font" type="font/woff2" crossorigin><link rel="stylesheet" href="/assets/site.css"><script src="/assets/instrument.js" defer></script></head>
<body><a class="skip-link" href="#main-content">Skip to content</a><header class="topbar"><a class="brand" href="/">TJHG <span>/ experiments</span></a><nav aria-label="Primary"><a class="cross-site-link" href="https://tyharbin.com/research/">Research ↗</a><a href="/#experiments">Catalog</a><a href="https://github.com/pH34r-pH/experiment-compiler">GitHub ↗</a></nav><details class="theme-panel"><summary>Theme <span data-theme-label>Auto</span></summary><div class="theme-options" role="group" aria-label="Theme"><button type="button" data-theme-choice="auto" aria-pressed="false">Auto</button><button type="button" data-theme-choice="light" aria-pressed="false">Light</button><button type="button" data-theme-choice="dark" aria-pressed="false">Dark</button></div></details></header>
<main id="main-content" class="detail-page" tabindex="-1"><p class="eyebrow">COMPILED EXPERIMENT · {identifier}</p><h1>{title}</h1><p class="lede">{summary}</p>
<section class="identity"><h2>Package identity</h2><dl><dt>Profile</dt><dd>{profile}</dd><dt>Package</dt><dd class="digest">{package_identity}</dd><dt>Source</dt><dd><a href="{source_url}" target="_blank" rel="noreferrer">{provenance}</a></dd></dl><div class="actions">{package_action}</div></section>
<section><h2>Research question</h2><p>{question}</p><h2>Method</h2><p>{method}</p></section>
{protocol_html}
<section><p>Package integrity checks establish byte consistency. Execution status records an attempt; completed execution does not establish scientific acceptance. Interpretation is source-authored evidence, not approval. Independent reproduction requires its own observed run and comparison.</p><details><summary>Lifecycle, evidence, and declared environment</summary><pre>{records}</pre></details></section>
{backlink_html}<p class="return-link"><a href="/#experiments">← Return to the compiled experiment catalog</a></p></main>
<footer><span>Apache-2.0 · Tyler J.H.G.</span><a href="https://github.com/pH34r-pH/experiment-compiler">Source on GitHub ↗</a></footer></body></html>\n'''


if __name__ == "__main__":
    raise SystemExit(main())
