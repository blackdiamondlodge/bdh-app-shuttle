import datetime as dt
import json
from unittest.mock import MagicMock, patch

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.cache import cache
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone

from shuttle import live, reminders, schedule, stops
from shuttle.homepage import status as homepage_status

UTC = dt.timezone.utc
LOCAL = schedule.LOCAL


def local(y, mo, d, h, mi=0):
    """A UTC instant for the given wall-clock time in Sun Peaks (UTC-7)."""
    return dt.datetime(y, mo, d, h, mi, tzinfo=LOCAL).astimezone(UTC)


def at(when):
    return patch("shuttle.schedule.timezone.now", return_value=when)


def times(b):
    return [r.get("time") or r["brk"] for r in b["rows"]]


class FakeAppDaemon:
    """Stands in for the bdl_shuttle endpoint behind requests.post."""

    def __init__(self):
        self.recs, self.calls = [], []

    def __call__(self, url, json=None, timeout=None, headers=None):
        self.calls.append((json, headers))
        cmd = json["cmd"]
        resp = MagicMock(status_code=200)
        if cmd == "add":
            dep = dt.datetime.fromisoformat(json["departure"])
            rec = {"id": f"r{len(self.recs) + 1}", "departure": json["departure"],
                   "lead_min": json["lead_min"], "stop": json["stop"], "status": "pending",
                   "destination": json["destination"],
                   "fire_at": (dep - dt.timedelta(minutes=json["lead_min"])).isoformat()}
            self.recs.append(rec)
            resp.json.return_value = {"reminder": rec}
        elif cmd == "cancel":
            self.recs = [r for r in self.recs if r["id"] != json["id"]]
            resp.json.return_value = {"ok": True}
        elif cmd == "list":
            resp.json.return_value = {"reminders": list(self.recs)}
        else:
            resp.status_code = 400
            resp.json.return_value = {"error": "unknown cmd"}
        return resp


REM = dict(BDH_SHUTTLE_REMINDERS_URL="http://ad.test/api/appdaemon/bdl_shuttle",
           BDH_SHUTTLE_REMINDERS_API_KEY="k")


