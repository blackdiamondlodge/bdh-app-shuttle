"""Client for the bdl_shuttle AppDaemon app, which keeps the reminders and
plays them on the dining room speaker even when nobody has this page open."""
import requests
from django.core.cache import cache

from . import config

STOP = "Lookout Ridge"
DESTINATION = "the Village"
PENDING_CACHE = "shuttle:reminders"


def enabled():
    return bool(config.reminders_url())


def call(cmd, timeout=10, **fields):
    """POST a command. Returns (payload, error, http_status)."""
    body = {"cmd": cmd}
    body.update({k: v for k, v in fields.items() if v is not None})
    try:
        r = requests.post(config.reminders_url(), json=body, timeout=timeout,
                          headers={"X-API-Key": config.reminders_api_key()})
    except requests.RequestException:
        return None, "The lodge speaker system isn't answering. Try again in a minute.", 502
    try:
        data = r.json()
    except ValueError:
        return None, f"Bad response from the lodge controller (HTTP {r.status_code})", 502
    if r.status_code != 200:
        msg = data.get("error") if isinstance(data, dict) else None
        return None, msg or f"HTTP {r.status_code}", (400 if r.status_code == 400 else 502)
    if cmd in ("add", "cancel", "clear"):
        cache.delete(PENDING_CACHE)
    return data, None, 200


def add(departure, lead_min):
    return call("add", departure=departure.isoformat(), lead_min=lead_min,
                stop=STOP, destination=DESTINATION)


def cancel(rid):
    return call("cancel", id=rid)


def pending():
    """Pending reminders (cached briefly), [] on any error."""
    if not enabled():
        return []
    items = cache.get(PENDING_CACHE)
    if items is None:
        data, err, _ = call("list", timeout=3)
        items = (data or {}).get("reminders", []) if not err else []
        cache.set(PENDING_CACHE, items, 30)
    return items
