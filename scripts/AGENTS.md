# `scripts/` map

Scripts are narrow helpers around authoritative recipes and existing validation contracts. Read the repository [`AGENTS.md`](../AGENTS.md) and [`docs/architecture.md`](../docs/architecture.md) first.

| Script | Relationship |
| --- | --- |
| `build_pages_site.py` | Reads `examples/` through `catalog.py` and writes a derived static site using `site/` assets/schemas. |
| `validate_current_ro_profiles.py` | Fail-closed local check for the current RO-Crate/Process Run Crate MUST subset; it does not claim to be an official validator. |
| `create_iteration_fixture.py` | Creates a new synthetic lifecycle fixture in a caller-provided temporary directory; it does not mutate a source fixture. |
| `validate_mutation_report.py` | Checks a mutation report against the pinned JSON schema. |
| `check_site_mobile.py` | Exercises generated detail pages at narrow viewports with a local browser. |
| `docs_hygiene.py` / `test_docs_hygiene.py` | Checks changed living-document names, argument-safe Git rename destinations, and incidental artifacts; the workflow sends only changed living Markdown to pinned style/link tools. |

Keep scripts deterministic, bounded, and explicit about whether output is derived or evidence. Do not make them fetch private inputs, execute packaged experiments implicitly, or overwrite authoritative historical artifacts.

## Focused commands

```sh
python scripts/build_pages_site.py --output /tmp/experiment-compiler-site
python scripts/validate_current_ro_profiles.py "$RUNNER_TEMP/runner-output/linear-regression-frozen-lifecycle-v1/result.source"
python scripts/test_docs_hygiene.py
python -m unittest discover -s tests -v
```
