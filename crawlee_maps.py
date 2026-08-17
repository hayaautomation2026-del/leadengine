"""Crawlee/Playwright Google Maps fallback for LeadEngine.

V1 contract: only returns usable leads with name + phone + address.
Website is included when Google exposes it. Maps URL is always included.

This intentionally avoids Google Maps CSS class names. It prefers semantic
attributes (roles, aria-labels, data-item-id) and raises explicit errors for
block pages or selector drift instead of silently returning an empty list.
"""

from __future__ import annotations

import asyncio
import re
from datetime import timedelta
from urllib.parse import quote_plus


class MapsBlockedError(RuntimeError):
    pass


class SelectorDriftError(RuntimeError):
    pass


def _strip_prefix(value: str | None, labels: tuple[str, ...]) -> str | None:
    if not value:
        return None
    text = re.sub(r"\s+", " ", value).strip()
    lowered = text.lower()
    for label in labels:
        prefix = label.lower() + ":"
        if lowered.startswith(prefix):
            return text[len(prefix):].strip() or None
    return text or None


async def _first_attr(page, selectors: list[str], attr: str) -> str | None:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.count():
                value = await locator.get_attribute(attr)
                if value:
                    return value.strip()
        except Exception:
            continue
    return None


async def _first_text(page, selectors: list[str]) -> str | None:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.count():
                value = (await locator.inner_text()).strip()
                if value:
                    return value
        except Exception:
            continue
    return None


async def _accept_consent_if_present(page) -> None:
    for pattern in (r"accept all", r"accept", r"agree", r"i agree"):
        try:
            button = page.get_by_role("button", name=re.compile(pattern, re.I)).first
            if await button.count() and await button.is_visible():
                await button.click(timeout=2500)
                await page.wait_for_timeout(1200)
                return
        except Exception:
            continue


async def _detect_block(page) -> None:
    try:
        body = (await page.locator("body").inner_text()).lower()
    except Exception:
        return
    markers = (
        "unusual traffic",
        "automated queries",
        "our systems have detected",
        "sorry, but your computer or network may be sending automated queries",
        "recaptcha",
    )
    if any(marker in body for marker in markers):
        raise MapsBlockedError("google_maps_blocked_or_challenged")


async def _collect_place_urls(page, limit: int) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    for _ in range(14):
        await _detect_block(page)
        links = page.locator('a[href*="/maps/place/"]')
        count = await links.count()
        for i in range(count):
            href = await links.nth(i).get_attribute("href")
            if not href or href in seen:
                continue
            seen.add(href)
            urls.append(href)
            if len(urls) >= limit:
                return urls

        try:
            feed = page.get_by_role("feed").first
            if await feed.count():
                await feed.evaluate("el => el.scrollBy(0, Math.max(el.clientHeight * 2, 1400))")
            else:
                await page.mouse.wheel(0, 2000)
        except Exception:
            await page.mouse.wheel(0, 2000)
        await page.wait_for_timeout(1400)

    return urls


async def _extract_detail(page, maps_url: str) -> dict | None:
    await page.goto(maps_url, wait_until="domcontentloaded", timeout=45_000)
    await page.wait_for_timeout(2200)
    await _accept_consent_if_present(page)
    await _detect_block(page)

    name = await _first_text(page, ["h1", '[role="main"] h1'])

    address_selectors = [
        'button[data-item-id="address"]',
        '[data-item-id="address"]',
        'button[aria-label^="Address:"]',
    ]
    address_label = await _first_attr(page, address_selectors, "aria-label")
    address = _strip_prefix(address_label, ("Address",))
    if not address:
        address = await _first_text(page, address_selectors)

    phone_selectors = [
        'button[data-item-id^="phone:tel:"]',
        '[data-item-id^="phone:tel:"]',
        'button[aria-label^="Phone:"]',
    ]
    phone_label = await _first_attr(page, phone_selectors, "aria-label")
    phone = _strip_prefix(phone_label, ("Phone", "Call"))
    if not phone:
        phone_item_id = await _first_attr(page, phone_selectors, "data-item-id")
        if phone_item_id and "phone:tel:" in phone_item_id:
            phone = phone_item_id.split("phone:tel:", 1)[1].strip() or None
    if not phone:
        phone = await _first_text(page, phone_selectors)

    website = await _first_attr(
        page,
        [
            'a[data-item-id="authority"]',
            'a[aria-label^="Website:"]',
        ],
        "href",
    )

    if not name or not phone or not address:
        return None

    return {
        "name": name,
        "phone": phone,
        "site": website,
        "url": maps_url,
        "address": address,
    }


async def _scrape(query: str, limit: int) -> list[dict]:
    try:
        from crawlee.crawlers import PlaywrightCrawler, PlaywrightCrawlingContext
    except Exception as exc:
        raise RuntimeError("crawlee_playwright_not_installed") from exc

    results: list[dict] = []
    diagnostics = {"search_page_loaded": False, "place_urls": 0, "detail_pages": 0}

    crawler = PlaywrightCrawler(
        headless=True,
        max_requests_per_crawl=1,
        max_request_retries=1,
        request_handler_timeout=timedelta(seconds=180),
        abort_on_error=True,
    )

    @crawler.router.default_handler
    async def handler(context: PlaywrightCrawlingContext) -> None:
        page = context.page
        diagnostics["search_page_loaded"] = True
        await _accept_consent_if_present(page)
        await page.wait_for_timeout(2200)
        await _detect_block(page)

        place_urls = await _collect_place_urls(page, max(limit * 3, limit + 8))
        diagnostics["place_urls"] = len(place_urls)

        if not place_urls:
            raise SelectorDriftError("selector_drift:no_place_links_found")

        for url in place_urls:
            if len(results) >= limit:
                break
            diagnostics["detail_pages"] += 1
            try:
                row = await _extract_detail(page, url)
                if row:
                    results.append(row)
            except MapsBlockedError:
                raise
            except Exception as exc:
                print(f"CRAWLEE detail skipped | {url} | {type(exc).__name__}: {exc}")
            await page.wait_for_timeout(850)

        if not results:
            raise SelectorDriftError(
                f"selector_drift:place_links={diagnostics['place_urls']},usable_details=0"
            )

    search_url = (
        "https://www.google.com/maps/search/?api=1&query="
        f"{quote_plus(query)}&hl=en"
    )
    await crawler.run([search_url])

    if not results:
        raise SelectorDriftError(
            f"selector_drift:loaded={diagnostics['search_page_loaded']},"
            f"place_links={diagnostics['place_urls']},detail_pages={diagnostics['detail_pages']}"
        )

    print(
        "CRAWLEE MAPS | "
        f"place_urls={diagnostics['place_urls']} "
        f"detail_pages={diagnostics['detail_pages']} "
        f"usable={len(results)}"
    )
    return results[:limit]


def run_crawlee_google_maps(query: str, limit: int = 20) -> list[dict]:
    return asyncio.run(_scrape(query, limit))
