import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from functools import partial
from pathlib import Path
from unittest.mock import patch

from experiment_compiler.core import (PackageError, canonical, compile_package, json_value, load_recipe,
                                      sha256, sha256_file, verify_bytes)
from experiment_compiler.catalog import describe_catalog
from experiment_compiler.finalization import finalize_package
from experiment_compiler.revision import _read_parent_attempt, revise_package
from experiment_compiler.runner import run_package
from fixture_requirements import HAS_RUNNER
from runner_fixture_helpers import prepare_container_journal, synthetic_worker_observation

ROOT = Path(__file__).resolve().parents[1]
_RO = "https://w3id.org/ro/crate/1.3"
_PROCESS = "https://w3id.org/ro/wfrun/process/0.6"
_PROV = "https://www.w3.org/ns/prov#"
_AUTHORITATIVE_PROTOCOL = "protocols/frozen-procedure.md"
_PROTOCOL_TEXT = "Follow the frozen procedure exactly. Compare <retained> sample groups.\n"
_MIXED_RESULT = "Negative primary endpoint, with a mixed comparison and an inconclusive overall finding."


def _store_catalog_member(recipe_path, recipe, name, data):
    member = next((entry for entry in recipe["members"] if entry["path"] == name), None)
    if member is None:
        member = {"path": name, "source": name}
        recipe["members"].append(member)
    path = recipe_path.parent / member["source"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    member.update(sha256=sha256(data), size=len(data))


def _catalog_observation(plan, directory, disposition):
    shutil.copytree(plan.parent, directory)
    recipe_path = directory / "experiment.json"
    recipe = load_recipe(recipe_path)
    recipe["id"] = f"catalog-{disposition}"
    recipe["title"] = f"Synthetic {disposition} catalog case"
    recipe.pop("expectedPackage", None)
    metadata = directory / "ro-crate-metadata.json"
    crate = json_value(metadata.read_bytes())
    crate["@context"] = [_RO + "/context", "https://w3id.org/ro/terms/workflow-run/context"]
    graph = crate["@graph"]
    root = next(node for node in graph if node["@id"] == "./")
    root.update(mainEntity={"@id": _AUTHORITATIVE_PROTOCOL}, datePublished="2026-01-01")
    root["conformsTo"] = [{"@id": _RO}, {"@id": _PROCESS}]
    graph.extend([
        {"@id": _PROCESS, "@type": ["CreativeWork", "Profile"], "version": "0.6"},
        {"@id": _AUTHORITATIVE_PROTOCOL, "@type": ["File", "CreativeWork"], "creativeWorkStatus": "Draft"},
        {"@id": "evidence/observation-receipt.json", "@type": "File"},
        {"@id": f"#{disposition}", "@type": "CreateAction", "instrument": {"@id": "experiment/workflow.cwl"},
         "actionStatus": "https://schema.org/" + ("ActiveActionStatus" if disposition == "active" else "FailedActionStatus"),
         "object": {"@id": _AUTHORITATIVE_PROTOCOL}, "result": {"@id": "evidence/observation-receipt.json"}},
        {"@id": "#observed-elapsed", "@type": "PropertyValue", "propertyID": "elapsed observation time",
         "value": 2.5, "unitText": "s", "measurementTechnique": "synthetic monotonic clock",
         "valueReference": {"@id": "evidence/observation-receipt.json"}},
    ])
    root["hasPart"].extend([{"@id": _AUTHORITATIVE_PROTOCOL}, {"@id": "evidence/observation-receipt.json"}])
    root["variableMeasured"].append({"@id": "#observed-elapsed"})
    _store_catalog_member(recipe_path, recipe, _AUTHORITATIVE_PROTOCOL, _PROTOCOL_TEXT.encode())
    _store_catalog_member(recipe_path, recipe, "evidence/observation-receipt.json",
                          canonical({"status": "started" if disposition == "active" else "cancelled"}))
    _store_catalog_member(recipe_path, recipe, metadata.name, canonical(crate))
    recipe_path.write_bytes(canonical(recipe))
    return recipe_path


def _validate_generated_crate(archive):
    with tempfile.TemporaryDirectory() as temporary:
        with zipfile.ZipFile(archive) as package:
            package.extractall(temporary)
        checked = subprocess.run(
            [sys.executable, str(ROOT / "scripts/validate_current_ro_profiles.py"), temporary],
            capture_output=True, text=True,
        )
        if checked.returncode:
            raise AssertionError(checked.stderr)
        return json.loads(checked.stdout)


def _retained_attempt_digests(attempt: Path) -> dict[Path, str]:
    source = attempt.with_suffix(".source")
    receipt = attempt.with_suffix(".attempt") / "runner-receipt.json"
    files = {attempt, attempt.with_name("plan.zip"), receipt, source / "experiment.json"}
    files.update(path for path in source.rglob("*") if path.is_file())
    return {path: sha256(path.read_bytes()) for path in files}


def _fail_package_write(output: Path):
    copy_file = shutil.copyfileobj

    def fail_selected_write(source, destination, length=0):
        if Path(destination.name) == output:
            destination.write(b"incomplete fixture")
            raise OSError("synthetic package write error")
        return copy_file(source, destination, length)

    return patch.object(shutil, "copyfileobj", side_effect=fail_selected_write)


def _publish_concurrent_output(output: Path, content: bytes):
    open_path = Path.open

    def open_with_existing_output(path, mode="r", *args, **kwargs):
        if path == output and mode == "xb":
            with open_path(path, "xb") as other_writer:
                other_writer.write(content)
        return open_path(path, mode, *args, **kwargs)

    return patch.object(Path, "open", new=open_with_existing_output)


class RevisionTests(unittest.TestCase):
    @staticmethod
    def _fixture_container_journal(attempt_directory, attempt_id, runner):
        return prepare_container_journal(attempt_directory, attempt_id, runner)

    def _attempt(self, directory: Path, recipe_path: Path | None = None,
                 fixture_result: str = '{"fixture":"result"}\n', *,
                 return_code: int = 0) -> tuple[Path, str, bytes, str]:
        if not HAS_RUNNER or os.name != "posix":
            self.skipTest("attempt fixture requires the runner extra and POSIX process control")
        plan = directory / "plan.zip"
        built = compile_package(recipe_path or ROOT / "examples/linear-regression-plan-v1/experiment.json", plan)
        plan_bytes = plan.read_bytes()

        class CompletedProcess:
            pid = 101
            returncode = return_code

            def wait(self, timeout=None):
                return self.returncode

        def fake_popen(command, **kwargs):
            outdir = Path(command[command.index("--outdir") + 1])
            provdir = Path(command[command.index("--provenance") + 1])
            outdir.mkdir(parents=True)
            (outdir / "result.json").write_text(fixture_result)
            provdir.mkdir(parents=True)
            (provdir / "workflow-run.json").write_text('{"fixture":"provenance"}\n')
            return CompletedProcess()

        attempt = directory / "attempt.zip"
        with patch.dict(os.environ, {
                "EXPERIMENT_RUNNER_WORKER_IMAGE": "sha256:fixture",
                "EXPERIMENT_RUNNER_SANDBOX": "test worker",
                "EXPERIMENT_RUNNER_RESOURCE_LIMITS":
                    '{"cores":1,"ramMiB":64,"tmpdirMiB":64,"outdirMiB":64,"wallSeconds":120}',
                "EXPERIMENT_RUNNER_TMPFS_ROOT": str(directory),
                "TMPDIR": str(directory),
        }), patch("experiment_compiler.runner._require_bounded_tmpfs"), \
                patch("experiment_compiler.runner.observe_worker", side_effect=synthetic_worker_observation), \
                patch("experiment_compiler.runner.importlib.metadata.version",
                      return_value="3.2.20260720092025"), \
                patch("experiment_compiler.runner._new_container_journal",
                      side_effect=self._fixture_container_journal), \
                patch("experiment_compiler.runner._cwl_process_identity", return_value={
                    "pid": 101, "bootId": "fixture", "processStartTicks": "1",
                    "processGroupId": 101, "sessionId": 101,
                }), patch("experiment_compiler.runner._process_group_exists", return_value=False), \
                patch("experiment_compiler.runner.subprocess.Popen", side_effect=fake_popen):
            run_package(plan, attempt, expected_sha256=built["packageSha256"],
                        allow_workflow_execution=True)
        with zipfile.ZipFile(attempt) as archive:
            receipt_name = next(name for name in archive.namelist()
                                if name.endswith("/runner-receipt.json"))
            receipt = json_value(archive.read(receipt_name))
        return attempt, built["packageSha256"], plan_bytes, receipt["attemptId"]

    def _catalog_matrix(self, directory, sources):
        plan = sources / "plan" / "experiment.json"
        shutil.copytree(ROOT / "examples/linear-regression-plan-v1", plan.parent)
        fixtures = {"plan": (plan, directory / "plan-reference.zip")}
        croissant = sources / "croissant" / "experiment.json"
        shutil.copytree(ROOT / "examples/linear-regression-v1", croissant.parent)
        fixtures["croissant"] = (croissant, directory / "croissant-reference.zip")
        for disposition in ("active", "cancelled"):
            recipe = _catalog_observation(plan, sources / disposition, disposition)
            fixtures[disposition] = (recipe, directory / f"{disposition}-reference.zip")
        for recipe, archive in fixtures.values():
            compile_package(recipe, archive)

        for disposition, code in (("completed", 0), ("failed", 3)):
            working = directory / disposition
            working.mkdir()
            attempt, _, _, attempt_id = self._attempt(working, plan, return_code=code)
            fixtures[disposition] = self._admit_generated_source(sources, disposition, attempt)
            if disposition == "completed":
                selected_attempt = attempt, attempt_id

        parent, selected = selected_attempt
        origin = {"expected_sha256": sha256_file(parent), "attempt_id": selected}
        revised = directory / "revised.zip"
        revise_package(parent, self._revision_recipe(directory), revised, **origin)
        final = directory / "final.zip"
        notes = directory / "source-review.md"
        notes.write_text(_MIXED_RESULT + "\n", encoding="utf-8")
        finalize_package(parent, final, **origin, experiment_id="catalog-final", title="Finalized catalog case",
                         decision_path=notes, decision_summary=_MIXED_RESULT, review_path=notes,
                         review_summary="Reviewed synthetic evidence.", reviewer_name="Catalog fixture reviewer")
        for name, archive in (("revised", revised), ("final", final)):
            fixtures[name] = self._admit_generated_source(sources, name, archive)
        return fixtures, origin

    def _admit_generated_source(self, sources, name, archive):
        destination = sources / name
        shutil.copytree(archive.with_suffix(".source"), destination)
        return destination / "experiment.json", archive

    @unittest.skipUnless(importlib.util.find_spec("jsonschema"), "catalog schema validation requires the repository extra")
    def test_lifecycle_catalog_round_trips_generated_packages(self):
        from jsonschema import Draft202012Validator, ValidationError
        from scripts.build_pages_site import build_site

        if not HAS_RUNNER or os.name != "posix":
            self.skipTest("catalog matrix uses reviewed runner and POSIX fixtures")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "recipes"
            fixtures, origin = self._catalog_matrix(directory, source)
            site = directory / "site"
            self.assertEqual(build_site(site, source), len(fixtures))
            catalog_path = site / "data/experiments.json"
            catalog = json_value(catalog_path.read_bytes())
            schema = json_value((site / "data/experiments.schema.json").read_bytes())
            Draft202012Validator.check_schema(schema)
            validator = Draft202012Validator(schema)
            validator.validate(catalog)
            with self.assertRaises(ValidationError):
                validator.validate({**catalog, "schemaVersion": 3})
            by_id = {entry["id"]: entry for entry in catalog["experiments"]}
            records = {case: by_id[load_recipe(recipe)["id"]] for case, (recipe, _) in fixtures.items()}
            self._check_catalog_matrix_identity(fixtures, records, site)
            self._check_catalog_matrix_states(fixtures, records, origin, site)
            self._check_catalog_croissant(fixtures["croissant"], records["croissant"])
            self._check_catalog_determinism_and_tampering(directory, source, site, catalog_path)

    def _check_catalog_matrix_identity(self, fixtures, records, site):
        for case, (recipe_path, archive) in fixtures.items():
            with self.subTest(case=case):
                recipe = load_recipe(recipe_path)
                record = records[case]
                digest = sha256_file(archive)
                self.assertEqual(record["package"], {"sha256": digest, "size": archive.stat().st_size})
                packaged = site / "packages" / f"{digest}.zip"
                self.assertEqual(packaged.read_bytes(), archive.read_bytes())
                verified = verify_bytes(packaged.read_bytes(), expected_sha256=digest, recipe=recipe)
                self.assertEqual(record["source"], verified["source"])
                self.assertEqual(record["standards"], recipe["manifest"]["standards"])
                if "lifecycle" in verified:
                    self.assertEqual(record["lifecycle"], verified["lifecycle"])
                    self.assertEqual(record["experimentIdentifier"], "linear-regression-plan-v1")
                self.assertTrue((site / record["detailUrl"].strip("/") / "index.html").is_file())
                for field in ("doi", "archivalRecord", "publicationStatus", "scientificReproduction"):
                    self.assertNotIn(field, record)

    def _check_catalog_matrix_states(self, fixtures, records, origin, site):
        statuses = {"plan": None, "revised": None, "active": "Active", "failed": "Failed",
                    "cancelled": "Failed", "completed": "Completed", "final": "Completed"}
        for case, status in statuses.items():
            with self.subTest(case=case):
                record = records[case]
                self.assertEqual(record["lifecycle"]["processRunCrate"], status is not None)
                if status is None:
                    self.assertEqual(record["executionAttempts"], [])
                    continue
                checked = _validate_generated_crate(fixtures[case][1])
                self.assertEqual(checked["status"], "current-profile-required-contract-passed")
                attempt, = record["executionAttempts"]
                self.assertEqual(attempt["actionStatus"], f"https://schema.org/{status}ActionStatus")
        self.assertIsNone(records["completed"]["scientificInterpretation"])
        self.assertEqual(records["final"]["scientificInterpretation"], [{
            "record": "evidence/scientific-decision.md", "summary": _MIXED_RESULT,
            "aboutAttempt": f"#attempt-{origin['attempt_id']}",
        }])
        with zipfile.ZipFile(fixtures["cancelled"][1]) as archive:
            result, = records["cancelled"]["executionAttempts"][0]["result"]
            self.assertEqual(json_value(archive.read(result))["status"], "cancelled")
        self._check_catalog_matrix_lineage(fixtures, records, origin)
        self._check_catalog_protocol_and_measurements(records, site)

    def _check_catalog_matrix_lineage(self, fixtures, records, origin):
        plan = fixtures["plan"][1]
        completed = fixtures["completed"][1]
        for case in ("completed", "failed"):
            self.assertEqual(records[case]["lineage"], [{
                "relation": _PROV + "wasDerivedFrom", "reference": "urn:sha256:" + sha256_file(plan),
                "package": records["plan"]["package"],
            }])
        for case, relation in (("revised", "wasRevisionOf"), ("final", "wasDerivedFrom")):
            lineage, = records[case]["lineage"]
            self.assertEqual(lineage["relation"], _PROV + relation)
            self.assertEqual(lineage["package"]["sha256"], origin["expected_sha256"])
            self.assertEqual(lineage["package"], records["completed"]["package"])
            with zipfile.ZipFile(fixtures[case][1]) as archive:
                self.assertEqual(archive.read(lineage["reference"]), completed.read_bytes())

    def _check_catalog_protocol_and_measurements(self, records, site):
        active = records["active"]
        self.assertEqual(active["protocol"], {"record": _AUTHORITATIVE_PROTOCOL, "text": _PROTOCOL_TEXT})
        self.assertIsNone(active["question"])
        self.assertIsNone(active["method"])
        detail = (site / active["detailUrl"].strip("/") / "index.html").read_text(encoding="utf-8")
        self.assertIn("Compare &lt;retained&gt; sample groups.", detail)
        measurements = records["plan"]["lifecycle"]["resourceMeasurements"]
        self.assertEqual(measurements[0], {
            "propertyID": "peak process memory", "value": "unknown", "unitText": None,
            "measurementTechnique": "no execution attempt has been made", "evidence": None,
        })
        self.assertEqual(active["lifecycle"]["resourceMeasurements"][-1], {
            "propertyID": "elapsed observation time", "value": 2.5, "unitText": "s",
            "measurementTechnique": "synthetic monotonic clock",
            "evidence": {"@id": "evidence/observation-receipt.json"},
        })

    def _check_catalog_croissant(self, fixture, record):
        recipe_path, archive = fixture
        self.assertEqual(record["standards"]["croissant"], "1.1")
        member = "metadata/croissant.jsonld"
        with zipfile.ZipFile(archive) as package:
            self.assertEqual(package.read(member), (recipe_path.parent / member).read_bytes())

    def _check_catalog_determinism_and_tampering(self, directory, source, site, catalog_path):
        again = directory / "again"
        from scripts.build_pages_site import build_site

        build_site(again, source)
        self.assertEqual(catalog_path.read_bytes(), (again / "data/experiments.json").read_bytes())
        self.assertEqual((site / "index.html").read_bytes(), (again / "index.html").read_bytes())
        pinned = source / "final" / "experiment/protocol.md"
        pinned.write_bytes(pinned.read_bytes() + b"\nChanged after pinning.\n")
        with self.assertRaisesRegex(PackageError, "Source integrity mismatch"):
            build_site(directory / "refused", source)

    def test_catalog_refuses_lineage_annotation_with_different_parent_bytes(self):
        from experiment_compiler.catalog import describe_recipe

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(ROOT / "examples/linear-regression-podman-lifecycle-v1", root / "revision")
            recipe_path = root / "revision" / "experiment.json"
            recipe = load_recipe(recipe_path)
            crate = json_value((recipe_path.parent / "ro-crate-metadata.json").read_bytes())
            graph = {node["@id"]: node for node in crate["@graph"]}
            reference = graph["./"][_PROV + "wasRevisionOf"]["@id"]
            graph[reference]["sha256"] = "e" * 64
            _store_catalog_member(recipe_path, recipe, "ro-crate-metadata.json", canonical(crate))
            recipe.pop("expectedPackage", None)
            recipe_path.write_bytes(canonical(recipe))
            with self.assertRaisesRegex(PackageError, "disagrees with the packaged parent identity"):
                describe_recipe(recipe_path)

    def _revision_recipe(self, directory: Path) -> Path:
        fixture = directory / "revision-v2"
        shutil.copytree(ROOT / "examples/linear-regression-plan-v1", fixture)
        recipe_path = fixture / "experiment.json"
        recipe = json.loads(recipe_path.read_text())
        recipe["id"] = "linear-regression-plan-v2"
        recipe["title"] = "Prospective linear-regression revision"
        recipe.pop("expectedPackage", None)
        protocol_path = fixture / "source/protocol.md"
        protocol_path.write_text(protocol_path.read_text() + "\n## Revision\nUpdated after attempt 1.\n")
        protocol_bytes = protocol_path.read_bytes()
        for member in recipe["members"]:
            if member["source"] == "source/protocol.md":
                member["sha256"] = sha256(protocol_bytes)
                member["size"] = len(protocol_bytes)
        recipe_path.write_text(json.dumps(recipe, indent=2) + "\n")
        return recipe_path

    def test_revision_carries_exact_attempt_and_preserves_prospective_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            attempt, _, attempt_bytes, attempt_id = self._attempt(directory)
            retained = _retained_attempt_digests(attempt)
            attempt_package_bytes = attempt.read_bytes()
            attempt_sha = sha256(attempt_package_bytes)
            recipe = self._revision_recipe(directory)
            revised = directory / "revision-v2.zip"

            result = revise_package(attempt, recipe, revised,
                                    expected_sha256=attempt_sha, attempt_id=attempt_id)

            self.assertEqual(attempt.read_bytes(), attempt_package_bytes)
            self.assertEqual(_retained_attempt_digests(attempt), retained)
            self.assertNotEqual(attempt_bytes, attempt_package_bytes)
            self.assertNotEqual(result["packageSha256"], attempt_sha)
            self.assertEqual(result["parentPackageSha256"], attempt_sha)
            self.assertEqual(result["selectedAttemptId"], attempt_id)
            verified = verify_bytes(revised.read_bytes(), recipe=load_recipe(
                revised.with_name("revision-v2.source") / "experiment.json"))
            self.assertEqual(verified["lifecycle"]["attemptCount"], 0)
            self.assertFalse(verified["lifecycle"]["processRunCrate"])
            with zipfile.ZipFile(revised) as archive:
                crate = json_value(archive.read("ro-crate-metadata.json"))
                entities = {node["@id"]: node for node in crate["@graph"]}
                root = entities["./"]
                protocol = entities[root["mainEntity"]["@id"]]
                parent_file = next(node for node in crate["@graph"]
                                   if node.get("sha256") == attempt_sha)
                self.assertEqual(archive.read(parent_file["@id"]), attempt_package_bytes)
                self.assertIn("https://www.w3.org/ns/prov#wasRevisionOf", protocol)
                self.assertEqual(root["https://www.w3.org/ns/prov#wasRevisionOf"]["@id"],
                                 parent_file["@id"])

    def test_revision_rejects_wrong_digest_or_attempt_without_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            attempt, _, _, attempt_id = self._attempt(directory)
            recipe = self._revision_recipe(directory)
            for index, (digest, selected_attempt) in enumerate((
                    ("0" * 64, attempt_id), (sha256(attempt.read_bytes()), "wrong-attempt"))):
                output = directory / f"rejected-{index}.zip"
                with self.subTest(index=index), self.assertRaises(PackageError):
                    revise_package(attempt, recipe, output, expected_sha256=digest,
                                   attempt_id=selected_attempt)
                self.assertFalse(output.exists())
                self.assertFalse(output.with_name(output.stem + ".source").exists())

    def test_revision_rejects_reuse_of_parent_catalog_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            attempt, _, _, attempt_id = self._attempt(directory)
            recipe_path = self._revision_recipe(directory)
            recipe = json.loads(recipe_path.read_text())
            recipe["id"] = "linear-regression-plan-v1"
            recipe_path.write_text(json.dumps(recipe, indent=2) + "\n")
            output = directory / "duplicate-id.zip"
            with self.assertRaisesRegex(PackageError, "new recipe ID"):
                revise_package(attempt, recipe_path, output,
                               expected_sha256=sha256(attempt.read_bytes()),
                               attempt_id=attempt_id)
            self.assertFalse(output.exists())
            self.assertFalse(output.with_name("duplicate-id.source").exists())

    def test_revision_rejects_prospective_action_masquerading_as_attempt(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            attempt, _, _, attempt_id = self._attempt(directory)
            with zipfile.ZipFile(attempt) as archive:
                files = {name: archive.read(name) for name in archive.namelist()}
            crate = json_value(files["ro-crate-metadata.json"])
            selected = next(node for node in crate["@graph"]
                            if node.get("@id") == f"#attempt-{attempt_id}")
            selected["actionStatus"] = "https://schema.org/PotentialActionStatus"
            # Keep the crate's Process Run claim valid with another executed action.
            executed = dict(selected)
            executed.update({"@id": "#unselected-execution",
                             "actionStatus": "https://schema.org/CompletedActionStatus"})
            crate["@graph"].append(executed)
            files["ro-crate-metadata.json"] = json.dumps(crate).encode()
            malformed = directory / "malformed-attempt.zip"
            with zipfile.ZipFile(malformed, "w") as archive:
                for name, content in files.items():
                    archive.writestr(name, content)
            with self.assertRaisesRegex(PackageError, "not an executed CreateAction"):
                _read_parent_attempt(malformed.read_bytes(), attempt_id)

    def test_finalization_binds_explicit_interpretation_and_review_to_attempt(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            attempt, _, _, attempt_id = self._attempt(directory)
            retained = _retained_attempt_digests(attempt)
            attempt_bytes = attempt.read_bytes()
            attempt_sha = sha256(attempt_bytes)
            decision = directory / "decision.md"
            decision.write_text("## Interpretation\nThe fixture run succeeded operationally; its scientific finding is inconclusive.\n")
            review = directory / "review.md"
            review.write_text("## Public release review\nThe fixture's synthetic inputs and outputs are shareable.\n")
            final = directory / "final.zip"
            result = finalize_package(
                attempt, final, expected_sha256=attempt_sha, attempt_id=attempt_id,
                experiment_id="linear-regression-final-v1", title="Final pipeline fixture",
                decision_path=decision,
                decision_summary="Operational success is recorded; the scientific finding remains inconclusive.",
                review_path=review,
                review_summary="The synthetic fixture closure is approved for this pipeline test.",
                reviewer_name="Synthetic CI fixture reviewer",
            )

            self.assertEqual(attempt.read_bytes(), attempt_bytes)
            self.assertEqual(_retained_attempt_digests(attempt), retained)
            self.assertEqual(result["parentPackageSha256"], attempt_sha)
            self.assertNotEqual(result["packageSha256"], attempt_sha)
            recipe_path = final.with_name("final.source") / "experiment.json"
            verified = verify_bytes(final.read_bytes(), recipe=load_recipe(recipe_path))
            self.assertEqual(verified["lifecycle"]["creativeWorkStatus"], "Draft")
            self.assertEqual(verified["lifecycle"]["attemptCount"], 1)
            self.assertEqual(verified["scientificReproduction"], "not-run")
            profile_check = subprocess.run(
                [sys.executable, str(ROOT / "scripts/validate_current_ro_profiles.py"),
                 str(recipe_path.parent)], capture_output=True, text=True)
            self.assertEqual(profile_check.returncode, 0, profile_check.stdout + profile_check.stderr)
            with zipfile.ZipFile(final) as archive:
                crate = json_value(archive.read("ro-crate-metadata.json"))
                decision_node = next(node for node in crate["@graph"]
                                     if node.get("@id") == "evidence/scientific-decision.md")
                review_node = next(node for node in crate["@graph"]
                                   if node.get("@id") == "evidence/publication-review.md")
                attempt_member = next(node for node in crate["@graph"]
                                     if node.get("sha256") == attempt_sha)
                self.assertEqual(archive.read(attempt_member["@id"]), attempt_bytes)
                self.assertEqual(review_node["itemReviewed"]["@id"], attempt_member["@id"])
                self.assertEqual(review_node["about"]["@id"], f"#attempt-{attempt_id}")
                self.assertEqual(decision_node["about"]["@id"], f"#attempt-{attempt_id}")
            catalog = describe_catalog(recipe_path.parent)
            interpretation = catalog["experiments"][0]["scientificInterpretation"]
            self.assertEqual(interpretation[0]["summary"],
                             "Operational success is recorded; the scientific finding remains inconclusive.")

    def test_lifecycle_output_failures_preserve_archived_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            attempt, _, _, attempt_id = self._attempt(directory)
            retained = _retained_attempt_digests(attempt)
            parent = {"expected_sha256": retained[attempt], "attempt_id": attempt_id}
            decision = directory / "decision.md"
            decision.write_text("The finding is inconclusive.\n")
            review = directory / "review.md"
            review.write_text("The synthetic fixture is shareable.\n")
            revision = partial(revise_package, attempt, self._revision_recipe(directory), **parent)
            finalization = partial(
                finalize_package, attempt, **parent, experiment_id="linear-regression-final-v1",
                title="Final pipeline fixture", decision_path=decision, decision_summary="Inconclusive.",
                review_path=review, review_summary="Approved for this test.", reviewer_name="Fixture reviewer",
            )

            for name, operation in (("revision", revision), ("finalization", finalization)):
                with self.subTest(operation=name):
                    output = directory / f"{name}.zip"
                    with _fail_package_write(output), self.assertRaisesRegex(OSError, "synthetic package write"):
                        operation(output)
                    self.assertFalse(output.exists())
                    self.assertFalse(output.with_suffix(".source").exists())
                    self.assertEqual(_retained_attempt_digests(attempt), retained)

                    prior_output = b"previous artifact"
                    with _publish_concurrent_output(output, prior_output), self.assertRaises(FileExistsError):
                        operation(output)
                    self.assertEqual(output.read_bytes(), prior_output)
                    self.assertFalse(output.with_suffix(".source").exists())
                    with self.assertRaisesRegex(PackageError, "already exists"):
                        operation(output)
                    self.assertEqual(output.read_bytes(), prior_output)
                    self.assertEqual(_retained_attempt_digests(attempt), retained)

    def test_catalog_rejects_tampered_finalization_source_closure(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            attempt, _, _, attempt_id = self._attempt(directory)
            decision = directory / "decision.md"
            decision.write_text("## Interpretation\nThe fixture finding is inconclusive.\n")
            review = directory / "review.md"
            review.write_text("## Review\nSynthetic evidence is shareable.\n")
            final = directory / "final.zip"
            finalize_package(
                attempt, final, expected_sha256=sha256(attempt.read_bytes()), attempt_id=attempt_id,
                experiment_id="linear-regression-final-v1", title="Final pipeline fixture",
                decision_path=decision, decision_summary="The fixture finding is inconclusive.",
                review_path=review, review_summary="Synthetic evidence is shareable.",
                reviewer_name="Fixture reviewer")
            decision_source = final.with_name("final.source") / "evidence/scientific-decision.md"
            decision_source.write_text("Changed after packaging.\n")
            with self.assertRaisesRegex(PackageError, "Source integrity mismatch"):
                describe_catalog(final.with_name("final.source"))

    def test_finalization_rejects_receipt_action_outcome_conflict(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            attempt, _, _, attempt_id = self._attempt(directory)
            with zipfile.ZipFile(attempt) as archive:
                files = {name: archive.read(name) for name in archive.namelist()}
            receipt_name = next(name for name in files if name.endswith("/runner-receipt.json"))
            receipt = json.loads(files[receipt_name])
            receipt["status"] = "failed"
            receipt["exitCode"] = 17
            receipt["timedOut"] = False
            files[receipt_name] = json.dumps(receipt).encode()
            malformed = directory / "conflicting-attempt.zip"
            with zipfile.ZipFile(malformed, "w") as archive:
                for name, content in files.items():
                    archive.writestr(name, content)
            with self.assertRaisesRegex(PackageError, "conflicts with selected CreateAction"):
                _read_parent_attempt(malformed.read_bytes(), attempt_id)

    def test_attempt_selection_accepts_consistent_failed_and_timed_out_runs(self):
        for status, exit_code, timed_out in (("failed", 17, False), ("timed-out", 124, True)):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                attempt, _, _, attempt_id = self._attempt(directory)
                with zipfile.ZipFile(attempt) as archive:
                    files = {name: archive.read(name) for name in archive.namelist()}
                receipt_name = next(name for name in files if name.endswith("/runner-receipt.json"))
                receipt = json.loads(files[receipt_name])
                receipt.update({"status": status, "exitCode": exit_code, "timedOut": timed_out})
                files[receipt_name] = json.dumps(receipt).encode()
                crate = json.loads(files["ro-crate-metadata.json"])
                action = next(node for node in crate["@graph"]
                              if node.get("@id") == f"#attempt-{attempt_id}")
                action["actionStatus"] = "https://schema.org/FailedActionStatus"
                files["ro-crate-metadata.json"] = json.dumps(crate).encode()
                consistent = directory / "consistent-attempt.zip"
                with zipfile.ZipFile(consistent, "w") as archive:
                    for name, content in files.items():
                        archive.writestr(name, content)
                _, selected_receipt, _ = _read_parent_attempt(consistent.read_bytes(), attempt_id)
                self.assertEqual(selected_receipt["status"], status)

    def test_finalization_rejects_missing_identity_without_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            attempt, _, _, attempt_id = self._attempt(directory)
            decision = directory / "decision.md"
            decision.write_text("An explicit source-authored decision.\n")
            review = directory / "review.md"
            review.write_text("An explicit source-authored review.\n")
            final = directory / "final.zip"
            with self.assertRaisesRegex(PackageError, "Selected attempt ID"):
                finalize_package(
                    attempt, final, expected_sha256=sha256(attempt.read_bytes()),
                    attempt_id="wrong-attempt", experiment_id="linear-regression-final-v1",
                    title="Final pipeline fixture", decision_path=decision,
                    decision_summary="Explicit inconclusive interpretation.", review_path=review,
                    review_summary="Explicit shareability review.", reviewer_name="Fixture reviewer")
            self.assertFalse(final.exists())
            self.assertFalse(final.with_name("final.source").exists())


if __name__ == "__main__":
    unittest.main()
