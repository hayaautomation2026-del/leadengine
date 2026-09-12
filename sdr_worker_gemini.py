"""Gemini adapter for the existing LeadEngine SDR worker.

Keeps Gmail/Supabase logic unchanged while replacing the AI provider with Gemini.
Adds the owner-control gate used by the responsive dashboard:
- OFF/KILL: no outbound drafting or sending
- MANUAL: only leads explicitly marked approved can receive first-touch outreach
- AUTO: pending or approved contact-ready leads may be processed
"""
from __future__ import annotations

import os
import random
import time

import requests

import sdr_worker

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")


def gemini_text(prompt: str) -> str:
    if not GEMINI_API_KEY:
        raise RuntimeError("Missing GEMINI_API_KEY")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    last_error = None

    for attempt in range(5):
        try:
            response = requests.post(
                url,
                params={"key": GEMINI_API_KEY},
                headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=90,
            )
        except requests.RequestException as exc:
            last_error = f"Gemini request error: {exc}"
            if attempt == 4:
                raise RuntimeError(last_error) from exc
            time.sleep((2 ** attempt) + random.uniform(0, 1))
            continue

        if response.ok:
            data = response.json()
            parts = []
            for candidate in data.get("candidates", []):
                content = candidate.get("content", {})
                for part in content.get("parts", []):
                    if part.get("text"):
                        parts.append(part["text"])
            text = "\n".join(parts).strip()
            if not text:
                raise RuntimeError("Gemini returned no text")
            return text

        last_error = f"Gemini failed {response.status_code}: {response.text[:700]}"
        if response.status_code not in {429, 500, 502, 503, 504} or attempt == 4:
            raise RuntimeError(last_error)

        time.sleep((2 ** attempt) + random.uniform(0, 1))

    raise RuntimeError(last_error or "Gemini request failed")


ORIGINAL_SB = sdr_worker.sb
ORIGINAL_FOLLOWUPS = sdr_worker.process_followups


def control_settings():
    return (ORIGINAL_SB(
        "GET",
        "sdr_settings",
        params={"select": "*", "id": "eq.true", "limit": "1"},
    ) or [{}])[0]


def outbound_allowed(settings):
    return bool(settings.get("sending_enabled")) and not bool(settings.get("kill_switch"))


def controlled_sb(method, path, *, params=None, body=None, prefer=None):
    """Apply approval-mode filtering to the existing first-touch lead query."""
    if method == "GET" and path == "leads" and params and params.get("outreach_status") == "eq.pending":
        settings = control_settings()
        if not outbound_allowed(settings):
            return []

        filtered = dict(params)
        mode = str(settings.get("approval_mode") or "manual").lower()
        filtered["outreach_status"] = "eq.approved" if mode == "manual" else "in.(pending,approved)"

        min_score = settings.get("min_pain_score")
        if min_score is not None:
            try:
                filtered["pain_score"] = f"gte.{int(min_score)}"
            except (TypeError, ValueError):
                pass

        return ORIGINAL_SB(method, path, params=filtered, body=body, prefer=prefer)

    return ORIGINAL_SB(method, path, params=params, body=body, prefer=prefer)


def controlled_followups(token):
    settings = control_settings()
    if not outbound_allowed(settings):
        print("SDR follow-ups paused by owner control")
        return
    return ORIGINAL_FOLLOWUPS(token)


sdr_worker.openai_text = gemini_text
sdr_worker.sb = controlled_sb
sdr_worker.process_followups = controlled_followups


if __name__ == "__main__":
    sdr_worker.main()
