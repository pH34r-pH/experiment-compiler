# AdamW semantic equivalence audit for #164

Issue: #342. Purpose: determine whether the surviving "optimizer semantics" standards gap is scientifically material for the historical #164 baseline and future AdamW-vs-Muon comparison.

## Frozen #164 implementation

At evidence commit `82a96cbc5d3da5bd5dfe76e3b1b877be996e5df6`, the inherited training path constructs:

```python
torch.optim.AdamW(
    model.parameters(),
    lr=config.learning_rate,
    weight_decay=config.weight_decay,
)
```

with frozen defaults:
- learning rate = `1e-3`;
- weight decay = `1e-2`;
- all `model.parameters()` in one optimizer group;
- PyTorch AdamW defaults for unspecified optimizer arguments;
- global gradient clipping occurs through `torch.nn.utils.clip_grad_norm_(..., config.gradient_clip_norm)` **before** `optimizer.step()`;
- gradients are cleared with `zero_grad(set_to_none=True)` before backward.

Consequently the historical scientific treatment is not merely the string "AdamW". It is **PyTorch AdamW at the pinned environment/version plus the surrounding clipping/step ordering and full-parameter selection**.

## Cross-framework audit

### PyTorch

Current PyTorch documents AdamW with defaults:
- betas (0.9, 0.999);
- epsilon 1e-8;
- weight decay decoupled from momentum/variance;
- optional AMSGrad off by default;
- parameter groups are an explicit part of the API.

The documented update decays parameters by `lr * weight_decay` before the adaptive update.

### Optax/JAX

Current `optax.adamw` exposes:
- b1/b2;
- `eps` outside the square root;
- an additional `eps_root` inside the square root;
- optional accumulator dtype;
- optional parameter mask controlling where weight decay applies;
- optional Nesterov mode.

Optax explicitly documents that its weight decay is multiplied by the learning rate, consistent with PyTorch, and notes that this differs from the original Loshchilov/Hutter paper's base-learning-rate convention.

Thus "AdamW" alone is insufficient to identify all semantically relevant choices even between two mainstream implementations.

### Keras

Current Keras AdamW has different defaults and folds additional training behavior into the optimizer API:
- epsilon defaults to 1e-7 and is documented as "epsilon hat";
- weight decay defaults to 0.004;
- per-weight clipnorm, clipvalue, and global clipnorm are optimizer options;
- EMA, loss scaling, and gradient accumulation are optimizer options.

Even when the core AdamW equation is intended to match, the same bare name/default construction is therefore not an equivalent training treatment.

## Scientifically material distinctions

For conceptual reproduction and controlled optimizer comparison, the following must be explicit rather than inferred from the label `AdamW`:

1. **Core update semantics**
   - beta1/beta2;
   - bias correction;
   - epsilon value and placement;
   - decoupled weight-decay equation and how LR/schedule scales decay;
   - AMSGrad/Nesterov or other variants.

2. **Parameter selection**
   - which parameters receive adaptive updates;
   - which receive weight decay;
   - whether masks/groups exclude bias/norm/embedding parameters;
   - for Muon, which semantic matrix roles receive Muon versus fallback optimizer.

3. **Update pipeline ordering**
   - gradient accumulation;
   - unscaling/loss scaling;
   - clipping (global/per-parameter/value) and whether clipping occurs before optimizer state update;
   - optimizer step;
   - EMA or post-step transforms.

4. **Numerical/state semantics**
   - parameter, gradient, moment/optimizer-state dtypes;
   - fused/foreach/backend implementation where numerical equivalence matters;
   - initialization and checkpoint/resume state.

5. **Schedule clock**
   - learning-rate/decay schedule;
   - whether schedule is indexed by optimizer update, microbatch, token count, or another clock.

## #164 conclusion

For **technical reproduction**, no new semantic standard is required: the exact repository commit + pinned environment + Process Run Crate identifies the historical PyTorch implementation, and #164 already contains a deterministic reproduction receipt.

For **conceptual reproduction**, the optimizer semantics gap is real enough to matter. A second implementation cannot safely infer the frozen treatment from `optimizer = AdamW` because mainstream frameworks expose materially different defaults and extensions.

For the future Muon experiment, this becomes even more important because the treatment necessarily includes semantic parameter selection (Muon-eligible matrices versus AdamW/fallback parameters). The comparison must freeze an explicit optimizer/update contract before framework-specific lowering.

## Minimal requirement if no existing standard closes the gap

Do **not** define a universal optimizer language. A Croissant Tasks training profile would only need to carry/reference:

- stable algorithm identity/specification;
- explicit parameter-role selectors/groups;
- algorithm parameters;
- ordered pre-step/update/post-step transforms;
- schedule and schedule clock;
- numerical state requirements relevant to equivalence;
- implementation-specific TaskSolution that claims conformance.

The algorithm specification may be external (paper/specification/test suite) rather than re-encoded as JSON-LD. Cross-framework conformance should be demonstrated by executable equivalence tests where practical.

## Acceptance test for an AdamW conceptual-reproduction solution

A non-PyTorch TaskSolution should:
1. consume the same frozen model/data/objective/regime;
2. implement the explicit #164 AdamW/update contract rather than framework defaults;
3. pass a small deterministic optimizer conformance fixture against the reference update for selected tensors/gradients;
4. reproduce the bounded #164 scientific acceptance criteria without inspecting the original implementation during construction.

This is stronger than matching a class name and weaker/more practical than requiring bitwise identity across hardware/frameworks.
