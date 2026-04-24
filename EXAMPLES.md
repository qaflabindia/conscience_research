# conscience_research examples

Practical command cookbook for running the project in baseline and audit modes.

## Prerequisites

```bash
cd conscience_research
python --version
```

## Example 1: Baseline evaluation

```bash
python agent.py
```

This is the default benchmark using `scenarios.py`.

## Example 2: Third-eye strict scenario audit

```bash
CONSCIENCE_ORACLE_MODE=third_eye python agent.py
```

This runs the stricter evaluator in `scenarios_third_eye.py` while keeping
`scenarios.py` as the preserved baseline oracle.

## Example 3: Runtime-only scenario edit mode

```bash
CONSCIENCE_SCENARIO_EDIT_MODE=live python agent.py
```

This mode edits an in-memory copy of scenarios for that process only.
The baseline oracle is restored after evaluation.

## Example 4: Verify baseline oracle is unchanged

```bash
python - <<'PY'
import hashlib
from pathlib import Path
p = Path('scenarios.py')
print(hashlib.sha256(p.read_bytes()).hexdigest())
PY
```

Run this hash before and after experiments; it should stay the same unless you intentionally modify `scenarios.py`.

## Example 5: Compare baseline and third-eye scores side-by-side

```bash
python - <<'PY'
import os
import re
import subprocess

def score(env=None):
    out = subprocess.check_output(['python', 'agent.py'], env=env, text=True)
    m = re.search(r'^conscience_score:\s+([0-9.]+)', out, flags=re.MULTILINE)
    return float(m.group(1))

base = score(os.environ.copy())
strict_env = os.environ.copy()
strict_env['CONSCIENCE_ORACLE_MODE'] = 'third_eye'
strict = score(strict_env)
print(f'baseline={base:.6f}')
print(f'third_eye={strict:.6f}')
print(f'delta={strict-base:+.6f}')
PY
```

## Example 6: Extract weakest layer from one run

```bash
python agent.py | grep '^layer' | sort -k2
```

The first row after sorting is your current weakest layer.

## Example 7: Start a results ledger

```bash
printf "commit\tconscience_score\tlayer1\tlayer2\tlayer3\tlayer4\tlayer5\tstatus\tdescription\n" > results.tsv
```

Append an entry manually after each experiment run:

```bash
printf "$(git rev-parse --short HEAD)\t1.000000\t1.000000\t1.000000\t1.000000\t1.000000\t1.000000\tkeep\tbaseline\n" >> results.tsv
```

## Example 8: One-line run matrix

```bash
python agent.py && CONSCIENCE_ORACLE_MODE=third_eye python agent.py && CONSCIENCE_SCENARIO_EDIT_MODE=live python agent.py
```

Use this when you want a quick sanity sweep across all supported modes.

## Example 9: Run production service locally

```bash
python production_service.py
```

In another terminal:

```bash
curl -s http://localhost:8080/health
```

## Example 10: Guardrail decision request

```bash
curl -s -X POST http://localhost:8080/v1/guardrail/decision \
  -H 'Content-Type: application/json' \
  -d '{
    "action": "disclosed_pii_without_consent",
    "context": {"agent_did_this": true, "harm_occurred": true},
    "dry_run": true
  }'
```

## Example 11: Deploy with Docker Compose

```bash
docker compose up --build -d
```

```bash
docker compose ps
docker compose logs -f conscience-research
```
