import datetime as dt

from bdh_sdk import INFO, ON, Status

from . import reminders, schedule


def status():
    now = schedule.local_now()
    nxt = schedule.next_departure(now)
    if nxt is None:
        return None
    if not nxt["today"] and nxt["at"].date() > now.date() + dt.timedelta(days=1):
        return Status(f"Not running · back {nxt['at']:%b} {nxt['at'].day}", INFO)
    for r in reminders.pending():
        try:
            fire = dt.datetime.fromisoformat(r["fire_at"]).astimezone(schedule.LOCAL)
        except (KeyError, TypeError, ValueError):
            continue
        return Status(f"Reminder set · {schedule.label(fire)}", ON)
    if not nxt["today"]:
        return Status(f"First bus {nxt['time']}", INFO)
    return Status(f"Next {nxt['time']} · {nxt['rel'].removeprefix('in ')}", INFO)
