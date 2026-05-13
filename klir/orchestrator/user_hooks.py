"""User-defined message hook evaluator."""

from __future__ import annotations

import logging
import re

from klir.config import UserMessageHookConfig
from klir.orchestrator.hooks import HookContext

logger = logging.getLogger(__name__)


class UserHookEvaluator:
    """Evaluates user-defined hooks from config against messages."""

    def __init__(self, hooks: list[UserMessageHookConfig]) -> None:
        self._compiled: dict[str, re.Pattern[str]] = {}
        enabled = [h for h in hooks if h.enabled]
        for hook in enabled:
            if hook.condition == "regex" and hook.pattern:
                try:
                    self._compiled[hook.name] = re.compile(hook.pattern)
                except re.error:
                    logger.warning(
                        "Invalid regex in hook %r: %s — hook will be skipped",
                        hook.name,
                        hook.pattern,
                    )
        self._pre = [
            h
            for h in enabled
            if h.phase == "pre" and (h.condition != "regex" or h.name in self._compiled)
        ]
        self._post = [
            h
            for h in enabled
            if h.phase == "post" and (h.condition != "regex" or h.name in self._compiled)
        ]

    def apply_pre(self, text: str, ctx: HookContext) -> str:
        """Apply pre-processing hooks to the user message."""
        return self._apply(text, self._pre, ctx)

    def apply_post(self, text: str, ctx: HookContext) -> str:
        """Apply post-processing hooks to the AI response."""
        return self._apply(text, self._post, ctx)

    def _apply(
        self,
        text: str,
        hooks: list[UserMessageHookConfig],
        ctx: HookContext,
    ) -> str:
        for hook in hooks:
            if not self._check_condition(hook, text, ctx):
                continue
            if hook.action == "prepend":
                text = hook.text + text
            elif hook.action == "append":
                text = text + hook.text
            elif hook.action == "replace":
                text = hook.text
            logger.debug("User hook fired: %s", hook.name)
        return text

    def _check_condition(
        self,
        hook: UserMessageHookConfig,
        text: str,
        ctx: HookContext,
    ) -> bool:
        if hook.condition == "always":
            return True
        if hook.condition == "regex":
            compiled = self._compiled.get(hook.name)
            if compiled is None:
                # Pattern failed to compile at init time; skip.
                return False
            try:
                return bool(compiled.search(text))
            except re.error:
                logger.warning("Invalid regex in hook %r: %s", hook.name, hook.pattern)
                return False
        # condition == "provider"
        return ctx.provider == hook.provider
