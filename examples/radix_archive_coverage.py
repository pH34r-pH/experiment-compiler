"""Read coverage from the exact public radix archive without executing it."""
from __future__ import annotations

import argparse
import io
import json
import math
import zipfile
from pathlib import Path

from experiment_compiler.core import PackageError, bounded_read, json_value, safe_path, sha256, verify_bytes

PACKAGE_SHA256 = "25612d81a480dd5f8148a0dc247db58cb2fd719fe619e75ef0cd4945b3c1364b"
PACKAGE_SIZE = 4_129_679
PROTOCOL = "experiment/preregistration.json"
SUMMARY = "evidence/reference/summary.json"
TRACE_INDEX = "experiment/traces/manifest.json"
GRID_AXES = ("lane_widths", "bank_counts", "write_ports_per_bank", "bank_maps", "lane_orders",
             "architectures", "cost_presets")


def _primary_pairs(protocol: dict, summary: dict, configurations: int) -> list[dict]:
    frozen = [pair["id"] for pair in protocol["frozen_pairs"]]
    observed = {row["pair_id"]: row for row in summary["pair_decisions"]}
    if (
        not frozen or len(set(frozen)) != len(frozen)
        or len(observed) != len(summary["pair_decisions"]) or set(frozen) != observed.keys()
    ):
        raise PackageError("Archive measurements must match the frozen primary pair roster")
    primary = []
    for identifier in frozen:
        row = observed[identifier]
        if row["configuration_count"] != configurations or type(row["pair_validates"]) is not bool:
            raise PackageError("Archive pair count or disposition is inconsistent")
        primary.append({"id": identifier, "role": "primary", "status": "observed",
                        "validated": row["pair_validates"]})
    return primary


def _capture(protocol: dict, summary: dict, trace_count: int) -> dict:
    held_out = protocol["held_out_capture"]
    captures = summary["capture_status"]
    if len(captures) != 1 or captures[0]["status"] != "captured":
        raise PackageError("Archive must retain one held-out capture")
    capture = captures[0]
    fields = {"model": "model", "revision": "revision", "tokenizer_model": "tokenizer",
              "tokenizer_revision": "tokenizer_revision", "prompts": "prompts"}
    if any(capture[current] != held_out[frozen] for current, frozen in fields.items()):
        raise PackageError("Archive capture identities differ from the frozen protocol")
    modules = held_out["modules"]
    if (
        trace_count != len(modules["layers"]) * len(modules["suffixes"])
        or capture["trace_count"] != trace_count or summary["held_out_trace_count"] != trace_count
        or len(set(capture["modules"])) != trace_count
    ):
        raise PackageError("Archive trace count differs from held-out modules or capture")
    return {"traceShards": trace_count, "model": capture["model"], "modelRevision": capture["revision"],
            "tokenizer": capture["tokenizer_model"], "tokenizerRevision": capture["tokenizer_revision"],
            "promptCount": len(capture["prompts"]), "modules": capture["modules"]}


def source_coverage(protocol: dict, summary: dict, trace_count: int) -> dict:
    """Project the source's frozen scope and dispositions from verified members."""
    capture = _capture(protocol, summary, trace_count)
    configurations = math.prod(len(protocol["bank_cost_grid"][axis]) for axis in GRID_AXES)
    primary = _primary_pairs(protocol, summary, configurations)
    calibration_id = protocol["non_counting_calibration_pair"]["id"]
    if calibration_id in {pair["id"] for pair in primary}:
        raise PackageError("The calibration pair cannot count as a primary obligation")
    validated = sum(pair["validated"] for pair in primary)
    required = summary["required_pair_count"]
    if (
        type(summary["validated_pair_count"]) is not int or summary["validated_pair_count"] != validated
        or type(required) is not int or not 0 < required <= len(primary)
    ):
        raise PackageError("Archive decision denominator or observed count is inconsistent")
    return {
        "schema": "radix-archive-coverage/v1",
        "obligations": [*primary, {"id": calibration_id, "role": "diagnostic", "status": "unreported"}],
        "sourceDecision": {"decision": summary["decision"], "validated": validated,
                           "frozenPairs": len(primary), "required": required},
        "scope": "engineering-proxies",
        "screening": {"recoverability": "checksum-only",
                      "aggregateCsvSha256": protocol["source_sweep"]["aggregate_csv_sha256"],
                      "productCsvSha256": protocol["source_sweep"]["product_csv_sha256"]},
        "fullModelCapturePrerequisite": "external-model-and-tokenizer",
        "heldOutCapture": capture,
        "sensitivityGrid": {"settingsPerPair": configurations, "role": "configuration-grid"},
        "scientificReproduction": "not-run",
    }


def _trace_count(package: zipfile.ZipFile) -> int:
    manifest = json_value(package.read(TRACE_INDEX))
    names = set()
    for row in manifest["entries"]:
        name = safe_path("experiment/traces/" + safe_path(row["file"]))
        if name in names or sha256(package.read(name)) != row["sha256"]:
            raise PackageError("Archive trace manifest has a duplicate path or digest mismatch")
        names.add(name)
    return len(names)


def report_archive(path: Path) -> dict:
    raw = bounded_read(path, PACKAGE_SIZE)
    verified = verify_bytes(raw, expected_sha256=PACKAGE_SHA256)
    with zipfile.ZipFile(io.BytesIO(raw)) as package:
        protocol, summary, manifest = (package.read(name) for name in (PROTOCOL, SUMMARY, TRACE_INDEX))
        report = source_coverage(json_value(protocol), json_value(summary), _trace_count(package))
    report.update(packageSha256=verified["packageSha256"], protocolSha256=sha256(protocol),
                  summarySha256=sha256(summary), traceManifestSha256=sha256(manifest))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    args = parser.parse_args()
    try:
        report = report_archive(args.package)
    except (OSError, PackageError) as exc:
        parser.exit(1, f"archive coverage failed: {exc}\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
