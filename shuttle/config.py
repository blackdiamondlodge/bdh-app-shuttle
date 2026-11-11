"""Settings for the shuttle app (``BDH_SHUTTLE_*``)."""
import datetime as dt

from bdh_sdk.settings import app_setting

APP_ID = "shuttle"


def _date(key, default):
    value = app_setting(APP_ID, key, "")
    if value:
        try:
            return dt.date.fromisoformat(str(value))
        except ValueError:
            pass
    return default


def season_start():
    """First day of winter service (2026/27 season)."""
    return _date("season_start", dt.date(2026, 11, 21))


def season_end():
    """Last day of winter service."""
    return _date("season_end", dt.date(2027, 4, 4))


def reminders_url():
    """URL of the bdl_shuttle AppDaemon endpoint; empty hides reminders."""
    return app_setting(APP_ID, "reminders_url", "")


def reminders_api_key():
    return app_setting(APP_ID, "reminders_api_key", "")


def skikrumb_key():
    """skiKrumb's public (anon) API key. Empty: read it from their embed page."""
    return app_setting(APP_ID, "skikrumb_key", "")
