"""Niche-aware worker for LeadEngine.

Reuses the existing free-first Maps engines from scraper.py, but writes into
public.leads and associates every lead/run with the selected niche.
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone, timedelta

import requests

from scraper import (
    FINDER_SOURCE,
    HARD_CAP_LEADS,
    calculate_pain_score,
    check_website,
    find_businesses,
)

SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SEARCH_REQUEST = (os.environ.get("SEARCH_REQUEST") or "").strip()


def now():
    return datetime.now(timezone.utc).isoformat()


def headers(prefer=None):
    h = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}
    if prefer:
        h["Prefer"] = prefer
    return h


def sb(method, path, *, params=None, body=None, prefer=None):
    r = requests.request(method, f"{SUPABASE_URL}/rest/v1/{path}", headers=headers(prefer), params=params, json=body, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Supabase {method} {path} failed {r.status_code}: {r.text[:500]}")
    return r.json() if r.text else None


def clean_phone(value):
    if not value:
        return None
    value = re.sub(r"[^\d+]", "", str(value))
    return value[:30] if len(value) >= 7 else None


def find_public_email(website):
    """Best-effort public email discovery from the business homepage.

    This is intentionally conservative: it only records an address visibly
    present in the fetched page and never guesses an email address.
    """
    if not website:
        return None
    try:
        url = website if website.startswith("http") else f"https://{website}"
        r = requests.get(url, timeout=12, headers={"User-Agent": "LeadEngine/1.0"})
        if not r.ok:
            return None
        text = r.text[:800_000]
        matches = re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.I)
        blocked = {"example.com", "example.org", "sentry.io", "wixpress.com"}
        for raw in matches:
            email = raw.lower().strip(".,;:()[]{}<>\"")
            if email.split("@")[-1] not in blocked:
                return email
    except requests.RequestException:
        return None
    return None


def fingerprint(name, phone):
    return hashlib.md5(f"{name.lower().strip()}|{phone}".encode()).hexdigest()[:16]


def run():
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_KEY are required")

    if SEARCH_REQUEST:
        rows = sb("GET", "search_requests", params={"select": "id,query,location,max_leads,niche_id", "id": f"eq.{SEARCH_REQUEST}", "limit": "1"}) or []
    else:
        rows = sb("GET", "search_requests", params={"select": "id,query,location,max_leads,niche_id", "status": "eq.pending", "order": "created_at.asc", "limit": "1"}) or []
    if not rows:
        print("No pending niche search.")
        return

    req = rows[0]
    if not req.get("niche_id"):
        raise RuntimeError("Search request has no niche_id")

    sb("PATCH", "search_requests", params={"id": f"eq.{req['id']}"}, body={"status": "running", "started_at": now()})
    run_rows = sb("POST", "scraper_runs", body={"status": "running", "started_at": now(), "search_request_id": req["id"], "provider": "free_multi_scraper", "niche_id": req["niche_id"]}, prefer="return=representation") or []
    run_id = run_rows[0]["run_id"] if run_rows else None

    query = str(req.get("query") or "").strip()
    location = str(req.get("location") or "").strip()
    search_text = f"{query} in {location}" if location else query
    limit = min(int(req.get("max_leads") or HARD_CAP_LEADS), HARD_CAP_LEADS)

    inserted = skipped = duplicates = 0
    errors = []
    engine = None
    try:
        raw, meta = find_businesses(search_text, limit)
        engine = meta.get("engine_used")
        for item in raw:
            name = str(item.get("name") or "").strip()[:150]
            phone = clean_phone(item.get("phone"))
            if not name or not phone:
                skipped += 1
                continue
            existing = sb("GET", "leads", params={"select": "id", "phone_number": f"eq.{phone}", "limit": "1"}) or []
            if existing:
                duplicates += 1
                continue

            website = str(item.get("site") or "").strip() or None
            signals = check_website(website)
            email = find_public_email(website)
            lead = {
                "niche_id": req["niche_id"],
                "full_name": name,
                "business_name": name,
                "phone_number": phone,
                "email": email,
                "email_source": "website" if email else None,
                "whatsapp_number": phone if ("+971" in phone or phone.startswith("971")) else None,
                "website_url": website,
                "google_maps_url": item.get("url"),
                "address": str(item.get("address") or "")[:500],
                "source": engine,
                "source_url": item.get("url"),
                "contact_status": "new",
                "outreach_status": "pending",
                "last_checked_at": now(),
                "followup_due_at": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
                "notes": f"Search: {search_text}"[:1000],
                "lead_fingerprint": fingerprint(name, phone),
                "scoring_version": "v3",
                **signals,
            }
            lead["pain_score"], lead["pain_reason"] = calculate_pain_score(lead)
            sb("POST", "leads", body=lead, prefer="return=minimal")
            inserted += 1
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")

    final_status = "success" if inserted and not errors else ("partial" if inserted else "fail")
    if run_id:
        sb("PATCH", "scraper_runs", params={"run_id": f"eq.{run_id}"}, body={"finished_at": now(), "leads_fetched": inserted + skipped + duplicates, "leads_inserted": inserted, "leads_skipped": skipped, "duplicates_skipped": duplicates, "error_log": "\n".join(errors) if errors else None, "status": final_status, "engine_used": engine})

    sb("PATCH", "search_requests", params={"id": f"eq.{req['id']}"}, body={"status": "failed" if errors and not inserted else "completed", "finished_at": now(), "result_count": inserted, "source_used": engine, "error_message": "\n".join(errors) if errors else None})
    print(f"NICHE WORKER | niche={req['niche_id']} inserted={inserted} duplicates={duplicates} skipped={skipped} engine={engine}")


if __name__ == "__main__":
    run()
