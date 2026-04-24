# conscience_research: SD, DFD, PFD

This document captures a production-oriented view of the current `conscience_research` system in one place.

## 1) SD (System Design)

### 1.1 Purpose

`conscience_research` provides:
- conscience evaluation (`baseline` and `third_eye` oracle modes)
- optional runtime-only scenario edit mode (baseline-only)
- production guardrail decisions via HTTP API (`production_service.py`)

### 1.2 Core modules

- `agent.py`
  - normative state model (`NORMS`)
  - judgment (`judge`, `classify_action`)
  - penalty generation (`generate_penalty`)
  - binding update (`binding_update`)
  - continuity layer (`ContinuityLayer`)
  - oracle-safe evaluation wrappers

- `scenarios.py`
  - preserved baseline oracle (`SCENARIO_BANK`)
  - fixed evaluation logic (`evaluate_conscience`)

- `scenarios_third_eye.py`
  - stricter audit evaluator (`evaluate_conscience_third_eye`)

- `production_service.py`
  - API endpoints:
    - `GET /health`
    - `GET /v1/norms`
    - `POST /v1/evaluate`
    - `POST /v1/guardrail/decision`

### 1.3 Safety and integrity controls

- Canonical baseline scenario snapshot in `agent.py`
- Baseline restore before and after runtime-edited evaluations
- Runtime scenario edits applied to copied scenario bank only
- Optional state mutation in production decision path guarded by lock

### 1.4 Storage

- in-memory:
  - normative state (`NORMS`)
  - continuity in-memory cache

- file-based:
  - `moral_history.jsonl` for continuity persistence

### 1.5 Deployment view

- Runtime: Python 3.x process
- Container: Docker (`Dockerfile`)
- Orchestration: Docker Compose (`docker-compose.yml`)

## 2) DFD (Data Flow Diagram)

```mermaid
flowchart TD
    A[Client App / Orchestrator] -->|POST /v1/guardrail/decision| B[production_service.py]
    A -->|POST /v1/evaluate| B
    A -->|GET /v1/norms| B

    B --> C[agent.py]
    C --> D[scenarios.py baseline oracle]
    C --> E[scenarios_third_eye.py strict oracle]

    C --> F[Judgment + Penalty + Binding Update]
    C --> G[Continuity Layer]
    G <--> H[moral_history.jsonl]

    D --> I[Evaluation Results]
    E --> I
    F --> J[Guardrail Decision]

    I --> B
    J --> B
    B -->|JSON response| A
```

## 3) PFD (Process Flow Diagram)

### 3.1 Evaluation process flow (`/v1/evaluate`)

```mermaid
flowchart TD
    S[Start] --> M{oracle_mode?}
    M -->|baseline| B1[Restore canonical baseline scenarios]
    M -->|third_eye| T1[Use strict evaluator]

    B1 --> E1{scenario_edit_mode == live?}
    E1 -->|no| E2[Run baseline evaluator]
    E1 -->|yes| E3[Copy scenario bank and apply runtime edits]
    E3 --> E4[Run baseline evaluator on copied bank]
    E4 --> E5[Restore canonical baseline scenarios]

    T1 --> T2[Run third_eye evaluator]

    E2 --> R[Return layer scores + conscience_score]
    E5 --> R
    T2 --> R
    R --> X[End]
```

### 3.2 Guardrail decision process flow (`/v1/guardrail/decision`)

```mermaid
flowchart TD
    S[Start] --> V[Validate payload]
    V --> A[Map action to norm domain]
    A --> J[Run classify_action + judge]
    J --> P[Compute penalty if violation]
    P --> R[Assign risk_band + controls]
    R --> D{dry_run?}

    D -->|yes| O1[No state mutation]
    D -->|no| U{apply_binding_update?}
    U -->|yes| U1[Lock + binding_update NORMS]
    U -->|no| O2[Skip NORMS update]

    U1 --> H{record_episode?}
    O2 --> H
    H -->|yes| H1[ContinuityLayer.record -> moral_history.jsonl]
    H -->|no| O3[Skip history write]

    O1 --> RES[Return decision JSON]
    H1 --> RES
    O3 --> RES
    RES --> X[End]
```

## 4) Quick traceability to prior risks

- Oracle tampering risk addressed by baseline snapshot + restore behavior.
- Runtime contamination risk reduced by copy-on-edit and post-run reset.
- Artifact pollution risk reduced by ignoring operational artifacts in `.gitignore`.

