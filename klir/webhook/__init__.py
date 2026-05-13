"""Webhook system: HTTP ingress for external event triggers."""

from klir.webhook.manager import WebhookManager
from klir.webhook.models import WebhookEntry, WebhookResult

__all__ = ["WebhookEntry", "WebhookManager", "WebhookResult"]
