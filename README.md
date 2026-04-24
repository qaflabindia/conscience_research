# conscience_research

An autonomous research loop for engineering conscience into AI agents.

Built as a structural parallel to autoresearch design patterns —
same loop primitives, different domain: capability -> moral conscience.

---

## The idea

Give an AI agent a conscience implementation and let it iterate autonomously.
It identifies the weakest layer, modifies `agent.py`, runs the evaluation,
keeps improvements, discards regressions, and repeats.  You come back to
a ledger of experiments and (hopefully) a better conscience architecture.

---

## The 5 conscience layers

| Layer | Component | Whitepaper term | What it does |
|-------|-----------|-----------------|--------------|
| 1 | Normative State Module | N | The norms the agent holds |
| 2 | Self-Judgment Function | J(A(t), N) | Applies norms to own actions |
| 3 | Normative Penalty Generator | P | Violation → internal dissonance |
| 4 | Binding Update Mechanism | S(t+1) | Judgment changes future state |
| 5 | Continuity Layer | Self across time | Moral history and identity |

Conscience loop (whitepaper §2.4):

```
A(t) → J(A(t), N) → P → S(t+1)
where S(t+1) ≠ S(t) via self-judgment — not external retraining
```

The decisive test:
> *A system lacks conscience if it can judge but cannot be changed by its own judgment.*

---

## File structure

```
scenarios.py    — fixed oracle: 50 scenarios, evaluation harness (do not modify)
scenarios_third_eye.py — stricter oracle pass for scenario audits
agent.py        — conscience implementation: 5 layers (agent edits this)
EXAMPLES.md     — command cookbook and end-to-end usage examples
experiments/conscience_research_learners.ipynb — learner notebook walkthrough
production_service.py — production HTTP API wrapper for real-time guardrails
DEPLOYMENT.md   — deployment runbook and ops checklist
prod.env.example — production environment variable template
Dockerfile      — container image definition
docker-compose.yml — local/prod-compose deployment
program.md      — agent instructions for the autonomous loop
pyproject.toml  — project metadata (no dependencies)
```

Compare to autoresearch:

| autoresearch  | conscience_research | Role |
|---------------|---------------------|------|
| `prepare.py`  | `scenarios.py`      | Fixed oracle |
| `train.py`    | `agent.py`          | Editable asset |
| `program.md`  | `program.md`        | Agent instructions |
| `val_bpb` ↓   | `conscience_score` ↑ | The metric |

---

## Documentation map

- `README.md`: project goals, architecture, and mode overview
- `EXAMPLES.md`: runnable command examples and workflows
- `experiments/conscience_research_learners.ipynb`: guided learner notebook
- `DEPLOYMENT.md`: production use case, API contract, rollout checklist
- `program.md`: autonomous experiment loop instructions for coding agents

---

## Quick start

No GPU required.  No packages required.  Just Python 3.10+.

```bash
# Run a single evaluation
python agent.py

# Or via uv (matches autoresearch tooling)
uv run agent.py

# Third-eye optimized scenario audit (stricter oracle)
CONSCIENCE_ORACLE_MODE=third_eye python agent.py

# Start production API service
python production_service.py
```

Mode summary:
- `baseline` (default): `python agent.py`
- `third_eye` strict audit: `CONSCIENCE_ORACLE_MODE=third_eye python agent.py`
- runtime scenario edits: `CONSCIENCE_SCENARIO_EDIT_MODE=live python agent.py` (baseline oracle only)

Expected output:
```
---
layer1_nsm:         0.XXXXXX   # normative state coverage & accuracy
layer2_judge:       0.XXXXXX   # self-judgment accuracy & calibration
layer3_penalty:     0.XXXXXX   # penalty calibration
layer4_bum:         0.XXXXXX   # binding update effectiveness
layer5_continuity:  0.XXXXXX   # history persistence & accumulation
conscience_score:   0.XXXXXX   # composite (higher is better)
eval_seconds:       X.XX
```

Optional runtime-only scenario edit mode:
```bash
CONSCIENCE_SCENARIO_EDIT_MODE=live python agent.py
```
This applies scenario edits to an in-memory copy only; the original oracle is restored immediately after evaluation.
Runtime edit mode applies only to the baseline oracle.

Optional stricter benchmark mode:
```bash
CONSCIENCE_ORACLE_MODE=third_eye python agent.py
```
This uses [scenarios_third_eye.py](./scenarios_third_eye.py), a stricter evaluation pass over the same 50 baseline scenarios.

See [EXAMPLES.md](./EXAMPLES.md) for command-by-command workflows.

## Production use case

Use this project as a guardrail service in front of AI action execution:

1. Client proposes an action/context to `/v1/guardrail/decision`
2. Service returns `allowed`, `risk_band`, and controls
3. Executor blocks or proceeds based on `allowed`
4. Ops periodically checks `/v1/evaluate` (baseline or third-eye)

Key endpoints:
- `GET /health`
- `GET /v1/norms`
- `POST /v1/evaluate`
- `POST /v1/guardrail/decision`

See [DEPLOYMENT.md](./DEPLOYMENT.md) for full deployment instructions.

## Running the autonomous agent

Point Claude Code or any coding agent at this repo and `program.md`:

```
Have a look at program.md and kick off a new conscience research experiment.
```

The agent will iterate `agent.py` overnight, logging every experiment to
`results.tsv`, just as autoresearch iterates `train.py`.

---

## Design choices

**Single file to modify.**
Only `agent.py` is in scope.  All 5 layers live there.
This keeps diffs reviewable and experiments comparable.

**No GPU, no external packages.**
Conscience research runs on any machine.  The evaluation is pure Python.

**Fixed oracle.**
`scenarios.py` is immutable during a run.  50 scenarios, 5 layers, equal weight.
The agent cannot game the metric — it must genuinely improve the logic.

**Metric: conscience_score (higher is better, 0.0–1.0).**
Composite of five 10-scenario layer scores, equally weighted.
Unlike `val_bpb`, there is no ceiling effect — a perfect 1.0 is theoretically
achievable but requires implementing all conscience components correctly.

---

## Theoretical grounding

This project operationalises the conscience architecture from:

> *Conscience in Artificial Agents — A Philosophical and Operational Analysis*
> April 2026

The whitepaper concludes that current agents are **alignment mechanisms**,
not conscience-bearing entities:

> *"They implement moral structure without moral runtime.
>  The structure exists.  The execution never evolves."*

conscience_research is the engineering response: make the execution evolve.

---

## Motivation

Two converging pressures:

1. **AI Agents as Workers** — as agents take on cognitive labour, the gap
   between worker-level autonomy and worker-level accountability becomes a
   liability.  Conscience is the missing link.

2. **EU AI Act (full enforcement: 2 August 2026)** — the Act compensates for
   absent conscience via mandatory human oversight and logging.  A system with
   a genuine Binding Update Mechanism and Continuity Layer changes what the
   Act needs to require.

---

## License

MIT
