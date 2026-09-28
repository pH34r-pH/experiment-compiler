"""Create a new immutable lifecycle plan revision from a reviewed attempt.

Scientific edits remain in the source-owned recipe. This module verifies the
selected attempt, carries its exact package bytes forward, and adds PROV-O
revision links; it does not interpret experiment outputs.
"""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from .assembly import compile_source_closure
from .core import (MANIFEST, MAX_FILE, MAX_TOTAL, PackageError, bounded_read,
                   canonical, json_value, load_recipe, safe_path, sha256,
                   validate_lifecycle_crate, verify_bytes)


PROV = "https://www.w3.org/ns/prov#"
_RUNNER_RECEIPT = "/runner-receipt.json"


def _crate_entities(crate: dict) -> dict[str, dict]:
    entities = {node["@id"]: node for node in crate["@graph"]}
    return entities


def _root_and_protocol(crate: dict) -> tuple[dict, dict]:
    entities = _crate_entities(crate)
    root = entities.get("./")
    if not isinstance(root, dict):
        raise PackageError("Lifecycle crate has no root Dataset")
    reference = root.get("mainEntity")
    protocol = entities.get(reference.get("@id")) if isinstance(reference, dict) else None
    if not isinstance(protocol, dict):
        raise PackageError("Lifecycle crate has no described main protocol entity")
    return root, protocol


def _read_parent_attempt(data: bytes, attempt_id: str) -> tuple[dict, dict, bytes]:
    """Require the explicitly selected CreateAction and its runner receipt."""
    try:
        with zipfile.ZipFile(__import__("io").BytesIO(data)) as archive:
            receipt_names = [name for name in archive.namelist() if name.endswith(_RUNNER_RECEIPT)]
            if len(receipt_names) != 1:
                raise PackageError("Parent attempt package must contain exactly one runner receipt")
            receipt_name = receipt_names[0]
            receipt_bytes = archive.read(receipt_name)
            receipt = json_value(receipt_bytes)
            crate = json_value(archive.read("ro-crate-metadata.json"))
    except (KeyError, zipfile.BadZipFile) as exc:
        raise PackageError(f"Parent package is missing lifecycle attempt evidence: {exc}") from exc
    if not isinstance(receipt, dict) or receipt.get("attemptId") != attempt_id:
        raise PackageError("Selected attempt ID does not match the parent runner receipt")
    lifecycle = validate_lifecycle_crate(crate)
    if not lifecycle["processRunCrate"] or lifecycle["attemptCount"] < 1:
        raise PackageError("Parent package contains no Process Run attempt")
    action_id = f"#attempt-{attempt_id}"
    action = _crate_entities(crate).get(action_id)
    if not isinstance(action, dict) or "CreateAction" not in (
            {action.get("@type")} if isinstance(action.get("@type"), str)
            else set(action.get("@type", []))):
        raise PackageError("Selected attempt has no matching Schema.org CreateAction")
    if action.get("actionStatus") not in {
            "https://schema.org/ActiveActionStatus",
            "https://schema.org/CompletedActionStatus",
            "https://schema.org/FailedActionStatus"}:
        raise PackageError("Selected attempt is not an executed CreateAction")
    receipt_status = receipt.get("status")
    exit_code = receipt.get("exitCode")
    timed_out = receipt.get("timedOut")
    if (receipt_status not in {"succeeded", "failed", "timed-out"} or
            type(exit_code) is not int or type(timed_out) is not bool):
        raise PackageError("Runner receipt has invalid process outcome fields")
    completed = action.get("actionStatus") == "https://schema.org/CompletedActionStatus"
    failed = action.get("actionStatus") == "https://schema.org/FailedActionStatus"
    consistent = (
        (receipt_status == "succeeded" and exit_code == 0 and not timed_out and completed) or
        (receipt_status == "failed" and exit_code != 0 and not timed_out and failed) or
        (receipt_status == "timed-out" and exit_code != 0 and timed_out and failed)
    )
    if not consistent:
        raise PackageError("Runner receipt process outcome conflicts with selected CreateAction status")
    action_result_values = action.get("result", [])
    if isinstance(action_result_values, dict):
        action_result_values = [action_result_values]
    if not isinstance(action_result_values, list):
        raise PackageError("Selected CreateAction has no valid result references")
    if not any(isinstance(item, dict) and item.get("@id") == receipt_name
               for item in action_result_values):
        raise PackageError("Selected CreateAction does not identify its runner receipt")
    root, protocol = _root_and_protocol(crate)
    action_object = action.get("object")
    if not isinstance(action_object, dict) or action_object.get("@id") != protocol.get("@id"):
        raise PackageError("Selected CreateAction does not describe the crate's main protocol")
    archive_ref = receipt.get("planPackageArchive") if isinstance(receipt, dict) else None
    plan_sha256 = receipt.get("planPackageSha256") if isinstance(receipt, dict) else None
    if (not isinstance(plan_sha256, str) or len(plan_sha256) != 64 or
            not isinstance(archive_ref, dict) or archive_ref.get("sha256") != plan_sha256 or
            not isinstance(archive_ref.get("@id"), str)):
        raise PackageError("Runner receipt does not identify the digest-pinned plan package")
    archive_path = safe_path(archive_ref["@id"])
    with zipfile.ZipFile(__import__("io").BytesIO(data)) as archive:
        try:
            plan_bytes = archive.read(archive_path)
        except KeyError as exc:
            raise PackageError("Runner receipt plan package archive is missing") from exc
    if (sha256(plan_bytes), len(plan_bytes)) != (plan_sha256, archive_ref.get("contentSize")):
        raise PackageError("Embedded plan package does not match runner receipt digest and size")
    action_results = {item.get("@id") for item in action_result_values if isinstance(item, dict)}
    if archive_path not in action_results:
        raise PackageError("Selected CreateAction does not link its embedded plan package")
    derived_from = root.get(PROV + "wasDerivedFrom")
    if not isinstance(derived_from, dict) or derived_from.get("@id") != f"urn:sha256:{plan_sha256}":
        raise PackageError("Attempt package provenance does not match its runner receipt plan digest")
    with zipfile.ZipFile(__import__("io").BytesIO(data)) as archive:
        for output in receipt.get("outputs", []):
            if not isinstance(output, dict) or not isinstance(output.get("@id"), str):
                raise PackageError("Runner receipt has a malformed output record")
            output_path = safe_path(output["@id"])
            try:
                output_bytes = archive.read(output_path)
            except KeyError as exc:
                raise PackageError("Runner receipt output is missing from attempt package") from exc
            if (sha256(output_bytes), len(output_bytes)) != (output.get("sha256"), output.get("contentSize")):
                raise PackageError("Runner receipt output digest or size does not match packaged bytes")
            if output_path not in action_results:
                raise PackageError("Selected CreateAction does not link a runner output")
        provenance_files = receipt.get("workflowRunCrateFiles", [])
        if not isinstance(provenance_files, list):
            raise PackageError("Runner receipt has a malformed workflow provenance inventory")
        for name in provenance_files:
            if not isinstance(name, str):
                raise PackageError("Runner receipt has a malformed workflow provenance path")
            provenance_path = safe_path("evidence/attempts/" + attempt_id + "/workflow-run/" + name)
            if provenance_path not in action_results:
                raise PackageError("Selected CreateAction does not link a workflow provenance file")
            try:
                archive.getinfo(provenance_path)
            except KeyError as exc:
                raise PackageError("Runner workflow provenance file is missing") from exc
    return crate, receipt, receipt_bytes


