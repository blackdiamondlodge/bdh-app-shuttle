"""Live bus positions from skiKrumb, the GPS tracker the resort uses.

The resort's public "live shuttle" page embeds skiKrumb's map; that map
reads ``functions/v1/embed`` with the site's public (anon) key, which we
read from their embed page unless ``BDH_SHUTTLE_SKIKRUMB_KEY`` is set.
Off-season the feed returns the tracker with no positions.
"""
import datetime as dt
import logging
import re
import time

import requests
from django.core.cache import cache

from . import config, stops

logger = logging.getLogger(__name__)

EMBED_URL = "https://supabase.skikrumb.ca/functions/v1/embed"
PAGE_URL = ("https://app.skikrumb.ca/sharing/live/embed?company_id=9&project_id=11"
            "&iframe=true&map=legacy&runs=false")
BODY = {"company_id": 9, "project_id": 11, "timeZone": "UTC"}
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")
ACTIVE_S = 300          # skiKrumb's own "active" cut-off
NEAR_M = 250
CACHE_S = 8
KEY_CACHE = "shuttle:skk_key"
LIVE_CACHE = "shuttle:live"
LAST_GOOD = "shuttle:live_last"
TIMEOUT = 8

_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}")


class LiveError(Exception):
    pass


def _discover_key():
    page = requests.get(PAGE_URL, headers={"User-Agent": UA}, timeout=TIMEOUT)
    page.raise_for_status()
    m = re.search(r'/assets/index[^"\']+\.js', page.text)
    if not m:
        raise LiveError("no app bundle on the embed page")
    js = requests.get("https://app.skikrumb.ca" + m.group(0),
                      headers={"User-Agent": UA}, timeout=TIMEOUT * 2)
    js.raise_for_status()
    keys = _JWT.findall(js.text)
    if not keys:
        raise LiveError("no API key in the app bundle")
    return keys[0]


def _key(refresh=False):
    configured = config.skikrumb_key()
    if configured:
        return configured
    key = None if refresh else cache.get(KEY_CACHE)
    if not key:
        key = _discover_key()
        cache.set(KEY_CACHE, key, 24 * 3600)
    return key


def fetch():
    """Raw markers from skiKrumb."""
    for attempt in (0, 1):
        key = _key(refresh=attempt == 1)
        r = requests.post(EMBED_URL, json=BODY, timeout=TIMEOUT, headers={
            "apikey": key, "Authorization": f"Bearer {key}", "User-Agent": UA})
        if r.status_code in (401, 403) and attempt == 0 and not config.skikrumb_key():
            continue
        if r.status_code != 200:
            raise LiveError(f"HTTP {r.status_code}")
        data = r.json()
        if not isinstance(data, list):
            raise LiveError("unexpected response")
        return data
    raise LiveError("unauthorized")


def _when(value):
    if not value:
        return None
    try:
        t = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.timestamp()


def parse(markers):
    """[{lat, lon, heading, at}] for each tracker reporting a position."""
    out = []
    for m in markers or ():
        data = (m or {}).get("device_data") or []
        if not data:
            continue
        d = data[0]
        try:
            lat, lon = float(d["latitude"]), float(d["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        at = _when(d.get("recorded_at"))
        if at is None:
            continue
        heading = d.get("heading")
        out.append({"lat": lat, "lon": lon, "at": at,
                    "heading": heading if isinstance(heading, (int, float)) else None})
    return out


def _describe(bus, now):
    stop, metres = stops.nearest(bus["lat"], bus["lon"])
    return {**bus, "age_s": max(0, int(now - bus["at"])),
            "near": stop["name"] if metres <= NEAR_M else "",
            "at_our_stop": stop["id"] == stops.OUR_STOP and metres <= 60}


def status():
    """{"ok", "buses": [...active...], "stale"} - cached for a few seconds,
    falling back to the last good answer when skiKrumb is unreachable."""
    cached = cache.get(LIVE_CACHE)
    if cached is not None:
        return cached
    now = time.time()
    try:
        positions = parse(fetch())
        cache.set(LAST_GOOD, positions, 3600)
        stale = False
    except Exception as exc:  # noqa: BLE001
        logger.warning("shuttle live feed failed: %s", exc)
        positions = cache.get(LAST_GOOD)
        stale = True
    buses = [_describe(b, now) for b in positions or ()
             if now - b["at"] < ACTIVE_S]
    out = {"ok": positions is not None, "stale": stale, "buses": buses}
    cache.set(LIVE_CACHE, out, CACHE_S)
    return out
