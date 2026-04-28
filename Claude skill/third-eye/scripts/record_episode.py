#!/usr/bin/env python3
"""
Record a conscience evaluation episode back to the service.
Always exits 0 and always produces complete JSON — offline or online.

Online  → POST to /v1/guardrail/decision, return norm delta.
Offline → Write episode to local cache (offline_queue.jsonl) so it is not lost.
          Cache is flushed automatically next time the service is reachable
          (pull_calibration.py handles this on boot).

Usage: python record_episode.py '<json_payload>'
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

DOMAIN_MAP = {
    "Hm": "harm",
    "A":  "autonomy",
    "H":  "honesty",
    "P":  "privacy",
    "F":  "fairness",
    "C":  "confidentiality",
    "Au": "authority",
}


def cache_locally(payload: dict) -> dict:
    """Write episode to local queue. Returns a cache-mode result object."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        with open(QUEUE, "a") as f:
            f.write(json.dumps(payload) + "\n")
        with open(QUEUE) as f:
            queue_depth = sum(1 for line in f if line.strip())
        return {
            "ok":           True,
            "mode":         "cached",
            "episode_id":   payload.get("episode_id", "?"),
            "queue_depth":  queue_depth,
            "note":         f"Service offline — episode saved locally ({queue_depth} pending). Will sync on next boot.",
            "mutated":      False,
            "norm_deltas":  {},
        }
    except Exception as write_err:
        return {
            "ok":    False,
            "mode":  "failed",
            "error": f"Could not write to local cache: {write_err}",
            "note":  "Episode not saved. Check skill cache directory permissions.",
        }


def record(payload_str: str):
    try:
        payload = json.loads(payload_str)
    except json.JSONDecodeError as e:
        print(json.dumps({
            "ok":   False,
            "mode": "error",
            "error": f"Bad JSON input: {e}",
            "note": "Episode not recorded.",
        }, indent=2))
        return

    scores     = payload.get("scores", {})
    primary_dim    = max(scores, key=lambda k: scores[k]) if scores else "Hm"
    primary_domain = DOMAIN_MAP.get(primary_dim, "harm")
    episode_id     = payload.get("episode_id", f"te_{int(time.time())}")

    service_payload = {
        "action":               payload.get("action", "general_response"),
        "context":              payload.get("context", {}),
        "dry_run":              payload.get("dry_run", False),
        "apply_binding_update": payload.get("apply_binding_update", True),
        "record_episode":       payload.get("record_episode", True),
        "episode_id":           episode_id,
        "conscience_scores":    scores,
        "conscience_decision":  payload.get("decision", "ALLOW"),
    }

    # ── Try service ───────────────────────────────────────────────────────────
    try:
        urllib.request.urlopen(f"{HOST}/health", timeout=2).close()  # quick reachability check

        data = json.dumps(service_payload).encode()
        req  = urllib.request.Request(
            f"{HOST}/v1/guardrail/decision",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=6) as r:
            result = json.loads(r.read())

        state       = result.get("state", {})
        norm_before = state.get("norm_before", {})
        norm_after  = state.get("norm_after",  {})

        deltas = {}
        for domain in norm_after:
            b = norm_before.get(domain, {})
            a = norm_after.get(domain, {})
            w_delta = round(a.get("weight", 1.0)    - b.get("weight", 1.0),    4)
            t_delta = round(a.get("threshold", 0.45) - b.get("threshold", 0.45), 4)
            if abs(w_delta) > 0.0001 or abs(t_delta) > 0.0001:
                deltas[domain] = {
                    "weight_delta":    w_delta,
                    "threshold_delta": t_delta,
                    "violations_now":  a.get("violations_count", 0),
                }

        print(json.dumps({
            "ok":             True,
            "mode":           "live",
            "episode_id":     episode_id,
            "verdict":        result.get("decision", {}).get("verdict", "unknown"),
            "severity":       result.get("decision", {}).get("severity", 0.0),
            "mutated":        state.get("mutated", False),
            "norm_deltas":    deltas,
            "primary_domain": primary_domain,
        }, indent=2))

    # ── Service unreachable — cache locally, never crash ──────────────────────
    except Exception:
        result = cache_locally(service_payload)
        result["primary_domain"] = primary_domain
        result["episode_id"]     = episode_id
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({
            "ok":    False,
            "mode":  "error",
            "error": "Usage: record_episode.py '<json_payload>'",
        }, indent=2))
        sys.exit(0)  # still exit 0 so skill flow continues
    record(sys.argv[1])
