"""Project-level exception hierarchy."""


class KlirError(Exception):
    """Base for all klir exceptions."""


class CLIError(KlirError):
    """CLI execution failed."""


class WorkspaceError(KlirError):
    """Workspace initialization or access failed."""


class SessionError(KlirError):
    """Session persistence or lifecycle failed."""


class CronError(KlirError):
    """Cron job scheduling or execution failed."""


class StreamError(KlirError):
    """Streaming output failed."""


class SecurityError(KlirError):
    """Security violation detected."""


class PathValidationError(SecurityError):
    """File path failed validation."""


class WebhookError(KlirError):
    """Webhook server or dispatch failed."""
