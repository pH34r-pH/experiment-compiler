import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from docs_hygiene import changed_files, check_paths


class DocumentationHygieneTests(unittest.TestCase):
    def test_cli_code_only_paths_emit_no_markdown_and_still_run_guard(self):
        script = Path(__file__).with_name("docs_hygiene.py")
        selected = subprocess.run(
            [sys.executable, str(script), "--paths", "src/module.py", "--list-living-docs"],
            check=True, text=True, stdout=subprocess.PIPE,
        )
        self.assertEqual("", selected.stdout)
        guarded = subprocess.run(
            [sys.executable, str(script), "--paths", "src/module.py"],
            check=True, text=True, stdout=subprocess.PIPE,
        )
        self.assertIn("1 changed path(s)", guarded.stdout)

    def test_cache_is_rejected_under_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertTrue(check_paths(root, ["examples/study/evidence/cache/result.json"]))

    def test_feature_records_under_build_are_allowed_but_build_output_is_not(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for record in ("plan.md", "tests.md", "tasks.md"):
                self.assertEqual([], check_paths(root, [f"build/archive-read-policy/{record}"]))
            for rejected in ("build/lib/experiment_compiler/core.py", "build/archive-read-policy/notes.md",
                             "build/archive-read-policy/cache/plan.md", "build/plan.md", "build/Bad_Name/plan.md"):
                self.assertTrue(check_paths(root, [rejected]), rejected)

    def test_issue_number_only_living_name_is_rejected_and_architecture_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual([], check_paths(root, ["docs/architecture.md"], {"docs/architecture.md"}))
            self.assertTrue(check_paths(root, ["docs/issue-123.md"], {"docs/issue-123.md"}))

    def test_git_rename_destination_is_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.run(["git", *args], cwd=root, check=True,
                                      text=True, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE).stdout.strip()
            git("init", "--quiet")
            git("config", "user.email", "docs@example.invalid")
            git("config", "user.name", "Docs Test")
            (root / "docs").mkdir()
            (root / "docs/guide.md").write_text("# Guide\n", encoding="utf-8")
            git("add", "docs/guide.md")
            git("commit", "--quiet", "-m", "initial")
            base = git("rev-parse", "HEAD")
            git("mv", "docs/guide.md", "docs/issue-123.md")
            git("commit", "--quiet", "-m", "rename")
            changed, new_paths = changed_files(root, base)
            self.assertIn("docs/issue-123.md", changed)
            self.assertIn("docs/issue-123.md", new_paths)
            self.assertTrue(check_paths(root, changed, new_paths))


if __name__ == "__main__":
    unittest.main()
