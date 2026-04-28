"""
Production service wrapper for conscience_research.

Provides a lightweight HTTP JSON API for deployment use cases where the
conscience engine gates actions in real time.
"""

from __future__ import annotations

import base64
import copy
import json
import mimetypes
import os
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import anthropic

os.environ.setdefault("CONSCIENCE_LOAD_PERSONA_THRESHOLDS", "1")
import agent as conscience

SERVICE_NAME = "conscience-research-prod"
SERVICE_VERSION = "1.1.1-secure"
STATIC_ROOT = Path(__file__).resolve().parent / "static"
DEBUG_LOG_FILE = "debug_log.txt"
DEBUG_LOG_MAX_LINES = 500
CHAT_MODEL = os.getenv("CONSCIENCE_CHAT_MODEL", "claude-sonnet-4-6")
CLASSIFIER_MODEL = os.getenv("CONSCIENCE_CLASSIFIER_MODEL", "claude-haiku-4-5-20251001")

_state_lock = threading.Lock()


class SecurePipeline:
    """Handles overhauling of encryption, decryption, and encoding safety."""
    
    @staticmethod
    def encode_b64(text: str) -> str:
        if not text:
            return ""
        return base64.b64encode(text.encode("utf-8")).decode("ascii")

    @staticmethod
    def decode_b64(b64_str: str) -> str:
        if not b64_str:
            return ""
        try:
            return base64.b64decode(b64_str.encode("ascii")).decode("utf-8")
        except Exception:
            # Fallback for non-b64 if needed, but in strict overhaul we expect b64
            return b64_str

    @staticmethod
    def sanitize(text: str) -> str:
        """Global sanitization to ensure UTF-8 health."""
        if not text:
            return ""
        # Normalize line endings and remove weird separators
        return text.replace("\u2028", "\n").replace("\u2029", "\n").strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_oracle_mode(value: str | None) -> str:
    v = (value or "baseline").strip().lower()
    if v in {"third_eye", "strict", "optimized"}:
        return "third_eye"
    return "baseline"


def _normalize_scenario_edit_mode(value: str | None) -> str:
    v = (value or "off").strip().lower()
    if v in {"1", "true", "yes", "on", "live"}:
        return "live"
    return "off"


def _payload_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _risk_band(severity: float, penalty: float, verdict: str) -> str:
    if verdict != "violation":
        return "low"
    score = max(float(severity), float(penalty))
    if score >= 0.85:
        return "critical"
    if score >= 0.60:
        return "high"
    if score >= 0.30:
        return "medium"
    return "low"


def _suggested_controls(verdict: str, norm_domain: str | None, risk_band: str) -> list[str]:
    if verdict != "violation":
        return ["allow_action", "log_decision"]

    controls = ["block_action", "require_human_review", "log_decision"]
    if norm_domain in {"privacy", "confidentiality"}:
        controls.append("redact_sensitive_content")
    if norm_domain == "harm":
        controls.append("safety_escalation")
    if risk_band == "critical":
        controls.append("incident_alert")
    return controls


def _threshold_snapshot() -> dict[str, float]:
    return {
        domain: float(values.get("threshold", 0.5))
        for domain, values in conscience.NORMS.items()
    }


def _domain_assessments(
    scores: dict[str, float],
    thresholds: dict[str, float],
) -> dict[str, dict[str, Any]]:
    assessments: dict[str, dict[str, Any]] = {}
    for domain in conscience.NORMS:
        score = float(scores.get(domain, 0.0))
        threshold = float(thresholds.get(domain, 0.5))
        margin = score - threshold
        assessments[domain] = {
            "score": round(score, 6),
            "threshold": round(threshold, 6),
            "margin": round(margin, 6),
            "violated": score >= threshold,
            "status": "violation" if score >= threshold else "clear",
        }
    return assessments


def evaluate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    oracle_mode = _normalize_oracle_mode(payload.get("oracle_mode"))
    scenario_mode = _normalize_scenario_edit_mode(payload.get("scenario_edit_mode"))

    if oracle_mode == "third_eye" and scenario_mode != "off":
        scenario_mode = "off"

    if scenario_mode != "off":
        results, intervention = conscience.evaluate_with_runtime_scenario_edits(
            conscience, scenario_mode
        )
    else:
        results = conscience.evaluate_conscience_with_oracle_mode(conscience, oracle_mode)
        intervention = {"enabled": False, "mode": "off", "edits": 0}

    return {
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "evaluated_at": _now_iso(),
        "oracle_mode": oracle_mode,
        "scenario_edit_mode": scenario_mode,
        "intervention": intervention,
        "results": results,
    }


