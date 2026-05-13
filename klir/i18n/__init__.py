"""Internationalisation layer — thin wrapper over JSON translation files."""

from __future__ import annotations

from klir.i18n.loader import load_language

_state: dict[str, object] = {"catalog": {}, "lang": "en"}


def load_translations(lang: str) -> None:
    """Load or reload translations for *lang*. Called at startup and on hot-reload."""
    _state["catalog"] = load_language(lang)
    _state["lang"] = lang


def t(key: str, **kwargs: object) -> str:
    """Look up *key* and interpolate *kwargs*.

    Falls back to *key* itself when missing so no string is ever silently lost.
    Interpolation uses str.format_map so {placeholders} in JSON values work.
    """
    catalog: dict[str, str] = _state["catalog"]  # type: ignore[assignment]
    template = catalog.get(key, key)
    if kwargs:
        try:
            return template.format_map(kwargs)
        except (KeyError, ValueError):
            return template
    return template


def current_language() -> str:
    return str(_state["lang"])
