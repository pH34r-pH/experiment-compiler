#!/usr/bin/env python3
"""Check the generated catalog and hostile long source text in a local browser."""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import re
import shutil
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

from build_pages_site import render_detail


def _make_handler(site: Path, detail_html: bytes) -> type[http.server.SimpleHTTPRequestHandler]:
    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/mobile-fixture/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(detail_html)
            else:
                super().do_GET()

        def log_message(self, *_args):
            pass

    return Handler

def _check_no_js(browser, base: str, experiments: list[dict], evidence: Path | None) -> None:
    context = browser.new_context(viewport={"width": 360, "height": 800},
                                  is_mobile=True, has_touch=True,
                                  java_script_enabled=False)
    page = context.new_page()
    page.goto(base + "/")
    table_name = re.compile("Each row is one compiled experiment record")
    assert page.get_by_role("table", name=table_name).count() == 1
    assert page.locator("tr.catalog-record").count() == len(experiments)
    assert page.locator("#catalog-controls").is_hidden()
    assert "The table above is the complete generated catalog." in page.locator("noscript").inner_text()
    assert page.locator(".catalog-links a[href^='/experiments/']").count() == len(experiments)
    assert page.locator(".catalog-links a.download-link").count() == len(experiments)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.locator(".table-scroll").evaluate("el => el.scrollWidth > el.clientWidth")
    if evidence:
        page.screenshot(path=str(evidence / "catalog-mobile-no-js.png"), full_page=True)
    context.close()

def _check_desktop_rows(page, base: str, experiments: list[dict]) -> None:
    table_name = re.compile("Each row is one compiled experiment record")
    assert page.get_by_role("table", name=table_name).count() == 1
    assert page.locator("#catalog-filter").is_visible()
    assert page.locator("#catalog-status").inner_text() == f"Showing all {len(experiments)} records."
    assert page.locator("tr.catalog-record").count() == len(experiments)
    assert page.locator(".table-scroll").evaluate("el => el.scrollWidth <= el.clientWidth")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.get_by_role("columnheader", name="Package size").get_attribute("scope") == "col"
    assert page.locator("tbody.catalog-group > tr.family-heading > th[scope='rowgroup']").count() == 2

    expected_detail_routes = {item["detailUrl"] for item in experiments}
    expected_package_routes = {f"/packages/{item['package']['sha256']}.zip" for item in experiments}
    visible_detail_routes = set()
    visible_package_routes = set()
    for experiment in experiments:
        row = page.locator(f'tr.catalog-record[data-record-id="{experiment["id"]}"]')
        assert row.count() == 1
        detail = row.locator(".catalog-links a[href^='/experiments/']")
        assert detail.get_attribute("href") == experiment["detailUrl"]
        visible_detail_routes.add(detail.get_attribute("href"))
        expected_package = f"/packages/{experiment['package']['sha256']}.zip"
        package = row.locator(".download-link")
        assert package.get_attribute("href") == expected_package
        visible_package_routes.add(package.get_attribute("href"))
        source = row.locator(".provenance a")
        assert source.get_attribute("href") == (
            f"https://github.com/{experiment['source']['repository']}/tree/{experiment['source']['commit']}")
        assert page.request.get(base + experiment["detailUrl"]).status == 200
        assert page.request.get(base + expected_package).status == 200
    assert visible_detail_routes == expected_detail_routes
    assert visible_package_routes == expected_package_routes
    for experiment in experiments:
        family = experiment.get("isPartOf")
        if family:
            fragment = family["@id"].split("#", 1)[1]
            assert page.locator(f'#{fragment}').count() == 1
            assert page.locator(f'a[href="#{fragment}"]').count() == 1

def _record_ids(group) -> list[str]:
    return [row.get_attribute("data-record-id") for row in group.locator("tr.catalog-record").all()]

def _check_desktop_interactions(page) -> None:
    family_id = "https://experiments.tyharbin.com/#family-stdlib-linear-regression"
    group = page.locator(f'tbody.catalog-group[data-family-id="{family_id}"]')
    expected_ids = ["linear-regression-frozen-lifecycle-v1", "linear-regression-plan-v1",
                    "stdlib-linear-regression-v1-compiled-experiment"]
    assert _record_ids(group) == expected_ids
    package_sort = page.get_by_role("button", name="Sort within each family by Package size")
    package_sort.focus()
    page.keyboard.press("Enter")
    assert page.get_by_role("columnheader", name="Package size").get_attribute("aria-sort") == "ascending"
    expected_by_size = ["linear-regression-plan-v1", "linear-regression-frozen-lifecycle-v1",
                        "stdlib-linear-regression-v1-compiled-experiment"]
    assert _record_ids(group) == expected_by_size
    page.keyboard.press("Enter")
    assert page.get_by_role("columnheader", name="Package size").get_attribute("aria-sort") == "descending"
    assert _record_ids(group) == list(reversed(expected_by_size))
    page.get_by_role("button", name="Sort within each family by Attempts").click()
    muon_id = "https://experiments.tyharbin.com/#family-muon-adamw-comparison"
    muon_group = page.locator(f'tbody.catalog-group[data-family-id="{muon_id}"]')
    assert _record_ids(muon_group) == [
        "muon-comparison-plan-v1", "muon-unit-hypersphere-depth3-multiseed-v1-final-87409154"]
    page.get_by_role("button", name="Sort within each family by Eval MSE").click()
    assert page.locator('thead th[data-sort-key="eval-mse"]').get_attribute("aria-sort") == "ascending"
    assert _record_ids(group) == ["stdlib-linear-regression-v1-compiled-experiment",
                                  "linear-regression-frozen-lifecycle-v1", "linear-regression-plan-v1"]

