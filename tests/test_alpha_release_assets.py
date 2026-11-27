"""The signing step admits only the exact reviewed alpha asset set."""
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from experiment_compiler.core import PackageError, canonical, compile_package
from scripts.stage_alpha_release_assets import PACKAGE, RECEIPTS, ROOT, stage_assets


class AlphaReleaseAssetsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.source = Path(directory.name) / "dist"
        self.source.mkdir()
        self.candidate = self.source.parent / "candidate"
        self.wheel = self.source / "experiment_compiler-0.1.0a1-py3-none-any.whl"
        with ZipFile(self.wheel, "w") as archive:
            archive.writestr("experiment_compiler-0.1.0a1.dist-info/METADATA",
                             "Name: experiment-compiler\nVersion: 0.1.0a1\n")
        self.receipt = compile_package(ROOT / "examples/issue-164/experiment.json", self.source / PACKAGE)
        for name in RECEIPTS:
            (self.source / name).write_bytes(canonical(self.receipt))
        (self.source / "SHA256SUMS").write_bytes(f"{self.receipt['packageSha256']}  dist/{PACKAGE}\n".encode())

    def test_stages_five_exact_assets_out_of_the_full_build_output(self):
        (self.source / "separate-experiment.zip").write_bytes(b"separate payload")
        stage_assets(self.source, self.candidate)
        self.assertEqual({p.name for p in self.candidate.iterdir()}, {PACKAGE, self.wheel.name, "SHA256SUMS", *RECEIPTS})
        for asset in self.candidate.iterdir():
            self.assertEqual(asset.read_bytes(), (self.source / asset.name).read_bytes())

    def test_rejects_unmatched_claim_receipt(self):
        (self.source / RECEIPTS[1]).write_bytes(canonical(self.receipt | {"scientificReproduction": "matched"}))
        with self.assertRaisesRegex(PackageError, "Scoped package verification"):
            stage_assets(self.source, self.candidate)
        self.assertFalse(self.candidate.exists())

    def test_rejects_wrong_wheel_metadata_inside_expected_filename(self):
        with ZipFile(self.wheel, "w") as archive:
            archive.writestr("experiment_compiler-0.1.0a1.dist-info/METADATA",
                             "Name: experiment-compiler\nVersion: 0.1.0a2\n")
        with self.assertRaisesRegex(PackageError, "Wheel metadata differs"):
            stage_assets(self.source, self.candidate)
        self.assertFalse(self.candidate.exists())
