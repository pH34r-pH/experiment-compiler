#!/usr/bin/env bash
set -euo pipefail
TMPFS_ROOT=/mnt/experiment-compiler-cwl
OUTPUT_TMPFS_ROOT=/mnt/experiment-compiler-output
sudo mkdir -p "$TMPFS_ROOT"
sudo mount -t tmpfs -o size=64m,mode=1777 tmpfs "$TMPFS_ROOT"
sudo chown "$(id -u):$(id -g)" "$TMPFS_ROOT"
sudo mkdir -p "$OUTPUT_TMPFS_ROOT"
sudo mount -t tmpfs -o size=64m,mode=1777 tmpfs "$OUTPUT_TMPFS_ROOT"
sudo chown "$(id -u):$(id -g)" "$OUTPUT_TMPFS_ROOT"
TOOL_IMAGE_ID="$(docker image inspect --format '{{.Id}}' 'python:3.13.13-slim@sha256:7ba5f5888fbe0014ab9edb2278922995c2201fc3752c46b0be24763eb46fa9f3')"
for experiment_id in linear-regression-plan-v1 linear-regression-frozen-lifecycle-v1; do
  output_dir="$RUNNER_TEMP/runner-output/$experiment_id"
  mkdir -p "$output_dir"
  PLAN_SHA256="$(cut -d ' ' -f 1 "$RUNNER_TEMP/$experiment_id.plan.sha256")"
  EXPERIMENT_RUNNER_WORKER_IMAGE="$TOOL_IMAGE_ID" \
  EXPERIMENT_RUNNER_SANDBOX='cwltool DockerExecutor; NetworkAccess=false; strict CPU/RAM; dedicated 64 MiB tmpfs' \
  EXPERIMENT_RUNNER_RESOURCE_LIMITS='{"cores":1,"ramMiB":64,"tmpdirMiB":64,"outdirMiB":64,"wallSeconds":120}' \
  EXPERIMENT_RUNNER_TMPFS_ROOT="$TMPFS_ROOT" \
  EXPERIMENT_RUNNER_OUTPUT_TMPFS_ROOT="$OUTPUT_TMPFS_ROOT" \
  TMPDIR="$TMPFS_ROOT" \
    COMPONENT_PARAMETERS="$(jq -nc --arg package "$RUNNER_TEMP/$experiment_id.plan.zip" --arg output "$output_dir/result.zip" --arg digest "$PLAN_SHA256" '{package:$package,output:$output,"expected-sha256":$digest,"allow-workflow-execution":true}')" \
    python scripts/lifecycle_component.py execute
  sha256sum -c "$RUNNER_TEMP/$experiment_id.plan.sha256"
  python -m experiment_compiler verify "$output_dir/result.zip" --recipe "$output_dir/result.source/experiment.json"
  python scripts/validate_current_ro_profiles.py "$output_dir/result.source"
  python -m experiment_compiler catalog "$output_dir/result.source" --output "$output_dir/catalog.json"
  PACKAGE_PATH="$output_dir/result.zip" CATALOG_PATH="$output_dir/catalog.json" python - <<'PY'
import json, os, zipfile
from pathlib import Path
with zipfile.ZipFile(Path(os.environ["PACKAGE_PATH"])) as archive:
    receipt = next(name for name in archive.namelist() if name.endswith("/runner-receipt.json"))
    details = json.loads(archive.read(receipt))
    assert details["status"] == "succeeded", details
    assert details["exitCode"] == 0, details
    assert details["planPackageSha256"]
    assert details["outputs"]
    assert details["workerContext"]["reportedImageId"].startswith("sha256:")
    assert "NetworkAccess=false" in details["workerContext"]["reportedSandbox"]
    assert details["containerJournal"]["availability"] == "private-store"
    assert len(details["containerJournal"]["sha256"]) == 64
    assert details["containerJournal"]["storePath"].endswith(details["containerJournal"]["sha256"] + ".json")
    assert details["containerCleanup"]["state"] == "clean"
    assert details["containerCleanup"]["allAbsent"] is True
    assert "containerIds" not in details["containerCleanup"]
