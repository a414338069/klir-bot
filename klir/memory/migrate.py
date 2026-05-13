"""One-time migration from flat MAINMEMORY.md to hierarchical structure.

Reads the existing MAINMEMORY.md, splits into sections, and writes
the appropriate category files. The original file is renamed to
MAINMEMORY.md.bak as a safety net.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from klir.memory.files import MemoryFile, MemoryFileManager
from klir.memory.store import MemoryStore

logger = logging.getLogger(__name__)

_EMPTY_MARKERS = frozenset(
    {
        "(empty",
        "(empty --",
        "(will be populated",
    }
)


def _is_empty_section(text: str) -> bool:
    """Check if a section body is just the default empty placeholder."""
    stripped = text.strip().lower()
    return not stripped or any(stripped.startswith(m) for m in _EMPTY_MARKERS)


def _extract_sections(text: str) -> dict[str, str]:
    """Extract markdown H2 sections from MAINMEMORY.md."""
    sections: dict[str, str] = {}
    current_heading = ""
    current_lines: list[str] = []

    for line in text.splitlines():
        if line.startswith("## "):
            if current_heading:
                sections[current_heading] = "\n".join(current_lines).strip()
            current_heading = line[3:].strip()
            current_lines = []
        elif current_heading:
            current_lines.append(line)

    if current_heading:
        sections[current_heading] = "\n".join(current_lines).strip()

    return sections


# Map existing section names to new categories
_SECTION_MAP: dict[str, str] = {
    "About the User": "profile",
    "Learned Facts": "entities",
    "Decisions and Preferences": "preferences",
}


def migrate_mainmemory(
    mainmemory_path: Path,
    memory_dir: Path,
    store: MemoryStore,
) -> bool:
    """Migrate existing MAINMEMORY.md to hierarchical structure.

    Returns True if migration was performed, False if skipped.
    """
    # Already migrated (backup exists means migration completed)
    backup = mainmemory_path.with_suffix(".md.bak")
    if backup.exists():
        logger.info("Migration skipped: MAINMEMORY.md.bak already exists")
        return False

    if not mainmemory_path.exists():
        return False

    text = mainmemory_path.read_text(encoding="utf-8")
    sections = _extract_sections(text)

    if not any(not _is_empty_section(body) for body in sections.values()):
        logger.info("Migration skipped: MAINMEMORY.md is empty/default")
        return False

    mgr = MemoryFileManager(memory_dir, store)

    for heading, body in sections.items():
        if _is_empty_section(body):
            continue

        category = _SECTION_MAP.get(heading, "entities")
        slug = (
            "main" if category == "profile" else re.sub(r"[^\w]+", "-", heading.lower()).strip("-")
        )
        abstract = body[:80].replace("\n", " ").strip()

        mgr.write(
            MemoryFile(
                category=category,
                slug=slug,
                abstract=abstract,
                content=body,
            )
        )

    # Backup original — only rename after all writes succeed
    mainmemory_path.rename(backup)
    logger.info("Migrated MAINMEMORY.md -> hierarchical structure (backup: %s)", backup.name)
    return True
