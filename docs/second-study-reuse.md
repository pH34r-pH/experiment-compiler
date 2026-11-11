# Second-study reuse handoff

The existing frozen linear-regression study is the selected
[#87](https://github.com/pH34r-pH/experiment-compiler/issues/87) reuse fixture.
Its original lifecycle package selects Docker. The prepared
[`linear-regression-podman-lifecycle-v1`](../examples/linear-regression-podman-lifecycle-v1/experiment.json)
selects the existing Podman adapter with a new immutable package identity and
exact parent provenance. This is an operational revision of the same study.

## Exact prepared closure

| Item | Identity |
| --- | --- |
| Recipe | `examples/linear-regression-podman-lifecycle-v1/experiment.json` |
| Prepared package SHA-256 | `279caf239c6e27dcf78093c29c7dd54a5df9c1ff8c906f02c14a42f1b737dcec` |
| Prepared package size | 24,327 bytes |
| Embedded parent plan SHA-256 | `ce2c5582c3d0ddf1b9d48041694a72360a7492436513914c85d985faa0744d20` |
| Parent plan size | 11,316 bytes |
| Scientific source | `pH34r-pH/experiment-compiler` at `a4bebe3fff748268f037e5fa622b51ad40322a91` |
| Backend | `cwltool-podman`, cwltool `3.2.20260720092025` |
| OCI image | `python:3.13.13-slim@sha256:7ba5f5888fbe0014ab9edb2278922995c2201fc3752c46b0be24763eb46fa9f3` |
| Current outcome | Draft plan; zero actual attempts; resource measurements unknown |

The scientific source identity describes the frozen study. The consuming source
must separately pin the repository revision containing this operational recipe
and the compiler revision used to build it. Neither pin is a worker qualification
or an execution request. The package's embedded
[operational handoff](../examples/linear-regression-podman-lifecycle-v1/source/operational-handoff.md)
describes the preserved members and admission/evidence requirements.

## Rebuild and inspect without executing

From a checkout of the exact reviewed operational-recipe revision:

```sh
python -m experiment_compiler compile \
  examples/linear-regression-podman-lifecycle-v1/experiment.json \
  --output /tmp/linear-regression-podman-lifecycle-v1.plan.zip
python -m experiment_compiler verify \
  /tmp/linear-regression-podman-lifecycle-v1.plan.zip \
  --recipe examples/linear-regression-podman-lifecycle-v1/experiment.json \
  --expected-sha256 279caf239c6e27dcf78093c29c7dd54a5df9c1ff8c906f02c14a42f1b737dcec
python -m experiment_compiler describe \
  examples/linear-regression-podman-lifecycle-v1/experiment.json
```

These commands check package bytes and project source metadata. They do not pull
the image, invoke cwltool, create an attempt, or qualify a worker. The recipe pins
the complete package, so changed bytes fail rather than silently becoming a new
accepted fixture.

## Source-owned execution handoff

The source owner can retain this ordinary recipe and its declared members through
the existing private plan/archive path. Preserve the scientific-source identity,
embedded parent and exact package digest; record the separate storage repository,
commit and path. A generic reviewed request can select the plan once the physical
worker, pinned image and recovery/retention boundary are qualified. Do not add an
experiment-name branch or route it into a worker-qualification bypass.

Keep execution pending until the owning controller supplies those observations
and a fresh, budgeted attempt identity. A source import, package build, public CI
success, merge or archive receipt cannot substitute for the launch gate. Duplicate
delivery of one request must not create a second attempt; an authorized retry
receives a new request/attempt identity and preserves the previous outcome.

After the authorized attempt, use the existing operations on the **retained new
result**: `verify --recipe`, `scripts/validate_current_ro_profiles.py`, `catalog`
and source-reviewed `finalize`. Preserve the exact `result.json`, frozen expected
result and reproduction receipt for the evaluator's byte comparison. The result
package, complete source closure, private attempt journal, bounded logs and
archive readback must remain available even if evaluation fails. A valid negative
outcome is retained; a missing or unresolved attempt remains unresolved.

The public example adds no queue, workflow, executor or evaluator registry. Its
admission uses the already implemented container adapter, so completing the native
Fleet controller integration in
[#119](https://github.com/pH34r-pH/experiment-compiler/issues/119) is not a prerequisite
for this container fixture. Conversely, this prepared container plan is not native
Windows/Linux qualification and cannot satisfy #119's physical acceptance.

## Acceptance evidence still required

The preparation regression rebuilds the parent and child, checks exact embedded
parent bytes and stable scientific identity, proves all frozen scientific members
are unchanged, and verifies that the new plan is Draft with no actual attempts.
It does not launch either plan.

#87 remains open until an actual same-boundary attempt retains truthful success or
failure, the source-owned comparison and interpretation bind the exact new outputs,
and the existing packaging/presentation path consumes the retained record. Public
release requires its own reviewed shareable closure. This prepared plan makes the
next run concrete while leaving physical qualification and execution unclaimed.