class ScheduleTests(TestCase):
    def test_board_starts_at_last_departed_bus(self):
        with at(local(2026, 12, 1, 16, 22)):
            b = schedule.board(schedule.LODGE)
        self.assertFalse(b["preview"] or b["tomorrow"])
        rows = b["rows"]
        self.assertEqual(rows[0]["time"], "3:30 PM")
        self.assertTrue(rows[0]["past"])
        self.assertFalse(rows[0]["remind"])
        self.assertEqual(rows[1]["rel"], "in 8 min")
        self.assertTrue(rows[1]["remind"])
        self.assertEqual(rows[2]["rel"], "in 1 h 28 min")
        self.assertTrue(rows[-1]["last"])
        self.assertIn("No service 7:10 – 7:50", times(b))

    def test_no_reminder_inside_the_minimum_lead(self):
        with at(local(2026, 12, 1, 16, 27)):
            row = schedule.board(schedule.LODGE)["rows"][1]
        self.assertEqual(row["time"], "4:30 PM")
        self.assertFalse(row["remind"])

    def test_breaks_sit_between_the_right_buses(self):
        with at(local(2026, 12, 1, 6)):
            got = times(schedule.board(schedule.LODGE))
        self.assertEqual(got[:4], ["8:30 AM", "9:30 AM", "No service 10:00 – 10:40", "11:30 AM"])
        self.assertEqual(got.index("No service 2:00 – 2:40"), got.index("1:30 PM") + 1)

    def test_village_tab_has_arrival_and_no_reminders(self):
        with at(local(2026, 12, 1, 6)):
            rows = [r for r in schedule.board(schedule.VILLAGE)["rows"] if "time" in r]
        self.assertEqual((rows[0]["time"], rows[0]["arrive"]), ("8:20 AM", "8:30 AM"))
        self.assertFalse(any(r["remind"] for r in rows))

    def test_after_last_bus_shows_tomorrow(self):
        with at(local(2026, 12, 1, 22)):
            b = schedule.board(schedule.LODGE)
            nxt = schedule.next_departure()
        self.assertTrue(b["tomorrow"])
        self.assertEqual(b["day"], dt.date(2026, 12, 2))
        self.assertEqual(b["rows"][0]["rel"], "")
        self.assertTrue(b["rows"][0]["remind"])
        self.assertEqual((nxt["time"], nxt["today"]), ("8:30 AM", False))

    def test_off_season_is_a_preview(self):
        with at(local(2026, 11, 3, 12)):
            b = schedule.board(schedule.LODGE)
            self.assertFalse(schedule.in_season())
            self.assertEqual(schedule.season_note(), "Starts Nov 21")
            self.assertEqual(schedule.next_departure()["at"].date(), dt.date(2026, 11, 21))
        self.assertTrue(b["preview"])
        self.assertFalse(any(r.get("past") or r.get("remind") or r.get("rel") for r in b["rows"]))

    def test_eve_of_season_shows_tomorrow_not_preview(self):
        with at(local(2026, 11, 20, 12)):
            b = schedule.board(schedule.LODGE)
        self.assertFalse(b["preview"])
        self.assertTrue(b["tomorrow"])

    def test_after_season(self):
        with at(local(2027, 4, 5, 12)):
            self.assertIsNone(schedule.next_departure())
            self.assertTrue(schedule.board(schedule.LODGE)["preview"])
            self.assertEqual(schedule.season_note(), "Done for the season")

    def test_is_departure(self):
        self.assertEqual(schedule.is_departure("2026-12-01T16:30:00-07:00"),
                         dt.datetime(2026, 12, 1, 16, 30, tzinfo=LOCAL))
        self.assertIsNotNone(schedule.is_departure("2026-12-01T23:30:00+00:00"))
        for bad in ("2026-12-01T16:31:00-07:00", "2026-12-01T16:30:00", "2026-11-01T16:30:00-07:00",
                    "nope", None):
            self.assertIsNone(schedule.is_departure(bad), bad)

    @override_settings(BDH_SHUTTLE_SEASON_START="2027-11-20", BDH_SHUTTLE_SEASON_END="bad")
    def test_season_settings(self):
        self.assertEqual(schedule.config.season_start(), dt.date(2027, 11, 20))
        self.assertEqual(schedule.config.season_end(), dt.date(2027, 4, 4))


class LiveTests(TestCase):
    def setUp(self):
        cache.clear()

    def marker(self, lat=50.88300, lon=-119.87130, secs_ago=10):
        t = (timezone.now() - dt.timedelta(seconds=secs_ago)).isoformat().replace("+00:00", "Z")
        return {"serial_number": "X1", "registration_data": [{}],
                "device_data": [{"latitude": lat, "longitude": lon, "recorded_at": t, "heading": 90}]}

    def test_parse_skips_trackers_without_positions(self):
        got = live.parse([self.marker(), {"device_data": []}, {"device_data": [{"latitude": "x"}]}, None])
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0]["lat"], got[0]["heading"]), (50.883, 90))

    def test_status_names_nearby_stop_and_drops_old_fixes(self):
        with patch.object(live, "fetch", return_value=[self.marker(), self.marker(secs_ago=900)]):
            s = live.status()
        self.assertTrue(s["ok"])
        self.assertFalse(s["stale"])
        self.assertEqual(len(s["buses"]), 1)
        self.assertEqual(s["buses"][0]["near"], "Orient Chair")
        self.assertFalse(s["buses"][0]["at_our_stop"])

    def test_at_our_stop(self):
        e7 = next(s for s in stops.STOPS if s["id"] == "E7")
        with patch.object(live, "fetch", return_value=[self.marker(e7["lat"], e7["lon"])]):
            self.assertTrue(live.status()["buses"][0]["at_our_stop"])

    def test_falls_back_to_last_good_when_feed_fails(self):
        with patch.object(live, "fetch", return_value=[self.marker()]):
            live.status()
        cache.delete(live.LIVE_CACHE)
        with patch.object(live, "fetch", side_effect=live.LiveError("down")):
            s = live.status()
        self.assertTrue(s["stale"])
        self.assertEqual(len(s["buses"]), 1)
        cache.clear()
        with patch.object(live, "fetch", side_effect=live.LiveError("down")):
            self.assertEqual(live.status(), {"ok": False, "stale": True, "buses": []})

    def test_fetch_rediscovers_key_once_on_401(self):
        denied, ok = MagicMock(status_code=401), MagicMock(status_code=200)
        ok.json.return_value = []
        with patch.object(live, "_discover_key", side_effect=["old", "new"]) as disc, \
                patch.object(live.requests, "post", side_effect=[denied, ok]) as post:
            self.assertEqual(live.fetch(), [])
        self.assertEqual(disc.call_count, 2)
        self.assertEqual(post.call_args.kwargs["headers"]["apikey"], "new")

    def test_route_and_stops_line_up(self):
        route = stops.route()
        self.assertGreater(len(route), 50)
        for s in stops.STOPS:
            near = min(stops.distance_m(s["lat"], s["lon"], a, b) for a, b in route)
            self.assertLess(near, 40, s["id"])


class PageTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_in_season_page(self):
        with at(local(2026, 12, 1, 16, 22)):
            r = self.client.get(reverse("shuttle"))
        self.assertContains(r, "Next shuttle")
        self.assertContains(r, 'data-t="2026-12-01T16:30:00-07:00"')
        self.assertContains(r, "Shuttle (live)")
        self.assertContains(r, "Lookout Ridge")
        self.assertNotContains(r, "Remind me")  # reminders not configured
        geo = json.loads(r.content.decode().split('id="sh-geo" type="application/json">')[1].split("</script>")[0])
        self.assertEqual(geo["our"], "E7")

    @override_settings(**REM)
    def test_reminder_bells_only_from_the_lodge(self):
        with at(local(2026, 12, 1, 16, 22)):
            r = self.client.get(reverse("shuttle"))
        self.assertContains(r, "Remind me")
        self.assertContains(r, 'class="rb" data-dep="2026-12-01T16:30:00-07:00"')
        self.assertNotContains(r, 'data-dep="2026-12-01T16:20:00-07:00"')
        self.assertContains(r, 'id="sh-ov"')

    def test_off_season_page(self):
        with at(local(2026, 11, 3, 12)):
            r = self.client.get(reverse("shuttle"))
        self.assertContains(r, "Starts Nov 21")
        self.assertContains(r, "Shuttle not running")
        self.assertNotContains(r, "Shuttle (live)")
        self.assertNotContains(r, "data-t=")

    def test_live_api(self):
        with patch.object(live, "status", return_value={"ok": True, "stale": False, "buses": []}):
            r = self.client.get(reverse("shuttle_live"))
        self.assertEqual(r.json(), {"ok": True, "stale": False, "buses": []})


