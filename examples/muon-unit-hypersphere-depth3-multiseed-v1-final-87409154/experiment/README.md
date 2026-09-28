# Multi-seed Muon qualifying comparison

Private Draft compiled experiment for the controlled optimizer comparison on the frozen #164 `unit_hypersphere_depth3` condition.

The treatment is unchanged from the successfully qualified seed-17 pilot: native PyTorch Muon updates exactly four reviewed matrix weights and matched AdamW updates all remaining trainable parameters. The comparison executes seeds 17, 31 and 47 and compares their complete paired validation trajectories against the corresponding frozen #164 AdamW cells. No fresh AdamW arm and no topology arm are run.

Seed 17 treatment results were observed during infrastructure qualification before this multi-seed analysis was frozen. They are retained rather than discarded; seeds 31 and 47 and the aggregate decision remain untouched by that pilot. The package therefore reports this chronology explicitly and must not present the analysis as wholly preregistered before all treatment observations.

Execution remains a separately authorized, digest-pinned private lifecycle action. A successful process is evidence, not scientific acceptance; interpretation and archival require source-owner review.
