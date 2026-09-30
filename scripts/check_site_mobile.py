#!/usr/bin/env python3
"""Check generated catalog and detail layouts with hostile long source text."""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

from build_pages_site import render_detail


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("site", type=Path)
    args = parser.parse_args()
    catalog = json.loads((args.site / "data/experiments.json").read_text())
    fixture = next(item for item in catalog["experiments"] if item.get("protocol"))
    protocol = "## Illustrative endpoints\n" + "denominator-" * 200 + "\n<script>throw Error('untrusted')</script>"
    fixture.update(question=None, method=None,
                   protocol={"record": "experiment/protocol.md", "text": protocol})
    catalog["experiments"] = [fixture]
    detail_html = render_detail(fixture).encode()

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

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Handler, directory=str(args.site)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                for width in (320, 360):
                    context = browser.new_context(viewport={"width": width, "height": 800},
                                                  is_mobile=True, has_touch=True)
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.route("**/data/experiments.json", lambda route: route.fulfill(json=catalog))
                    for path, selector in (("/", ".details > details"),
                                           ("/mobile-fixture/", ".detail-page details")):
                        page.goto(f"http://127.0.0.1:{server.server_port}{path}")
                        details = page.locator(selector).filter(has_text="Full authoritative protocol")
                        summary = details.locator("summary")
                        summary.click()
                        assert details.locator("pre").inner_text() == protocol
                        assert details.locator("pre script").count() == 0
                        assert summary.bounding_box()["height"] >= 48
                        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (width, path)
                        assert not errors, errors
                    context.close()
                print("Catalog and detail protocol fixtures pass at 320/360px with 48px touch controls")
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
