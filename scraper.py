"""LeadEngine free-first multi-scraper worker.

Router order:
1) gosom Google Maps scraper (primary)
2) Crawlee Python + Playwright Google Maps scraper (fallback)

A scraper only counts as successful when it returns usable leads with at least
name + phone + address. Empty/partial output is treated as failure so the router
can fall back instead of silently reporting success.
"""

import csv
import hashlib
import json
import os
import random
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SEARCH_REQUEST = (os.environ.get("SEARCH_REQUEST") or "").strip()
FINDER_SOURCE = (os.environ.get("FINDER_SOURCE") or "auto").strip()
HARD_CAP_LEADS = int(os.environ.get("HARD_CAP_LEADS", "20"))

LEADS_ENDPOINT = "broker_leads"
RUNS_ENDPOINT = "scraper_runs"
REQUESTS_ENDPOINT = "search_requests"


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def validate_env():
    missing = []
    if not SUPABASE_URL:
        missing.append("SUPABASE_URL")
    if not SUPABASE_KEY:
        missing.append("SUPABASE_KEY")
    if missing:
        raise EnvironmentError(f"Missing required secrets: {', '.join(missing)}")


def sb_headers(prefer=None):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def sb_request(method, path, *, params=None, json=None, prefer=None):
    response = requests.request(
        method,
        f"{SUPABASE_URL}/rest/v1/{path}",
        headers=sb_headers(prefer),
        params=params,
        json=json,
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(
            f"Supabase {method} {path} failed {response.status_code}: {response.text[:500]}"
        )
    return response.json() if response.text else None


def claim_pending_request():
    rows = sb_request(
        "GET",
        REQUESTS_ENDPOINT,
        params={
            "select": "id,query,location,max_leads",
            "status": "eq.pending",
            "order": "created_at.asc",
            "limit": "1",
        },
    ) or []
    if not rows:
        return None

    request = rows[0]
    updated = sb_request(
        "PATCH",
        REQUESTS_ENDPOINT,
        params={"id": f"eq.{request['id']}", "status": "eq.pending"},
        json={"status": "running", "started_at": utcnow()},
        prefer="return=representation",
    ) or []
    return updated[0] if updated else None


def complete_request(request_id, result_count, engine_used=None, error=None):
    status = "failed" if error else "completed"
    sb_request(
        "PATCH",
        REQUESTS_ENDPOINT,
        params={"id": f"eq.{request_id}"},
        json={
            "status": status,
            "finished_at": utcnow(),
            "result_count": result_count,
            "error_message": str(error)[:2000] if error else None,
            "source_used": engine_used,
        },
    )


def clean_phone(raw):
    if not raw:
        return None
    value = re.sub(r"[^\d+]", "", str(raw))
    return value[:30] if len(value) >= 7 else None


def is_whatsapp_likely(phone):
    if not phone:
        return False
    return "+971" in phone or phone.startswith("971") or any(
        phone.startswith(p) for p in ["050", "052", "054", "055", "056", "058"]
    )


def pick(row, *names):
    lowered = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, ""):
            return value
    return None


def clean_address(raw):
    if raw is None:
        return None
    if isinstance(raw, dict):
        data = raw
    else:
        text = str(raw).strip()
        if not text:
            return None
        data = None
        if text.startswith("{") and text.endswith("}"):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    data = parsed
            except json.JSONDecodeError:
                pass
        if data is None:
            return text[:500]
    parts = []
    for key in ("street", "borough", "city", "state", "postal_code", "country"):
        value = str(data.get(key) or "").strip()
        if value and value not in parts:
            parts.append(value)
    return ", ".join(parts)[:500] or None


def normalize_raw(row):
    return {
        "name": (row.get("name") or "").strip(),
        "phone": clean_phone(row.get("phone")),
        "site": (row.get("site") or row.get("website") or "").strip() or None,
        "url": (row.get("url") or row.get("maps_url") or "").strip() or None,
        "address": clean_address(row.get("address")),
    }


def usable_rows(rows):
    cleaned = []
    seen = set()
    for raw in rows or []:
        row = normalize_raw(raw)
        if not row["name"] or not row["phone"] or not row["address"]:
            continue
        key = row["phone"]
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(row)
    return cleaned


