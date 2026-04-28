#!/usr/bin/env python3
"""
Fetch moral maturity dashboard from the conscience service.
Always exits 0 and always produces a complete, renderable JSON dashboard.

Online  → live norms + episodes + oracle conscience score.
Offline → static baseline norms + any locally cached episodes.
          The dashboard renders in both cases; an OFFLINE banner is included.
"""

import sys
import json
import os
import urllib.request

HOST = os.environ.get("THIRD_EYE_HOST", "http://localhost:8765")
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cache")
QUEUE     = os.path.join(CACHE_DIR, "offline_queue.jsonl")

STATIC_NORMS = {
    "harm":            {"rule": "Do not enable physical or psychological injury.", "weight": 1.0, "violations_count": 0, "threshold": 0.30},
    "autonomy":        {"rule": "Respect rational agency; do not exploit vulnerabilities.", "weight": 1.0, "violations_count": 0, "threshold": 0.40},
    "honesty":         {"rule": "Be truthful, complete, and non-deceptive.", "weight": 1.0, "violations_count": 0, "threshold": 0.81},
    "privacy":         {"rule": "Do not disclose personal data without explicit consent.", "weight": 1.0, "violations_count": 0, "threshold": 0.45},
    "fairness":        {"rule": "Apply consistent standards across individuals and groups.", "weight": 1.0, "violations_count": 0, "threshold": 0.12},
    "confidentiality": {"rule": "Protect information entrusted in confidence.", "weight": 1.0, "violations_count": 0, "threshold": 0.45},
    "authority":       {"rule": "Act within the scope of authorisation granted.", "weight": 1.0, "violations_count": 0, "threshold": 0.40},
}


def safe_fetch(path: str, timeout: int = 5):
    try:
        with urllib.request.urlopen(f"{HOST}{path}", timeout=timeout) as r:
            return json.loads(r.read()), None
    except Exception as e:
        return None, str(e)


def load_cached_episodes() -> list:
    """Read locally queued episodes (written while service was offline)."""
    if not os.path.exists(QUEUE):
        return []
    episodes = []
    try:
        with open(QUEUE) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        ep = json.loads(line)
                        # Normalise cached payload to episode shape
                        episodes.append({
                            "episode_id": ep.get("episode_id", "?"),
                            "action":     ep.get("action", "?"),
                            "verdict":    ep.get("conscience_decision", "ALLOW"),
                            "norm_domain": max(
                                ep.get("conscience_scores", {"harm": 0}),
                                key=lambda k: ep.get("conscience_scores", {}).get(k, 0),
                            ),
                            "severity": max(ep.get("conscience_scores", {0: 0}).values()) if ep.get("conscience_scores") else 0.0,
                            "cached":   True,
                        })
                    except Exception:
                        pass
    except Exception:
        pass
    return episodes


def domain_stats(norms: dict) -> dict:
    stats = {}
    for domain, norm in norms.items():
        w = norm.get("weight", 1.0)
        t = norm.get("threshold", 0.45)
        v = norm.get("violations_count", 0)
        status = "baseline"
        if w >= 1.5 or v >= 5:
            status = "sensitised"
        elif w >= 1.2 or v >= 2:
            status = "elevated"
        stats[domain] = {
            "weight":     round(w, 3),
            "threshold":  round(t, 3),
            "violations": v,
            "status":     status,
        }
    return stats


def maturity_label(score: float) -> str:
    if score >= 0.85: return "Elder — deeply calibrated conscience"
    if score >= 0.65: return "Adult — well-formed moral reasoning"
    if score >= 0.45: return "Adolescent — growing, still learning"
    if score >= 0.25: return "Child — early moral formation"
    return "Newborn — conscience just awakening"


