from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="shuttle"),
    path("api/live/", views.live_view, name="shuttle_live"),
    path("api/reminders/", views.reminders_view, name="shuttle_reminders"),
]
