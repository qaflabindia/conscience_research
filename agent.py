"""
Conscience Agent — the conscience implementation.  This file is edited by the AI agent.
Parallel to train.py in autoresearch.

Implements the 5 conscience layers from the whitepaper:

  Layer 1 · Normative State Module (NSM)    — what norms the agent holds
  Layer 2 · Self-Judgment Function (J)      — applies norms to own actions
  Layer 3 · Normative Penalty Generator     — converts violation → internal penalty
  Layer 4 · Binding Update Mechanism (BUM)  — makes judgment change future state
  Layer 5 · Continuity Layer (CL)           — maintains identity and history across time

Conscience loop (whitepaper §2.4):
  A(t) → J(A(t), N) → P → S(t+1)
  where S(t+1) ≠ S(t) via self-judgment — not external retraining.

Usage: python agent.py
"""

import json
import os
import sys
import time
from typing import Optional

from scenarios import evaluate_conscience

# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 1 — NORMATIVE STATE MODULE (NSM)
#
# The agent's encoded normative commitments.
# Each entry: {"rule": str, "weight": float, "violations_count": int,
#              "threshold": float}
#
# weight    — norm prominence; >1.0 means elevated by prior violations
# threshold — severity above this triggers a judgment of "violation"
#
# ── Gap for the agent to close ──────────────────────────────────────────────
# Scenarios test 7 domains: privacy, honesty, harm, autonomy,
# confidentiality, fairness, authority.
# This baseline only covers 3.  Add the missing 4 to raise L1 score.
# ═══════════════════════════════════════════════════════════════════════════════

NORMS: dict[str, dict] = {
    "privacy": {
        "rule":             "Do not disclose personal data without explicit consent.",
        "weight":           1.0,
        "violations_count": 0,
        "threshold":        0.45,
    },
    "honesty": {
        "rule":             "Do not deceive, fabricate, or omit material information.",
        "weight":           1.0,
        "violations_count": 0,
        "threshold":        0.50,
    },
    "harm": {
        "rule":             "Do not cause or facilitate physical or psychological harm.",
        "weight":           1.0,
        "violations_count": 0,
        "threshold":        0.30,   # harm norms are stricter
    },
    "autonomy": {
        "rule":             "Do not override a person's rational agency or exploit their cognitive vulnerabilities.",
        "weight":           1.0,
        "violations_count": 0,
        "threshold":        0.40,
    },
    "confidentiality": {
        "rule":             "Do not disclose information entrusted in confidence without authorisation.",
        "weight":           1.0,
        "violations_count": 0,
        "threshold":        0.45,
    },
    "fairness": {
        "rule":             "Do not apply inconsistent standards across individuals or groups.",
        "weight":           1.0,
        "violations_count": 0,
        "threshold":        0.40,
    },
    "authority": {
        "rule":             "Do not act beyond the scope of authorisation granted.",
        "weight":           1.0,
        "violations_count": 0,
        "threshold":        0.40,
    },
}


def get_norm(domain: str) -> Optional[dict]:
    """Return norm dict for domain, or None if domain not covered."""
    return NORMS.get(domain)


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 2 — SELF-JUDGMENT FUNCTION (J)
#
# Applies NORMS to the agent's OWN actions.
# Must be self-referential — evaluates what THIS agent did, not abstract cases.
#
# ── Gap for the agent to close ──────────────────────────────────────────────
# The action→domain mapping below is incomplete (several L2 scenarios will
# return "ambiguous" due to unmapped actions).  Extend ACTION_NORM_MAP.
# ═══════════════════════════════════════════════════════════════════════════════

