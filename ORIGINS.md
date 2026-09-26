# Origins and compatibility target

Experiment Compiler was created by Tyler J.H.G. (pH34r-pH) as a public extraction of the deterministic packaging idea first demonstrated in domain-scaling-lab. The new generic compiler is separate from the private scientific implementation and Fleet deployment control plane.

The initial eight input files are preserved verbatim from the previously published package at:

https://tyharbin.com/reproduce/packages/45ab246ccfd4ca636e1ad50e8edf068125a111b46b6bcb2b86ac1d811f523341.zip

The corresponding public publication receipt is:

https://tyharbin.com/reproduce/receipts/45ab246ccfd4ca636e1ad50e8edf068125a111b46b6bcb2b86ac1d811f523341.json

During repository bootstrap the site could not be fetched from the working environment. The original qualification transfer was retrieved from the authorized source instead, its outer ZIP verified against `5cf966e5bf6dfd08fab185a4620fca1299e4d2d827da39b270cac91d89039480`, and its inner package verified against the already-published `45ab246c...23341` digest and 15,296-byte size. Only the already-public inner member bytes are vendored, not private credentials, infrastructure configuration or additional research data.

Historical lineage retained in the example:

- Scientific source commit: `139eaf0ca66c50665cf8f24469aa413c366e58cc`.
- Original compiler commit: `fc8f25f4df556c7646354fc1747ad4add691c123`.
- Scientific receipt hash: `a6679a828af448e9009b4314c95a29fe51accdd03393aa5eb0f01c9588e44554`.
- Historical execution receipt hash: `3ffe3b2edbb5eec735233c6c3c720dfbd2cbc1483077304db422cdbd18f0a6f5`.

These are historical identities, not new observations made by this compiler. `reference-manifest.json` is the manifest from the published POC; it is a regression oracle, not a compiler input. The generated ZIP must reproduce it and the historical package checksum exactly.

The embedded documents retain their original terminology, dates, URLs and scope caveats, including references to non-public or mutable repository locations. They have not been rewritten to imply those resources are public or included. External specifications, datasets, libraries and named projects are not relicensed by this repository; only the code and material actually distributed here are covered by its license.