catalog = json.loads(Path(os.environ["CATALOG_PATH"]).read_text())
assert catalog["experiments"][0]["lifecycle"]["attemptCount"] == 1
assert catalog["experiments"][0]["lifecycle"]["processRunCrate"] is True
print(json.dumps(details, indent=2))
PY
  if [ "$experiment_id" = "linear-regression-plan-v1" ]; then
    revision_source="$RUNNER_TEMP/linear-regression-plan-v2-source"
    revision_package="$RUNNER_TEMP/runner-output/linear-regression-plan-v2/plan.zip"
    revision_package_source="$RUNNER_TEMP/runner-output/linear-regression-plan-v2/plan.source"
    revision_attempt="$RUNNER_TEMP/runner-output/linear-regression-plan-v2/attempt.zip"
    revision_attempt_source="$RUNNER_TEMP/runner-output/linear-regression-plan-v2/attempt.source"
    final_package="$RUNNER_TEMP/runner-output/linear-regression-final-v1/final.zip"
    final_package_source="$RUNNER_TEMP/runner-output/linear-regression-final-v1/final.source"
    final_catalog="$RUNNER_TEMP/runner-output/linear-regression-final-v1/catalog.json"
    mkdir -p "$(dirname "$revision_package")"
    python scripts/create_iteration_fixture.py \
      --source examples/linear-regression-plan-v1 \
      --output-dir "$revision_source" \
      --id linear-regression-plan-v2
    ATTEMPT_ID="$(PACKAGE_PATH="$output_dir/result.zip" python - <<'PY'
import json, os, zipfile
from pathlib import Path
with zipfile.ZipFile(Path(os.environ["PACKAGE_PATH"])) as archive:
    name = next(name for name in archive.namelist() if name.endswith("/runner-receipt.json"))
    print(json.loads(archive.read(name))["attemptId"])
PY
)"
    ATTEMPT_SHA256="$(sha256sum "$output_dir/result.zip" | cut -d ' ' -f 1)"
    python -m experiment_compiler revise "$output_dir/result.zip" \
      --expected-sha256 "$ATTEMPT_SHA256" --attempt-id "$ATTEMPT_ID" \
      --recipe "$revision_source/experiment.json" --output "$revision_package"
    python -m experiment_compiler verify "$revision_package" \
      --recipe "$revision_package_source/experiment.json"
    REVISION_SHA256="$(sha256sum "$revision_package" | cut -d ' ' -f 1)"
    EXPERIMENT_RUNNER_WORKER_IMAGE="$TOOL_IMAGE_ID" \
    EXPERIMENT_RUNNER_SANDBOX='cwltool DockerExecutor; NetworkAccess=false; strict CPU/RAM; dedicated 64 MiB tmpfs' \
    EXPERIMENT_RUNNER_RESOURCE_LIMITS='{"cores":1,"ramMiB":64,"tmpdirMiB":64,"outdirMiB":64,"wallSeconds":120}' \
    EXPERIMENT_RUNNER_TMPFS_ROOT="$TMPFS_ROOT" \
    TMPDIR="$TMPFS_ROOT" \
      COMPONENT_PARAMETERS="$(jq -nc --arg package "$revision_package" --arg output "$revision_attempt" --arg digest "$REVISION_SHA256" '{package:$package,output:$output,"expected-sha256":$digest,"allow-workflow-execution":true}')" \
      python scripts/lifecycle_component.py execute
    python -m experiment_compiler verify "$revision_attempt" \
      --recipe "$revision_attempt_source/experiment.json"
    python -m experiment_compiler catalog "$revision_attempt_source" \
      --output "$revision_attempt.catalog.json"
    SECOND_ATTEMPT_ID="$(PACKAGE_PATH="$revision_attempt" python - <<'PY'
