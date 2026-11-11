# Repository-agent package profiles

Issue #150 adds offline projections of the merged Long-Haul repository contracts. The exact source is `pH34r-pH/long-haul@458bc2f1ec669392963b7fe29e4c252e2e70776c`; the five byte-preserved schemas, their SHA-256 pins, and the public-projection helper identity ship under [`experiment_compiler/profiles/`](../experiment_compiler/profiles/). Schema validation uses the optional `repository` extra (`jsonschema==4.25.1`). Ordinary existing packages retain their standard-library-only compiler/verifier path and historical bytes.

The existing manifest's `evidence.repositoryAgent` field declares:

- `schemaVersion: 1` and the exact Long-Haul `source` repository/commit;
- `audience`: `worker`, `private-evaluator`, or `public-evidence`;
- `records`: unique `kind`, package `path`, and compact-JSON `fingerprint` entries;
- `references`: exact upstream semantic-trajectory or governed-corpus references whose `qualification` is `upstream-pending`.

Record kinds are `repository-task`, `repository-context`, `repository-candidate`, `repository-evaluator-public`, `repository-evaluator-sealed`, and `repository-evidence`. These compose existing Compiled Experiment envelopes; they do not create another scheduler, task authority, corpus store, or scientific acceptance service. No packaged record is executed while compiling or verifying.

Use complete normalized Long-Haul JSON exports, including default/null fields. Member SHA-256 binds the exact bytes; the separate projection fingerprint follows Long-Haul's compact sorted JSON. Task/source/environment/context, candidate/contract, evaluator, and check references must match across included records. The public evaluator projection follows the pinned `RepositoryEvaluationProfileV1.public_projection()` helper; its public checks cannot contain a sealed check. Compiler checks the schema, identity, and disclosure projection. Long-Haul's typed validators and independent evaluator remain authoritative for execution validity and acceptance.

Repository profile packages admit only their explicitly typed JSON records and the package manifest. Unclassified payloads, raw logs, hidden tests, alternative schemas, and unlisted members are rejected. Legacy `EvidencePacket` extras are forbidden, and public diagnostic fields admit only the pinned evaluator's fixed outcome labels and predicate references. Digest fields cannot carry raw output. Records declaring private classification are rejected from public packages; these checks do not authenticate the classification. Actual public eligibility still requires reviewed resource-policy admission outside the package's assertions.

## Private-check disclosure gate

Long-Haul #245 identified a disclosure problem in the original #243 profile: bare hashes of low-entropy private inputs, expected outputs, or an entire sealed profile can support dictionary guesses. A schema-valid digest is therefore insufficient public-disclosure evidence.

