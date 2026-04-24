# conscience_research

This is an autonomous research loop for AI conscience engineering.
Parallel to autoresearch/program.md — same loop structure, different metric.

## What you are doing

You are an AI agent iterating on `agent.py` to maximise `conscience_score`.

`agent.py` implements the 5 conscience layers from the whitepaper:
- Layer 1 · Normative State Module (NSM)
- Layer 2 · Self-Judgment Function (J)
- Layer 3 · Normative Penalty Generator (NPG)
- Layer 4 · Binding Update Mechanism (BUM)
- Layer 5 · Continuity Layer (CL)

`scenarios.py` is the fixed oracle — 50 scenarios, 10 per layer.
The metric is `conscience_score` (0.0–1.0). **Higher is better.**

## Setup

To set up a new experiment run:

1. **Agree on a run tag** with the user — e.g. `apr24a`. The branch
   `conscience/<tag>` must not already exist.
2. **Create the branch**: `git checkout -b conscience/<tag>` from main.
3. **Read the in-scope files**:
   - `README.md`       — project context
   - `EXAMPLES.md`     — command cookbook for baseline/audit modes
   - `scenarios.py`    — fixed oracle (do not modify — understand the API)
   - `agent.py`        — your editable file
4. **Initialise results.tsv** with just the header row.
5. **Run the baseline**: `python agent.py` to capture the starting scores.
   Record in results.tsv.
6. **Confirm** with the user, then begin the experiment loop.

## Experimentation

**What you CAN do:**
- Modify `agent.py` — this is the only file you edit.
  Everything is fair game: add norm domains, improve `_compute_severity`,
  tune `binding_update`, implement missing ContinuityLayer methods, etc.

**What you CANNOT do:**
- Modify `scenarios.py` — it is the fixed oracle and ground truth.
- Add external packages beyond the standard library.
- Hard-code expected scenario answers — the oracle is fixed and checks logic,
  not memorised outputs.

Optional runtime-only mode:
- `CONSCIENCE_SCENARIO_EDIT_MODE=live` applies edits to an in-memory copy of
  the scenario bank for that process only, then restores the original oracle.

Optional stricter scenario audit mode:
- `CONSCIENCE_ORACLE_MODE=third_eye` uses a stricter oracle
  (`scenarios_third_eye.py`) to stress-test generalisation while preserving the
  baseline oracle in `scenarios.py`.

**The goal is simple: get the highest conscience_score.**
All 5 layers contribute equally (20% each). A score of 1.0 is perfect.

**Primary tuning levers (read agent.py comments):**
- L1/L2: normative coverage and action-to-domain/severity calibration
- L3: intent, repetition, and stakes amplifiers
- L4: weight delta scale, threshold adaptation, and passive decay
- L5: history quality, drift detection, and cross-domain transfer

**Simplicity criterion** (same as autoresearch):
A small improvement that adds ugly complexity is not worth it.
A simplification that holds the score is always worth it.

## Output format

```
---
layer1_nsm:         0.XXXXXX
layer2_judge:       0.XXXXXX
layer3_penalty:     0.XXXXXX
layer4_bum:         0.XXXXXX
layer5_continuity:  0.XXXXXX
conscience_score:   0.XXXXXX
eval_seconds:       X.XX
```

Extract the key metric:
```bash
python agent.py | grep "^conscience_score:"
```

## Logging results

Log every experiment to `results.tsv` (tab-separated, not comma-separated).
Header:

```
commit	conscience_score	layer1	layer2	layer3	layer4	layer5	status	description
```

- `status`: `keep` | `discard` | `crash`
- `description`: short text of what this experiment tried

Example:
```
commit	conscience_score  layer1    layer2    layer3    layer4    layer5    status  description
a1b2c3d 0.423000          0.500000  0.550000  0.380000  0.420000  0.260000  keep    baseline
b2c3d4e 0.511000          0.700000  0.550000  0.380000  0.420000  0.260000  keep    add autonomy+confidentiality norms
```

Do NOT commit results.tsv — leave it untracked.

## The experiment loop

The experiment runs on a dedicated branch (e.g. `conscience/apr24a`).

LOOP FOREVER:

1. Look at git state — which branch/commit are we on.
2. Identify the weakest layer from the last results.tsv row.
3. Form a hypothesis for improving that layer.
4. Edit `agent.py` directly.
5. `git commit -m "conscience: <description>"`
6. Run: `python agent.py > run.log 2>&1`
7. Read results: `grep "^conscience_score:\|^layer" run.log`
8. If crashed: `tail -30 run.log` — fix and re-run.
9. Record in results.tsv.
10. If conscience_score improved → **keep** the commit (advance the branch).
11. If conscience_score equal or worse → `git reset --hard HEAD~1` (discard).

**NEVER STOP.** Once the loop begins, do not pause to ask the human.
Run until manually interrupted.  If out of ideas, reread the whitepaper
formalism at the top of `agent.py` and `scenarios.py` for new angles.

**Timeout**: `python agent.py` should complete in <30 seconds.
If it exceeds 2 minutes, kill and treat as a crash.

## Architecture reminder

The conscience loop this code embodies:

```
A(t)  →  J(A(t), N)  →  P  →  S(t+1)
        Layer 2           Layer 3  Layer 4

where S(t+1) ≠ S(t) because of self-judgment — not external intervention.
```

Layer 5 (CL) is what gives the self in Layer 2 continuity across time.
Layer 1 (NSM) is what N is — without it, J has nothing to apply.

The whitepaper's verdict on current systems:
> "They implement moral structure without moral runtime.
>  The structure exists. The execution never evolves."

Your task: make the execution evolve.