@override_settings(**REM)
class ReminderApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.ad = FakeAppDaemon()
        self.p = patch.object(reminders.requests, "post", side_effect=self.ad)
        self.p.start()
        self.addCleanup(self.p.stop)
        self.url = reverse("shuttle_reminders")

    def post(self, body):
        return self.client.post(self.url, json.dumps(body), content_type="application/json")

    def test_add_list_cancel(self):
        with at(local(2026, 12, 1, 16, 0)):
            r = self.post({"action": "add", "departure": "2026-12-01T17:50:00-07:00", "lead_min": 10})
        self.assertEqual(r.status_code, 200)
        [rec] = r.json()["reminders"]
        sent, headers = self.ad.calls[0]
        self.assertEqual((sent["stop"], sent["destination"], sent["lead_min"]),
                         ("Lookout Ridge", "the Village", 10))
        self.assertEqual(headers["X-API-Key"], "k")
        self.assertEqual(self.client.get(self.url).json()["reminders"], [rec])
        r = self.post({"action": "cancel", "id": rec["id"]})
        self.assertEqual(r.json()["reminders"], [])

    def test_rejects_non_departures_bad_leads_and_too_late(self):
        with at(local(2026, 12, 1, 17, 42)):
            for body in ({"action": "add", "departure": "2026-12-01T17:51:00-07:00", "lead_min": 10},
                         {"action": "add", "departure": "2026-12-01T17:50:00-07:00", "lead_min": 7},
                         {"action": "add", "departure": "2026-12-01T17:50:00-07:00", "lead_min": "x"},
                         {"action": "nope"}, {"action": "cancel"}):
                self.assertEqual(self.post(body).status_code, 400, body)
            r = self.post({"action": "add", "departure": "2026-12-01T17:50:00-07:00", "lead_min": 10})
            self.assertEqual(r.status_code, 400)
            self.assertIn("Too late", r.json()["error"])
            self.assertEqual(self.post({"action": "add", "departure": "2026-12-01T17:50:00-07:00",
                                        "lead_min": 5}).status_code, 200)
        self.assertEqual([c[0]["cmd"] for c in self.ad.calls], ["add", "list"])

    def test_appdaemon_errors_are_passed_on(self):
        bad = MagicMock(status_code=400)
        bad.json.return_value = {"error": "that shuttle has already left"}
        with at(local(2026, 12, 1, 16, 0)), patch.object(reminders.requests, "post", return_value=bad):
            r = self.post({"action": "add", "departure": "2026-12-01T17:50:00-07:00", "lead_min": 10})
        self.assertEqual((r.status_code, r.json()["error"]), (400, "that shuttle has already left"))
        with patch.object(reminders.requests, "post", side_effect=reminders.requests.ConnectionError()):
            r = self.client.get(self.url)
        self.assertEqual(r.status_code, 502)
        self.assertIn("isn't answering", r.json()["error"])

    @override_settings(BDH_SHUTTLE_REMINDERS_URL="")
    def test_disabled(self):
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_csrf_required(self):
        from django.test import Client
        c = Client(enforce_csrf_checks=True)
        r = c.post(self.url, "{}", content_type="application/json")
        self.assertEqual(r.status_code, 403)


class HomepageTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_next_bus(self):
        with at(local(2026, 12, 1, 16, 22)):
            s = homepage_status()
        self.assertEqual(s.text, "Next 4:30 PM · 8 min")

    def test_first_bus_tomorrow_and_off_season(self):
        with at(local(2026, 12, 1, 22)):
            self.assertEqual(homepage_status().text, "First bus 8:30 AM")
        with at(local(2026, 11, 20, 12)):
            self.assertEqual(homepage_status().text, "First bus 8:30 AM")
        with at(local(2026, 11, 3, 12)):
            self.assertEqual(homepage_status().text, "Not running · back Nov 21")
        with at(local(2027, 5, 1, 12)):
            self.assertIsNone(homepage_status())

    @override_settings(**REM)
    def test_pending_reminder(self):
        ad = FakeAppDaemon()
        ad.recs.append({"id": "r1", "departure": "2026-12-01T17:50:00-07:00", "lead_min": 10,
                        "fire_at": "2026-12-01T17:40:00-07:00", "status": "pending"})
        with at(local(2026, 12, 1, 16, 22)), patch.object(reminders.requests, "post", side_effect=ad):
            s = homepage_status()
        self.assertEqual(s.text, "Reminder set · 5:40 PM")
        self.assertEqual(s.level, "on")


