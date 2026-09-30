"""Real POC parity, generic recipe support and hostile-input regression tests."""
from __future__ import annotations
import copy
import hashlib
import io
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest.mock import patch

from experiment_compiler.catalog import describe_catalog, describe_recipe, discover_recipes
from experiment_compiler.core import (MANIFEST, PackageError, canonical, compile_package,
    json_value, load_recipe, safe_path, sha256, unique_paths, verify_bytes)
from experiment_compiler.resources import bytes_to_mib_minimum, seconds_to_cwl_limit

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/issue-164"
SELF_CONTAINED = ROOT / "examples/linear-regression-v1"
EXPECTED = "45ab246ccfd4ca636e1ad50e8edf068125a111b46b6bcb2b86ac1d811f523341"


class CompilerTests(unittest.TestCase):
    def test_cwl_resource_unit_conversions_are_conservative_and_reject_unknown(self):
        # The smallest valid positive budgets are part of the admission boundary.
        self.assertEqual(bytes_to_mib_minimum(1), 1)
        self.assertEqual(seconds_to_cwl_limit(1), 1)
        self.assertEqual(bytes_to_mib_minimum(1024 * 1024), 1)
        self.assertEqual(bytes_to_mib_minimum(1024 * 1024 + 1), 2)
        self.assertEqual(seconds_to_cwl_limit(30), 30)
        self.assertEqual(seconds_to_cwl_limit(30.01), 31)

        # PackageError text is surfaced directly by the CLI, so preserve the
        # diagnostic category without pinning the entire sentence verbatim.
        for value in (0, -1, True, "unknown"):
            with self.subTest(value=value), self.assertRaisesRegex(PackageError, "positive integer"):
                bytes_to_mib_minimum(value)
        for value in (True, "unknown"):
            with self.subTest(value=value), self.assertRaisesRegex(PackageError, "must be numeric"):
                seconds_to_cwl_limit(value)
        for value in (0, -1, float("inf"), float("nan")):
            with self.subTest(value=value), self.assertRaisesRegex(PackageError, "finite and positive"):
                seconds_to_cwl_limit(value)

    def test_git_lfs_pointer_is_not_accepted_as_payload_bytes(self):
        pointer = (b"version https://git-lfs.github.com/spec/v1\n" +
                   b"oid sha256:" + b"a" * 64 + b"\nsize 4096\n")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(SELF_CONTAINED, root / "example")
            recipe = load_recipe(root / "example/experiment.json")
            item = next(item for item in recipe["members"] if item["path"] == "experiment/data.csv")
            data = root / "example" / item["source"]
            data.write_bytes(pointer)
            item["sha256"] = sha256(pointer)
            item["size"] = len(pointer)
            (root / "example/experiment.json").write_bytes(canonical(recipe))
            with self.assertRaisesRegex(PackageError, "Git LFS pointer is not the referenced payload"):
                compile_package(root / "example/experiment.json", root / "pointer.zip")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        shutil.copytree(EXAMPLE, self.root / "example")
        self.recipe_path = self.root / "example/experiment.json"
        self.output = self.root / "poc.zip"

    def recipe(self):
        return json.loads(self.recipe_path.read_text())

    def save(self, recipe):
        self.recipe_path.write_bytes(canonical(recipe))

    def build(self):
        compile_package(self.recipe_path, self.output)
        return self.output.read_bytes()

    def mutate_zip(self, transform):
        raw = self.build()
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            pairs = [(i, z.read(i)) for i in z.infolist()]
        modified = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(modified, "w") as z:
                for info, data in transform(pairs):
                    z.writestr(info, data)
        return modified.getvalue()

    def test_published_poc_exact_sha_size_manifest_and_rebuild(self):
        raw = self.build()
        self.assertEqual((sha256(raw), len(raw)), (EXPECTED, 15296))
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            self.assertEqual(len(z.infolist()), 9)
            self.assertEqual(z.read(MANIFEST), (EXAMPLE / "reference-manifest.json").read_bytes())
        second = self.root / "second.zip"
        for path in (self.root / "example/inputs").iterdir():
            os.utime(path, (1234567890, 1234567890))
        compile_package(self.recipe_path, second)
        self.assertEqual(raw, second.read_bytes())
        # Same output is idempotent rather than an overwrite/error.
        compile_package(self.recipe_path, self.output)

    def test_offline_build_has_no_subprocess_or_network(self):
        with patch.object(socket, "socket", side_effect=AssertionError("network")), \
             patch.object(subprocess, "Popen", side_effect=AssertionError("execution")):
            result = compile_package(self.recipe_path, self.output)
        self.assertEqual(result["scope"], "package-integrity-only")
        self.assertEqual(result["scientificReproduction"], "not-run")

    def test_generic_second_recipe_has_no_issue164_assumptions(self):
        recipe = self.recipe()
        recipe.pop("expectedPackage")
        recipe["id"] = "tiny-public-example"
        recipe["manifest"]["source"] = {"repository": "example/research", "commit": "a" * 40}
        recipe["manifest"]["evidence"] = {"note": "synthetic packaging fixture, no scientific evidence"}
        content = b"a different public experiment\n"
        (self.recipe_path.parent / "tiny.txt").write_bytes(content)
        recipe["members"] = [{"source": "tiny.txt", "path": "research/README.txt",
                              "sha256": sha256(content), "size": len(content)}]
        self.save(recipe)
        result = verify_bytes(self.build(), recipe=load_recipe(self.recipe_path))
        self.assertEqual(result["source"]["repository"], "example/research")
        self.assertFalse(result["matchedExpectedPackage"])

    def test_compiled_experiment_v1_builds_without_issue164_logic(self):
        output = self.root / "self-contained.zip"
        result = compile_package(SELF_CONTAINED / "experiment.json", output)
        self.assertEqual(result["profile"], "compiled-experiment-v1")
        self.assertEqual(result["source"]["repository"], "pH34r-pH/experiment-compiler")
        checked = verify_bytes(output.read_bytes(), recipe=load_recipe(SELF_CONTAINED / "experiment.json"))
        self.assertEqual(checked["profile"], "compiled-experiment-v1")
        self.assertEqual(checked["memberCount"], 18)

    def lifecycle_fixture(self, *, action_status=None, process_run=False, directory="lifecycle"):
        root = self.root / directory
        root.mkdir()
        files = {
            "README.md": b"# Prospective example\n\n## Hypothesis\nA testable hypothesis.\n",
            "protocol.md": b"# Protocol\n\n## Question\nCan a plan be packaged without a result?\n\n## Method\nNo execution has occurred.\n",
            "runner.py": b"# Example executable identity\n",
        }
        action = {"@id": "#planned-run", "@type": "CreateAction",
                  "actionStatus": "https://schema.org/PotentialActionStatus"}
        graph = [
            {"@id": "ro-crate-metadata.json", "@type": "CreativeWork", "about": {"@id": "./"},
             "conformsTo": {"@id": "https://w3id.org/ro/crate/1.3"}},
            {"@id": "./", "@type": "Dataset", "name": "Prospective example",
             "description": "A plan-only lifecycle fixture.",
             "conformsTo": [{"@id": "https://w3id.org/ro/crate/1.3"}],
             "mainEntity": {"@id": "experiment/protocol.md"},
             "hasPart": [{"@id": "experiment/README.md"}, {"@id": "experiment/protocol.md"},
                         {"@id": "experiment/runner.py"}, {"@id": "dependency-closure.json"}],
             "variableMeasured": [{"@id": "#ram"}, {"@id": "#gpu"}]},
            {"@id": "dependency-closure.json", "@type": "File", "encodingFormat": "application/json"},
            {"@id": "experiment/protocol.md", "@type": ["File", "CreativeWork"], "name": "Prospective protocol",
             "creativeWorkStatus": "Draft", "potentialAction": {"@id": "#planned-run"}},
            action,
            {"@id": "#ram", "@type": "PropertyValue", "propertyID": "CWL ResourceRequirement.ramMin",
             "value": 32, "unitText": "MiB", "measurementTechnique": "conservative headroom; measured peak RSS plus margin",
             "valueReference": {"@id": "https://example.org/receipt#ram"}},
            {"@id": "#gpu", "@type": "PropertyValue", "propertyID": "accelerator VRAM",
             "value": "unknown", "measurementTechnique": "no approved training pilot"},
            {"@id": "experiment/README.md", "@type": "File", "encodingFormat": "text/markdown"},
            {"@id": "experiment/runner.py", "@type": ["File", "SoftwareSourceCode"],
             "programmingLanguage": "Python"},
        ]
        if process_run:
            graph[1]["conformsTo"].append({"@id": "https://w3id.org/ro/wfrun/process/0.6"})
            graph.append({"@id": "https://w3id.org/ro/wfrun/process/0.6",
                          "@type": ["CreativeWork", "Profile"], "version": "0.6"})
            graph.append({"@id": "#attempt-1", "@type": "CreateAction",
                          "actionStatus": action_status or "https://schema.org/CompletedActionStatus",
                          "instrument": {"@id": "experiment/runner.py"}})
        context = ["https://w3id.org/ro/crate/1.3/context"]
        if process_run:
            context.append("https://w3id.org/ro/terms/workflow-run/context")
        files["ro-crate-metadata.json"] = canonical({"@context": context,
            "@graph": graph})
        files["dependency-closure.json"] = canonical({"classifications": {
            "embedded": [{"item": "runner", "path": "experiment/runner.py"}],
            "external": [], "unavailable": [{"item": "frozen checkpoint weights", "id": "missing/checkpoint.bin"}]}})
        (root / "experiment").mkdir()
        members = []
        for name, content, path in (
            ("experiment/README.md", files["README.md"], "experiment/README.md"),
            ("experiment/protocol.md", files["protocol.md"], "experiment/protocol.md"),
            ("experiment/runner.py", files["runner.py"], "experiment/runner.py"),
            ("ro-crate-metadata.json", files["ro-crate-metadata.json"], "ro-crate-metadata.json"),
            ("dependency-closure.json", files["dependency-closure.json"], "dependency-closure.json"),
        ):
            if path != "ro-crate-metadata.json":
                (root / path).write_bytes(content)
            members.append({"source": name, "path": path, "sha256": sha256(content), "size": len(content)})
        (root / "ro-crate-metadata.json").write_bytes(files["ro-crate-metadata.json"])
        recipe = {
            "buildRecipeVersion": 1, "id": "prospective-example", "title": "Prospective example",
            "profile": "compiled-experiment-lifecycle-v1",
            "manifest": {"schemaVersion": 2, "packageType": "compiled-experiment",
                         "profile": "compiled-experiment-lifecycle-v1",
                         "source": {"repository": "example/research", "commit": "a" * 40},
                         "standards": {"roCrate": "1.3", "processRunCrate": "0.6"}, "evidence": {}},
            "members": members,
        }
        path = root / "experiment.json"
        path.write_bytes(canonical(recipe))
        return path, recipe, graph

    def test_lifecycle_plan_builds_without_result_or_process_run_claim(self):
        recipe_path, _, _ = self.lifecycle_fixture()
        output = self.root / "plan.zip"
        result = compile_package(recipe_path, output)
        self.assertEqual(result["profile"], "compiled-experiment-lifecycle-v1")
        self.assertEqual(result["lifecycle"], {"creativeWorkStatus": "Draft", "attemptCount": 0,
                         "processRunCrate": False, "potentialActionCount": 1,
                         "resourceMeasurements": [
                             {"propertyID": "CWL ResourceRequirement.ramMin", "value": 32, "unitText": "MiB",
                              "measurementTechnique": "conservative headroom; measured peak RSS plus margin",
                              "evidence": {"@id": "https://example.org/receipt#ram"}},
                             {"propertyID": "accelerator VRAM", "value": "unknown", "unitText": None,
                              "measurementTechnique": "no approved training pilot", "evidence": None}]})
        self.assertEqual(result["scope"], "package-integrity-only")
        self.assertEqual(result["scientificReproduction"], "not-run")
        self.assertIsNone(describe_recipe(recipe_path)["scientificInterpretation"])
        self.assertEqual(describe_catalog(recipe_path.parent)["experiments"][0]["lifecycle"]["attemptCount"], 0)

    def test_source_owned_comparison_failures_remain_integrity_valid_evidence(self):
        # Illustrative source reports, not a compiler-defined comparison schema.
        cases = (
            ("mismatched-reference", "Completed", "succeeded", "inconclusive",
             {"selectedSamples": [1, 2], "referenceSamples": [3, 4]}),
            ("failed-check", "Completed", "succeeded", "negative",
             {"checkPassed": False, "observedError": 0.4, "maximumError": 0.1}),
            ("missing-reference", "Completed", "succeeded", "inconclusive",
             {"reference": None, "omission": "reference result unavailable"}),
            ("non-comparable-reference", "Completed", "succeeded", "inconclusive",
             {"endpointUnit": "error/sample", "referenceUnit": "error/batch"}),
            ("failed-attempt", "Failed", "failed", "inconclusive",
             {"exitCode": 9, "checkPassed": None}),
            ("cancelled-attempt", "Failed", "cancelled", "inconclusive",
             {"terminationReason": "source operator cancelled", "checkPassed": None}),
        )
        for name, action_status, receipt_status, decision, evidence in cases:
            with self.subTest(case=name):
                recipe_path, recipe, graph = self.lifecycle_fixture(
                    directory=name, action_status=f"https://schema.org/{action_status}ActionStatus",
                    process_run=True)
                summary = f"{decision}: {name}; no overall winner."
                payloads = {
                    "evidence/comparison.json": canonical({"decision": decision, "winner": None,
                        "denominator": "declared selected samples", **evidence}),
                    "evidence/attempt.json": canonical({"status": receipt_status}),
                    "evidence/interpretation.md": (summary + "\n").encode(),
                }
                for path, content in payloads.items():
                    target = recipe_path.parent / path
                    target.parent.mkdir(exist_ok=True)
                    target.write_bytes(content)
                    recipe["members"].append({"source": path, "path": path,
                        "sha256": sha256(content), "size": len(content)})
                    graph[1]["hasPart"].append({"@id": path})
                    graph.append({"@id": path, "@type": "File"})
                interpretation = graph[-1]
                interpretation.update({"@type": ["File", "CreativeWork"],
                    "name": "Scientific decision and interpretation", "abstract": summary,
                    "about": {"@id": "#attempt-1"}})
                attempt = next(node for node in graph if node["@id"] == "#attempt-1")
                attempt["result"] = [{"@id": path} for path in payloads]
                metadata = recipe_path.parent / "ro-crate-metadata.json"
                metadata.write_bytes(canonical({"@context": ["https://w3id.org/ro/crate/1.3/context",
                    "https://w3id.org/ro/terms/workflow-run/context"], "@graph": graph}))
                recipe["members"][3].update(sha256=sha256(metadata.read_bytes()),
                                            size=metadata.stat().st_size)
                recipe_path.write_bytes(canonical(recipe))
                output = self.root / f"{name}.zip"
                compile_package(recipe_path, output)
                verified = verify_bytes(output.read_bytes(), recipe=load_recipe(recipe_path))
                self.assertEqual(verified["scope"], "package-integrity-only")
                self.assertEqual(verified["scientificReproduction"], "not-run")
                with zipfile.ZipFile(output) as archive:
                    for path, content in payloads.items():
                        self.assertEqual(archive.read(path), content)
                record = describe_recipe(recipe_path)
                self.assertEqual(record["scientificInterpretation"], [{
                    "record": "evidence/interpretation.md", "summary": summary,
                    "aboutAttempt": "#attempt-1"}])
                self.assertEqual(record["executionAttempts"], [{"id": "#attempt-1",
                    "actionStatus": f"https://schema.org/{action_status}ActionStatus",
                    "result": list(payloads)}])
                self.assertNotIn("scientificAcceptance", record)
                self.assertNotIn("winner", record)

    def test_lifecycle_catalog_shows_external_prerequisite_only_with_digest_size_and_license(self):
        recipe_path, recipe, graph = self.lifecycle_fixture()
        closure_path = recipe_path.parent / "dependency-closure.json"
        closure = {"classifications": {"embedded": [], "external": [{
            "item": "frozen checkpoint", "id": "https://data.example.org/frozen.npz",
            "contentUrl": "https://data.example.org/frozen.npz", "sha256": "a" * 64,
            "contentSize": 4096, "license": "https://creativecommons.org/licenses/by/4.0/"}], "unavailable": []}}
        closure_path.write_bytes(canonical(closure))
        recipe["members"][4]["sha256"] = sha256(closure_path.read_bytes())
        recipe["members"][4]["size"] = closure_path.stat().st_size
        recipe_path.write_bytes(canonical(recipe))
        record = describe_catalog(recipe_path.parent)["experiments"][0]
        self.assertEqual(record["contents"]["external"][0]["id"], "https://data.example.org/frozen.npz")
        self.assertEqual(record["unavailablePrerequisites"], [])

    def test_catalog_keeps_real_protocol_shape_and_exposes_full_source(self):
        recipe_path, recipe, _ = self.lifecycle_fixture(directory="real-protocol-shape")
        protocol = ("# Illustrative evaluation study protocol\n\n"
                    "## Preregistered endpoints\nMeasure held-out error per selected sample.\n\n"
                    "## Frozen comparison denominator\nUse the declared reference sample set; missing evidence is inconclusive.\n")
        member = next(item for item in recipe["members"] if item["path"] == "experiment/protocol.md")
        (recipe_path.parent / member["source"]).write_text(protocol)
        member.update(sha256=sha256(protocol.encode()), size=len(protocol.encode()))
        recipe_path.write_bytes(canonical(recipe))
        record = describe_recipe(recipe_path)
        self.assertIsNone(record["question"])
        self.assertIsNone(record["method"])
        self.assertEqual(record["protocol"], {"record": "experiment/protocol.md", "text": protocol})

    def test_catalog_projects_attempt_status_without_scientific_verdict(self):
        for status in ("Active", "Failed", "Completed"):
            recipe_path, _, _ = self.lifecycle_fixture(
                directory=f"catalog-attempt-{status}",
                action_status=f"https://schema.org/{status}ActionStatus", process_run=True)
            record = describe_recipe(recipe_path)
            self.assertEqual(len(record["executionAttempts"]), 1)
            self.assertEqual(record["executionAttempts"][0]["actionStatus"],
                             f"https://schema.org/{status}ActionStatus")
            self.assertIsNone(record["scientificInterpretation"])
            self.assertNotIn("scientificAcceptance", record)
            self.assertNotIn("scientificReproduction", record)
        recipe_path, _, _ = self.lifecycle_fixture(directory="catalog-prospective")
        self.assertEqual(describe_recipe(recipe_path)["executionAttempts"], [])

    def test_catalog_rejects_unretained_or_ambiguous_attempt_results(self):
        for index, result in enumerate((
                {"@id": "https://example.org/unretained.json"},
                {"@id": "#ram"},
                [{"@id": "experiment/README.md"}, {"@id": "experiment/README.md"}],
                "experiment/README.md")):
            recipe_path, recipe, graph = self.lifecycle_fixture(
                directory=f"catalog-invalid-result-{index}",
                action_status="https://schema.org/CompletedActionStatus", process_run=True)
            action = next(node for node in graph if node.get("actionStatus") ==
                          "https://schema.org/CompletedActionStatus")
            action["result"] = result
            metadata = recipe_path.parent / "ro-crate-metadata.json"
            metadata.write_bytes(canonical({"@context": ["https://w3id.org/ro/crate/1.3/context",
                "https://w3id.org/ro/terms/workflow-run/context"], "@graph": graph}))
            recipe["members"][3]["sha256"] = sha256(metadata.read_bytes())
            recipe["members"][3]["size"] = metadata.stat().st_size
            recipe_path.write_bytes(canonical(recipe))
            with self.subTest(result=result), self.assertRaisesRegex(PackageError, "Catalog execution result"):
                describe_recipe(recipe_path)

    def test_lifecycle_rejects_unknown_resource_encoded_as_zero_or_with_unit(self):
        for index, measurement in enumerate((
                {"@id": "#x", "@type": "PropertyValue", "propertyID": "RAM", "value": 0,
                 "unitText": "MiB", "measurementTechnique": "unknown"},
                {"@id": "#x", "@type": "PropertyValue", "propertyID": "VRAM", "value": "unknown",
                 "unitText": "MiB", "measurementTechnique": "not measured"})):
            recipe_path, recipe, graph = self.lifecycle_fixture(directory=f"lifecycle-invalid-resource-{index}")
            graph[1]["variableMeasured"] = [{"@id": "#x"}]
            graph.append(measurement)
            metadata = recipe_path.parent / "ro-crate-metadata.json"
            metadata.write_bytes(canonical({"@context": ["https://w3id.org/ro/crate/1.3/context"], "@graph": graph}))
            recipe["members"][3]["sha256"] = sha256(metadata.read_bytes())
            recipe["members"][3]["size"] = metadata.stat().st_size
            recipe_path.write_bytes(canonical(recipe))
            with self.subTest(value=measurement["value"]), self.assertRaises(PackageError):
                compile_package(recipe_path, self.root / f"invalid-resource-{measurement['propertyID']}.zip")

    def test_lifecycle_execution_requires_explicit_action_status_and_run_profile(self):
        recipe_path, recipe, graph = self.lifecycle_fixture(
            action_status="https://schema.org/FailedActionStatus", process_run=True)
        graph.append({"@id": "#attempt-2", "@type": "CreateAction",
                      "actionStatus": "https://schema.org/FailedActionStatus",
                      "instrument": {"@id": "experiment/runner.py"}})
        metadata = recipe_path.parent / "ro-crate-metadata.json"
        metadata.write_bytes(canonical({"@context": ["https://w3id.org/ro/crate/1.3/context",
            "https://w3id.org/ro/terms/workflow-run/context"], "@graph": graph}))
        recipe["members"][3]["sha256"] = sha256(metadata.read_bytes())
        recipe["members"][3]["size"] = metadata.stat().st_size
        recipe_path.write_bytes(canonical(recipe))
        # Failed process provenance is valid package evidence, but integrity is
        # not converted into a scientific negative or a successful reproduction.
        output = self.root / "failed-attempt.zip"
        result = compile_package(recipe_path, output)
        self.assertEqual(result["lifecycle"]["attemptCount"], 2)
        self.assertEqual(result["lifecycle"]["processRunCrate"], True)
        self.assertIsNone(describe_recipe(recipe_path)["scientificInterpretation"])

        graph[-1].pop("actionStatus")
        metadata = recipe_path.parent / "ro-crate-metadata.json"
        metadata.write_bytes(canonical({"@context": ["https://w3id.org/ro/crate/1.3/context",
            "https://w3id.org/ro/terms/workflow-run/context"], "@graph": graph}))
        recipe["members"][3]["sha256"] = sha256(metadata.read_bytes())
        recipe["members"][3]["size"] = metadata.stat().st_size
        recipe_path.write_bytes(canonical(recipe))
        with self.assertRaises(PackageError):
            compile_package(recipe_path, self.root / "invalid-attempt.zip")

    def test_prospective_crate_cannot_claim_process_run_profile_without_attempt(self):
        recipe_path, recipe, graph = self.lifecycle_fixture()
        graph[1]["conformsTo"] = {"@id": "https://w3id.org/ro/wfrun/process/0.6"}
        metadata = recipe_path.parent / "ro-crate-metadata.json"
        metadata.write_bytes(canonical({"@context": ["https://w3id.org/ro/crate/1.3/context",
            "https://w3id.org/ro/terms/workflow-run/context"], "@graph": graph}))
        recipe["members"][3]["sha256"] = sha256(metadata.read_bytes())
        recipe["members"][3]["size"] = metadata.stat().st_size
        recipe_path.write_bytes(canonical(recipe))
        with self.assertRaises(PackageError):
            compile_package(recipe_path, self.root / "false-run-claim.zip")

    def test_lifecycle_rejects_execution_with_undescribed_instrument(self):
        recipe_path, recipe, graph = self.lifecycle_fixture(
            action_status="https://schema.org/ActiveActionStatus", process_run=True)
        graph[-1]["instrument"] = {"@id": "missing/runner.py"}
        metadata = recipe_path.parent / "ro-crate-metadata.json"
        metadata.write_bytes(canonical({"@context": ["https://w3id.org/ro/crate/1.3/context",
            "https://w3id.org/ro/terms/workflow-run/context"], "@graph": graph}))
        recipe["members"][3]["sha256"] = sha256(metadata.read_bytes())
        recipe["members"][3]["size"] = metadata.stat().st_size
        recipe_path.write_bytes(canonical(recipe))
        with self.assertRaises(PackageError):
            compile_package(recipe_path, self.root / "invalid-instrument.zip")

    def test_catalog_metadata_is_derived_from_authoritative_artifacts(self):
        descriptor = describe_recipe(SELF_CONTAINED / "experiment.json")
        self.assertEqual(descriptor["title"], "Self-contained stdlib linear-regression reproduction")
        self.assertIn("32 full-batch gradient-descent updates", descriptor["hypothesis"])
        self.assertEqual(descriptor["package"]["sha256"], "251a43f8719a17bb0898a5e5e51f4d8c22f9280b29febadd72d98ffbba20e544")
        self.assertEqual(descriptor["resources"]["ram"]["planningRamBytes"], 33554432)
        self.assertEqual(descriptor["resources"]["accelerator"]["peakVramBytes"], 0)
        self.assertEqual(descriptor["reproduction"]["entrypoint"], "experiment/reproduce.py")
        self.assertEqual(descriptor["contents"]["unavailable"], [])

    def test_catalog_discovers_plan_and_published_package_without_registry(self):
        recipes = discover_recipes(ROOT / "examples")
        self.assertEqual(recipes, [ROOT / "examples/linear-regression-frozen-lifecycle-v1/experiment.json",
                                   ROOT / "examples/linear-regression-plan-v1/experiment.json",
                                   SELF_CONTAINED / "experiment.json",
                                   ROOT / "examples/muon-comparison-plan-v1/experiment.json",
                                   ROOT / "examples/muon-unit-hypersphere-depth3-multiseed-v1-final-87409154/experiment.json"])
        catalog = describe_catalog(ROOT / "examples")
        self.assertEqual(catalog["schemaVersion"], 2)
        self.assertEqual([item["id"] for item in catalog["experiments"]],
                         ["linear-regression-frozen-lifecycle-v1", "linear-regression-plan-v1",
                          "stdlib-linear-regression-v1-compiled-experiment", "muon-comparison-plan-v1",
                          "muon-unit-hypersphere-depth3-multiseed-v1-final-87409154"])
        self.assertIsNone(catalog["experiments"][1]["package"])
        self.assertEqual(catalog["experiments"][1]["lifecycle"]["creativeWorkStatus"], "Draft")
        self.assertEqual(catalog["experiments"][2]["package"]["sha256"],
                         "251a43f8719a17bb0898a5e5e51f4d8c22f9280b29febadd72d98ffbba20e544")
        muon = catalog["experiments"][3]
        self.assertEqual(muon["lifecycle"]["creativeWorkStatus"], "Draft")
        self.assertEqual(muon["lifecycle"]["attemptCount"], 0)
        self.assertEqual(muon["lifecycle"]["potentialActionCount"], 1)
        self.assertFalse(muon["lifecycle"]["processRunCrate"])
        self.assertTrue(muon["unavailablePrerequisites"])
        self.assertTrue(all(item["value"] == "unknown" for item in muon["lifecycle"]["resourceMeasurements"]))
        published = catalog["experiments"][4]
        self.assertEqual(published["package"]["sha256"],
                         "28d2d6c6dba2ff2370b9536c4428f40dd23de4ea42a26c98f3e8225b4dd9a8c4")
        self.assertEqual(published["backlinks"], [{
            "title": "Milestone 005 — The unit-hypersphere anomaly (later comparison, not a replay)",
            "url": "https://tyharbin.com/articles/005-unit-hypersphere-anomaly/",
            "sourceCommit": "afbdf454b6550189af6d617754d53bbb42a7d6c2",
        }])
        self.assertEqual(published["lifecycle"]["creativeWorkStatus"], "Draft")
        self.assertEqual(len(published["scientificInterpretation"]), 1)
        attempt = published["executionAttempts"][0]
        self.assertEqual(attempt["id"], "#attempt-87409154e60d")
        self.assertEqual(attempt["actionStatus"], "https://schema.org/CompletedActionStatus")
        self.assertIn("evidence/attempts/87409154e60d/pilot-result.json", attempt["result"])
        self.assertIn("no overall winner or cost claim", published["scientificInterpretation"][0]["summary"])
        self.assertEqual(published["protocol"]["record"], "experiment/protocol.md")

    def test_related_article_backlink_is_pinned_in_the_authoritative_recipe(self):
        with tempfile.TemporaryDirectory() as directory:
            example = Path(directory) / "example"
            shutil.copytree(SELF_CONTAINED, example)
            recipe_path = example / "experiment.json"
            recipe = json.loads(recipe_path.read_text())
            backlink = {"title": "Accessible does not imply used",
                        "url": "https://tyharbin.com/articles/accessible-does-not-imply-used/",
                        "sourceCommit": "c" * 40}
            recipe["relatedArticles"] = [backlink]
            recipe_path.write_bytes(canonical(recipe))
            descriptor = describe_recipe(recipe_path)
            self.assertEqual(descriptor["backlinks"], [backlink])
            receipt = compile_package(recipe_path, Path(directory) / "article-linked.zip")
            self.assertEqual(receipt["packageSha256"], recipe["expectedPackage"]["sha256"])

            recipe["relatedArticles"][0]["url"] = "https://example.org/article"
            recipe_path.write_bytes(canonical(recipe))
            with self.assertRaisesRegex(PackageError, "canonical tyharbin.com article route"):
                load_recipe(recipe_path)

    def test_source_tampering_fails_before_output(self):
        (self.recipe_path.parent / "inputs/VALIDATION.md").write_text("tampered")
        with self.assertRaises(PackageError):
            self.build()
        self.assertFalse(self.output.exists())

    def test_missing_source_fails(self):
        (self.recipe_path.parent / "inputs/VALIDATION.md").unlink()
        with self.assertRaises(PackageError):
            self.build()

    def test_source_symlink_fails(self):
        path = self.recipe_path.parent / "inputs/VALIDATION.md"
        target = self.root / "outside"
        path.rename(target)
        path.symlink_to(target)
        with self.assertRaises(PackageError):
            self.build()

    def test_source_parent_symlink_fails(self):
        path = self.recipe_path.parent / "inputs"
        target = self.root / "outside-directory"
        path.rename(target)
        path.symlink_to(target, target_is_directory=True)
        with self.assertRaises(PackageError):
            self.build()

    def test_recipe_rejects_paths_and_duplicate_inventory(self):
        for value in ("../outside", "/absolute", "C:/absolute", "a\\b", "x\x00y", "a//b", "a/./b", "CON"):
            with self.subTest(value=value), self.assertRaises(PackageError):
                safe_path(value)
        for names in (["a", "A"], ["a", "a/b"], ["a", "a"]):
            with self.subTest(names=names), self.assertRaises(PackageError):
                unique_paths(names)

    def test_recipe_rejects_unsupported_schema_and_extra_fields(self):
        for key, value in (("profile", "imaginary-v2"), ("buildRecipeVersion", True), ("arbitraryCommand", "run this")):
            recipe = self.recipe()
            recipe[key] = value
            self.save(recipe)
            with self.subTest(key=key), self.assertRaises(PackageError):
                load_recipe(self.recipe_path)
            shutil.copyfile(EXAMPLE / "experiment.json", self.recipe_path)

    def test_json_duplicate_keys_and_nonfinite_numbers_fail(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b'bad json'):
            with self.subTest(raw=raw), self.assertRaises(PackageError):
                json_value(raw)

    def test_bad_expected_digest_fails_without_writing(self):
        recipe = self.recipe()
        recipe["expectedPackage"]["sha256"] = "0" * 64
        self.save(recipe)
        with self.assertRaises(PackageError):
            self.build()
        self.assertFalse(self.output.exists())

    def test_output_is_never_silently_replaced(self):
        self.output.write_bytes(b"keep me")
        with self.assertRaises(PackageError):
            self.build()
        self.assertEqual(self.output.read_bytes(), b"keep me")

    def test_verifier_distinguishes_external_digest_from_self_consistency(self):
        raw = self.build()
        self.assertFalse(verify_bytes(raw)["matchedExpectedPackage"])
        self.assertTrue(verify_bytes(raw, expected_sha256=EXPECTED)["matchedExpectedPackage"])
        with self.assertRaises(PackageError):
            verify_bytes(raw, expected_sha256="0" * 64)

    def test_verifier_rejects_changed_member(self):
        raw = self.mutate_zip(lambda pairs: [(i, b"changed" if i.filename.endswith("VALIDATION.md") else b) for i, b in pairs])
        with self.assertRaises(PackageError):
            verify_bytes(raw)

    def test_verifier_rejects_missing_member(self):
        raw = self.mutate_zip(lambda pairs: pairs[:-1])
        with self.assertRaises(PackageError):
            verify_bytes(raw)

    def test_verifier_rejects_unlisted_member(self):
        raw = self.mutate_zip(lambda pairs: pairs + [(zipfile.ZipInfo("unlisted.txt"), b"extra")])
        with self.assertRaises(PackageError):
            verify_bytes(raw)

    def test_verifier_rejects_duplicate_zip_member(self):
        raw = self.mutate_zip(lambda pairs: pairs + [pairs[-1]])
        with self.assertRaises(PackageError):
            verify_bytes(raw)

    def test_verifier_rejects_traversal_and_symlink(self):
        for name, mode in (("../outside", stat.S_IFREG), ("link", stat.S_IFLNK)):
            self.output.unlink(missing_ok=True)
            info = zipfile.ZipInfo(name)
            info.external_attr = (mode | 0o644) << 16
            raw = self.mutate_zip(lambda pairs: pairs + [(info, b"target")])
            with self.subTest(name=name), self.assertRaises(PackageError):
                verify_bytes(raw)

    def test_verifier_rejects_empty_manifest_inventory(self):
        def change(pairs):
            result = []
            for info, content in pairs:
                if info.filename == MANIFEST:
                    manifest = json.loads(content)
                    manifest["members"] = []
                    content = canonical(manifest)
                result.append((info, content))
            return result
        with self.assertRaises(PackageError):
            verify_bytes(self.mutate_zip(change))

    def test_verifier_checks_recipe_header_not_just_inventory(self):
        raw = self.build()
        recipe = load_recipe(self.recipe_path)
        recipe.pop("expectedPackage")
        recipe["manifest"]["source"]["commit"] = "b" * 40
        with self.assertRaises(PackageError):
            verify_bytes(raw, recipe=recipe)

    def test_verifier_rejects_size_limits_and_malformed_zip(self):
        raw = self.build()
        with patch("experiment_compiler.core.MAX_FILE", 100), self.assertRaises(PackageError):
            verify_bytes(raw)
        with patch("experiment_compiler.core.MAX_TOTAL", 100), self.assertRaises(PackageError):
            verify_bytes(raw)
        with self.assertRaises(PackageError):
            verify_bytes(b"not a zip")

    def test_cli_build_verify_and_clean_error(self):
        command = [sys.executable, "-m", "experiment_compiler"]
        result = subprocess.run(command + ["compile", str(self.recipe_path), "--output", str(self.output)],
                                cwd=ROOT, check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout)["packageSha256"], EXPECTED)
        result = subprocess.run(command + ["verify", str(self.output), "--recipe", str(self.recipe_path)],
                                cwd=ROOT, check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout)["status"], "integrity-verified")
        result = subprocess.run(command + ["verify", str(self.output), "--expected-sha256", "bad"],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
