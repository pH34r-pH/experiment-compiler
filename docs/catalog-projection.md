# Lifecycle catalog source convention

The public catalog is a projection of recipes and their RO-Crate records, not a second scientific registry. Its additive version 2 fields preserve source facts without judging scientific acceptance or independent reproduction.

Future lifecycle sources may use `## Hypothesis` in the experiment README and `## Question` and `## Method` in the protocol for concise display summaries. These exact headings are optional presentation conventions, not scientific requirements. Missing sections project as null. The `protocol` object provides the authoritative protocol member `record` and its full UTF-8 `text`, including when its structure differs. Text is never rewritten or inferred; display clients must escape it as untrusted source content. Frozen protocols are not edited to populate summaries.

`executionAttempts` projects every execution CreateAction as `id`, its exact Schema.org `actionStatus`, and `result` member paths. PotentialActionStatus describes a plan and is excluded. Active, failed and completed attempts remain distinct. Cancellation may be recorded as a failed action with explanatory retained receipt evidence; this projection does not invent a separate Schema.org status. Result references must resolve to described File entities in the recipe inventory; absent results remain an empty list and do not imply acceptance. Duplicate entities and result references are rejected.

Completed execution, a retained interpretation summary, publication review and byte integrity establish different facts. None implies a successful scientific comparison or independent reproduction. Missing references, mismatched denominators and failed scientific checks retain their source-owned meaning in packaged records. Consumers must not synthesize a green scientific verdict from action status, package presence or workflow success.

Pinned lifecycle catalog records verify the deterministic package and every source member before projection. Unpinned prospective descriptions remain available without requiring the full source closure to be built; their presentation is not an integrity verification receipt.
