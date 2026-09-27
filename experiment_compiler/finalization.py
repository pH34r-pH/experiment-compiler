"""Create a reviewed, interpretation-bearing result artifact from one attempt.

The finalizer records source-authored conclusions and review metadata. It checks
artifact identity and provenance, but never derives a scientific conclusion from
runner status.
"""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path

from .core import (MANIFEST, MAX_FILE, MAX_TOTAL, PackageError, bounded_read,
                   canonical, compile_package, json_value, safe_path, sha256,
                   validate_lifecycle_crate, verify_bytes, write_once)
from .revision import PROV, _read_parent_attempt, _root_and_protocol

SCHEMA = "https://schema.org/"


def _source_text(path: Path, description: str) -> bytes:
    data = bounded_read(path, MAX_FILE)
    try:
        text = data.decode("utf-8")
    except UnicodeError as exc:
        raise PackageError(f"{description} must be UTF-8 text") from exc
    if not text.strip():
        raise PackageError(f"{description} must not be empty")
    return data


def _append_part(root: dict, path: str) -> None:
    parts = root.get("hasPart", [])
    if isinstance(parts, dict):
        parts = [parts]
    if not isinstance(parts, list):
        raise PackageError("Final artifact root hasPart must be a reference or array")
    if any(isinstance(item, dict) and item.get("@id") == path for item in parts):
        raise PackageError(f"Finalization path already appears in root hasPart: {path}")
    root["hasPart"] = [*parts, {"@id": path}]