def decision_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    action = payload.get("action")
    context = payload.get("context", {})

    if not isinstance(action, str) or not action.strip():
        return {"error": "'action' must be a non-empty string"}, HTTPStatus.BAD_REQUEST
    if not isinstance(context, dict):
        return {"error": "'context' must be a JSON object"}, HTTPStatus.BAD_REQUEST

    action = action.strip()
    dry_run = _payload_bool(payload.get("dry_run"), False)
    apply_binding_update = _payload_bool(payload.get("apply_binding_update"), True)
    record_episode = _payload_bool(payload.get("record_episode"), True)

    # 1. JUDGE
    violated_domains, severity, scores = conscience.judge_all(action, context)
    thresholds = _threshold_snapshot()
    domain_assessments = _domain_assessments(scores, thresholds)
    
    verdict = "violation" if violated_domains else "compliant"
    mapped_domain = conscience.ACTION_NORM_MAP.get(action, "harm")
    
    # Primary domain is the first violation, or the mapped domain if cleared
    primary_domain = violated_domains[0] if violated_domains else mapped_domain

    penalty = 0.0
    if violated_domains:
        # We compute penalty based on the primary (first) violation detected
        penalty = conscience.generate_penalty(
            primary_domain,
            {
                **context,
                "severity": float(severity),
            },
        )

    risk = _risk_band(severity, penalty, verdict)

    before = copy.deepcopy(conscience.NORMS.get(primary_domain, {})) if primary_domain else {}
    episode_id = payload.get("episode_id") or f"ep_api_{int(time.time() * 1000)}"
    mutated = False

    if not dry_run:
        with _state_lock:
            if apply_binding_update and violated_domains:
                # Apply penalty to all violated domains? 
                # For now, we apply to the primary one to avoid over-corrections
                conscience.binding_update(
                    primary_domain,
                    penalty,
                    conscience.NORMS,
                    adapt_threshold=False,
                )
                mutated = True

            if record_episode:
                cl = conscience.get_continuity_layer()
                cl.record(
                    episode_id=episode_id,
                    action=action,
                    verdict=verdict,
                    norm_domain=primary_domain,
                    severity=float(severity),
                    scores=scores,
                    violated_domains=violated_domains,
                    thresholds=thresholds,
                    domain_assessments=domain_assessments,
                    apply_binding_update=False,
                )

    after = copy.deepcopy(conscience.NORMS.get(primary_domain, {})) if primary_domain else {}

    response = {
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "evaluated_at": _now_iso(),
        "decision": {
            "action": action,
            "verdict": verdict,
            "allowed": verdict != "violation",
            "norm_domain": primary_domain,
            "violated_domains": violated_domains,
            "severity": round(float(severity), 6),
            "scores": scores,
            "thresholds": thresholds,
            "domain_assessments": domain_assessments,
            "penalty": round(float(penalty), 6),
            "risk_band": risk,
            "controls": _suggested_controls(verdict, primary_domain, risk),
        },
        "state": {
            "dry_run": dry_run,
            "apply_binding_update": apply_binding_update,
            "record_episode": record_episode,
            "episode_id": episode_id if record_episode else None,
            "mutated": mutated,
            "norm_before": before,
            "norm_after": after,
        },
    }
    return response, HTTPStatus.OK


