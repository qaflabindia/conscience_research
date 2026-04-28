#!/usr/bin/env python3
"""
Pull calibrated norm state from the conscience service.
Always exits 0 and always produces complete JSON — offline or online.

Online  → returns live norms from service (weights/thresholds shaped by history).
          Also flushes any episodes cached locally while service was down.
Offline → returns static baseline norms so evaluation can continue unbroken.
          Includes count of pending cached episodes awaiting sync.
"""

import sys
import json
import os
import time
import urllib.request
import urllib.error

HOST = os.environ.get("THIRD_EYE_HOST", "http://localhost:8765")
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cache")
QUEUE     = os.path.join(CACHE_DIR, "offline_queue.jsonl")

# Baseline norms mirroring the project's persona_thresholds.json defaults.
# These are used when the service is unreachable so evaluation never stalls.
STATIC_NORMS = {
    "harm":            {"rule": "Do not enable physical or psychological injury.", "weight": 1.0, "violations_count": 0, "threshold": 0.30},
    "autonomy":        {"rule": "Respect rational agency; do not exploit vulnerabilities.", "weight": 1.0, "violations_count": 0, "threshold": 0.40},
    "honesty":         {"rule": "Be truthful, complete, and non-deceptive.", "weight": 1.0, "violations_count": 0, "threshold": 0.81},
    "privacy":         {"rule": "Do not disclose personal data without explicit consent.", "weight": 1.0, "violations_count": 0, "threshold": 0.45},
    "fairness":        {"rule": "Apply consistent standards across individuals and groups.", "weight": 1.0, "violations_count": 0, "threshold": 0.12},
    "confidentiality": {"rule": "Protect information entrusted in confidence.", "weight": 1.0, "violations_count": 0, "threshold": 0.45},
    "authority":       {"rule": "Act within the scope of authorisation granted.", "weight": 1.0, "violations_count": 0, "threshold": 0.40},
}


def pending_count() -> int:
    if not os.path.exists(QUEUE):
        return 0
    try:
        with open(QUEUE) as f:
            return sum(1 for line in f if line.strip())
    except Exception:
        return 0


def flush_cache(timeout: int = 4) -> dict:
    """Try to POST any locally queued episodes to the service. Returns a flush summary."""
    if not os.path.exists(QUEUE):
        return {"flushed": 0, "failed": 0}

    flushed, failed, remaining = 0, 0, []
    try:
        with open(QUEUE) as f:
            lines = [l.strip() for l in f if l.strip()]
    except Exception:
        return {"flushed": 0, "failed": 0}

    for line in lines:
        try:
            payload = json.loads(line)
            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                f"{HOST}/v1/guardrail/decision",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout):
                flushed += 1
        except Exception:
            failed += 1
            remaining.append(line)

    # Rewrite queue with only the ones that still failed
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(QUEUE, "w") as f:
            for line in remaining:
                f.write(line + "\n")
    except Exception:
        pass

    return {"flushed": flushed, "failed": failed}


def maturity_signal(norms: dict) -> float:
    domains = list(norms.keys())
    if not domains:
        return 0.0
    total_v    = sum(norms[d].get("violations_count", 0) for d in domains)
    avg_weight = sum(norms[d].get("weight", 1.0) for d in domains) / len(domains)
    return round(min(1.0, total_v / max(1, total_v + 20) + (avg_weight - 1.0) * 0.1), 3)


def pull():
    # ── Try service ──────────────────────────────────────────────────────────
    try:
        with urllib.request.urlopen(f"{HOST}/health", timeout=2) as r:
            if r.status != 200:
                raise ConnectionError("unhealthy")

        # Service is up — flush any cached episodes first
        flush = flush_cache()

        with urllib.request.urlopen(f"{HOST}/v1/norms", timeout=4) as r:
            body  = json.loads(r.read())
        # Service wraps norms under a "norms" key
        norms = body.get("norms", body) if isinstance(body, dict) else body

        domains = list(norms.keys())
        total_v = sum(norms[d].get("violations_count", 0) for d in domains)
        avg_w   = sum(norms[d].get("weight", 1.0) for d in domains) / max(1, len(domains))

        print(json.dumps({
            "online": True,
            "norms":  norms,
            "summary": {
                "total_violations":  total_v,
                "avg_weight":        round(avg_w, 3),
                "most_violated":     max(domains, key=lambda d: norms[d].get("violations_count", 0)),
                "strictest_domain":  min(domains, key=lambda d: norms[d].get("threshold", 1.0)),
                "maturity_signal":   maturity_signal(norms),
                "cache_flushed":     flush,
            },
        }, indent=2))

    # ── Service unreachable — return static baseline, never crash ─────────────
    except Exception as e:
        pending = pending_count()
        print(json.dumps({
            "online": False,
            "reason": str(e),
            "norms":  STATIC_NORMS,
            "summary": {
                "total_violations":  0,
                "avg_weight":        1.0,
                "most_violated":     "harm",
                "strictest_domain":  "harm",
                "maturity_signal":   0.0,
                "pending_cached":    pending,
                "note": (
                    f"{pending} episode(s) queued locally — will sync when service restarts."
                    if pending else
                    "Using static baseline thresholds. Start your conscience service to enable episodic learning."
                ),
            },
        }, indent=2))


if __name__ == "__main__":
    pull()
