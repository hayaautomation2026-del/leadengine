"""Backfill public contact details for LeadEngine leads missing email/WhatsApp."""

from niche_worker import SUPABASE_KEY, SUPABASE_URL, discover_contact_details, now, sb


def run(limit=30):
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_KEY are required")

    rows = sb(
        "GET",
        "leads",
        params={
            "select": "id,business_name,website_url,email,whatsapp_number",
            "website_url": "not.is.null",
            "order": "created_at.asc",
            "limit": str(limit),
            "or": "(email.is.null,whatsapp_number.is.null)",
        },
    ) or []

    checked = email_found = whatsapp_found = updated = 0

    for lead in rows:
        website = str(lead.get("website_url") or "").strip()
        if not website:
            continue

        checked += 1
        email, whatsapp = discover_contact_details(website)
        patch = {"last_checked_at": now()}
        changed = False

        if email and not lead.get("email"):
            patch["email"] = email
            patch["email_source"] = "website"
            email_found += 1
            changed = True

        if whatsapp and not lead.get("whatsapp_number"):
            patch["whatsapp_number"] = whatsapp
            whatsapp_found += 1
            changed = True

        if changed:
            sb("PATCH", "leads", params={"id": f"eq.{lead['id']}"}, body=patch)
            updated += 1
        else:
            sb("PATCH", "leads", params={"id": f"eq.{lead['id']}"}, body={"last_checked_at": now()})

    print(
        f"CONTACT ENRICHMENT | checked={checked} updated={updated} "
        f"emails={email_found} whatsapp={whatsapp_found}"
    )


if __name__ == "__main__":
    run()