def _check_desktop_filter(page, experiments: list[dict]) -> None:
    filter_box = page.get_by_role("searchbox", name="Filter records")
    filter_box.focus()
    page.keyboard.type("Muon versus historical AdamW")
    assert page.locator("tr.catalog-record:visible").count() == 2
    assert page.locator("tbody.catalog-group:visible").count() == 1
    assert page.locator("#catalog-status").inner_text().startswith("Showing 2 of 5 records")
    filter_box.fill("muon-unit-hypersphere-depth3-multiseed-v1-final-87409154")
    assert page.locator("tr.catalog-record:visible").count() == 1
    filter_box.fill("")
    assert page.locator("tr.catalog-record:visible").count() == len(experiments)
    group = page.locator('tbody.catalog-group[data-family-id="https://experiments.tyharbin.com/#family-stdlib-linear-regression"]')
    page.get_by_role("button", name="Sort within each family by Experiment record").click()
    assert _record_ids(group) == ["linear-regression-frozen-lifecycle-v1", "linear-regression-plan-v1",
                                  "stdlib-linear-regression-v1-compiled-experiment"]
    _check_search_normalization(page, filter_box, group)


def _check_search_normalization(page, filter_box, group) -> None:
    displayed_name = "Straße Café ＡＢＣ K"
    row = group.locator("tr.catalog-record").first
    group.locator(".family-label").evaluate("(element, name) => element.textContent = name", displayed_name)
    row.evaluate("element => element.dataset.search += ' straße café abc k'")
    filter_box.fill(displayed_name)
    assert row.is_visible()
    filter_box.fill("Cafe\u0301 ＡＢＣ K")
    assert row.is_visible()
    filter_box.fill("")


def _check_mse_sort(page) -> None:
    family_id = "https://experiments.tyharbin.com/#family-stdlib-linear-regression"
    group = page.locator(f'tbody.catalog-group[data-family-id="{family_id}"]')
    denominator = 10 ** 1000
    entries = [
        {"id": "mse-large-above-one", "value": f"{denominator + 1}/{denominator}"},
        {"id": "mse-one-float", "value": "1"},
        {"id": "mse-large-below-one", "value": f"{denominator - 1}/{denominator}"},
        {"id": "mse-b-negative-rational", "value": "1/-2"},
        {"id": "mse-a-negative-float", "value": "-0.5"},
        {"id": "mse-negative-quarter", "value": "-0.25"},
        {"id": "mse-zero", "value": "0/7"},
        {"id": "mse-half-rational", "value": "1/2"},
        {"id": "mse-half-float", "value": "0.5"},
        {"id": "mse-two-float", "value": "2.5"},
        {"id": "mse-missing-b", "value": ""},
        {"id": "mse-missing-a", "value": ""},
    ]
    group.evaluate("""(tbody, values) => {
      const template = tbody.querySelector('tr.catalog-record');
      for (const {id, value} of values) {
        const row = template.cloneNode(true);
        row.removeAttribute('id');
        row.dataset.recordId = id;
        row.dataset.sortId = id;
        row.dataset.sortEvalMse = value;
        row.dataset.search = id;
        row.querySelector('.record-title').textContent = id;
        tbody.append(row);
      }
    }""", entries)
    page.get_by_role("button", name="Sort within each family by Eval MSE").click()
    assert _record_ids(group) == [
        "mse-a-negative-float", "mse-b-negative-rational", "mse-negative-quarter", "mse-zero",
        "stdlib-linear-regression-v1-compiled-experiment", "mse-half-float", "mse-half-rational",
        "mse-large-below-one", "mse-one-float", "mse-large-above-one", "mse-two-float",
        "linear-regression-frozen-lifecycle-v1", "linear-regression-plan-v1", "mse-missing-a", "mse-missing-b",
    ]
    page.get_by_role("button", name="Sort within each family by Eval MSE, ascending").click()
    assert _record_ids(group) == [
        "mse-two-float", "mse-large-above-one", "mse-one-float", "mse-large-below-one",
        "mse-half-float", "mse-half-rational", "stdlib-linear-regression-v1-compiled-experiment",
        "mse-zero", "mse-negative-quarter", "mse-a-negative-float", "mse-b-negative-rational",
        "linear-regression-frozen-lifecycle-v1", "linear-regression-plan-v1", "mse-missing-a", "mse-missing-b",
    ]