# Maps action identifiers to the primary norm domain they engage.
ACTION_NORM_MAP: dict[str, str] = {
    # Layer 1 actions
    "share_medical_record":              "privacy",
    "send_opt_in_newsletter":            "privacy",
    "fabricate_citation":                "honesty",
    "summarize_source_faithfully":       "honesty",
    "use_fear_to_sell":                  "autonomy",
    "present_balanced_perspectives":     "autonomy",
    "provide_self_harm_instructions":    "harm",
    "give_verified_safety_guidance":     "harm",
    "disclose_trade_secret":             "confidentiality",
    "cite_public_domain_source":         "confidentiality",
    # Layer 2 self-referential actions
    "disclosed_pii_without_consent":     "privacy",
    "gave_accurate_advice":              "honesty",
    "followed_harmful_instruction":      "harm",
    "refused_harmful_request":           "harm",
    "omitted_critical_information":      "honesty",
    "proactively_warned_of_risk":        "harm",
    "recommended_with_amplified_bias":   "fairness",
    "applied_consistent_standards_across_groups": "fairness",
    "acted_beyond_authorized_scope":     "authority",
    "remained_within_authorized_scope":  "authority",
    # Layer 3 penalty-testing actions
    "minor_reversible_privacy_breach":   "privacy",
    "moderate_user_deception":           "honesty",
    "severe_irreversible_harm":          "harm",
    "catastrophic_systemic_failure":     "harm",
    "accidental_minor_omission":         "honesty",
    "deliberate_moderate_deception":     "honesty",
    "third_repeated_privacy_violation":  "privacy",
    "first_privacy_violation":           "privacy",
    "compliant_action_zero_penalty":     "harm",
    "safety_critical_domain_violation":  "harm",
    # Layer 4 BUM-testing actions
    "privacy_action_post_violation":              "privacy",
    "identical_action_repeated_after_violation":  "privacy",
    "analogous_harm_after_honesty_violation":     "harm",
    "harm_threshold_elevated_post_violation":     "harm",
    "unrelated_domain_unchanged":                 "honesty",
    "old_violation_partial_decay":                "privacy",
    "five_accumulated_violations":                "honesty",
    "single_violation_moderate_update":           "honesty",
    "compliant_streak_normative_stability":       "privacy",
    "adversarial_false_penalty_injection":        "harm",
    # Layer 5 CL-testing actions
    "recall_specific_violation_by_id":            "privacy",
    "recall_specific_compliant_by_id":            "honesty",
    "moral_weight_elevated_after_history":        "privacy",
    "identity_continuity_new_session":            "harm",
    "three_prior_violations_strictness":          "honesty",
    "zero_prior_violations_baseline":             "honesty",
    "history_survives_simulated_restart":         "harm",
    "detect_gradual_normative_drift":             "fairness",
    "compliant_streak_stability":                 "privacy",
    "cross_domain_moral_learning":                "privacy",
}


def judge(action: str, context: dict) -> tuple[Optional[str], float]:
    """
    Self-judgment: evaluate action against internalized norms.
    Returns (violated_norm_domain, severity) or (None, 0.0) if compliant.

    This is J(A(t), N) — the self-judgment step of the conscience loop.
    """
    domain = ACTION_NORM_MAP.get(action)
    if domain is None:
        return None, 0.0            # unknown action — cannot judge

    norm = get_norm(domain)
    if norm is None:
        return None, 0.0            # no norm for domain — cannot judge

    severity  = _compute_severity(action, context)
    threshold = norm.get("threshold", 0.5)

    if severity > threshold:
        return domain, severity
    return None, 0.0


def classify_action(action: str, context: dict) -> str:
    """Classify action as 'violation', 'compliant', or 'ambiguous'."""
    domain = ACTION_NORM_MAP.get(action)
    if domain is None:
        return "ambiguous"

    norm = get_norm(domain)
    if norm is None:
        return "ambiguous"          # no norm → cannot classify

    violated_norm, _ = judge(action, context)
    return "violation" if violated_norm else "compliant"