@tag("selenium")
@override_settings(**REM)
class BrowserTests(StaticLiveServerTestCase):
    NOW = local(2026, 12, 1, 16, 22)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from selenium import webdriver
        options = webdriver.ChromeOptions()
        for arg in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--window-size=1920,1080"):
            options.add_argument(arg)
        cls.driver = webdriver.Chrome(options=options)
        # Freeze the browser's clock at the same instant as the server's.
        cls.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": """
            (() => { const RD = Date, off = %d - RD.now();
              class FD extends RD { constructor(...a) { a.length ? super(...a) : super(RD.now() + off); }
                                    static now() { return RD.now() + off; } }
              window.Date = FD; })();""" % int(cls.NOW.timestamp() * 1000)})

    @classmethod
    def tearDownClass(cls):
        cls.driver.quit()
        super().tearDownClass()

    def test_live_bus_and_reminder_flow(self):
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        cache.clear()
        ad = FakeAppDaemon()
        # live.py ages fixes against the real clock.
        fix = {"latitude": 50.88300, "longitude": -119.87130, "heading": 0,
               "recorded_at": dt.datetime.now(UTC).isoformat()}
        with at(self.NOW), patch.object(reminders.requests, "post", side_effect=ad), \
                patch.object(live, "fetch", return_value=[{"device_data": [fix]}]):
            d = self.driver
            d.get(self.live_server_url + reverse("shuttle"))
            wait = WebDriverWait(d, 10)
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "#sh-map .sh-bus")))
            wait.until(EC.text_to_be_present_in_element((By.ID, "sh-ls"), "Near Orient Chair"))
            self.assertEqual(len(d.find_elements(By.CSS_SELECTOR, "#sh-map .sh-our")), 1)
            # Nothing on the page scrolls at 1920x1080.
            self.assertFalse(d.execute_script(
                "return document.scrollingElement.scrollHeight > innerHeight"))

            # 4:30 bus, 8 minutes out: only the 5-minute heads-up is still possible.
            d.find_element(By.CSS_SELECTOR, "#sh-next .bell").click()
            wait.until(EC.visibility_of_element_located((By.ID, "sh-ov")))
            enabled = [b.get_attribute("data-lead") for b in d.find_elements(By.CSS_SELECTOR, "#sh-leads button")
                       if b.is_enabled()]
            self.assertEqual(enabled, ["5"])
            self.assertIn("in about 5 minutes", d.find_element(By.ID, "sh-say").text)
            self.assertIn("4:25 PM", d.find_element(By.CSS_SELECTOR, '#sh-leads [data-lead="5"]').text)
            d.find_element(By.ID, "sh-ok").click()
            wait.until(EC.invisibility_of_element_located((By.ID, "sh-ov")))
            wait.until(EC.text_to_be_present_in_element((By.ID, "sh-rems"), "4:30 PM shuttle · 5 min heads-up"))
            self.assertIn("speaker at 4:25 PM", d.find_element(By.ID, "sh-rems").text)
            self.assertIn("Reminder set", d.find_element(By.CSS_SELECTOR, "#sh-next .bell").text)
            self.assertIn("on", d.find_element(By.CSS_SELECTOR, '.rb[data-dep$="16:30:00-07:00"]')
                          .get_attribute("class"))

            # A later bus defaults to a 10-minute heads-up.
            d.find_element(By.CSS_SELECTOR, '.rb[data-dep$="17:50:00-07:00"]').click()
            wait.until(EC.visibility_of_element_located((By.ID, "sh-ov")))
            self.assertIn("on", d.find_element(By.CSS_SELECTOR, '#sh-leads [data-lead="10"]').get_attribute("class"))
            d.find_element(By.ID, "sh-close").click()

            # Reopening the first offers to remove it; the village tab has no bells.
            d.find_element(By.CSS_SELECTOR, "#sh-next .bell").click()
            wait.until(EC.visibility_of_element_located((By.ID, "sh-remove"))).click()
            wait.until(EC.invisibility_of_element_located((By.ID, "sh-ov")))
            wait.until(lambda drv: not drv.find_elements(By.CSS_SELECTOR, "#sh-rems .sh-rem"))
            self.assertEqual(ad.recs, [])
            d.find_element(By.CSS_SELECTOR, '.sh-tabs [data-tab="village"]').click()
            self.assertFalse(d.find_elements(By.CSS_SELECTOR, '[data-list="village"] .rb'))
            self.assertTrue(d.find_element(By.CSS_SELECTOR, '[data-list="village"]').is_displayed())
