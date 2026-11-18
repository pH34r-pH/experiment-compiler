# Experiment Compiler architecture

> Status: current project architecture and north-star boundaries, 2026-09-29.

Experiment Compiler turns reviewed research inputs into immutable, inspectable experiment artifacts. The diagrams on this page make the project's boundaries explicit: what the compiler owns, what the runner owns, how immutable lifecycle artifacts relate, which public standards carry semantics, and how the public reproduction surface connects back to explanatory research articles.

The diagrams are maintained as Mermaid source under [`docs/diagrams/`](diagrams/).

## 1. Project map

The project is deliberately split into five stages: author/review, compile/inspect, explicit execution, immutable lifecycle transitions, and publication/reproduction.

```mermaid
flowchart TB
  subgraph A["1 · Author and review the research closure"]
    LAB["Source-owned research workspace<br/>protocols · data · code · evidence"]
    REVIEW["Disclosure / review decision<br/>choose a shareable closure"]
    RECIPE["Versioned experiment recipe<br/>profile · exact members · hashes · sizes"]
    LAB --> REVIEW --> RECIPE
  end

  subgraph B["2 · Compile and inspect without executing"]
    COMPILE["compile<br/>deterministic immutable ZIP"]
    PACKAGE["Compiled Experiment<br/>manifest · standards metadata<br/>dependency/resource closure"]
    VERIFY["verify<br/>byte + inventory integrity"]
    DESCRIBE["describe / catalog<br/>derived display projections"]
    COMPILE --> PACKAGE --> VERIFY
    PACKAGE --> DESCRIBE
  end

  subgraph C["3 · Execute only through the explicit runner boundary"]
    RUNNER["experiment-runner<br/>digest-pinned + explicit opt-in"]
    ATTEMPT["Immutable attempt/result package<br/>Process Run Crate · outputs · logs · receipt"]
    DECISION{"What happens next?"}
    RUNNER --> ATTEMPT --> DECISION
  end

  subgraph D["4 · Iterate or finalize without mutating history"]
    REVISE["revise<br/>new prospective plan"]
    FINALIZE["finalize<br/>bind source-authored interpretation + review"]
    FINAL["Immutable finalized artifact"]
    DECISION -->|"revise method / protocol"| REVISE
    REVISE --> RECIPE
    DECISION -->|"select attempt"| FINALIZE --> FINAL
  end

  subgraph E["5 · Publish, reproduce, and archive"]
    REVIEWPUB["Source-owner disclosure / publication review"]
    SITE["experiments.tyharbin.com<br/>live static reproduction projection"]
    ARCHIVE["compiled-experiments<br/>exact reviewed public bytes"]
    RELEASE["Human-reviewed GitHub Release"]
    ZENODO["Zenodo archival record + DOI"]
    ARTICLE["Portfolio / MyST article<br/>explanation · intuition · browser interaction"]
    FINAL --> REVIEWPUB
    REVIEWPUB --> SITE
    REVIEWPUB --> ARCHIVE --> RELEASE --> ZENODO
    ARTICLE -->|"exact experiment reference"| SITE
    SITE -->|"canonical article backlink"| ARTICLE
    SITE -.->|"archival citation when released"| ARCHIVE
  end

  RECIPE --> COMPILE
  PACKAGE --> RUNNER

  NOTE1["Compiler never infers scientific correctness."]
  NOTE2["Process success ≠ scientific acceptance ≠ publication."]
  PACKAGE -.-> NOTE1
  ATTEMPT -.-> NOTE2
```

Source: [`01-project-map.mmd`](diagrams/01-project-map.mmd)

## 2. Immutable lifecycle and artifact lineage

Plans, attempts, revisions, finalizations, and public releases are separate immutable artifacts. Historical packages are never silently edited in place.