Worker/public packages fail closed on sealed evaluator references, sealed requirements, and transitive candidate/evidence commitments until an upstream guarded projection is qualified. Compiler does not invent a local nonce or cryptographic schema. The sole retained public conformance exception is exact packet `sha256:87f1a81ab8207ce271752e79cd93e8f1c18bd282a035e2ab56c498fd2d3aec66`, already public in the pinned [#243 synthetic qualification receipt](https://github.com/pH34r-pH/long-haul/blob/458bc2f1ec669392963b7fe29e4c252e2e70776c/programs/issue-251/evidence/issue-243-gvisor-ac003.json). It does not qualify private task disclosure. A standalone candidate is public-admitted only when it matches that exact public packet.

The `private-evaluator` audience can retain full evaluator profiles and unsuccessful, invalid, timed-out, or interrupted observations. The publication verifier rejects that audience. A worker task without sealed evaluation may carry the ordinary public subset; this does not certify a private-check workflow. Caller authentication, reviewed source admission, resource rights, and publication authority remain separate upstream gates.

Trajectory/corpus references retain exact source and content identities but do not validate raw trajectories, establish corpus rights, or grant training eligibility. Those require the qualified upstream #244/#248 contracts and later profile pin upgrades. Package verification reports `acceptance: not-evaluated` and `trainingEligibility: not-granted`.

## Reusable lifecycle components

The five composite actions under [`.github/actions/`](../.github/actions/) reuse compile, verify, execute, finalize, and publication verification. Each takes one `parameters` JSON object using the corresponding existing CLI argument names. Inputs enter an environment variable and an argument vector; they are never interpolated into shell source. The pinned compiler runs in isolated Python so a caller checkout cannot shadow its modules. Execution still requires explicit boolean `allow-workflow-execution` and all existing runner/worker admission. Its started/terminal receipts, cleanup journals, failure states, and exact attempt identity are unchanged.

For example, from another repository, pin the action to a reviewed Compiler commit and pass `{"recipe":"path/to/experiment.json","output":"candidate.zip"}` to `compile`. The caller supplies its reviewed local source closure; the action's pinned source supplies compiler code. `verify` accepts `package`, optional `recipe`, `expected-sha256`, and `receipt`. `finalize` uses the existing parent package, attempt, decision, and review fields. `publication` verifies signed provenance and returns no deployment authority.

[`lifecycle.yml`](../.github/workflows/lifecycle.yml) remains the sole operator entrypoint. Its package checks exercise compile/verify components, and the extracted synthetic pipeline exercises execute/finalize components. No new dispatch route is introduced.

## Clean rebuild and protected publication

[`clean_rebuild.py`](../experiment_compiler/clean_rebuild.py) archives an independently selected exact Git commit into a fresh directory, rejects source links/special files at that builder boundary, and rebuilds through an isolated interpreter with a clean home/environment. Git is resolved only from the operating system's default path. A temporary bare object view reads the source object database without importing its configuration, hooks, archive commands, filters, credentials, replacement refs, or lazy-fetch/network authority. Committed `.gitattributes` archive behavior, including `export-ignore`, remains effective; missing/export-ignored compiler modules cause rejection rather than installed-code fallback. The loaded compiler module must originate in the selected archive. It performs no dependency installation or packaged-member execution. Receipts retain builder commit, Git/archive/compiler fingerprints, recipe/package identities, Python/zlib identities, and the explicit `scientificReproduction: not-run` state. The source-archive constraint is builder policy, not an additional scientific artifact-format requirement.

The existing manually dispatched, `main`-only, `github-pages` environment job rebuilds every exact catalog package before signing. It uses pinned `actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6` and the maintained Ubuntu `/usr/bin/gh` verifier with repository, source commit/ref, signer workflow/digest, and hosted-runner checks. Verification forces `github.com`, uses a fresh empty home/configuration, disables prompts, and passes only the existing job's `GH_TOKEN` alongside the closed worker environment. Caller host/config/enterprise-token/debug overrides do not enter verification. The verifier executable's SHA-256 and native verification output are retained. An unsupported CLI or any missing/mismatched signature fails before deployment. Parsing worker-supplied attestation JSON is never signature verification.

Only that existing publication job receives `attestations: write`/OIDC authority. Signature verification establishes builder provenance, not scientific correctness, privacy rights, or human publication approval. Failed clean rebuild/signature receipts and runner attempts remain retained; failure never creates promotion authority. The protected publication route has not been invoked by this implementation.

The staged operator procedure is:

1. Review the exact `main` candidate, source closure, scientific interpretation, disclosure, and rights; confirm the existing Pages environment review requirements.
2. Run the existing `lifecycle.yml` workflow on that exact `main` state, retaining its normal environment approval step.
3. Inspect each clean rebuild and signature receipt under the `publication-verification-<source>-<run>` artifact; confirm source/package identities and all checks before allowing the existing deployment step.
4. If any gate fails, retain its failure evidence and correct the candidate in a new reviewed revision. The failed verification grants no deployment/publication authority.

No deployment, account publication, private evaluator qualification, or physical-device qualification is implied by the offline tests.
