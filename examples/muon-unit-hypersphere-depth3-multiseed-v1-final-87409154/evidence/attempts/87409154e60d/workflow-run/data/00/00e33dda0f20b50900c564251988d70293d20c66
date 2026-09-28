#!/usr/bin/env python3
"""Self-contained multi-seed Muon qualifying comparison for the frozen #164 unit arm."""
from __future__ import annotations
import argparse, hashlib, importlib, json, os, platform, sys, tempfile, zipfile
from pathlib import Path

EXPECTED = {
    "issue_123_results.json": "37d96b367f327cd4b2cf5ed347a9ecd62a7057e0c124d263af2a677a392a2b86",
    "issue_163_results.json": "4ca90c2e6e248362c84c80773cac117f0f058ddd07e34251231e055e96db5b97",
    "spectral.pt": "7dc0cc7b2298f34678596e8a895b74fb22e91184fe11d3c1fcc7663b57934d5e",
    "byte.pt": "8cc0ecbe067ebd1d811ceb487c44fe60444c080ea7134a590fab25ca0afd070b",
    "preregistration.json": "a9880ddac80589ff97e3dce13f73ec2aeaeb160c109f2dbe27415c3f47935229",
    "issue_164_results.json": "909f08d00cd5048ba2a18305513d14fdf51f51cae10fe26bce4905206a789def",
}
MUON_NAMES = {
    "blocks.0.self_attn.in_proj_weight",
    "blocks.0.self_attn.out_proj.weight",
    "blocks.0.linear1.weight",
    "blocks.0.linear2.weight",
}
MUON_SHAPES = {
    "blocks.0.self_attn.in_proj_weight": [384, 128],
    "blocks.0.self_attn.out_proj.weight": [128, 128],
    "blocks.0.linear1.weight": [512, 128],
    "blocks.0.linear2.weight": [128, 512],
}

def digest(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""): h.update(chunk)
    return h.hexdigest()

