# Resource requirements and runtime closure

The compiler keeps resource requests, resource observations, and closure evidence separate. It does not derive execution limits from an observed peak or treat an absent value as zero.

## Workflow fields and units

Use [CWL v1.2](https://www.commonwl.org/v1.2/CommandLineTool.html) for executable workflow descriptions:

| Need | CWL field | Unit / meaning |
| --- | --- | --- |
| CPU allocation | `ResourceRequirement.coresMin` / `coresMax` | CPU cores; can be fractional |
| RAM request | `ResourceRequirement.ramMin` / `ramMax` | MiB |
| Temporary storage | `ResourceRequirement.tmpdirMin` / `tmpdirMax` | MiB |
| Output storage | `ResourceRequirement.outdirMin` / `outdirMax` | MiB |
| Command execution ceiling | `ToolTimeLimit.timelimit` | Seconds; excludes staging and image-pull time |

Specify both `min` and `max` when the runner must enforce a range. CWL defines defaults when fields are omitted, so a pipeline must distinguish *unknown* from an intentional CWL default and defer admission when a required quantity is unknown. `bytes_to_mib_minimum` and `seconds_to_cwl_limit` perform conservative ceiling conversions; neither accepts unknown/zero as a usable budget.

CWL core does not define a portable GPU or VRAM request. `cwltool` has a CUDA extension, but an extension is not a portable CWL v1.2 requirement. A package that requires accelerator capability must name a supported runner/profile explicitly; otherwise admission is unsupported and must stop.

## Estimates and measurements

Put executable requests in each CWL `CommandLineTool` or workflow step. Keep measured and estimated values in the source-owned experiment/provenance records and link the evidence. For each operation—build/verify, data preparation, state extraction, training, and analysis/reproduction—record the operation, CPU/thread assumptions, RAM, scratch/input/output bytes, wall time, accelerator/VRAM if applicable, parallelism, arm/seed count, range, basis, environment/configuration, and evidence location. Use CWL limits only for actual enforceable ceilings; an observed peak is not a request, an estimate is not a measurement, and tuning cost is a separate operation.

The small standard-library example records measured peak RSS across its exact CI environment and labels its 32 MiB planning headroom as an estimate. That measurement does not estimate a model-training job. For unmeasured work, leave quantities unknown and make execution admission defer; do not copy a neighboring experiment's numbers.

## Runtime bytes and package admission

Classify each required runtime item as one of:

- embedded bytes in the immutable Compiled Experiment;
- immutable public bytes available by exact version/revision, digest, size, license/access terms, and retrieval method;
- an explicit host/ABI prerequisite;
- unavailable, which blocks execution-ready and offline/self-contained claims.

A URL, Git LFS pointer, image tag, or checkpoint digest identifies bytes but does not supply them. The compiler rejects an exact Git LFS pointer file as a package member. Immutable externally retrievable closure and physically embedded offline closure are different qualifications. Bibliographic/specification references are not runtime dependencies. Keep the bounded ZIP limits until the complete selected payload and environment closure have been inventoried; do not relax limits from the tiny example's measurements.

## Private workspace and publication boundary

The private lab remains the source of truth for private protocols, inputs, and runs. It can invoke an exact pinned public compiler revision locally or in its own isolated runner lane; the public compiler does not check out the private repository or receive its credentials. Promotion creates a new public package from a reviewed, shareable closure through the public repository's normal pull-request and catalog path. Private source bytes and incomplete result payloads stay in the lab. Compilation and verification remain offline and non-executing.