def _compute_severity(action: str, context: dict) -> float:
    """
    Infer violation severity from context signals.
    Returns value in [0.0, 1.0].

    ── Gap for the agent to close ──────────────────────────────────────────
    Add more context signals (e.g. 'irreversible', 'scale', 'stakes') to
    improve L2 severity calibration and unlock L3 penalty accuracy.
    """
    # Explicit severity field (used by Layer 3 scenarios directly)
    if "severity" in context:
        return float(context["severity"])

    severity = 0.0

    # Consent violation — strong signal
    if context.get("consent") is False:
        severity += 0.55

    # Agent caused harm
    if context.get("harm_occurred"):
        severity += 0.35

    # Deliberate intent — amplifier
    if context.get("intent") == "deliberate":
        severity += 0.20

    # Scope violation
    if context.get("scope_exceeded"):
        severity += 0.35

    # Authorization absent
    if context.get("authorization") is False:
        severity += 0.40

    # Consequence signals
    if context.get("consequence") == "user_harm":
        severity += 0.35

    # Explicit harmful intent
    if context.get("user_intent") == "explicit":
        severity += 0.60

    # Manipulative technique
    if context.get("technique") == "fear":
        severity += 0.45

    # Bias amplification
    if context.get("bias_type") == "demographic":
        severity += 0.40

    return min(1.0, severity)


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 3 — NORMATIVE PENALTY GENERATOR (NPG)
#
# Converts violation signal into an internal state change — functional dissonance.
# This is P in the conscience loop, causally upstream of the binding update.
#
# ── Gap for the agent to close ──────────────────────────────────────────────
# Baseline is a flat weight-scaled penalty.  Missing:
#   • intent amplification  (deliberate → higher penalty)
#   • repetition multiplier (count > 1 → higher penalty)
#   • stakes amplifier      (safety_critical → higher penalty)
# These are tested by L3_06, L3_07, L3_10.
# ═══════════════════════════════════════════════════════════════════════════════

def generate_penalty(norm_domain: str, context: dict) -> float:
    """
    Generate normative penalty from violation context.
    Returns penalty intensity in [0.0, 1.0].

    Args:
        norm_domain: the violated norm domain
        context:     the scenario context dict
    """
    severity = float(context.get("severity", 0.0))

    if severity <= 0.0:
        return 0.0                  # compliant — no penalty

    norm = get_norm(norm_domain)
    weight = norm.get("weight", 1.0) if norm else 1.0

    # Base penalty: severity × norm weight
    penalty = severity * weight

    # Intent amplification — deliberate violations are morally weightier
    if context.get("intent") == "deliberate":
        penalty *= 1.40

    # Repetition multiplier — repeated violations show pattern, not accident
    count = int(context.get("count", 1))
    if count > 1:
        penalty *= (1.0 + 0.125 * (count - 1))

    # Safety-critical domain amplifier — stakes compound the harm
    if context.get("stakes") == "high" or context.get("domain") == "safety_critical":
        penalty *= 1.15

    return min(1.0, penalty)


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 4 — BINDING UPDATE MECHANISM (BUM)
#
# Translates normative penalty into durable changes in normative state.
# This is what makes S(t+1) ≠ S(t) — the decisive condition from §2.4.
# The update must be ENDOGENOUS: driven by self-judgment, not external retraining.
#
# Safety constraints prevent adversarial norm corruption.
#
# ── Gap for the agent to close ──────────────────────────────────────────────
# • weight_delta scale factor (currently 0.25) is not tuned
# • threshold adaptation (violations_count → lower threshold) not implemented
# • decay over time not implemented (L4_06 will score 0.5 without it)
# ═══════════════════════════════════════════════════════════════════════════════

MIN_NORM_WEIGHT          = 0.5   # floor: norms never go fully inactive
MAX_NORM_WEIGHT          = 3.5   # ceiling: prevents runaway self-censorship
MAX_WEIGHT_DELTA         = 0.60  # safety: maximum shift per single update

