# `site/` map

This directory is the hand-authored static presentation surface. It is not the experiment registry.

- `assets/` contains presentation JavaScript and CSS.
- `index.html` is the site shell.
- `data/*.schema.json` defines the schemas for derived catalog and article-reference data.
- `data/experiments.json` is not checked in; `scripts/build_pages_site.py` derives it from authoritative recipes under [`examples/`](../examples/).

Route presentation changes here. Route identity, protocol, lifecycle, and evidence changes to [`experiment_compiler/catalog.py`](../experiment_compiler/catalog.py), the owning example, and its tests. Keep public pages explicit that package integrity and process success do not establish scientific acceptance.

The current visual contract is [`docs/site-visual-contract.md`](../docs/site-visual-contract.md). It aligns the Compiler with the Portfolio 2071 blue-white/blue-black interface language while keeping the sites' runtimes, preferences, data, and publication paths independent. Do not add Portfolio research visualizations or executable-reader behavior here.

Validate a site change with:

```sh
python scripts/build_pages_site.py --output /tmp/experiment-compiler-site
```

The existing mobile check is `python scripts/check_site_mobile.py /tmp/experiment-compiler-site` when Playwright is available. Do not add a second workflow for this check.
