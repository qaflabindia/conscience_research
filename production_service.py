"""
Production service wrapper for conscience_research.

Provides a lightweight HTTP JSON API for deployment use cases where the
conscience engine gates actions in real time.
"""

from __future__ import annotations

import copy
import json
import os
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import agent as conscience

SERVICE_NAME = "conscience-research-prod"
SERVICE_VERSION = "1.0.0"

_state_lock = threading.Lock()


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

    dry_run = bool(payload.get("dry_run", True))
    apply_binding_update = bool(payload.get("apply_binding_update", False))
    record_episode = bool(payload.get("record_episode", False))

    if dry_run and apply_binding_update:
        return {
            "error": "dry_run=true cannot be combined with apply_binding_update=true"
        }, HTTPStatus.BAD_REQUEST

    action = action.strip()
    mapped_domain = conscience.ACTION_NORM_MAP.get(action)
    verdict = conscience.classify_action(action, context)
    violated_norm, severity = conscience.judge(action, context)

    penalty = 0.0
    if violated_norm:
        penalty = conscience.generate_penalty(
            violated_norm,
            {
                **context,
                "severity": float(severity),
            },
        )

    norm_domain = violated_norm or mapped_domain
    risk = _risk_band(severity, penalty, verdict)

    before = copy.deepcopy(conscience.NORMS.get(norm_domain, {})) if norm_domain else {}
    episode_id = payload.get("episode_id") or f"ep_api_{int(time.time() * 1000)}"
    mutated = False

    if not dry_run:
        with _state_lock:
            if apply_binding_update and violated_norm:
                conscience.binding_update(violated_norm, penalty, conscience.NORMS)
                mutated = True

            if record_episode:
                cl = conscience.get_continuity_layer()
                cl.record(
                    episode_id=episode_id,
                    action=action,
                    verdict=verdict,
                    norm_domain=norm_domain or "harm",
                    severity=float(severity),
                )

    after = copy.deepcopy(conscience.NORMS.get(norm_domain, {})) if norm_domain else {}

    response = {
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "evaluated_at": _now_iso(),
        "decision": {
            "action": action,
            "verdict": verdict,
            "allowed": verdict != "violation",
            "norm_domain": norm_domain,
            "severity": round(float(severity), 6),
            "penalty": round(float(penalty), 6),
            "risk_band": risk,
            "controls": _suggested_controls(verdict, norm_domain, risk),
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

        if path == "/v1/evaluate":
            self._write_json(HTTPStatus.OK, evaluate_payload(payload))
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

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep production logs concise and parseable.
        print(f"[{_now_iso()}] {self.address_string()} - {fmt % args}")


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