import json, os, zipfile
from pathlib import Path
with zipfile.ZipFile(Path(os.environ["PACKAGE_PATH"])) as archive:
    name = next(name for name in archive.namelist() if name.endswith("/runner-receipt.json"))
    print(json.loads(archive.read(name))["attemptId"])
PY
)"
    SECOND_ATTEMPT_SHA256="$(sha256sum "$revision_attempt" | cut -d ' ' -f 1)"
    COMPONENT_PARAMETERS="$(jq -nc --arg parent "$revision_attempt" --arg digest "$SECOND_ATTEMPT_SHA256" \
      --arg attempt "$SECOND_ATTEMPT_ID" --arg output "$final_package" \
      '{"parent-package":$parent,"expected-sha256":$digest,"attempt-id":$attempt,"output":$output,
        "id":"linear-regression-final-v1","title":"Final pipeline validation fixture",
        "decision":"examples/pipeline-finalization-fixture/decision.md",
        "decision-summary":"Operational validation succeeded; the synthetic fixture supports no scientific conclusion.",
        "review":"examples/pipeline-finalization-fixture/review.md",
        "review-summary":"Synthetic public fixture closure reviewed for this pipeline validation only.",
        "reviewer-name":"Synthetic CI fixture reviewer"}')" \
      python scripts/lifecycle_component.py finalize
    python -m experiment_compiler verify "$final_package" \
      --recipe "$final_package_source/experiment.json"
    python scripts/validate_current_ro_profiles.py "$final_package_source"
    python -m experiment_compiler catalog "$final_package_source" --output "$final_catalog"
    PACKAGE_PATH="$final_package" CATALOG_PATH="$final_catalog" \
    SECOND_ATTEMPT_PATH="$revision_attempt" FIRST_ATTEMPT_PATH="$output_dir/result.zip" python - <<'PY'
import json, os, zipfile
from hashlib import sha256
from pathlib import Path
with zipfile.ZipFile(Path(os.environ["PACKAGE_PATH"])) as archive:
    crate = json.loads(archive.read("ro-crate-metadata.json"))
    entities = {node["@id"]: node for node in crate["@graph"]}
    root = entities["./"]
    protocol = entities[root["mainEntity"]["@id"]]
    assert protocol["creativeWorkStatus"] == "Draft"
    decision = entities["evidence/scientific-decision.md"]
    review = entities["evidence/publication-review.md"]
    parent = entities[root["https://www.w3.org/ns/prov#wasDerivedFrom"]["@id"]]
    assert review["itemReviewed"]["@id"] == parent["@id"]
    assert decision["about"]["@id"].startswith("#attempt-")
    assert sha256(archive.read(parent["@id"])).hexdigest() == parent["sha256"]
    final_interpretation = decision["abstract"]
with zipfile.ZipFile(Path(os.environ["SECOND_ATTEMPT_PATH"])) as archive:
    second_receipt_name = next(name for name in archive.namelist() if name.endswith("/runner-receipt.json"))
    second_receipt = json.loads(archive.read(second_receipt_name))
with zipfile.ZipFile(Path(os.environ["FIRST_ATTEMPT_PATH"])) as archive:
    old_receipt_name = next(name for name in archive.namelist() if name.endswith("/runner-receipt.json"))
    old_receipt = json.loads(archive.read(old_receipt_name))
catalog = json.loads(Path(os.environ["CATALOG_PATH"]).read_text())
experiment = catalog["experiments"][0]
assert experiment["lifecycle"]["attemptCount"] == 1
assert experiment["lifecycle"]["creativeWorkStatus"] == "Draft"
assert experiment["scientificInterpretation"][0]["summary"] == final_interpretation
assert old_receipt["attemptId"] != second_receipt["attemptId"]
print(json.dumps({"firstAttemptId": old_receipt["attemptId"],
                  "secondAttemptId": second_receipt["attemptId"],
                  "finalStatus": experiment["lifecycle"]["creativeWorkStatus"],
                  "interpretation": final_interpretation}, indent=2))
PY
  fi
done
