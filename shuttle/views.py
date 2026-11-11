import datetime as dt
import json

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_http_methods

from . import live, reminders, schedule, stops


def index(request):
    now = schedule.local_now()
    day = schedule.departures(schedule.config.season_start())
    lodge = schedule.board(schedule.LODGE, now)
    return render(request, "shuttle/index.html", {
        "next": schedule.next_departure(now),
        "in_season": schedule.in_season(now),
        "season_note": schedule.season_note(now),
        "first_bus": schedule.label(day[0]),
        "last_bus": schedule.label(day[-1]),
        "lodge": lodge,
        "boards": [(schedule.LODGE, lodge),
                   (schedule.VILLAGE, schedule.board(schedule.VILLAGE, now))],
        "reminders_on": reminders.enabled(),
        "leads": schedule.LEADS,
        "geo": {"stops": stops.STOPS, "route": stops.route(), "our": stops.OUR_STOP},
    })


@require_GET
def live_view(request):
    return JsonResponse(live.status())


def _reminder_list():
    data, err, code = reminders.call("list")
    if err:
        return JsonResponse({"error": err}, status=code)
    return JsonResponse({"reminders": data.get("reminders", [])})


@require_http_methods(["GET", "POST"])
def reminders_view(request):
    if not reminders.enabled():
        return JsonResponse({"error": "Reminders aren't set up here."}, status=404)
    if request.method == "GET":
        return _reminder_list()
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "Bad request"}, status=400)
    action = body.get("action")
    if action == "add":
        departure = schedule.is_departure(body.get("departure"))
        try:
            lead = int(body.get("lead_min"))
        except (TypeError, ValueError):
            lead = None
        if departure is None or lead not in schedule.LEADS:
            return JsonResponse({"error": "That isn't a shuttle time."}, status=400)
        if departure - dt.timedelta(minutes=lead) <= schedule.local_now():
            return JsonResponse({"error": f"Too late for a {lead}-minute heads-up."}, status=400)
        _, err, code = reminders.add(departure, lead)
    elif action == "cancel" and body.get("id"):
        _, err, code = reminders.cancel(str(body["id"]))
    else:
        return JsonResponse({"error": "Bad request"}, status=400)
    if err:
        return JsonResponse({"error": err}, status=code)
    return _reminder_list()