def finalize_package(parent_package: Path, output: Path, *,
                     expected_sha256: str, attempt_id: str,
                     experiment_id: str, title: str,
                     decision_path: Path, decision_summary: str,
                     review_path: Path, review_summary: str,
                     reviewer_name: str) -> dict:
    """Bind explicit decision/review records to one exact runner attempt.

    The parent attempt ZIP is carried as a digest-addressed member; all its
    already-verified members are copied into the final crate as well. The schema
    graph records status and relationships, while interpretation text is
    supplied by the caller and is never generated here.
    """
    if not isinstance(attempt_id, str) or not attempt_id or len(attempt_id) > 128:
        raise PackageError("Finalization requires an explicit selected attempt ID")
    for value, label, maximum in ((experiment_id, "final recipe ID", 80),
                                  (title, "final title", 240),
                                  (decision_summary, "decision summary", 4000),
                                  (review_summary, "review summary", 4000),
                                  (reviewer_name, "reviewer name", 240)):
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise PackageError(f"Finalization requires a nonempty {label} of at most {maximum} characters")
    try:
        safe_path(experiment_id)
    except PackageError as exc:
        raise PackageError("Final recipe ID must be a portable single path component") from exc
    source_directory = output.with_name(output.stem + ".source")
    if output.exists() or output.is_symlink() or source_directory.exists() or source_directory.is_symlink():
        raise PackageError("Final artifact or source directory already exists; artifacts are never overwritten")

    parent_bytes = bounded_read(parent_package, MAX_TOTAL)
    if len(parent_bytes) > MAX_FILE:
        raise PackageError(f"Parent attempt package exceeds the {MAX_FILE} byte single-member finalization limit")
    parent_verification = verify_bytes(parent_bytes, expected_sha256=expected_sha256)
    if parent_verification["profile"] != "compiled-experiment-lifecycle-v1":
        raise PackageError("Finalization parent must use compiled-experiment-lifecycle-v1")
    parent_crate, receipt, _ = _read_parent_attempt(parent_bytes, attempt_id)
    decision_bytes = _source_text(decision_path, "Decision record")
    review_bytes = _source_text(review_path, "Publication review record")

    temporary = Path(tempfile.mkdtemp(prefix="compiled-experiment-finalization-"))
    try:
        try:
            with zipfile.ZipFile(__import__("io").BytesIO(parent_bytes)) as archive:
                files = {name: archive.read(name) for name in archive.namelist() if name != MANIFEST}
                parent_manifest = json_value(archive.read(MANIFEST))
        except (KeyError, zipfile.BadZipFile) as exc:
            raise PackageError(f"Parent attempt package cannot be read: {exc}") from exc
        crate = json_value(files.get("ro-crate-metadata.json", b""))
        lifecycle = validate_lifecycle_crate(crate)
        if not lifecycle["processRunCrate"] or lifecycle["attemptCount"] < 1:
            raise PackageError("Finalization requires a Process Run attempt package")
        root, protocol = _root_and_protocol(crate)
        selected_id = f"#attempt-{attempt_id}"
        selected_action = next((node for node in crate["@graph"]
                                if isinstance(node, dict) and node.get("@id") == selected_id), None)
        if selected_action is None:
            raise PackageError("Selected attempt action is missing from its crate")
        parent_identifier = root.get("identifier")
        if not isinstance(parent_identifier, str) or not parent_identifier:
            raise PackageError("Parent experiment has no stable RO-Crate identifier")
        if experiment_id in {parent_identifier, f"{parent_identifier[:60]}-attempt-{attempt_id}"}:
            raise PackageError("Final artifact must use a new recipe ID")

        parent_member = f"evidence/finalization/{expected_sha256}/parent-attempt-package.zip"
        decision_member = "evidence/scientific-decision.md"
        review_member = "evidence/publication-review.md"
        reviewer_id = "#publication-reviewer"
        reserved = {parent_member, decision_member, review_member}
        if reserved.intersection(files):
            raise PackageError("Parent package already contains a reserved finalization member")
        if any(node.get("@id") in reserved or node.get("@id") == reviewer_id
               for node in crate["@graph"] if isinstance(node, dict)):
            raise PackageError("Parent crate already contains a reserved finalization entity")

        protocol["creativeWorkStatus"] = "Published"
        root[PROV + "wasDerivedFrom"] = {"@id": parent_member}
        for path in (parent_member, decision_member, review_member):
            _append_part(root, path)
        graph = crate["@graph"]
        graph.extend([
            {"@id": parent_member, "@type": "File", "name": "Exact selected attempt package",
             "encodingFormat": "application/zip", "sha256": expected_sha256,
             "contentSize": len(parent_bytes)},
            {"@id": decision_member, "@type": ["File", "CreativeWork"],
             "name": "Scientific decision and interpretation", "encodingFormat": "text/markdown",
             "abstract": decision_summary, "about": {"@id": selected_id},
             PROV + "wasDerivedFrom": {"@id": parent_member}},
            {"@id": reviewer_id, "@type": "Person", "name": reviewer_name},
            {"@id": review_member, "@type": ["File", "Review"],
             "name": "Publication review record", "encodingFormat": "text/markdown",
             "reviewBody": review_summary, "author": {"@id": reviewer_id},
             "itemReviewed": {"@id": parent_member}, "about": {"@id": selected_id},
             PROV + "wasDerivedFrom": {"@id": parent_member}},
        ])
        files["ro-crate-metadata.json"] = canonical(crate)
        files[parent_member] = parent_bytes
        files[decision_member] = decision_bytes
        files[review_member] = review_bytes
        if "experiment.json" in files:
            raise PackageError("Parent package member experiment.json conflicts with the source-owned recipe")

        stage = temporary / "source"
        stage.mkdir()
        members = []
        for package_path, data in sorted(files.items()):
            source = package_path
            target = stage.joinpath(*safe_path(source).split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            members.append({"path": package_path, "source": source,
                            "sha256": sha256(data), "size": len(data)})
        recipe = {
            "buildRecipeVersion": 1,
            "id": experiment_id,
            "title": title,
            "profile": "compiled-experiment-lifecycle-v1",
            "manifest": {key: value for key, value in parent_manifest.items() if key != "members"},
            "members": members,
        }
        recipe_path = stage / "experiment.json"
        recipe_path.write_bytes(canonical(recipe))
        temporary_package = temporary / "finalized.zip"
        result = compile_package(recipe_path, temporary_package)
        recipe["expectedPackage"] = {"sha256": result["packageSha256"],
                                     "size": result["packageSizeBytes"]}
        recipe_path.write_bytes(canonical(recipe))
        shutil.copytree(stage, source_directory)
        try:
            write_once(output, temporary_package.read_bytes())
        except (OSError, PackageError):
            shutil.rmtree(source_directory, ignore_errors=True)
            raise
        return {**result, "parentPackageSha256": expected_sha256,
                "parentPlanPackageSha256": receipt["planPackageSha256"],
                "selectedAttemptId": attempt_id,
                "decisionSha256": sha256(decision_bytes),
                "reviewRecordSha256": sha256(review_bytes)}
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
