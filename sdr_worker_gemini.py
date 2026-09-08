"""Gemini adapter for the existing LeadEngine SDR worker.

Keeps Gmail/Supabase logic unchanged while replacing the AI provider with Gemini.
"""
from __future__ import annotations

import os
import requests

import sdr_worker

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.7-flash")


def gemini_text(prompt: str) -> str:
    if not GEMINI_API_KEY:
        raise RuntimeError("Missing GEMINI_API_KEY")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    response = requests.post(
        url,
        params={"key": GEMINI_API_KEY},
        headers={"Content-Type": "application/json"},
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=90,
    )
    if not response.ok:
        raise RuntimeError(f"Gemini failed {response.status_code}: {response.text[:700]}")
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


sdr_worker.openai_text = gemini_text


if __name__ == "__main__":
    sdr_worker.main()