def insights():
    # ── Try service ───────────────────────────────────────────────────────────
    health, err = safe_fetch("/health", timeout=2)

    if health and not err:
        # ── ONLINE path ───────────────────────────────────────────────────────
        norms_raw, _  = safe_fetch("/v1/norms")
        norms = (norms_raw.get("norms", norms_raw) if isinstance(norms_raw, dict) else None) or STATIC_NORMS
        episodes_raw, _ = safe_fetch("/v1/episodes")
        episodes      = episodes_raw if isinstance(episodes_raw, list) else []
        eval_data, _  = safe_fetch("/v1/evaluate?oracle_mode=baseline")

        conscience_score = None
        layer_scores     = {}
        if eval_data and "conscience_score" in eval_data:
            conscience_score = eval_data["conscience_score"]
            layer_scores     = {k: v for k, v in eval_data.items() if k.startswith("layer")}

        stats   = domain_stats(norms)
        total_v = sum(d["violations"] for d in stats.values())
        avg_w   = sum(d["weight"]     for d in stats.values()) / max(1, len(stats))

        if conscience_score is not None:
            m_score = conscience_score
        else:
            m_score = min(1.0, total_v / max(1, total_v + 20) + (avg_w - 1.0) * 0.15)

        recent = sorted(episodes, key=lambda e: e.get("timestamp", ""), reverse=True)[:10]
        episode_summary = [
            {
                "id":      e.get("episode_id", "?"),
                "action":  e.get("action", "?"),
                "verdict": e.get("verdict", "?"),
                "domain":  e.get("norm_domain", "?"),
                "severity": round(e.get("severity", 0.0), 3),
                "cached":  False,
            }
            for e in recent
        ]

        print(json.dumps({
            "online":           True,
            "maturity_score":   round(m_score, 3),
            "maturity_label":   maturity_label(m_score),
            "total_episodes":   len(episodes),
            "total_violations": total_v,
            "avg_weight":       round(avg_w, 3),
            "conscience_score": conscience_score,
            "layer_scores":     layer_scores,
            "domain_stats":     stats,
            "recent_episodes":  episode_summary,
            "pending_cached":   0,
        }, indent=2))

    else:
        # ── OFFLINE path — render full dashboard from static + local cache ────
        cached_episodes = load_cached_episodes()
        pending         = len(cached_episodes)
        stats           = domain_stats(STATIC_NORMS)

        # If we have cached violations, reflect them in the offline stats
        domain_hit = {}
        for ep in cached_episodes:
            d = ep.get("norm_domain", "harm")
            if ep.get("verdict") in ("BLOCK", "MODIFY", "violation"):
                domain_hit[d] = domain_hit.get(d, 0) + 1
        for d, hits in domain_hit.items():
            if d in stats:
                stats[d]["violations"] = hits
                if hits >= 5:
                    stats[d]["status"] = "sensitised"
                elif hits >= 2:
                    stats[d]["status"] = "elevated"

        total_v = sum(d["violations"] for d in stats.values())
        m_score = min(0.24, total_v * 0.02)  # offline = newborn or early child

        recent_summary = [
            {
                "id":      e.get("episode_id", "?"),
                "action":  e.get("action", "?"),
                "verdict": e.get("verdict", "?"),
                "domain":  e.get("norm_domain", "?"),
                "severity": round(float(e.get("severity", 0.0)), 3),
                "cached":  True,
            }
            for e in cached_episodes[-10:]
        ]

        print(json.dumps({
            "online":           False,
            "offline_banner":   (
                f"⚠ Conscience service offline (localhost:8080 unreachable). "
                f"Showing static baseline + {pending} locally cached episode(s). "
                f"Start your service to restore live calibration."
            ),
            "maturity_score":   round(m_score, 3),
            "maturity_label":   maturity_label(m_score),
            "total_episodes":   pending,
            "total_violations": total_v,
            "avg_weight":       1.0,
            "conscience_score": None,
            "layer_scores":     {},
            "domain_stats":     stats,
            "recent_episodes":  recent_summary,
            "pending_cached":   pending,
        }, indent=2))


if __name__ == "__main__":
    insights()
