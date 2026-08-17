"""LeadEngine replaceable finder worker.

Supabase owns orchestration and queue state.
This worker only claims one pending search request, runs the configured finder,
normalizes/scores results, saves leads, and reports completion back to Supabase.
"""

import csv
import hashlib
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
FINDER_SOURCE = (os.environ.get("FINDER_SOURCE") or "gosom_gmaps").strip()
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
        json={"status": "running", "started_at": utcnow(), "source_used": FINDER_SOURCE},
        prefer="return=representation",
    ) or []
    return updated[0] if updated else None


def complete_request(request_id, result_count, error=None):
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
            "source_used": FINDER_SOURCE,
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


def run_gosom_google_maps(search_request, limit=20):
    print(f"FINDER [gosom_gmaps] -> {search_request}")
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
            raise RuntimeError(f"gosom finder failed: {details}")
        if not output_file.exists():
            raise RuntimeError("finder produced no results file")

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
                if len(rows) >= limit:
                    break
        print(f"FINDER returned {len(rows)} rows")
        return rows


def find_businesses(search_request, limit=20):
    if FINDER_SOURCE == "gosom_gmaps":
        return run_gosom_google_maps(search_request, limit)
    raise ValueError(f"Unknown FINDER_SOURCE: {FINDER_SOURCE}")


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


def process_result(raw, search_request):
    phone = clean_phone(raw.get("phone"))
    name = (raw.get("name") or "").strip()[:150]
    if not phone or len(name) < 2:
        return None

    website = (raw.get("site") or "").strip()
    lead = {
        "full_name": name,
        "agency_name": name,
        "phone_number": phone,
        "whatsapp_number": phone if is_whatsapp_likely(phone) else None,
        "website_url": website or None,
        "google_maps_url": raw.get("url") or None,
        "source": FINDER_SOURCE,
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


def start_run():
    data = sb_request(
        "POST",
        RUNS_ENDPOINT,
        json={"status": "running", "started_at": utcnow()},
        prefer="return=representation",
    )
    return data[0]["run_id"]


def finish_run(run_id, stats, errors):
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

    run_id = start_run()
    stats = {"leads_fetched": 0, "leads_inserted": 0, "leads_skipped": 0, "duplicates_skipped": 0}
    errors = []

    print(f"LEADENGINE FINDER | request={request_id or 'manual'} | source={FINDER_SOURCE} | cap={hard_cap}")
    try:
        raw_results = find_businesses(search_request, hard_cap)
        for raw in raw_results:
            if stats["leads_inserted"] >= hard_cap:
                break
            stats["leads_fetched"] += 1
            try:
                lead = process_result(raw, search_request)
                if not lead:
                    stats["leads_skipped"] += 1
                    continue
                if insert_lead(lead):
                    stats["leads_inserted"] += 1
                    print(f"ADDED {lead['full_name']} | score={lead['pain_score']}")
                else:
                    stats["duplicates_skipped"] += 1
            except Exception as exc:
                stats["leads_skipped"] += 1
                errors.append(f"Lead processing failed: {exc}")
            time.sleep(random.uniform(1, 2))
    except Exception as exc:
        errors.append(f"Finder failed for '{search_request}': {exc}")
    finally:
        finish_run(run_id, stats, errors)
        if request_id:
            complete_request(
                request_id,
                stats["leads_inserted"],
                error="\n".join(errors) if errors and stats["leads_inserted"] == 0 else None,
            )
        print(f"DONE {stats} errors={len(errors)}")


if __name__ == "__main__":
    main()
