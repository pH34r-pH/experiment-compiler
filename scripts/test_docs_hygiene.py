import subprocess
import tempfile
import unittest
from pathlib import Path

from docs_hygiene import changed_files, check_paths


class DocumentationHygieneTests(unittest.TestCase):
    def test_cache_is_rejected_under_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertTrue(check_paths(root, ["examples/study/evidence/cache/result.json"]))

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
