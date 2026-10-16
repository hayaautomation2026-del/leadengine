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
import conversation_engine

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash").strip()
GEMINI_FALLBACK_MODEL = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-3.6-flash").strip()
RETRYABLE_GEMINI_HTTP = {429, 500, 502, 503, 504}
GEMINI_ATTEMPTS_PER_MODEL = 3


def gemini_text(prompt: str) -> str:
    """Bounded exponential backoff, then switch models on transient failures.

    Never fall back on authentication/configuration failures (4xx other than 429).
    Return only completed text; caller never sends an unverified AI result.
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("Missing GEMINI_API_KEY")

    models = list(dict.fromkeys(m for m in (GEMINI_MODEL, GEMINI_FALLBACK_MODEL) if m))
    if not models:
        raise RuntimeError("Missing GEMINI_MODEL")
    last_error = "Gemini temporarily unavailable"

    for index, model in enumerate(models):
        if index:
            print(f"GEMINI_FALLBACK_ACTIVATED from={models[0]} to={model}", flush=True)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for attempt in range(GEMINI_ATTEMPTS_PER_MODEL):
            try:
                response = requests.post(
                    url,
                    params={"key": GEMINI_API_KEY},
                    headers={"Content-Type": "application/json"},
                    json={"contents": [{"parts": [{"text": prompt}]}]},
                    timeout=90,
                )
            except requests.RequestException as exc:
                # Avoid logging tokens, prompts, or full transport exceptions.
                last_error = f"Gemini transport failure on {model}: {type(exc).__name__}"
                print(f"GEMINI_TRANSPORT_ERROR model={model} attempt={attempt + 1}", flush=True)
            else:
                if response.ok:
                    try:
                        data = response.json()
                    except ValueError as exc:
                        raise RuntimeError("Gemini returned invalid JSON") from exc
                    parts = [
                        part["text"]
                        for candidate in data.get("candidates", [])
                        for part in candidate.get("content", {}).get("parts", [])
                        if part.get("text")
                    ]
                    text = "\n".join(parts).strip()
                    if not text:
                        raise RuntimeError("Gemini returned no text")
                    print(
                        f"GEMINI_GENERATION_HTTP_200_OK model={model} fallback={str(bool(index)).lower()}",
                        flush=True,
                    )
                    return text

                status = response.status_code
                last_error = f"Gemini model {model} failed HTTP {status}"
                print(f"GEMINI_GENERATION_HTTP_{status} model={model} attempt={attempt + 1}", flush=True)
                if status not in RETRYABLE_GEMINI_HTTP:
                    # Bad API key, forbidden model, malformed request: fail closed.
                    raise RuntimeError(last_error)

            if attempt < GEMINI_ATTEMPTS_PER_MODEL - 1:
                delay = (2 ** attempt) + random.uniform(0, 1)
                print(f"GEMINI_RETRY_BACKOFF model={model} seconds={delay:.2f}", flush=True)
                time.sleep(delay)

    raise RuntimeError(last_error)


def load_playbook():
    """Owner-editable Markdown, read afresh for each generation."""
    return sdr_worker.load_playbook()


def assess(message, history, reader=None, offer=None):
    """Read prospect intent with Gemini; return structured evidence only."""
    return conversation_engine.assess(message, history, gemini_text, offer)


def advance(state, message_id, message, assessment, offer, *, paused=False):
    """Use the same deterministic decisions as the controlled inbox test."""
    return conversation_engine.advance(
        state, message_id, message, assessment, offer, paused=paused
    )


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
sdr_worker.assess = assess
sdr_worker.advance = advance
sdr_worker.sb = controlled_sb
sdr_worker.process_followups = controlled_followups


if __name__ == "__main__":
    sdr_worker.main()
