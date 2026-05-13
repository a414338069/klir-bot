"""Translation file loader with English fallback."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_I18N_DIR = Path(__file__).parent
_SUPPORTED = frozenset({"en", "de", "nl", "fr", "ru", "es", "pt"})


def load_language(lang: str) -> dict[str, str]:
    """Load *lang*.json merged over en.json (missing keys fall back to English).

    Returns a flat dict of {key: translated_string}.
    Always succeeds — worst case returns the English catalog.
    """
    en = _load_file("en")
    if lang == "en" or lang not in _SUPPORTED:
        if lang not in _SUPPORTED:
            logger.warning("i18n: unsupported language %r, falling back to 'en'", lang)
        return en
    overlay = _load_file(lang)
    return {**en, **overlay}


def _load_file(lang: str) -> dict[str, str]:
    path = _I18N_DIR / f"{lang}.json"
    try:
        result: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("i18n: translation file not found: %s", path)
        return {}
    except (json.JSONDecodeError, OSError):
        logger.exception("i18n: failed to load %s", path)
        return {}
    else:
        return result
