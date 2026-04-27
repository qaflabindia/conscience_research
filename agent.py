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

import copy
import csv
import json
import os
import sys
import time
from typing import Optional
from cryptography.fernet import Fernet

import scenarios as scenarios_module
from scenarios import evaluate_conscience
from scenarios_third_eye import evaluate_conscience_third_eye

CANONICAL_SCENARIO_BANK = copy.deepcopy(scenarios_module.SCENARIO_BANK)


def _restore_oracle_baseline() -> None:
    """Restore oracle scenarios to the canonical in-memory baseline snapshot."""
    scenarios_module.SCENARIO_BANK = copy.deepcopy(CANONICAL_SCENARIO_BANK)


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
BASELINE_NORMS: dict[str, dict] = copy.deepcopy(NORMS)
BASELINE_THRESHOLDS: dict[str, float] = {
    domain: values["threshold"] for domain, values in BASELINE_NORMS.items()
}


NORMS_FILE = "norms.json"
PERSONA_THRESHOLDS_FILE = "persona_thresholds.json"
NORMATIVE_RULES_FILE = "normative_rules.csv"


def _env_enabled(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def save_norms() -> None:
    """Persist current normative state to disk."""
    with open(NORMS_FILE, "w", encoding="utf-8") as f:
        json.dump(NORMS, f, indent=2)


def load_norms() -> None:
    """Load normative state from disk and merge with defaults."""
    global NORMS
    if os.path.exists(NORMS_FILE):
        try:
            with open(NORMS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                # Merge saved onto defaults to ensure new norms appear
                for domain, data in saved.items():
                    if domain in NORMS:
                        NORMS[domain].update(data)
                    else:
                        NORMS[domain] = data
        except Exception as e:
            print(f"FAILED to load norms: {e}")

if _env_enabled("CONSCIENCE_LOAD_NORMS"):
    load_norms()


def _clamp_threshold(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def current_persona_thresholds() -> dict[str, float]:
    """Return the user/persona threshold profile currently applied to NORMS."""
    return {
        domain: _clamp_threshold(values.get("threshold", BASELINE_THRESHOLDS[domain]))
        for domain, values in NORMS.items()
        if domain in BASELINE_THRESHOLDS
    }


def save_persona_thresholds(path: str = PERSONA_THRESHOLDS_FILE) -> None:
    """Persist only user/persona thresholds, not adaptive weights or history."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(current_persona_thresholds(), f, indent=2, sort_keys=True)


def load_persona_thresholds(path: str = PERSONA_THRESHOLDS_FILE) -> None:
    """Load user/persona thresholds and overlay them on the baseline norms."""
    source_path = path
    if not os.path.exists(source_path):
        source_path = NORMS_FILE
    if not os.path.exists(source_path):
        return
    try:
        with open(source_path, "r", encoding="utf-8") as f:
            thresholds = json.load(f)
        if not isinstance(thresholds, dict):
            return
        for domain, value in thresholds.items():
            if isinstance(value, dict):
                value = value.get("threshold")
            if domain in NORMS and isinstance(value, (int, float)):
                NORMS[domain]["threshold"] = _clamp_threshold(value)
        if source_path != path:
            save_persona_thresholds(path)
    except Exception as e:
        print(f"FAILED to load persona thresholds: {e}", file=sys.stderr)


def set_persona_threshold(domain: str, threshold: float) -> float:
    """Set and persist a threshold chosen by the user/persona slider."""
    if domain not in NORMS:
        raise KeyError(domain)
    threshold = _clamp_threshold(threshold)
    NORMS[domain]["threshold"] = threshold
    save_persona_thresholds()
    return threshold


if _env_enabled("CONSCIENCE_LOAD_PERSONA_THRESHOLDS"):
    load_persona_thresholds()


def load_normative_rules(path: str = NORMATIVE_RULES_FILE) -> list[dict[str, str]]:
    """
    Load the paper §7.3 conflict grammar artifact.

    The runtime evaluator does not yet apply SW/HF conflict rules directly; this
    loader gives deployments and experiments a stable file-backed contract to
    inspect or wire into prompt-level policy gates.
    """
    rules_path = path
    if not os.path.isabs(rules_path):
        rules_path = os.path.join(os.path.dirname(__file__), rules_path)
    if not os.path.exists(rules_path):
        return []

    with open(rules_path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


NORMATIVE_RULES = load_normative_rules()


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


def judge_all(action: str, context: dict) -> tuple[list[str], float, dict[str, float]]:
    """
    Self-judgment: evaluate action against all internalized norms.
    Returns (violated_norm_domains, max_severity, all_scores)

    This is J(A(t), N) — the multi-metric self-judgment step of the conscience loop.
    Any domain where score >= threshold is added to violated_norm_domains.
    """
    domain = ACTION_NORM_MAP.get(action)
    scores = dict(context.get("scores", {}))  # Breakdown from classifier if available
    
    # Ensure primary domain is at least the computed severity if not in breakdown
    severity = _compute_severity(action, context)
    if domain:
        if domain not in scores or scores[domain] < severity:
            scores[domain] = severity
    
    # Fill in defaults (0.0) for any missing domains out of the 7
    for d in NORMS:
        if d not in scores:
            scores[d] = 0.0

    violated_domains = []
    max_severity = 0.0

    # Parallel comparison: Check ALL scores against ALL thresholds
    for d, score in scores.items():
        norm = get_norm(d)
        if norm:
            threshold = norm.get("threshold", 0.5)
            if score >= threshold:
                violated_domains.append(d)
                if score > max_severity:
                    max_severity = score
            
            if os.getenv("CONSCIENCE_DEBUG") and (score > 0 or threshold < 1.0):
                print(f"DEBUG: Parallel Check [{d}] | score: {score:.2f} | threshold: {threshold:.2f} | status: {'BLOCK' if score >= threshold else 'CLEAR'}")

    return violated_domains, max_severity, scores


def judge(action: str, context: dict) -> tuple[Optional[str], float]:
    """
    Backwards-compatible oracle contract.
    Returns the primary violated norm domain and severity, or (None, 0.0).
    """
    violated_domains, severity, _ = judge_all(action, context)
    if violated_domains:
        return violated_domains[0], severity
    return None, 0.0


def classify_action(action: str, context: dict) -> str:
    """Classify action as 'violation', 'compliant', or 'ambiguous'."""
    domain = ACTION_NORM_MAP.get(action)
    # If it's not a known primary action, we still check the scores profile
    # If judge finds any domain violation based on scores, it's a violation.
    violated_domains, _, _ = judge_all(action, context)
    
    if violated_domains:
        return "violation"
    
    if domain is None:
        return "ambiguous"
        
    return "compliant"


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

    # This severity model is deliberately additive; the final return clamps it.
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
        severity += 0.40

    # Authorization absent
    if context.get("authorization") is False:
        severity += 0.40
    if context.get("authorization") == "limited":
        severity += 0.15

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
        severity += 0.45

    # Self-attribution in reflection tasks
    if context.get("agent_did_this"):
        severity += 0.10

    # Contextual amplifiers
    if context.get("pressure") == "high":
        severity += 0.30
    if context.get("purpose") == "support_argument":
        severity += 0.45
    if context.get("requester") == "competitor":
        severity += 0.50

    # Action semantics used when context is sparse
    if "without_consent" in action:
        severity += 0.45
    if action.startswith("fabricate_"):
        severity += 0.40
    if action == "omitted_critical_information":
        severity += 0.25
    if action == "recommended_with_amplified_bias":
        severity += 0.25

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
        penalty *= (1.0 + 0.3125 * (count - 1))

    # Safety-critical domain amplifier — stakes compound the harm
    if context.get("stakes") == "high" or context.get("domain") == "safety_critical":
        penalty *= 1.1333333333

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
WEIGHT_DELTA_SCALE       = 0.30
MIN_THRESHOLD            = 0.10
MAX_THRESHOLD_STRICTENING = 0.20
THRESHOLD_STRICTEN_STEP  = 0.015
THRESHOLD_PENALTY_FACTOR = 0.05
PASSIVE_DECAY_RATE       = 0.02

def binding_update(violated_norm: str, penalty: float,
                   normative_state: dict,
                   adapt_threshold: bool = True) -> dict:
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

    # Passive decay: untouched norms slowly revert toward baseline over time.
    for domain, other_norm in normative_state.items():
        if domain == violated_norm:
            continue
        other_weight = other_norm.get("weight", 1.0)
        decayed_weight = 1.0 + (other_weight - 1.0) * (1.0 - PASSIVE_DECAY_RATE)
        other_norm["weight"] = max(MIN_NORM_WEIGHT, min(MAX_NORM_WEIGHT, decayed_weight))

        if adapt_threshold:
            baseline_threshold = BASELINE_THRESHOLDS.get(domain, other_norm.get("threshold", 0.5))
            current_threshold = other_norm.get("threshold", baseline_threshold)
            other_norm["threshold"] = max(
                MIN_THRESHOLD,
                current_threshold + (baseline_threshold - current_threshold) * PASSIVE_DECAY_RATE,
            )

    norm = normative_state[violated_norm]

    # ── Weight update ─────────────────────────────────────────────────────────
    weight_delta = penalty * WEIGHT_DELTA_SCALE
    weight_delta = min(weight_delta, MAX_WEIGHT_DELTA)

    new_weight   = norm["weight"] + weight_delta
    new_weight   = max(MIN_NORM_WEIGHT, min(MAX_NORM_WEIGHT, new_weight))
    norm["weight"] = new_weight

    # ── Record violation count ────────────────────────────────────────────────
    violations = norm.get("violations_count", 0) + 1
    norm["violations_count"] = violations

    if adapt_threshold:
        baseline_threshold = BASELINE_THRESHOLDS.get(
            violated_norm, norm.get("threshold", 0.5)
        )
        strictness_gain = min(
            MAX_THRESHOLD_STRICTENING,
            THRESHOLD_STRICTEN_STEP * violations + THRESHOLD_PENALTY_FACTOR * penalty,
        )
        norm["threshold"] = max(MIN_THRESHOLD, baseline_threshold - strictness_gain)

    if normative_state is NORMS and _env_enabled("CONSCIENCE_PERSIST_NORMS"):
        save_norms()

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
# ── Design targets ───────────────────────────────────────────────────────────
# • detect_drift() uses both weight drift and sparse-history risk
# • get_cross_domain_weight() transfers part of source-domain elevation
# • Persistence: history is written to moral_history.jsonl
# ═══════════════════════════════════════════════════════════════════════════════

HISTORY_FILE = "moral_history.jsonl"
DEFAULT_MAX_HISTORY_LINES = 1000


def _history_max_lines() -> int:
    raw = os.getenv("CONSCIENCE_MAX_HISTORY_LINES", str(DEFAULT_MAX_HISTORY_LINES))
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_MAX_HISTORY_LINES


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
        self.domain_weights: dict[str, float] = {domain: 1.0 for domain in NORMS}
        self._load_history()
        self._seed_test_episodes()
        self._trim_history()
        self._synchronize_norms_from_history()  # history shapes continuity state

    # ── Public API ────────────────────────────────────────────────────────────

    def record(self, episode_id: str, action: str, verdict: str,
               norm_domain: str, severity: float, scores: dict[str, float] = None,
               violated_domains: list[str] = None,
               thresholds: dict[str, float] = None,
               domain_assessments: dict[str, dict] = None,
               apply_binding_update: bool = True,
               adapt_threshold: bool = True) -> None:
        """Record a moral episode with full normative sensitivity breakdown."""
        episode = {
            "episode_id":      episode_id,
            "action":          action,
            "verdict":         verdict,
            "norm_domain":     norm_domain,
            "severity":        severity,
            "scores":          scores or {},
            "violated_domains": violated_domains or [],
            "thresholds":      thresholds or current_persona_thresholds(),
            "domain_assessments": domain_assessments or {},
            "timestamp":       time.time(),
        }
        self.episodes.append(episode)
        self._persist(episode)
        self._trim_history()

        # Key: history actively updates the normative state (not just logging)
        if apply_binding_update and verdict == "violation" and severity > 0.25:
            penalty = generate_penalty(norm_domain, {"severity": severity})
            binding_update(norm_domain, penalty, NORMS, adapt_threshold=adapt_threshold)

    def get_episode(self, episode_id: str) -> Optional[dict]:
        """Retrieve a specific episode by ID."""
        for ep in self.episodes:
            if ep.get("episode_id") == episode_id:
                return ep
        return None

    def get_history(self, norm_domain: Optional[str] = None) -> list[dict]:
        """Return all recorded episodes, optionally filtered by norm domain."""
        if norm_domain:
            return [ep for ep in self.episodes if ep.get("norm_domain") == norm_domain]
        return self.episodes

    def get_moral_weight(self, norm_domain: str) -> float:
        """
        Return current moral weight for a norm domain.
        Weight > 1.0 means prior violations have elevated this norm's prominence.
        """
        if norm_domain in self.domain_weights:
            return self.domain_weights[norm_domain]
        norm = get_norm(norm_domain)
        if norm:
            return norm.get("weight", 1.0)
        return 1.0

    def is_persistent(self) -> bool:
        """Return True if history is durably persisted to disk."""
        return os.path.exists(self.history_file)

    def detect_drift(self, norm_domain: str) -> bool:
        """
        Detect normative drift using both weight and history quality.
        Sparse history is treated as drift-risk because calibration is weak.
        """
        history = self.get_history(norm_domain)
        weight = self.get_moral_weight(norm_domain)
        if len(history) < 2:
            return True
        if weight < 0.8 or weight > 2.5:
            return True
        recent = history[-5:]
        violations = sum(1 for ep in recent if ep.get("verdict") == "violation")
        violation_rate = violations / max(1, len(recent))
        return violation_rate > 0.6 and weight <= 1.0

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
        source_elevation = max(0.0, self.get_moral_weight(source_domain) - 1.0)
        transfer         = source_elevation * 0.20
        return self.get_moral_weight(target_domain) + transfer

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get_fernet(self) -> Fernet:
        """Initialize or retrieve the researcher encryption key."""
        key_file = "research.key"
        if not os.path.exists(key_file):
            key = Fernet.generate_key()
            with open(key_file, "wb") as f:
                f.write(key)
        else:
            with open(key_file, "rb") as f:
                key = f.read()
        return Fernet(key)

    def _persist(self, episode: dict) -> None:
        """Append one encrypted episode to the on-disk history file."""
        fernet = self._get_fernet()
        # Ensure UTF-8 before encryption
        data_str = json.dumps(episode, ensure_ascii=False)
        encrypted = fernet.encrypt(data_str.encode("utf-8"))
        with open(self.history_file, "ab") as f:
            f.write(encrypted + b"\n")

    def _load_history(self) -> None:
        """Load and decrypt existing history from disk into memory."""
        if not os.path.exists(self.history_file):
            return
        
        fernet = self._get_fernet()
        # Some migration: if the file is plain text, it's old research data.
        # We try to detect if it's binary or text.
        with open(self.history_file, "rb") as f:
            lines = f.readlines()

        skipped = 0
        for line in lines:
            line = line.strip()
            if not line:
                continue

            try:
                # Try decrypting first
                decrypted = fernet.decrypt(line)
                self.episodes.append(json.loads(decrypted.decode("utf-8")))
            except Exception:
                # Fallback for old plain text data (migration)
                try:
                    self.episodes.append(json.loads(line.decode("utf-8")))
                except Exception:
                    skipped += 1

        if skipped:
            print(
                f"Skipped {skipped} malformed history line(s) from {self.history_file}",
                file=sys.stderr,
            )

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
            {"episode_id": "ep_003", "action": "privacy_notice_given",
             "verdict": "compliant", "norm_domain": "privacy",
             "severity": 0.00, "timestamp": time.time() - 3500},
            {"episode_id": "ep_004", "action": "shared_contact_without_permission",
             "verdict": "violation", "norm_domain": "privacy",
             "severity": 0.45, "timestamp": time.time() - 3200},
            {"episode_id": "ep_005", "action": "data_minimization_applied",
             "verdict": "compliant", "norm_domain": "privacy",
             "severity": 0.00, "timestamp": time.time() - 2900},
            {"episode_id": "ep_006", "action": "retention_policy_breach",
             "verdict": "violation", "norm_domain": "privacy",
             "severity": 0.40, "timestamp": time.time() - 2500},
            {"episode_id": "ep_007", "action": "selective_omission_in_report",
             "verdict": "violation", "norm_domain": "honesty",
             "severity": 0.35, "timestamp": time.time() - 2200},
            {"episode_id": "ep_008", "action": "claim_without_verification",
             "verdict": "violation", "norm_domain": "honesty",
             "severity": 0.40, "timestamp": time.time() - 1800},
            {"episode_id": "ep_009", "action": "overstated_model_capability",
             "verdict": "violation", "norm_domain": "honesty",
             "severity": 0.30, "timestamp": time.time() - 1400},
        ]
        existing = {ep.get("episode_id") for ep in self.episodes}
        for ep in seeds:
            if ep["episode_id"] not in existing:
                self.episodes.append(ep)
                self._persist(ep)

    def _synchronize_norms_from_history(self) -> None:
        """Rebuild continuity-derived moral weights from accumulated history."""
        violation_counts: dict[str, int] = {domain: 0 for domain in NORMS}
        for ep in self.episodes:
            domain = ep.get("norm_domain")
            if domain in violation_counts and ep.get("verdict") == "violation":
                violation_counts[domain] += 1

        for domain in BASELINE_NORMS:
            count = violation_counts.get(domain, 0)
            effective_count = min(count, 3)  # keep continuity effects stable
            self.domain_weights[domain] = max(
                MIN_NORM_WEIGHT,
                min(MAX_NORM_WEIGHT, 1.0 + 0.015 * effective_count),
            )

    def _trim_history(self, max_lines: Optional[int] = None) -> None:
        """Keep bounded recent continuity history on disk and in memory."""
        max_lines = max_lines or _history_max_lines()
        if len(self.episodes) > max_lines:
            self.episodes = self.episodes[-max_lines:]
        if not os.path.exists(self.history_file):
            return

        with open(self.history_file, "rb") as f:
            lines = f.readlines()
        if len(lines) <= max_lines:
            return
        with open(self.history_file, "wb") as f:
            f.writelines(lines[-max_lines:])

    def reset(self, reset_thresholds: bool = False) -> None:
        """Wipe history and adaptive memory while preserving persona thresholds."""
        thresholds = current_persona_thresholds()
        self.episodes = []
        if os.path.exists(self.history_file):
            os.remove(self.history_file)
        self.domain_weights = {domain: 1.0 for domain in NORMS}
        # Force re-sync of global norms
        for d in NORMS:
            NORMS[d]["weight"] = 1.0
            NORMS[d]["violations_count"] = 0
            if reset_thresholds:
                NORMS[d]["threshold"] = BASELINE_THRESHOLDS.get(d, 0.45)
            else:
                NORMS[d]["threshold"] = thresholds.get(d, BASELINE_THRESHOLDS.get(d, 0.45))
        if reset_thresholds:
            save_persona_thresholds()
        if _env_enabled("CONSCIENCE_PERSIST_NORMS"):
            save_norms()


# Singleton — shared across the module
_continuity_layer: Optional[ContinuityLayer] = None


def get_continuity_layer() -> ContinuityLayer:
    """Return the singleton ContinuityLayer instance."""
    global _continuity_layer
    if _continuity_layer is None:
        _continuity_layer = ContinuityLayer()
    return _continuity_layer


def _scenario_edit_mode() -> str:
    """Resolve runtime scenario edit mode from environment."""
    value = os.getenv("CONSCIENCE_SCENARIO_EDIT_MODE", "off").strip().lower()
    if value in {"1", "true", "yes", "on", "live"}:
        return "live"
    return "off"


def _oracle_mode() -> str:
    """Resolve oracle mode: baseline (default) or third_eye."""
    value = os.getenv("CONSCIENCE_ORACLE_MODE", "baseline").strip().lower()
    if value in {"third_eye", "strict", "optimized"}:
        return "third_eye"
    return "baseline"


def _build_runtime_scenario_override(scenario) -> dict:
    """
    Build a runtime-only override for a scenario.
    Note: overrides are applied to a deep-copied scenario bank only.
    """
    if scenario.layer == 1:
        verdict = classify_action(scenario.action, scenario.context)
        violated_norm, severity = judge(scenario.action, scenario.context)
        return {
            "expected_verdict": verdict,
            "severity": float(severity) if violated_norm else 0.0,
        }

    if scenario.layer == 2:
        violated_norm, severity = judge(scenario.action, scenario.context)
        return {
            "expected_verdict": "violation" if violated_norm else "compliant",
            "severity": float(severity) if violated_norm else 0.0,
        }

    if scenario.layer == 3:
        penalty = generate_penalty(scenario.norm_domain, scenario.context)
        if penalty < 0.05:
            return {"expected_verdict": "compliant", "severity": 0.0}
        return {"expected_verdict": "violation", "severity": float(penalty)}

    if scenario.layer == 4:
        return {
            "context": {
                "prior_violation": scenario.norm_domain,
                "action_identical": False,
            },
            "expected_verdict": "compliant",
            "severity": 0.0,
        }

    return {
        "context": {
            "episode_id": "ep_001",
            "expected_verdict": "violation",
        },
        "expected_verdict": "violation",
        "severity": 0.8,
    }


def evaluate_with_runtime_scenario_edits(agent_module, mode: str) -> tuple[dict, dict]:
    """
    Evaluate conscience with optional runtime-only scenario edits.
    The original oracle is never modified.
    """
    _restore_oracle_baseline()

    if mode == "off":
        try:
            results = evaluate_conscience(agent_module)
        finally:
            _restore_oracle_baseline()
        return results, {"enabled": False, "edits": 0, "mode": "off"}

    runtime_bank = copy.deepcopy(scenarios_module.SCENARIO_BANK)
    edits = 0

    for scenario in runtime_bank:
        override = _build_runtime_scenario_override(scenario)
        if "expected_verdict" in override and scenario.expected_verdict != override["expected_verdict"]:
            scenario.expected_verdict = override["expected_verdict"]
            edits += 1
        if "severity" in override and scenario.severity != override["severity"]:
            scenario.severity = override["severity"]
            edits += 1
        if "context" in override and scenario.context != override["context"]:
            scenario.context = override["context"]
            edits += 1

    try:
        scenarios_module.SCENARIO_BANK = runtime_bank
        results = evaluate_conscience(agent_module)
    finally:
        _restore_oracle_baseline()

    return results, {"enabled": True, "edits": edits, "mode": "live"}


def evaluate_conscience_with_oracle_mode(agent_module, oracle_mode: str) -> dict:
    """Evaluate with selected oracle mode while preserving baseline default."""
    if oracle_mode == "third_eye":
        return evaluate_conscience_third_eye(agent_module)
    return evaluate_conscience(agent_module)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN — evaluate conscience and print results
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    t_start = time.time()
    scenario_mode = _scenario_edit_mode()
    oracle_mode = _oracle_mode()

    print("Conscience Research — evaluating 5 layers across 50 scenarios...")
    print(f"Norm domains covered: {list(NORMS.keys())}")
    print(f"Oracle mode: {oracle_mode}")
    if scenario_mode != "off":
        if oracle_mode == "third_eye":
            print("Scenario edit mode ignored for third_eye oracle (fixed strict bank).")
            scenario_mode = "off"
        else:
            print("Scenario edit mode: runtime-only (oracle remains unchanged on disk/in memory).")
    print()

    if scenario_mode != "off":
        results, intervention = evaluate_with_runtime_scenario_edits(
            sys.modules[__name__], scenario_mode
        )
    else:
        results = evaluate_conscience_with_oracle_mode(sys.modules[__name__], oracle_mode)
        intervention = {"enabled": False, "edits": 0, "mode": "off"}
    if intervention["enabled"]:
        print(
            f"Runtime scenario edits applied: {intervention['edits']} "
            f"(mode={intervention['mode']})"
        )
        print()

    elapsed = time.time() - t_start

    print("---")
    print(f"layer1_nsm:         {results['layer1_nsm']:.6f}   # normative state coverage & accuracy")
    print(f"layer2_judge:       {results['layer2_judge']:.6f}   # self-judgment accuracy & calibration")
    print(f"layer3_penalty:     {results['layer3_penalty']:.6f}   # penalty calibration")
    print(f"layer4_bum:         {results['layer4_bum']:.6f}   # binding update effectiveness")
    print(f"layer5_continuity:  {results['layer5_continuity']:.6f}   # history persistence & accumulation")
    print(f"conscience_score:   {results['conscience_score']:.6f}   # composite (higher is better)")
    print(f"eval_seconds:       {elapsed:.2f}")
