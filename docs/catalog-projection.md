# Lifecycle catalog source convention

The public catalog is a projection of recipes and their RO-Crate records, not a second scientific registry. Its additive version 2 fields preserve source facts without judging scientific acceptance or independent reproduction.

Future lifecycle sources may use `## Hypothesis` in the experiment README and `## Question` and `## Method` in the protocol for concise display summaries. These exact headings are optional presentation conventions, not scientific requirements. Missing sections project as null. The `protocol` object provides the authoritative protocol member `record` and its full UTF-8 `text`, including when its structure differs. Text is never rewritten or inferred; display clients must escape it as untrusted source content. Frozen protocols are not edited to populate summaries.

`executionAttempts` projects every execution CreateAction as `id`, its exact Schema.org `actionStatus`, and `result` member paths. PotentialActionStatus describes a plan and is excluded. Active, failed and completed attempts remain distinct. Cancellation may be recorded as a failed action with explanatory retained receipt evidence; this projection does not invent a separate Schema.org status. Result references must resolve to described File entities in the recipe inventory; absent results remain an empty list and do not imply acceptance. Duplicate entities and result references are rejected.

Completed execution, a retained interpretation summary, publication review and byte integrity establish different facts. None implies a successful scientific comparison or independent reproduction. Missing references, mismatched denominators and failed scientific checks retain their source-owned meaning in packaged records. Consumers must not synthesize a green scientific verdict from action status, package presence or workflow success.

Pinned lifecycle catalog records verify the deterministic package and every source member before projection. Unpinned prospective descriptions remain available without requiring the full source closure to be built; their presentation is not an integrity verification receipt.


## Exact article reference contract v1

An article stores only `compiled_experiment: {ref: <immutable experiment ID>}`.
The reference schema is `/data/article-reference-v1.schema.json`; it consumes
`/data/experiments.json` schemaVersion 2. IDs are exact ASCII recipe identities,
not URLs, patterns, aliases, or `latest` selectors. The matching canonical detail
route is `https://experiments.tyharbin.com/experiments/{id}/`. Require exactly one
record, reject duplicate IDs across the projection before lookup, and reject a
route whose ID differs. Unknown IDs fail publication rather than selecting a
similar or newest record.

Optional `expected` assertions contain `sha256`, `profile`, and/or `source`.
Source assertions require both repository and full commit. These assertions
check identity; display metadata still comes from the projection. Missing or
mismatched asserted fields fail. The checked-in
`tests/fixtures/article-reference-v1.json` contains a real deterministic package
identity and a synthetic article assertion for cross-repository contract tests.
Its envelope `contractVersion: 1` versions the fixture, not article frontmatter.
`resolve_article_reference` demonstrates the executable checks without parsing
MyST or executing experiment members.

Backlinks project optional source-owned recipe `relatedArticles` publication
metadata; they are navigation relations, not scientific evidence or a new
ontology. The enclosing record binds each relation to that experiment ID.
`sourceCommit` identifies the article's source revision, not the experiment's
scientific source commit. Canonical URLs use the existing exact
`https://tyharbin.com/articles/{slug}/` convention without credentials, query,
or fragment. An article build claiming reciprocal linkage must explicitly
require its canonical URL and article source commit in the selected record's
backlinks. A different URL or revision, missing backlink, or unsafe URL fails
that strict check. Titles may change without changing the relation; multiple
article revisions may reference the same package, but reciprocal provenance
must match each declared revision. Historical records with an empty backlink
list remain valid projections; they cannot claim verified reciprocal linkage.

Adding reviewed recipe publication metadata changes the derived presentation,
not package member bytes. It must retain the package digest and source identity.
No relation is inferred from titles, package hashes, article slugs, or successful
execution. The current projection declares no public qualification or publication
verdict. Those fields remain absent/unknown: package availability, `acceptance`,
completed CreateActions, and CI success do not establish independent reproduction.
Clients may show the exact inspect detail route, obtain the digest-addressed ZIP,
and offer local byte verification; they must not fabricate qualification actions.

Cache the static catalog as a mutable derived publication, with normal HTTP
revalidation. Pin the immutable experiment ID and optionally its digest/source;
never pin a catalog position or assume the catalog URL is immutable. Package
URLs `/packages/{sha256}.zip` address exact bytes. A changed digest for a pinned
reference fails its expected assertion and requires explicit review, not a
silent update. Incompatible reference or projection changes require a new
versioned schema and shared fixture update. No live service, private access, or
cross-repository write credentials are required for these tests.
