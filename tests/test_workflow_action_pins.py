"""Check that repository-owned workflows and actions use immutable remote references."""
import re
import unittest
from pathlib import Path

from fixture_requirements import REQUIRES_RUNNER

GITHUB = Path(__file__).resolve().parents[1] / ".github"
REMOTE_ACTION = re.compile(r"[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+(?:/[^@\s/]+)*@[0-9a-f]{40}")
DOCKER_IMAGE = re.compile(r"docker://[^@\s]+@sha256:[0-9a-f]{64}")


def immutable_reference(reference: str) -> bool:
    return isinstance(reference, str) and (
        reference.startswith("./") or REMOTE_ACTION.fullmatch(reference) is not None
        or DOCKER_IMAGE.fullmatch(reference) is not None
    )


def step_references(steps, parent):
    for index, step in enumerate(steps, start=1):
        if "uses" in step:
            yield f"{parent}.steps[{index}].uses", step["uses"]


def workflow_references(workflow):
    for name, job in workflow["jobs"].items():
        location = f"jobs.{name}"
        if "uses" in job:
            yield f"{location}.uses", job["uses"]
        yield from step_references(job.get("steps", []), location)


def action_references(action):
    runs = action["runs"]
    if runs["using"] == "composite":
        yield from step_references(runs["steps"], "runs")
    if runs["using"] == "docker" and runs["image"].startswith("docker://"):
        yield "runs.image", runs["image"]


class WorkflowActionPinsTests(unittest.TestCase):
    def test_local_actions_commit_shas_and_image_digests_are_admitted(self):
        for reference in (
            "./.github/actions/compile",
            "./.github/workflows/shared.yml",
            "actions/checkout@" + "a" * 40,
            "owner/repo/.github/workflows/reusable.yml@" + "b" * 40,
            "docker://ghcr.io/owner/image@sha256:" + "a" * 64,
        ):
            with self.subTest(reference=reference):
                self.assertTrue(immutable_reference(reference))

    def test_unpinned_refs_and_partial_digests_are_rejected(self):
        for reference in (
            "actions/checkout@v4",
            "owner/repo/actions/build@main",
            "owner/repo/.github/workflows/reusable.yml@v2",
            "actions/checkout@" + "a" * 39,
            "docker://ghcr.io/owner/image:latest",
            "docker://ghcr.io/owner/image@sha256:" + "a" * 63,
        ):
            with self.subTest(reference=reference):
                self.assertFalse(immutable_reference(reference))

    def test_finds_workflow_calls_steps_and_composite_steps(self):
        workflow = {
            "jobs": {
                "call": {"uses": "owner/repo/.github/workflows/shared.yml@main"},
                "build": {"steps": [
                    {"uses": "./.github/actions/compile", "with": {"uses": "input data"}},
                    {"run": "echo uses: example only"},
                ]},
            },
        }
        composite = {"runs": {"using": "composite", "steps": [{"uses": "actions/checkout@v4"}]}}
        docker = {"runs": {"using": "docker", "image": "docker://ghcr.io/owner/image:latest"}}
        self.assertEqual(list(workflow_references(workflow)), [
            ("jobs.call.uses", "owner/repo/.github/workflows/shared.yml@main"),
            ("jobs.build.steps[1].uses", "./.github/actions/compile"),
        ])
        self.assertEqual(list(action_references(composite)), [("runs.steps[1].uses", "actions/checkout@v4")])
        self.assertEqual(list(action_references(docker)), [("runs.image", "docker://ghcr.io/owner/image:latest")])
        self.assertEqual(list(action_references({"runs": {"using": "docker", "image": "Dockerfile"}})), [])

    @REQUIRES_RUNNER
    def test_repository_workflows_and_actions_pin_remote_code(self):
        import yaml

        workflows = sorted((GITHUB / "workflows").glob("*.yml")) + sorted((GITHUB / "workflows").glob("*.yaml"))
        actions = sorted((GITHUB / "actions").rglob("action.yml")) + sorted((GITHUB / "actions").rglob("action.yaml"))
        for files, references in ((workflows, workflow_references), (actions, action_references)):
            for path in files:
                document = yaml.safe_load(path.read_text(encoding="utf-8"))
                for location, reference in references(document):
                    with self.subTest(path=path.relative_to(GITHUB), location=location):
                        self.assertTrue(immutable_reference(reference), f"Expected an immutable reference: {reference}")