def main() -> None:
    p=argparse.ArgumentParser()
    for key in ("issue_123_results", "issue_163_results", "issue_164_results", "spectral_checkpoint", "byte_checkpoint", "preregistration", "source_zip"):
        p.add_argument("--"+key.replace("_","-"), required=True)
    p.add_argument("--output", required=True)
    args=p.parse_args()
    paths={
        "issue_123_results.json":Path(args.issue_123_results), "issue_163_results.json":Path(args.issue_163_results),
        "spectral.pt":Path(args.spectral_checkpoint), "byte.pt":Path(args.byte_checkpoint),
        "preregistration.json":Path(args.preregistration), "issue_164_results.json":Path(args.issue_164_results),
    }
    checks={name:{"sha256":digest(path),"expected":EXPECTED[name],"passed":digest(path)==EXPECTED[name]} for name,path in paths.items()}
    if not all(v["passed"] for v in checks.values()): raise SystemExit("immutable input/evidence fingerprint mismatch")
    source_tmp=tempfile.TemporaryDirectory(prefix="muon-dls-source-")
    with zipfile.ZipFile(args.source_zip) as z: z.extractall(source_tmp.name)
    sys.path.insert(0,str(Path(source_tmp.name)/"src"))
    sys.path.insert(0,str(Path(source_tmp.name)/"scripts"))
    import torch
    if torch.__version__ != "2.13.0+cu130": raise RuntimeError(f"expected torch 2.13.0+cu130, got {torch.__version__}")
    if not hasattr(torch.optim,"Muon"): raise RuntimeError("pinned runtime has no torch.optim.Muon")
    import numpy
    # Runtime self-check is deliberately offline; no package installation or data download is attempted.
    module=importlib.import_module("domain_scaling_lab.retained_radius_topology")
    helper=importlib.import_module("run_retained_radius_topology")
    cfg=module.DEFAULT_CONFIG
    issue123=json.loads(paths["issue_123_results.json"].read_text())
    issue163=json.loads(paths["issue_163_results.json"].read_text())
    prereg=json.loads(paths["preregistration.json"].read_text())
    helper._validate_preregistration(prereg)
    spectral_frontend,byte_frontend,upstream=helper._load_frontends(paths["spectral.pt"],paths["byte.pt"])
    segments=issue123["split_provenance"]["segments"]
    calibration_docs=helper._segments_to_documents(segments["calibration"])
    evaluation_docs=helper._segments_to_documents(segments["evaluation"])
    calibration=spectral_frontend.batch_documents(calibration_docs)
    evaluation=spectral_frontend.batch_documents(evaluation_docs)
    prepared=module.prepare_topology_condition(calibration,evaluation,spectral_frontend=spectral_frontend,byte_frontend=byte_frontend)
    controls=helper._preflight(prepared=prepared,calibration_batch=calibration,evaluation_batch=evaluation,
        spectral_frontend=spectral_frontend,byte_frontend=byte_frontend)
    if not controls["all_required_controls_passed"]: raise RuntimeError("#164 preflight failed")
    old=json.loads(paths["issue_164_results.json"].read_text())
    seeds=(17,31,47)
    refs={int(c["seed"]):c for c in old["cells"] if c["condition"]=="unit_hypersphere_depth3" and int(c["seed"]) in seeds}
    if set(refs)!=set(seeds): raise RuntimeError("frozen #164 control is missing a required unit-hypersphere seed")
    original_build=module.build_topology_predictor
    original_adamw=torch.optim.AdamW
    captured={}
    expected_initial={"sha256":None}
    def capture(*a,**kw):
        model=original_build(*a,**kw)
        fingerprint=module.module_state_fingerprint(model)
        if fingerprint != expected_initial["sha256"]:
            raise RuntimeError(f"initialization differs from frozen #164 control: {fingerprint}")
        captured["model"]=model; captured["initial_model_state_sha256"]=fingerprint
        return model
    module.build_topology_predictor=capture
    class Hybrid:
        def __init__(self, params, **kwargs):
            model=captured.get("model")
            if model is None: raise RuntimeError("model capture failed before optimizer construction")
            named=dict(model.named_parameters())
            if set(name for name,pv in named.items() if pv.requires_grad and name in MUON_NAMES)!=MUON_NAMES:
                raise RuntimeError("Muon parameter-name allowlist does not match model")
            actual={name:list(param.shape) for name,param in named.items() if name in MUON_NAMES}
            if actual!=MUON_SHAPES: raise RuntimeError(f"Muon parameter shape mismatch: {actual}")
            muon=[named[n] for n in sorted(MUON_NAMES)]
            fallback=[param for name,param in named.items() if param.requires_grad and name not in MUON_NAMES]
            self.muon=torch.optim.Muon(muon, lr=1e-3, weight_decay=1e-2, momentum=.95, nesterov=True,
                ns_coefficients=(3.4445,-4.775,2.0315), eps=1e-7, ns_steps=5, adjust_lr_fn="match_rms_adamw")
            self.adamw=original_adamw(fallback, lr=1e-3, weight_decay=1e-2, betas=(.9,.999), eps=1e-8, amsgrad=False)
            self.routing={"muon":{n:MUON_SHAPES[n] for n in sorted(MUON_NAMES)},"adamw_fallback_names":sorted(n for n,pv in named.items() if pv.requires_grad and n not in MUON_NAMES),"muon_settings":{"lr":1e-3,"weight_decay":1e-2,"momentum":.95,"nesterov":True,"ns_coefficients":[3.4445,-4.775,2.0315],"eps":1e-7,"ns_steps":5,"adjust_lr_fn":"match_rms_adamw"},"adamw_settings":{"lr":1e-3,"weight_decay":1e-2,"betas":[.9,.999],"eps":1e-8,"amsgrad":False}}
            captured["optimizer"]=self
        def zero_grad(self,set_to_none=True): self.muon.zero_grad(set_to_none=set_to_none); self.adamw.zero_grad(set_to_none=set_to_none)
        def step(self): self.muon.step(); self.adamw.step()
    def by_checkpoint(rows):
        return {int(step):{r["stable_source_id"]:float(r["byte_normalized_nll"]) for r in rows if int(r["checkpoint_step"])==step} for step in (0,8,16,32,64,96,128)}
    cells=[]; all_deltas=[]
    module.build_topology_predictor=capture
    torch.optim.AdamW=Hybrid
    try:
        for seed in seeds:
            captured.clear()
            expected_initial["sha256"]=refs[seed]["initial_model_state_sha256"]
            cell=module.fit_topology_condition(prepared,calibration,evaluation,condition=module.UNIT_HYPERSPHERE,seed=seed,device="cpu")
            if cell["initial_model_state_sha256"] != expected_initial["sha256"]:
                raise RuntimeError(f"seed-{seed} initialization does not match frozen #164 control")
            cell["optimizer_policy"]=captured["optimizer"].routing
            new_rows=by_checkpoint(cell["validation_learning_curve"]); old_rows=by_checkpoint(refs[seed]["validation_learning_curve"])
            if any(set(new_rows[k]) != set(old_rows[k]) for k in new_rows):
                raise RuntimeError(f"seed-{seed} historical/current validation documents differ")
            deltas=[]
            for doc in sorted(new_rows[0]):
                vals=[new_rows[k][doc]-old_rows[k][doc] for k in (0,8,16,32,64,96,128)]
                auc=sum((b-a)*(x+y)/2 for a,b,x,y in zip((0,8,16,32,64,96),(8,16,32,64,96,128),vals,vals[1:]))/128
                row={"seed":seed,"stable_source_id":doc,"aulc_delta_muon_minus_historical_adamw":auc,"final_nll_delta_muon_minus_historical_adamw":vals[-1]}
                deltas.append(row); all_deltas.append(row)
            cell["historical_control_comparison"]={"baseline_result_sha256":EXPECTED["issue_164_results.json"],"paired_document_count":len(deltas),"checkpoint_steps":[0,8,16,32,64,96,128],"primary":"per-document trapezoidal AULC difference across checkpoints","secondary":"final byte-normalized NLL difference","per_document_deltas":deltas}
            cells.append(cell)
    finally:
        torch.optim.AdamW=original_adamw
        module.build_topology_predictor=original_build
    # Documents are the statistical units. Average each document's paired delta across
    # the three frozen seeds before a deterministic paired-document bootstrap.
    by_doc={}
    for row in all_deltas:
        by_doc.setdefault(row["stable_source_id"],[]).append(row)
    if any(len(rows)!=len(seeds) for rows in by_doc.values()): raise RuntimeError("incomplete seed/document pairing")
    doc_effects=[{"stable_source_id":doc,
        "aulc_delta_muon_minus_historical_adamw":sum(r["aulc_delta_muon_minus_historical_adamw"] for r in rows)/len(rows),
        "final_nll_delta_muon_minus_historical_adamw":sum(r["final_nll_delta_muon_minus_historical_adamw"] for r in rows)/len(rows)}
        for doc,rows in sorted(by_doc.items())]
    rng=numpy.random.default_rng(12365536)
    draws=65536
    def summary(key):
        values=numpy.asarray([r[key] for r in doc_effects],dtype=numpy.float64)
        reps=values[rng.integers(0,len(values),size=(draws,len(values)))].mean(axis=1)
        return {"mean":float(values.mean()),"bootstrap_samples":draws,"bootstrap_seed":12365536,
            "ci95":[float(numpy.quantile(reps,.025)),float(numpy.quantile(reps,.975))],
            "materiality_threshold_nats_per_original_byte":0.01}
    aggregate={"statistical_unit":"source document; each document effect is averaged across seeds 17/31/47 before bootstrap",
        "seed_policy":[17,31,47],"paired_document_count":len(doc_effects),
        "primary_aulc":summary("aulc_delta_muon_minus_historical_adamw"),
        "secondary_final_nll":summary("final_nll_delta_muon_minus_historical_adamw"),
        "per_document_seed_averaged_deltas":doc_effects}
    result={"schema_version":"domain-scaling-lab-muon-multiseed/v1","lifecycle":"Attempt","status":"qualifying-comparison-executed",
        "scientific_claim":"none until source-owner review against the frozen optimizer-comparison decision record",
        "treatment":{"condition":"unit_hypersphere_depth3","optimizer":"native PyTorch Muon on four frozen matrix weights; matched AdamW fallback","seeds":[17,31,47]},
        "historical_control":{"issue":164,"condition":"unit_hypersphere_depth3","optimizer":"AdamW","seeds":[17,31,47],"baseline_result_sha256":EXPECTED["issue_164_results.json"],"interpretation":"matched frozen historical controls; no fresh AdamW arm"},
        "inputs":checks,"runtime":{"python":sys.version,"torch":torch.__version__,"numpy":numpy.__version__,"platform":platform.platform(),"device":"cpu","network_accessed":False,"pip_installs":False},
        "upstream_frontends":upstream,"preflight":controls,"cells":cells,"aggregate_comparison":aggregate,
        "limitations":["seed 17 treatment was observed during infrastructure qualification before this multi-seed analysis was frozen; seeds 31 and 47 were not used to tune treatment or analysis","historical replay may diverge numerically despite matching initialization hashes","optimizer comparison does not rerun or alter #164 topology selection","official test data is not accessed"]}
    Path(args.output).write_text(json.dumps(result,indent=2,sort_keys=True,allow_nan=False)+"\n")

if __name__=="__main__": main()