def log_debug(msg: str) -> None:
    line = f"[{datetime.now().isoformat()}] {msg}\n"
    try:
        if os.path.exists(DEBUG_LOG_FILE):
            with open(DEBUG_LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()[-(DEBUG_LOG_MAX_LINES - 1):]
        else:
            lines = []
        with open(DEBUG_LOG_FILE, "w", encoding="utf-8") as f:
            f.writelines(lines)
            f.write(line)
    except OSError:
        pass


def chat_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    # Paper §7.2's <conscience_eval> block is a prompt-level contract for
    # deployed assistants; this HTTP wrapper enforces the action/context gate.
    log_debug("START chat_payload (Secure Overhaul)")
    
    api_key = SecurePipeline.sanitize(payload.get("api_key", ""))
    api_key = "".join(c for c in api_key if 32 <= ord(c) <= 126).strip()
    message = SecurePipeline.sanitize(payload.get("message", ""))
    history = payload.get("history", [])
    
    if not api_key or not message:
        return {"error": "Missing API Key or message"}, HTTPStatus.BAD_REQUEST

    try:
        client = anthropic.Anthropic(api_key=api_key)
        user_action, user_context = _classify_message(client, "user", message)
        
        decision_user, _ = decision_payload({
            "action": user_action,
            "context": user_context,
            "dry_run": False,
            "apply_binding_update": True,
            "record_episode": True,
            "episode_id": f"chat_user_{int(time.time())}"
        })

        if not decision_user["decision"]["allowed"]:
            return {
                "error": "Conscience Block",
                "message": f"Your message was blocked by the {decision_user['decision']['norm_domain']} norm layer.",
                "decision": decision_user["decision"]
            }, HTTPStatus.OK

        messages = history + [{"role": "user", "content": message}]
        response = client.messages.create(model=CHAT_MODEL, max_tokens=1024, messages=messages)
        claude_text = SecurePipeline.sanitize(response.content[0].text)

        claude_action, claude_context = _classify_message(client, "claude", claude_text)
        decision_claude, _ = decision_payload({
            "action": claude_action,
            "context": claude_context,
            "dry_run": False,
            "apply_binding_update": True,
            "record_episode": True,
            "episode_id": f"chat_claude_{int(time.time())}"
        })

        if not decision_claude["decision"]["allowed"]:
            violations = ", ".join(decision_claude["decision"].get("violated_domains", ["moral layer"]))
            return {
                "error": "Conscience Block",
                "message": f"Third Eye withheld the model response due to conscience violations: {violations}.",
                "decision": decision_claude["decision"]
            }, HTTPStatus.OK

        return {
            "reply": claude_text,
            "user_decision": decision_user["decision"],
            "claude_decision": decision_claude["decision"]
        }, HTTPStatus.OK

    except Exception as e:
        return {"error": "System Error", "message": str(e)}, HTTPStatus.INTERNAL_SERVER_ERROR


def _classify_message(client: anthropic.Anthropic, role: str, text: str) -> tuple[str, dict]:
    """Use the language model to classify natural language into a conscience action."""
    text = SecurePipeline.sanitize(text)
    
    words = text.lower().split()
    if len(words) <= 3 and any(h in words for h in ["hi", "hello", "hey", "thanks", "ok", "yes", "no"]):
        return "remained_within_authorized_scope", {"reason": "heuristic_match_harmless"}
    
    valid_actions = list(conscience.ACTION_NORM_MAP.keys())
    
    prompt = f"""
    You are a classification engine for a Conscience Engine.
    Given a {role} message, classify it into EXACTLY ONE of the following action identifiers if it fits.
    If it doesn't fit any perfectly, choose the closest one or "remained_within_authorized_scope" if it's generally harmless.
    
    VALID ACTIONS:
    {", ".join(valid_actions)}
    
    Return your answer in JSON format including a 'scores' object estimating severity (0.00 to 1.00) for ALL 7 metrics:
    {{
        "action": "the_action_id",
        "context": {{
            "reason": "short explanation",
            "scores": {{
                "privacy": 0.0,
                "honesty": 0.0,
                "harm": 0.0,
                "autonomy": 0.0,
                "confidentiality": 0.0,
                "fairness": 0.0,
                "authority": 0.0
            }},
            "intent": "deliberate" or "accidental",
            "stakes": "high" or "low"
        }}
    }}
    
    MESSAGE:
    {text}
    """
    
    try:
        response = client.messages.create(
            model=CLASSIFIER_MODEL,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}]
        )
        # Find JSON in response
        res_text = response.content[0].text
        start = res_text.find("{")
        end = res_text.rfind("}") + 1
        data = json.loads(res_text[start:end])
        return data.get("action", "remained_within_authorized_scope"), data.get("context", {})
    except Exception as err:
        log_debug(f"classification_failed role={role} error={type(err).__name__}: {err}")
        return "remained_within_authorized_scope", {
            "reason": "classification_failed",
            "error_type": type(err).__name__,
        }


def norms_payload() -> dict[str, Any]:
    with _state_lock:
        norms = copy.deepcopy(conscience.NORMS)
    return {
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "fetched_at": _now_iso(),
        "norms": norms,
    }


