# #164 AdamW optimizer/update contract v1

Status: normative **for conceptual reproduction of the frozen #164 training treatment**. This is not a proposed universal AdamW standard.

## Identity

- Contract ID: `dsl.issue164.adamw-update/v1`
- Historical implementation: PyTorch `torch.optim.AdamW` as resolved by the frozen #164 environment.
- Scope: trained #164 conditions only; the depth-1 #163 anchor is replayed rather than retrained.

## Parameter selection

A single optimizer parameter group contains **every trainable parameter returned by `model.parameters()`**. There are no bias, LayerNorm, embedding, head, matrix-shape, or other weight-decay exclusions.

## AdamW parameters

The historical constructor supplies only:

```python
torch.optim.AdamW(
    model.parameters(),
    lr=1e-3,
    weight_decay=1e-2,
)
```

Therefore conceptual implementations MUST implement the historical PyTorch defaults in addition to the explicit arguments:

- learning rate: `1e-3`
- beta1: `0.9`
- beta2: `0.999`
- epsilon: `1e-8`
- weight decay: `1e-2`
- AMSGrad: false
- maximize: false

The contract is the resulting update semantics, not permission to substitute another framework's defaults.

## Per-update ordering

For each optimizer update:

1. clear prior gradients with semantics equivalent to `zero_grad(set_to_none=True)`;
2. execute the frozen forward/objective;
3. backpropagate;
4. compute global L2 norm across all parameter gradients and clip with max norm `1.0`, using the semantics of PyTorch `clip_grad_norm_`;
5. execute one AdamW step;
6. no EMA/post-step averaging is part of the treatment.

No gradient accumulation across training batches is part of this contract.

## Schedule clock and budget

- learning rate is constant for the qualifying run;
- the optimizer/schedule clock advances once per optimizer update;
- qualifying endpoint is update 128;
- intermediate checkpoints/evaluation follow the frozen inherited #164/#162 schedule and are not optimizer steps.

## Numerical semantics

Exact technical reproduction is defined by the historical implementation/environment and existing #164 reproduction receipt.

Conceptual reproduction does **not** require bitwise cross-framework identity. A candidate implementation must:
1. satisfy deterministic optimizer/update conformance fixtures for representative parameter/gradient states within declared tolerance;
2. satisfy the full experiment's preregistered scientific criteria independently.

## Exclusions

This contract does not define:
- a generic AdamW ontology;
- Muon;
- optimizer behavior for sparse gradients;
- alternate parameter groups;
- AMSGrad/Nesterov variants;
- gradient accumulation, EMA, loss scaling, or mixed-precision substitutions.

Any such change is a different treatment unless separately qualified.