```mermaid
flowchart TB
  P0["Prospective Plan P0<br/>protocol + inputs + dependency/resource closure"]
  A0["Attempt A0<br/>exact P0 digest-pinned<br/>CreateAction + receipt + outputs"]
  OUT{"Process outcome"}
  OK["succeeded"]
  FAIL["failed"]
  TIME["timed out"]
  CHOOSE{"Source-owner decision"}

  P0 --> A0 --> OUT
  OUT --> OK --> CHOOSE
  OUT --> FAIL --> CHOOSE
  OUT --> TIME --> CHOOSE

  subgraph ITERATE["Revision branch"]
    REV["revise<br/>new prospective Plan P1"]
    P1["Plan P1<br/>new immutable identity + digest"]
    A1["Attempt A1<br/>new immutable attempt"]
    REV --> P1 --> A1
  end

  subgraph CLOSE["Finalization branch"]
    FIN["finalize selected attempt<br/>attach scientific decision + publication review"]
    F0["Final reviewable artifact F0"]
    PUB["Source-owner reviewed public finalization"]
    CAT["Derived live catalog + stable detail route"]
    ARC["compiled-experiments<br/>exact-byte archival copy"]
    REL["Reviewed release → Zenodo DOI"]
    FIN --> F0 --> PUB --> CAT
    PUB --> ARC --> REL
  end

  CHOOSE -->|"method / protocol change"| REV
  A1 --> CHOOSE
  CHOOSE -->|"selected attempt ready for interpretation"| FIN

  I["Invariant: prior packages are never edited in place."]
  S["Separation: process success ≠ scientific acceptance ≠ publication."]
  P0 -.-> I
  F0 -.-> S
```

Source: [`02-lifecycle-lineage.mmd`](diagrams/02-lifecycle-lineage.mmd)

## 3. Package anatomy and standards

The Compiled Experiment ZIP is an immutable byte envelope. Experiment Compiler owns package construction, integrity, and lifecycle relationships; source-authored research content and established public standards carry the scientific meaning.

```mermaid
flowchart TB
  RECIPE["experiment.json recipe<br/>profile · exact source/path/hash/size"]

  subgraph ZIP["Compiled Experiment ZIP · immutable byte envelope"]
    subgraph ID["Identity + research-object structure"]
      MAN["experiment-package-manifest.json<br/>generated member inventory"]
      ROC["ro-crate-metadata.json<br/>RO-Crate graph + lifecycle links"]
    end

    subgraph SCI["Source-authored scientific content"]
      PROT["protocol.md<br/>question · method · controls · stop/accept rules"]
      SRC["implementation · config · shareable data"]
      CRO["Croissant metadata<br/>when dataset semantics apply"]
    end

    subgraph REP["Reproduction closure + execution handoff"]
      CLOS["dependency-closure.json<br/>embedded · immutable public · host/ABI · unavailable"]
      RES["resource evidence / estimates<br/>CPU · RAM · storage · time"]
      EXEC["workflow.cwl · job.yml · runner.json<br/>when executable"]
    end

    subgraph EVI["Evidence + provenance"]
      EVID["attempts · outputs · logs · receipts"]
      DEC["scientific decision · publication review"]
    end
  end

  RECIPE --> MAN
  RECIPE --> ROC
  RECIPE --> PROT
  RECIPE --> SRC
  RECIPE --> CRO
  RECIPE --> CLOS
  RECIPE --> RES
  RECIPE --> EXEC
  RECIPE --> EVID
  RECIPE --> DEC

  subgraph STD["Standards reused instead of a custom ontology"]
    RO["RO-Crate 1.3<br/>research-object structure"]
    PRC["Process Run Crate 0.6<br/>actual execution provenance"]
    SCH["Schema.org<br/>Action / CreativeWork / Review states"]
    PROV["PROV-O<br/>derivation + revision links"]
    CWL["CWL 1.2<br/>workflow + portable resource requests"]
    CR["MLCommons Croissant<br/>dataset metadata"]
  end

  ROC --> RO
  EVID --> PRC
  ROC --> SCH
  ROC --> PROV
  EXEC --> CWL
  CRO --> CR

  BOUND["Compiler owns the byte envelope, lifecycle relationships, and integrity checks.<br/>Scientific meaning remains source-authored."]
  ZIP -.-> BOUND
```

Source: [`03-package-anatomy.mmd`](diagrams/03-package-anatomy.mmd)

## 4. Trust boundaries and claim scope

Each command has a deliberately narrow evidence scope. Package integrity, process execution, scientific interpretation, publication, and independent reproduction are related but distinct claims.