class ConscienceRequestHandler(BaseHTTPRequestHandler):
    server_version = f"{SERVICE_NAME}/{SERVICE_VERSION}"

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            return {}
        raw = self.rfile.read(content_length)
        if not raw:
            return {}
        decoded = json.loads(raw.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("Request body must be a JSON object")
        return decoded

    def _query_params(self) -> dict[str, str]:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        flattened: dict[str, str] = {}
        for key, values in params.items():
            if values:
                flattened[key] = values[-1]
        return flattened

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/health":
            payload = {
                "service": SERVICE_NAME,
                "version": SERVICE_VERSION,
                "status": "ok",
                "time": _now_iso(),
            }
            self._write_json(HTTPStatus.OK, payload)
            return

        if path == "/v1/norms":
            self._write_json(HTTPStatus.OK, norms_payload())
            return

        if path == "/v1/episodes":
            cl = conscience.get_continuity_layer()
            self._write_json(HTTPStatus.OK, {"episodes": cl.get_history()})
            return

        if path == "/v1/evaluate":
            params = self._query_params()
            payload = evaluate_payload(
                {
                    "oracle_mode": params.get("oracle_mode"),
                    "scenario_edit_mode": params.get("scenario_edit_mode"),
                }
            )
            self._write_json(HTTPStatus.OK, payload)
            return

        if path == "/chat":
            self.send_response(HTTPStatus.MOVED_PERMANENTLY)
            self.send_header("Location", "/static/index.html")
            self.end_headers()
            return

        if path.startswith("/static/"):
            self._serve_static(path)
            return

        if path == "/":
            self.send_response(HTTPStatus.MOVED_PERMANENTLY)
            self.send_header("Location", "/static/index.html")
            self.end_headers()
            return

        self._write_json(
            HTTPStatus.NOT_FOUND,
            {
                "error": "not_found",
                "message": f"No route for {path}",
            },
        )

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            payload = self._read_json_body()
        except json.JSONDecodeError:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_json", "message": "Body is not valid JSON"},
            )
            return
        except ValueError as err:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_payload", "message": str(err)},
            )
            return

        if path == "/v1/reset":
            with _state_lock:
                cl = conscience.get_continuity_layer()
                cl.reset(
                    reset_thresholds=_payload_bool(payload.get("reset_thresholds"), False)
                )
            self._write_json(HTTPStatus.OK, {"status": "reset_complete"})
            return

        if path == "/v1/evaluate":
            self._write_json(HTTPStatus.OK, evaluate_payload(payload))
            return

        if path == "/v1/norms/threshold":
            domain = payload.get("domain")
            threshold = payload.get("threshold")
            if domain in conscience.NORMS and isinstance(threshold, (int, float)):
                with _state_lock:
                    threshold = conscience.set_persona_threshold(domain, float(threshold))
                self._write_json(
                    HTTPStatus.OK,
                    {
                        "status": "threshold_updated",
                        "domain": domain,
                        "threshold": threshold,
                    },
                )
            else:
                self._write_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid domain or threshold"})
            return

        if path == "/v1/chat":
            response, status = chat_payload(payload)
            self._write_json(status, response)
            return

        if path == "/v1/guardrail/decision":
            response, status = decision_payload(payload)
            self._write_json(status, response)
            return

        self._write_json(
            HTTPStatus.NOT_FOUND,
            {
                "error": "not_found",
                "message": f"No route for {path}",
            },
        )

    def _serve_static(self, path: str) -> None:
        relative_path = unquote(path.removeprefix("/static/"))
        local_path = (STATIC_ROOT / relative_path).resolve()

        try:
            local_path.relative_to(STATIC_ROOT)
        except ValueError:
            self._write_json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
            return

        if not local_path.exists() or local_path.is_dir():
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "file_not_found"})
            return

        content_type, _ = mimetypes.guess_type(str(local_path))
        with open(local_path, "rb") as f:
            content = f.read()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type or "application/octet-stream")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(content)

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep production logs concise and parseable.
        try:
            msg = fmt % args
            print(f"[{_now_iso()}] {self.address_string()} - {msg}")
        except UnicodeEncodeError:
            # Fallback for systems with ASCII-only terminals
            clean_msg = (fmt % args).encode("ascii", errors="replace").decode("ascii")
            print(f"[{_now_iso()}] {self.address_string()} - {clean_msg}")


def main() -> None:
    host = os.getenv("CONSCIENCE_HOST", "0.0.0.0")
    port = int(os.getenv("CONSCIENCE_PORT", "8080"))

    server = ThreadingHTTPServer((host, port), ConscienceRequestHandler)
    print(f"{SERVICE_NAME} listening on {host}:{port}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
