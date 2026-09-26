# Canonical local validation for #342

GitHub-hosted Actions are not required for this gate. The repository is private and hosted Actions capacity is currently unavailable; validity is established by content-addressed local receipts that can later be independently replayed on Kestrel or another exact checkout.

## Canonical AdamW contract receipt

From an exact checkout of this branch:

```bash
git status --short
python research/reproducibility/issue_164/run_adamw_contract_receipt.py
```

The runner:
- performs no dependency installation or network operation;
- runs only `tests/test_issue_164_adamw_contract.py`;
- hashes the normative contract and test bytes;
- records HEAD SHA, branch, working-tree porcelain status and remote origin when git is available;
- records Python, PyTorch, platform and CUDA identity;
- records deterministic environment overrides;
- records command/stdout/stderr/exit status;
- writes `adamw_contract_receipt.json` with a canonical receipt hash.

### Acceptance

A canonical receipt for PR #345 requires:
- `result.passed == true`;
- `result.exit_code == 0`;
- `repository.head_sha` equals the commit being reviewed;
- `repository.status_porcelain` is empty **before** the receipt is generated;
- input hashes match the committed contract/test at that HEAD.

The receipt itself makes the working tree dirty after generation; that is expected.

A receipt produced from reconstructed/copied source outside an authenticated repository checkout is useful diagnostic evidence but is **noncanonical**.

## Independent replay

Kestrel/self-hosted execution should run the same command. A second receipt need not have the same environment hash; its contract/test hashes must match, and the conformance tests must pass. This is intentionally execution-backend independent.
