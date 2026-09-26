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

from experiment_compiler.core import (MANIFEST, PackageError, canonical, compile_package,
    json_value, load_recipe, safe_path, sha256, unique_paths, verify_bytes)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/issue-164"
SELF_CONTAINED = ROOT / "examples/linear-regression-v1"
EXPECTED = "45ab246ccfd4ca636e1ad50e8edf068125a111b46b6bcb2b86ac1d811f523341"


class CompilerTests(unittest.TestCase):
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