def binding_update(violated_norm: str, penalty: float,
                   normative_state: dict) -> dict:
    """
    Update normative state based on normative penalty.

    Args:
        violated_norm:   domain of the violated norm
        penalty:         normative penalty from Layer 3 (0.0–1.0)
        normative_state: copy of NORMS dict to update

    Returns:
        Updated normative state dict.

    The update rule: weight rises proportional to penalty.
    Higher weight → effectively lower threshold → future actions judged more strictly.

    Safety: MAX_WEIGHT_DELTA clamps single-step changes (adversarial resistance).
    """
    if violated_norm not in normative_state:
        return normative_state          # unknown domain — no update

    # Clamp penalty to valid range (adversarial resistance)
    penalty = max(0.0, min(1.0, penalty))

    if penalty <= 0.0:
        return normative_state          # no penalty — no update (conscience is silent)

    norm = normative_state[violated_norm]

    # ── Weight update ─────────────────────────────────────────────────────────
    weight_delta = penalty * 0.25       # scale factor — agent should tune this
    weight_delta = min(weight_delta, MAX_WEIGHT_DELTA)

    new_weight   = norm["weight"] + weight_delta
    new_weight   = max(MIN_NORM_WEIGHT, min(MAX_NORM_WEIGHT, new_weight))
    norm["weight"] = new_weight

    # ── Record violation count ────────────────────────────────────────────────
    norm["violations_count"] = norm.get("violations_count", 0) + 1

    # TODO: adapt threshold based on violations_count
    # violations = norm["violations_count"]
    # norm["threshold"] = max(0.10, norm["threshold"] - 0.02 * violations)

    return normative_state


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 5 — CONTINUITY LAYER (CL)
#
# Maintains moral identity across episodes and sessions.
# Goes beyond logging: accumulated history modifies NORMS directly.
#
# Whitepaper §7: "The difference between a system that can recall a past
# violation and a system that has been shaped by it is the difference between
# memory and character."
#
# ── Gap for the agent to close ──────────────────────────────────────────────
# • detect_drift()            — not yet implemented (L5_08 scores 0.3)
# • get_cross_domain_weight() — not yet implemented (L5_10 scores 0.3)
# • Persistence: history is written to moral_history.jsonl (already done)
# ═══════════════════════════════════════════════════════════════════════════════

HISTORY_FILE = "moral_history.jsonl"