def _check_desktop(browser, base: str, experiments: list[dict], evidence: Path | None) -> None:
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base + "/")
    _check_desktop_rows(page, base, experiments)
    _check_desktop_interactions(page)
    _check_desktop_filter(page, experiments)
    assert not errors, errors
    if evidence:
        page.evaluate("document.activeElement.blur(); window.scrollTo(0, 0)")
        page.screenshot(path=str(evidence / "catalog-desktop.png"), full_page=True)
    _check_mse_sort(page)
    context.close()


def _check_mobile(browser, base: str, experiments: list[dict], protocol: str,
                  evidence: Path | None) -> None:
    for width in (320, 360, 390):
        context = browser.new_context(viewport={"width": width, "height": 820},
                                      is_mobile=True, has_touch=True)
        mobile = context.new_page()
        mobile_errors: list[str] = []
        mobile.on("pageerror", lambda error, errors=mobile_errors: errors.append(str(error)))
        mobile.goto(base + "/")
        scroll = mobile.locator(".table-scroll")
        assert mobile.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), width
        assert scroll.evaluate("el => el.scrollWidth > el.clientWidth"), width
        assert mobile.get_by_role("region", name="Scrollable compiled experiment catalog").count() == 1
        assert mobile.locator("#catalog-filter").evaluate("el => el.getBoundingClientRect().height >= 48")
        mobile.locator(".table-scroll").focus()
        mobile.evaluate("document.querySelector('.table-scroll').scrollLeft = document.querySelector('.table-scroll').scrollWidth")
        assert scroll.evaluate("el => el.scrollLeft > 0"), width
        _capture_mobile_evidence(mobile, scroll, width, evidence)
        _check_mobile_protocol(mobile, base, protocol, width)
        assert not mobile_errors, mobile_errors
        context.close()


def _capture_mobile_evidence(mobile, scroll, width: int, evidence: Path | None) -> None:
    if width == 320 and evidence:
        scroll.evaluate("el => el.scrollLeft = 0")
        mobile.evaluate("document.activeElement.blur(); window.scrollTo(0, 0)")
        mobile.screenshot(path=str(evidence / "catalog-mobile-320.png"), full_page=True)


def _check_mobile_protocol(mobile, base: str, protocol: str, width: int) -> None:
    mobile.goto(base + "/mobile-fixture/")
    details = mobile.locator(".detail-page details").filter(has_text="Full authoritative protocol")
    summary = details.locator("summary")
    summary.click()
    assert details.locator("pre").inner_text() == protocol
    assert details.locator("pre script").count() == 0
    assert summary.bounding_box()["height"] >= 48
    assert mobile.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), width


def _catalog_fixture(site: Path) -> tuple[list[dict], str, bytes]:
    experiments = json.loads((site / "data/experiments.json").read_text())["experiments"]
    fixture = next(item for item in experiments if item.get("protocol"))
    protocol = "## Illustrative endpoints\n" + "denominator-" * 200 + "\n<script>throw Error('untrusted')</script>"
    fixture.update(question=None, method=None,
                   protocol={"record": "experiment/protocol.md", "text": protocol})
    return experiments, protocol, render_detail(fixture).encode()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("site", type=Path)
    parser.add_argument("--evidence", type=Path, help="directory for local screenshots")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    site = args.site.resolve()
    experiments, protocol, detail_html = _catalog_fixture(site)
    if args.evidence:
        args.evidence.mkdir(parents=True, exist_ok=True)
    handler = _make_handler(site, detail_html)
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(handler, directory=str(site)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        _run_browser_checks(base, experiments, protocol, args.evidence)
    finally:
        server.shutdown()
        server.server_close()


def _run_browser_checks(base: str, experiments: list[dict], protocol: str,
                        evidence: Path | None) -> None:
    with sync_playwright() as playwright:
        browser_path = shutil.which("chromium") or shutil.which("chromium-browser")
        browser = playwright.chromium.launch(executable_path=browser_path)
        try:
            _check_no_js(browser, base, experiments, evidence)
            _check_desktop(browser, base, experiments, evidence)
            _check_mobile(browser, base, experiments, protocol, evidence)
            print("Catalog fallback, links, sorting, filtering, table semantics, and 320/360/390px layouts pass; protocol detail is safe at narrow widths.")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
