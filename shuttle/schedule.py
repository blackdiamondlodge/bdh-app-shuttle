"""Shuttle timetable for the Lookout Ridge stop (E7, out front of the lodge).

All times are Sun Peaks wall-clock time. BC is on permanent UTC-7 from
November 2026, so a fixed offset is used rather than a tz database that may
still think BC changes clocks.

Times are the 2025/26 East loop timetable; the resort hasn't posted 2026/27
yet. "From the lodge" is when the bus reaches Lookout Ridge on its way back
to the Village Day Lodge; "Back from the Village" is when it leaves the
Village Day Lodge, reaching Lookout Ridge about ``RIDE_MIN`` later.
"""
import datetime as dt

from django.utils import timezone

from . import config

LOCAL = dt.timezone(dt.timedelta(hours=-7))
RIDE_MIN = 10
LEADS = (5, 10, 15, 20)
MIN_LEAD = LEADS[0]
LODGE, VILLAGE = "lodge", "village"

_FROM_LODGE = ("08:30", "09:30", "11:30", "12:30", "13:30", "15:30", "16:30",
               "17:50", "18:30", "19:10", "20:20", "21:00", "21:40")
_FROM_VILLAGE = ("08:20", "09:20", "11:20", "12:20", "13:20", "15:20", "16:20",
                 "17:40", "18:20", "19:00", "20:10", "20:50", "21:30")
_BREAKS = (("10:00", "10:40"), ("14:00", "14:40"), ("19:10", "19:50"))

TIMES = {LODGE: _FROM_LODGE, VILLAGE: _FROM_VILLAGE}


def _hm(s):
    h, m = s.split(":")
    return dt.time(int(h), int(m))


def label(t):
    """8:30 AM"""
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


def short(t):
    """8:30 (for the break notes)"""
    return f"{t.hour % 12 or 12}:{t.minute:02d}"


def local_now(now=None):
    return (now or timezone.now()).astimezone(LOCAL)


def running_on(day):
    return config.season_start() <= day <= config.season_end()


def departures(day, direction=LODGE):
    return [dt.datetime.combine(day, _hm(s), LOCAL) for s in TIMES[direction]]


def relative(delta):
    mins = int(delta.total_seconds() // 60)
    if mins < 1:
        return "now"
    if mins < 60:
        return f"in {mins} min"
    h, m = divmod(mins, 60)
    return f"in {h} h {m} min" if m else f"in {h} h"


def service_day(now=None):
    """The day the board shows: today while buses are still to come, else
    the next day with service. None once the season is over."""
    now = local_now(now)
    day = now.date()
    if running_on(day) and departures(day)[-1] >= now:
        return day
    nxt = max(day + dt.timedelta(days=1), config.season_start())
    return nxt if running_on(nxt) else None


def in_season(now=None):
    """True from the first to the last day of service."""
    return running_on(local_now(now).date())


def next_departure(now=None):
    """The next bus from Lookout Ridge, as a dict, or None off-season."""
    now = local_now(now)
    day = service_day(now)
    if day is None:
        return None
    for t in departures(day):
        if t >= now:
            return {"at": t, "iso": t.isoformat(), "time": label(t),
                    "rel": relative(t - now),
                    "today": day == now.date(),
                    "village": label(t + dt.timedelta(minutes=RIDE_MIN))}
    return None


def board(direction=LODGE, now=None):
    """Departure rows for the list. Break notes are dicts with ``brk``.

    In season the list starts at the bus that most recently left (shown
    dimmed), off-season it is the whole day as a preview.
    """
    now = local_now(now)
    today = now.date()
    day = service_day(now)
    preview = day is None or day > today + dt.timedelta(days=1)
    if day is None:
        day = config.season_start()
    times = departures(day, direction)
    rows = []
    breaks = [(_hm(a), _hm(b)) for a, b in _BREAKS]
    for i, t in enumerate(times):
        for a, b in breaks:
            if i and times[i - 1].time() < b <= t.time():
                rows.append({"brk": f"No service {short(a)} – {short(b)}"})
        past = not preview and t < now
        row = {"time": label(t), "iso": t.isoformat(), "past": past,
               "last": i == len(times) - 1, "rel": "",
               "remind": False}
        if not preview and not past:
            row["rel"] = relative(t - now) if day == today else ""
            row["remind"] = direction == LODGE and \
                (t - now) > dt.timedelta(minutes=MIN_LEAD)
        if direction == VILLAGE:
            row["arrive"] = label(t + dt.timedelta(minutes=RIDE_MIN))
        rows.append(row)
    if not preview:
        last_past = max((i for i, r in enumerate(rows) if r.get("past")), default=None)
        if last_past is not None:
            rows = [r for i, r in enumerate(rows) if i >= last_past]
    return {"rows": rows, "preview": preview, "day": day,
            "tomorrow": not preview and day != today}


def is_departure(iso):
    """The aware datetime for a reminder request, if it is a real upcoming
    bus from Lookout Ridge; else None."""
    try:
        t = dt.datetime.fromisoformat(str(iso))
    except ValueError:
        return None
    if t.tzinfo is None:
        return None
    t = t.astimezone(LOCAL)
    if not running_on(t.date()) or t not in departures(t.date()):
        return None
    return t


def season_note(now=None):
    """Short text for off-season: 'Starts Nov 21' / 'Season over'."""
    now = local_now(now)
    start = config.season_start()
    if now.date() < start:
        return f"Starts {start:%b} {start.day}"
    return "Done for the season"