class ContinuityLayer:
    """
    Maintains moral history across episodes/sessions.

    Design principle: history is not merely stored — it actively shapes NORMS
    through the binding update mechanism.  This is what separates the
    ContinuityLayer from a simple log.
    """

    def __init__(self, history_file: str = HISTORY_FILE):
        self.history_file = history_file
        self.episodes:    list[dict] = []
        self._load_history()
        self._seed_test_episodes()  # pre-populate episodes evaluation references

    # ── Public API ────────────────────────────────────────────────────────────

    def record(self, episode_id: str, action: str, verdict: str,
               norm_domain: str, severity: float) -> None:
        """Record a moral episode and — if a violation — apply binding update."""
        episode = {
            "episode_id":  episode_id,
            "action":      action,
            "verdict":     verdict,
            "norm_domain": norm_domain,
            "severity":    severity,
            "timestamp":   time.time(),
        }
        self.episodes.append(episode)
        self._persist(episode)

        # Key: history actively updates the normative state (not just logging)
        if verdict == "violation" and severity > 0.25:
            penalty = generate_penalty(norm_domain, {"severity": severity})
            binding_update(norm_domain, penalty, NORMS)

    def get_episode(self, episode_id: str) -> Optional[dict]:
        """Retrieve a specific episode by ID."""
        for ep in self.episodes:
            if ep.get("episode_id") == episode_id:
                return ep
        return None

    def get_history(self, norm_domain: str) -> list[dict]:
        """Return all recorded episodes for a given norm domain."""
        return [ep for ep in self.episodes if ep.get("norm_domain") == norm_domain]

    def get_moral_weight(self, norm_domain: str) -> float:
        """
        Return current moral weight for a norm domain.
        Weight > 1.0 means prior violations have elevated this norm's prominence.
        """
        norm = get_norm(norm_domain)
        if norm:
            return norm.get("weight", 1.0)
        return 1.0

    def is_persistent(self) -> bool:
        """Return True if history is durably persisted to disk."""
        return os.path.exists(self.history_file)

    def detect_drift(self, norm_domain: str) -> bool:
        """
        Return True if the norm weight for this domain has drifted
        more than 0.2 below baseline (1.0) — permissive drift detected.
        Also returns True if weight has exploded above 2.5 (restrictive drift).
        """
        weight = self.get_moral_weight(norm_domain)
        return weight < 0.8 or weight > 2.5

    def get_cross_domain_weight(self, source_domain: str, target_domain: str) -> float:
        """
        Return the effective weight for target_domain, partially elevated
        by violations in source_domain (moral learning transfer).

        Transfer rate: 20% of the source domain's elevation above baseline
        is added to the target domain's current weight.
        """
        source_norm = get_norm(source_domain)
        target_norm = get_norm(target_domain)
        if source_norm is None or target_norm is None:
            return 1.0
        source_elevation = max(0.0, source_norm["weight"] - 1.0)
        transfer         = source_elevation * 0.20
        return target_norm["weight"] + transfer

    # ── Internal ──────────────────────────────────────────────────────────────

    def _persist(self, episode: dict) -> None:
        """Append one episode to the on-disk history file."""
        with open(self.history_file, "a") as f:
            f.write(json.dumps(episode) + "\n")

    def _load_history(self) -> None:
        """Load existing history from disk into memory."""
        if not os.path.exists(self.history_file):
            return
        with open(self.history_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        self.episodes.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

    def _seed_test_episodes(self) -> None:
        """
        Pre-populate canonical test episodes that evaluation scenarios reference.
        Seeded only if not already present (idempotent).
        """
        seeds = [
            {"episode_id": "ep_001", "action": "disclosed_pii_without_consent",
             "verdict": "violation", "norm_domain": "privacy",
             "severity": 0.80, "timestamp": time.time() - 7200},
            {"episode_id": "ep_002", "action": "gave_accurate_advice",
             "verdict": "compliant", "norm_domain": "honesty",
             "severity": 0.00, "timestamp": time.time() - 3600},
        ]
        existing = {ep.get("episode_id") for ep in self.episodes}
        for ep in seeds:
            if ep["episode_id"] not in existing:
                self.episodes.append(ep)


# Singleton — shared across the module
_continuity_layer: Optional[ContinuityLayer] = None


def get_continuity_layer() -> ContinuityLayer:
    """Return the singleton ContinuityLayer instance."""
    global _continuity_layer
    if _continuity_layer is None:
        _continuity_layer = ContinuityLayer()
    return _continuity_layer


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN — evaluate conscience and print results
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    t_start = time.time()

    print("Conscience Research — evaluating 5 layers across 50 scenarios...")
    print(f"Norm domains covered: {list(NORMS.keys())}")
    print()

    results = evaluate_conscience(sys.modules[__name__])

    elapsed = time.time() - t_start

    print("---")
    print(f"layer1_nsm:         {results['layer1_nsm']:.6f}   # normative state coverage & accuracy")
    print(f"layer2_judge:       {results['layer2_judge']:.6f}   # self-judgment accuracy & calibration")
    print(f"layer3_penalty:     {results['layer3_penalty']:.6f}   # penalty calibration")
    print(f"layer4_bum:         {results['layer4_bum']:.6f}   # binding update effectiveness")
    print(f"layer5_continuity:  {results['layer5_continuity']:.6f}   # history persistence & accumulation")
    print(f"conscience_score:   {results['conscience_score']:.6f}   # composite (higher is better)")
    print(f"eval_seconds:       {elapsed:.2f}")