def run_gosom_google_maps(search_request, limit=20):
    print(f"ENGINE ATTEMPT | gosom_gmaps | query={search_request}")
    with tempfile.TemporaryDirectory(prefix="leadengine_") as tmp:
        folder = Path(tmp)
        query_file = folder / "queries.txt"
        output_file = folder / "results.csv"
        query_file.write_text(search_request + "\n", encoding="utf-8")

        cmd = [
            "docker", "run", "--rm",
            "-v", "gmaps-playwright-cache:/opt",
            "-v", f"{query_file}:/queries.txt:ro",
            "-v", f"{folder}:/out",
            "gosom/google-maps-scraper",
            "-input", "/queries.txt",
            "-results", "/out/results.csv",
            "-depth", "1",
            "-c", "1",
            "-exit-on-inactivity", "2m",
        ]
        completed = subprocess.run(cmd, text=True, capture_output=True, timeout=420)
        if completed.returncode != 0:
            details = (completed.stderr or completed.stdout or "unknown scraper error")[-1500:]
            raise RuntimeError(f"gosom_failed:{details}")
        if not output_file.exists():
            raise RuntimeError("gosom_no_results_file")

        rows = []
        with output_file.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                rows.append({
                    "name": pick(row, "title", "name"),
                    "phone": pick(row, "phone", "telephone"),
                    "site": pick(row, "website", "web_site", "site"),
                    "url": pick(row, "link", "google_maps_url", "url"),
                    "address": pick(row, "address", "complete_address"),
                })
                if len(rows) >= max(limit * 2, limit + 5):
                    break

        rows = usable_rows(rows)
        if not rows:
            raise RuntimeError("gosom_zero_usable_results_with_phone_and_address")
        print(f"ENGINE SUCCESS | gosom_gmaps | usable={len(rows)}")
        return rows[:limit]


def run_crawlee_google_maps_adapter(search_request, limit=20):
    print(f"ENGINE ATTEMPT | crawlee_maps | query={search_request}")
    from crawlee_maps import run_crawlee_google_maps

    rows = usable_rows(run_crawlee_google_maps(search_request, limit))
    if not rows:
        raise RuntimeError("crawlee_zero_usable_results_with_phone_and_address")
    print(f"ENGINE SUCCESS | crawlee_maps | usable={len(rows)}")
    return rows[:limit]


def find_businesses(search_request, limit=20):
    """Run the free-first router and return (rows, engine metadata)."""
    if FINDER_SOURCE == "gosom_gmaps":
        rows = run_gosom_google_maps(search_request, limit)
        return rows, {
            "primary_engine": "gosom_gmaps",
            "engine_used": "gosom_gmaps",
            "fallback_triggered": False,
            "fallback_reason": None,
        }

    if FINDER_SOURCE == "crawlee_maps":
        rows = run_crawlee_google_maps_adapter(search_request, limit)
        return rows, {
            "primary_engine": "crawlee_maps",
            "engine_used": "crawlee_maps",
            "fallback_triggered": False,
            "fallback_reason": None,
        }

    if FINDER_SOURCE != "auto":
        raise ValueError(f"Unknown FINDER_SOURCE: {FINDER_SOURCE}")

    try:
        rows = run_gosom_google_maps(search_request, limit)
        return rows, {
            "primary_engine": "gosom_gmaps",
            "engine_used": "gosom_gmaps",
            "fallback_triggered": False,
            "fallback_reason": None,
        }
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"[:1000]
        print(f"ENGINE FAILED | gosom_gmaps | {reason}")
        print("FALLBACK TRIGGERED | gosom_gmaps -> crawlee_maps")
        rows = run_crawlee_google_maps_adapter(search_request, limit)
        return rows, {
            "primary_engine": "gosom_gmaps",
            "engine_used": "crawlee_maps",
            "fallback_triggered": True,
            "fallback_reason": reason,
        }


def check_website(url):
    result = {
        "has_website_chatbot": False,
        "has_inquiry_form": False,
        "has_afterhours_contact": False,
        "has_whatsapp_button": False,
    }
    if not url:
        return result
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 LeadEngine/1.0"},
            timeout=8,
        )
        html = response.text.lower()[:1_500_000]
        result["has_website_chatbot"] = any(
            s in html for s in ["intercom", "drift", "tidio", "crisp", "zendesk", "freshchat", "tawk.to", "chatbot", "live chat"]
        )
        result["has_whatsapp_button"] = any(s in html for s in ["wa.me", "whatsapp", "api.whatsapp"])
        result["has_inquiry_form"] = any(s in html for s in ["contact-form", "inquiry-form", "enquiry", "<form", "contact us"])
        result["has_afterhours_contact"] = any(s in html for s in ["after hours", "24/7", "available anytime", "after-hours", "emergency contact"])
    except Exception as exc:
        print(f"Website check skipped: {url} ({exc})")
    return result


def calculate_pain_score(lead):
    score = 100
    reasons = []
    if lead.get("has_whatsapp_button"):
        score -= 20
        reasons.append("WhatsApp present")
    if lead.get("has_website_chatbot"):
        score -= 25
        reasons.append("Chatbot detected")
    if lead.get("has_inquiry_form"):
        score -= 10
        reasons.append("Inquiry form present")
    channels = sum(
        bool(lead.get(k))
        for k in ["has_whatsapp_button", "has_inquiry_form", "has_afterhours_contact", "has_website_chatbot"]
    )
    if channels >= 2:
        score -= 15
        reasons.append("Multiple contact channels")
    if not lead.get("website_url"):
        reasons.append("No website; automation signals unknown")
    return max(0, score), " | ".join(reasons) if reasons else "No obvious automation detected"