```mermaid
flowchart TB
  SRC["Reviewed source bytes"]

  C["1 · compile"]
  CYES["Establishes<br/>built from declared members + selected profile"]
  CNO["Does not establish<br/>scientific validity · completeness · authorship"]

  V["2 · verify"]
  VYES["Establishes<br/>inventory / digest / path / JSON integrity<br/>+ optional whole-package pin"]
  VNO["Does not establish<br/>trusted bytes · safe code · reproduction"]

  A["3 · runner admission + bounded run"]
  AYES["Establishes<br/>admitted workflow attempt occurred<br/>with bounded evidence + provenance"]
  ANO["Does not establish<br/>scientific conclusion"]

  F["4 · finalize"]
  FYES["Establishes<br/>source-authored interpretation + review<br/>bound to one exact attempt"]
  FNO["Does not establish<br/>independent validation or publication authorization"]

  P["5 · reviewed promotion"]
  PYES["Establishes<br/>reviewed artifact was promoted publicly"]
  PNO["Still distinct from<br/>independent reproduction or universal scientific truth"]

  SRC --> C --> V --> A --> F --> P
  C --> CYES
  C --> CNO
  V --> VYES
  V --> VNO
  A --> AYES
  A --> ANO
  F --> FYES
  F --> FNO
  P --> PYES
  P --> PNO
```

Source: [`04-trust-boundaries.mmd`](diagrams/04-trust-boundaries.mmd)

## 5. Public research ↔ reproduction surface

The publication system has three distinct responsibilities. Portfolio/MyST owns explanation and bounded browser interaction. Experiment Compiler owns immutable reproduction identity, closure, provenance, verification/execution handoff, and the derived live experiment presentation. `pH34r-pH/compiled-experiments` owns the reviewed immutable archival copy and release/DOI lineage after source-owner publication approval.

```mermaid
flowchart LR
  subgraph P["tyharbin.com · research publication"]
    ARTICLE["MyST research article<br/>question · argument · evidence<br/>interactive figures · editable browser cells<br/>limitations"]
    REF["Minimal exact Compiled Experiment reference"]
    ARTICLE --> REF
  end

  subgraph E["experiments.tyharbin.com · reproduction surface"]
    PROJ["Versioned static public projection<br/>derived from authoritative artifacts"]
    DETAIL["Stable experiment detail route<br/>profile · SHA-256 · source commit<br/>protocol · lifecycle · evidence · resources"]
    ACTIONS["Inspect · verify · obtain · run guidance"]
    BACK["Canonical article backlink<br/>when declared"]
    PKG["Exact immutable package bytes"]
    PROJ --> DETAIL
    DETAIL --> ACTIONS
    DETAIL --> BACK
    DETAIL --> PKG
  end

  subgraph G["Public repository + GitHub Pages build"]
    REC["Reviewed recipes · packages · source closures"]
    BUILD["Static build<br/>catalog · detail pages · package publishing"]
    REC --> BUILD --> PROJ
    REC --> PKG
  end

  subgraph A["compiled-experiments · archival citation surface"]
    ARC["Exact reviewed finalized package bytes"]
    REL["Human-reviewed GitHub Release"]
    DOI["Zenodo archival record + DOI"]
    ARC --> REL --> DOI
  end

  REF -->|"resolve exact experiment"| PROJ
  BACK -.->|"read the explanation"| ARTICLE
  DETAIL -.->|"immutable archive relation when published"| ARC

  RULE["Boundary<br/>Portfolio owns explanation/browser interaction.<br/>Experiment Compiler owns live reproduction identity and presentation.<br/>compiled-experiments owns exact archival release lineage; Zenodo supplies DOI."]
  ARTICLE -.-> RULE
  DETAIL -.-> RULE
  ARC -.-> RULE
```

Source: [`05-public-surface.mmd`](diagrams/05-public-surface.mmd)

## Design invariants

- Compile and verify never execute packaged experiment code.
- Historical artifact bytes are immutable; revisions create new identities and provenance links.
- A process succeeding does not imply scientific acceptance.
- Finalization carries source-authored interpretation; it does not invent or independently validate a conclusion.
- Public promotion is a reviewed publication event, not a synonym for independent reproduction.
- `compiled-experiments` archival releases never rebuild or rerun a finalized package; they preserve exact reviewed public bytes plus release metadata.
- A GitHub Release/Zenodo DOI is archival/citation provenance, not a scientific-acceptance or independent-reproduction state.
- The public catalog is derived from authoritative recipes/artifacts rather than a second manually maintained registry.
- Portfolio articles own explanation and interactive presentation; Experiment Compiler owns exact reproduction identity and evidence.
- Where an established standard fits, prefer it over a repository-local ontology.