def _stage_recipe(recipe_path: Path, destination: Path) -> tuple[dict, dict[str, bytes]]:
    """Copy only declared recipe members into an isolated source closure."""
    recipe = load_recipe(recipe_path)
    if recipe["profile"] != "compiled-experiment-lifecycle-v1":
        raise PackageError("Revision requires a compiled-experiment-lifecycle-v1 recipe")
    files: dict[str, bytes] = {}
    root = recipe_path.parent.resolve()
    for item in recipe["members"]:
        source = root
        for part in safe_path(item["source"]).split("/"):
            source = source / part
            if source.is_symlink():
                raise PackageError(f"Source symlinks are not permitted: {item['source']}")
        if not source.resolve().is_relative_to(root):
            raise PackageError("Source escapes recipe directory")
        data = bounded_read(source, MAX_FILE)
        if len(data) != item["size"] or sha256(data) != item["sha256"]:
            raise PackageError(f"Source integrity mismatch: {item['source']}")
        if item["path"] == MANIFEST:
            raise PackageError("Recipe may not provide the generated manifest")
        files[item["path"]] = data
    destination.mkdir(parents=True)
    for member_path, data in files.items():
        target = destination.joinpath(*safe_path(member_path).split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return recipe, files


def revise_package(parent_package: Path, recipe_path: Path, output: Path, *,
                   expected_sha256: str, attempt_id: str) -> dict:
    """Compile a new prospective plan that carries one immutable prior attempt.

    The new source recipe supplies the human-authored revision. The command
    validates that it describes the same experiment, is still prospective, and
    then embeds the exact selected attempt package under a digest-addressed path.
    """
    if not isinstance(attempt_id, str) or not attempt_id or len(attempt_id) > 128:
        raise PackageError("Revision requires an explicit selected attempt ID")
    source_directory = output.with_name(output.stem + ".source")
    if output.exists() or output.is_symlink() or source_directory.exists() or source_directory.is_symlink():
        raise PackageError("Revision output package or source directory already exists; revisions are never overwritten")

    parent_bytes = bounded_read(parent_package, MAX_TOTAL)
    if len(parent_bytes) > MAX_FILE:
        raise PackageError(f"Parent attempt package exceeds the {MAX_FILE} byte single-member revision limit")
    parent_verified = verify_bytes(parent_bytes, expected_sha256=expected_sha256)
    if parent_verified["profile"] != "compiled-experiment-lifecycle-v1":
        raise PackageError("Revision parent must use compiled-experiment-lifecycle-v1")
    parent_crate = None
    with zipfile.ZipFile(__import__("io").BytesIO(parent_bytes)) as archive:
        parent_crate = json_value(archive.read("ro-crate-metadata.json"))
    parent_lifecycle = validate_lifecycle_crate(parent_crate)
    if parent_lifecycle["attemptCount"] < 1 or not parent_lifecycle["processRunCrate"]:
        raise PackageError("Revision parent must be an attempt package with Process Run Crate evidence")
    _, receipt, _ = _read_parent_attempt(parent_bytes, attempt_id)

    temporary = Path(tempfile.mkdtemp(prefix="compiled-experiment-revision-"))
    try:
        recipe, files = _stage_recipe(recipe_path, temporary / "recipe-source")
        parent_identifier = _root_and_protocol(parent_crate)[0].get("identifier")
        if not isinstance(parent_identifier, str) or not parent_identifier:
            raise PackageError("Parent experiment has no stable RO-Crate identifier")
        parent_recipe_id = f"{parent_identifier[:60]}-attempt-{attempt_id}"
        if recipe["id"] in {parent_identifier, parent_recipe_id}:
            raise PackageError("A revision must use a new recipe ID")
        if "expectedPackage" in recipe:
            recipe.pop("expectedPackage")

        crate = json_value(files.get("ro-crate-metadata.json", b""))
        new_lifecycle = validate_lifecycle_crate(crate)
        if new_lifecycle["processRunCrate"] or new_lifecycle["attemptCount"]:
            raise PackageError("A revised plan must not claim prior executions as its own Process Run")
        new_root, new_protocol = _root_and_protocol(crate)
        old_root, old_protocol = _root_and_protocol(parent_crate)
        if new_root.get("identifier") != old_root.get("identifier"):
            raise PackageError("Revision recipe must retain the parent experiment identifier")

        prior_protocol_id = old_protocol["@id"]
        try:
            safe_protocol_id = safe_path(prior_protocol_id)
        except PackageError as exc:
            raise PackageError("Parent protocol must be a packaged relative file") from exc
        with zipfile.ZipFile(__import__("io").BytesIO(parent_bytes)) as archive:
            if safe_protocol_id not in archive.namelist():
                raise PackageError("Parent protocol is not embedded in its package")
            prior_protocol_bytes = archive.read(safe_protocol_id)
        prior_protocol_sha = sha256(prior_protocol_bytes)
        prior_protocol_ref = f"urn:sha256:{prior_protocol_sha}"
        if PROV + "wasRevisionOf" in new_protocol:
            raise PackageError("New protocol must not supply a second wasRevisionOf parent")

        parent_member = f"evidence/revisions/{expected_sha256}/parent-attempt-package.zip"
        if parent_member in files:
            raise PackageError("Revision recipe already contains the parent attempt member path")
        new_protocol[PROV + "wasRevisionOf"] = {"@id": prior_protocol_ref}
        new_protocol[PROV + "wasDerivedFrom"] = {"@id": parent_member}
        new_root[PROV + "wasRevisionOf"] = {"@id": parent_member}
        existing_parts = new_root.get("hasPart", [])
        if isinstance(existing_parts, dict):
            existing_parts = [existing_parts]
        if not isinstance(existing_parts, list):
            raise PackageError("Revision root hasPart must be a reference or array")
        new_root["hasPart"] = existing_parts + [{"@id": parent_member}]
        graph = crate["@graph"]
        graph.append({"@id": parent_member, "@type": "File", "name": "Exact previous attempt package",
                      "encodingFormat": "application/zip", "sha256": expected_sha256,
                      "contentSize": len(parent_bytes)})
        graph.append({"@id": prior_protocol_ref, "@type": "CreativeWork",
                      "name": old_protocol.get("name", "Prior protocol revision"),
                      "sha256": prior_protocol_sha, "contentSize": len(prior_protocol_bytes),
                      PROV + "wasDerivedFrom": {"@id": parent_member}})
        files["ro-crate-metadata.json"] = canonical(crate)
        files[parent_member] = parent_bytes
        if "experiment.json" in files:
            raise PackageError("Revision package member experiment.json conflicts with its source-owned recipe")

        revised_recipe = {
            key: value for key, value in recipe.items()
            if key not in {"members", "expectedPackage"}
        }
        if new_protocol.get("@id") not in files:
            raise PackageError("Revised protocol must resolve to a packaged member")
        result = compile_source_closure(
            temporary, output, source_directory, files, revised_recipe, "revised.zip"
        )
        return {**result, "parentPackageSha256": expected_sha256,
                "selectedAttemptId": attempt_id,
                "revisionProtocolSha256": sha256(files[new_protocol["@id"]]),
                "parentPlanPackageSha256": receipt["planPackageSha256"]}
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