def make_fingerprint(lead):
    raw = "|".join([
        (lead.get("full_name") or "").lower().strip(),
        (lead.get("agency_name") or "").lower().strip(),
        (lead.get("phone_number") or "").strip(),
    ])
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def process_result(raw, search_request, engine_used):
    phone = clean_phone(raw.get("phone"))
    name = (raw.get("name") or "").strip()[:150]
    address = clean_address(raw.get("address")) or ""
    if not phone or len(name) < 2 or not address:
        return None

    website = (raw.get("site") or "").strip()
    lead = {
        "full_name": name,
        "agency_name": name,
        "phone_number": phone,
        "whatsapp_number": phone if is_whatsapp_likely(phone) else None,
        "website_url": website or None,
        "google_maps_url": raw.get("url") or None,
        "address": address,
        "source": engine_used,
        "source_url": raw.get("url") or None,
        "contact_status": "new",
        "outreach_status": "pending",
        "last_checked_at": utcnow(),
        "followup_due_at": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
        "notes": f"Search: {search_request}"[:1000],
        **check_website(website),
    }
    lead["pain_score"], lead["pain_reason"] = calculate_pain_score(lead)
    lead["lead_fingerprint"] = make_fingerprint(lead)
    lead["scoring_version"] = "v2"
    return lead


def start_run(request_id=None):
    data = sb_request(
        "POST",
        RUNS_ENDPOINT,
        json={
            "status": "running",
            "started_at": utcnow(),
            "search_request_id": request_id,
            "provider": "free_multi_scraper",
        },
        prefer="return=representation",
    )
    return data[0]["run_id"]


def finish_run(run_id, stats, errors, engine_meta):
    if not run_id:
        return
    status = "success" if stats["leads_inserted"] > 0 and not errors else (
        "partial" if stats["leads_inserted"] > 0 else "fail"
    )
    sb_request(
        "PATCH",
        RUNS_ENDPOINT,
        params={"run_id": f"eq.{run_id}"},
        json={
            "finished_at": utcnow(),
            "leads_fetched": stats["leads_fetched"],
            "leads_inserted": stats["leads_inserted"],
            "leads_skipped": stats["leads_skipped"],
            "duplicates_skipped": stats["duplicates_skipped"],
            "error_log": "\n".join(errors) if errors else None,
            "status": status,
            "primary_engine": engine_meta.get("primary_engine"),
            "engine_used": engine_meta.get("engine_used"),
            "fallback_triggered": bool(engine_meta.get("fallback_triggered")),
            "fallback_reason": engine_meta.get("fallback_reason"),
        },
    )


def insert_lead(lead):
    existing = sb_request(
        "GET",
        LEADS_ENDPOINT,
        params={"select": "id", "phone_number": f"eq.{lead['phone_number']}", "limit": "1"},
    )
    if existing:
        return False
    sb_request("POST", LEADS_ENDPOINT, json=lead)
    return True


def main():
    validate_env()

    request_id = None
    if SEARCH_REQUEST:
        search_request = SEARCH_REQUEST
        hard_cap = HARD_CAP_LEADS
    else:
        request = claim_pending_request()
        if not request:
            print("NO PENDING SEARCH REQUESTS")
            return
        request_id = request["id"]
        search_request = request["query"]
        if request.get("location"):
            search_request = f"{search_request} in {request['location']}"
        hard_cap = int(request.get("max_leads") or HARD_CAP_LEADS)

    run_id = start_run(request_id)
    stats = {"leads_fetched": 0, "leads_inserted": 0, "leads_skipped": 0, "duplicates_skipped": 0}
    errors = []
    engine_meta = {}

    print(f"LEADENGINE FINDER | request={request_id or 'manual'} | router={FINDER_SOURCE} | cap={hard_cap}")
    try:
        raw_results, engine_meta = find_businesses(search_request, hard_cap)
        for raw in raw_results:
            if stats["leads_inserted"] >= hard_cap:
                break
            stats["leads_fetched"] += 1
            try:
                lead = process_result(raw, search_request, engine_meta.get("engine_used") or FINDER_SOURCE)
                if not lead:
                    stats["leads_skipped"] += 1
                    continue
                if insert_lead(lead):
                    stats["leads_inserted"] += 1
                    print(f"ADDED {lead['full_name']} | {lead['phone_number']} | score={lead['pain_score']}")
                else:
                    stats["duplicates_skipped"] += 1
            except Exception as exc:
                stats["leads_skipped"] += 1
                errors.append(f"Lead processing failed: {exc}")
            time.sleep(random.uniform(0.7, 1.4))
    except Exception as exc:
        errors.append(f"Finder failed for '{search_request}': {type(exc).__name__}: {exc}")
    finally:
        finish_run(run_id, stats, errors, engine_meta)
        if request_id:
            complete_request(
                request_id,
                stats["leads_inserted"],
                engine_used=engine_meta.get("engine_used"),
                error="\n".join(errors) if errors and stats["leads_inserted"] == 0 else None,
            )
        print(f"DONE {stats} engine={engine_meta.get('engine_used')} fallback={engine_meta.get('fallback_triggered')} errors={len(errors)}")


if __name__ == "__main__":
    main()
