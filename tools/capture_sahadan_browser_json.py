#!/usr/bin/env python3
"""Capture Sahadan JSON from a same-origin Chromium browser session.

The script deliberately writes the output only after every requested day has
returned a valid HTTP 200 JSON payload with the expected data.soccer structure.
"""
import argparse
import asyncio
import json
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.async_api import async_playwright

SITE_URL = "https://www.sahadan.com/genis-iddaa-programi"
API_URL = (
    "https://www.sahadan.com/api/index/betting-service-bulletin-soccer"
    "?a=bs&e=bsbp&u=soccer&application=mackolik.com&language=tr&country=tr&date={}"
)


def day_range(start_text, end_text):
    start = date.fromisoformat(start_text)
    end = date.fromisoformat(end_text)
    if start > end:
        raise RuntimeError("start_date is after end_date")
    return [(start + timedelta(days=i)).isoformat() for i in range((end-start).days + 1)]


async def fetch_one(page, day):
    result = await page.evaluate(
        """async ({url, day}) => {
            const response = await fetch(url, {
                method: 'GET',
                credentials: 'include',
                cache: 'no-store',
                headers: {
                    'Accept': 'application/json, text/plain, */*',
                    'x-render-mode': 'csr',
                    'x-route-name': 'genis-iddaa-programi___tr',
                    'x-route-path': '/genis-iddaa-programi'
                }
            });
            const text = await response.text();
            let payload = null;
            let jsonError = '';
            try { payload = JSON.parse(text); }
            catch (e) { jsonError = String(e); }
            return {
                status: response.status,
                contentType: response.headers.get('content-type') || '',
                payload,
                jsonError,
                bodyStart: text.slice(0, 160)
            };
        }""",
        {"url": API_URL.format(day), "day": day},
    )
    status = int(result.get("status") or 0)
    if status != 200:
        raise RuntimeError(
            f"{day}: HTTP {status}; content-type={result.get('contentType') or 'unknown'}; "
            f"body-start={result.get('bodyStart') or ''!r}"
        )
    payload = result.get("payload")
    if not isinstance(payload, dict):
        raise RuntimeError(f"{day}: HTTP 200 but response is not valid JSON ({result.get('jsonError')})")
    data = payload.get("data")
    soccer = data.get("soccer") if isinstance(data, dict) else None
    if not isinstance(soccer, list):
        raise RuntimeError(f"{day}: HTTP 200 but data.soccer is missing")
    match_count = 0
    finished_count = 0
    for league in soccer:
        if not isinstance(league, dict):
            continue
        matches = league.get("matches") or []
        if not isinstance(matches, list):
            continue
        for match in matches:
            if not isinstance(match, dict):
                continue
            match_count += 1
            if match.get("ft_A") not in (None, "") and match.get("ft_B") not in (None, ""):
                finished_count += 1
    return {
        "ok": True,
        "http_status": 200,
        "league_count": len(soccer),
        "match_count": match_count,
        "finished_count": finished_count,
        "payload": payload,
    }


async def capture(start, end, output):
    dates = day_range(start, end)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            locale="tr-TR",
            timezone_id="Europe/Istanbul",
            viewport={"width": 1440, "height": 1000},
        )
        page = await context.new_page()
        try:
            response = await page.goto(
                SITE_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page_status = response.status if response else 0
            print(f"SAHADAN_PAGE status={page_status or 'no-response'} url={page.url}", flush=True)
            if page_status and page_status >= 400:
                raise RuntimeError(f"Sahadan site page could not be opened: HTTP {page_status}")
            await page.wait_for_timeout(2500)

            day_results = {}
            for index, day in enumerate(dates, start=1):
                print(f"CAPTURE {index}/{len(dates)} {day}", flush=True)
                try:
                    day_results[day] = await fetch_one(page, day)
                except Exception as exc:
                    # A single retry is reserved for transient gateway errors.
                    message = str(exc)
                    if "HTTP 502" in message or "HTTP 503" in message or "HTTP 504" in message:
                        await page.wait_for_timeout(8000)
                        day_results[day] = await fetch_one(page, day)
                    else:
                        raise
                item = day_results[day]
                print(
                    f"CAPTURE_OK {day} leagues={item['league_count']} "
                    f"matches={item['match_count']} finished={item['finished_count']}",
                    flush=True,
                )
                if index < len(dates):
                    await page.wait_for_timeout(1800 + random.randint(0, 700))

            wrapper = {
                "source": "Sahadan browser auto capture",
                "requested_at": datetime.now(timezone.utc).isoformat(),
                "timezone": "Europe/Istanbul",
                "start_date": start,
                "end_date": end,
                "days": day_results,
            }
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary = output.with_suffix(output.suffix + ".tmp")
            temporary.write_text(
                json.dumps(wrapper, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(output)
            print(
                f"CAPTURE_COMPLETE dates={start}..{end} "
                f"days={len(day_results)} file={output} bytes={output.stat().st_size}",
                flush=True,
            )
        finally:
            await context.close()
            await browser.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(capture(args.start, args.end, args.output))


if __name__ == "__main__":
    main()
