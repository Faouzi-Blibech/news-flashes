"""Delivery package: chart generation, HTML email rendering, and sending."""

from news_flashes.delivery.charts import chart_data_uri, render_history_chart
from news_flashes.delivery.render import load_disclaimer, render_email
from news_flashes.delivery.clients import import_clients_from_csv, load_clients
from news_flashes.delivery.sender import EmailSender, StubSender, send_flash

__all__ = [
    "render_history_chart",
    "chart_data_uri",
    "render_email",
    "load_disclaimer",
    "load_clients",
    "import_clients_from_csv",
    "EmailSender",
    "StubSender",
    "send_flash",
]
