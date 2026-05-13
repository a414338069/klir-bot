"""Memory file manager: reads/writes the hierarchical directory layout.

Keeps the on-disk markdown files and the SQLite FTS5 index in sync.
Parses a minimal YAML-like frontmatter for abstract/category metadata.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from klir.infra.atomic_io import atomic_text_save
from klir.memory.store import MemoryEntry, MemoryStore

logger = logging.getLogger(__name__)

# Categories and their directory prefixes
_CATEGORY_DIRS: dict[str, str] = {
    "profile": "user",
    "preferences": "user/preferences",
    "entities": "user/entities",
    "events": "user/events",
    "cases": "agent/cases",
    "patterns": "agent/patterns",
}

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass(slots=True)
class MemoryFile:
    """Parsed memory file data."""

    category: str
    slug: str
    abstract: str
    content: str


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse YAML-like frontmatter from markdown text.

    Returns (metadata_dict, body).
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()

    body = text[match.end() :]
    return meta, body


def _render_frontmatter(mf: MemoryFile) -> str:
    """Render a memory file to markdown with frontmatter."""
    return f"---\nabstract: {mf.abstract}\ncategory: {mf.category}\n---\n\n{mf.content}\n"


def _uri_for(category: str, slug: str) -> str:
    """Build the URI for a memory entry."""
    prefix = _CATEGORY_DIRS.get(category, f"other/{category}")
    if category == "profile" and slug == "main":
        return f"{prefix}/profile.md"
    return f"{prefix}/{slug}.md"


class MemoryFileManager:
    """Manages memory markdown files on disk and syncs with the FTS5 index."""

    def __init__(self, memory_dir: Path, store: MemoryStore) -> None:
        self._root = memory_dir
        self._store = store

    def write(self, mf: MemoryFile) -> Path:
        """Write a memory file to disk and index it."""
        uri = _uri_for(mf.category, mf.slug)
        file_path = self._root / uri
        file_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_text_save(file_path, _render_frontmatter(mf))

        self._store.upsert(
            MemoryEntry(
                uri=uri,
                abstract=mf.abstract,
                category=mf.category,
                content=mf.content,
            )
        )
        logger.info("Memory written: %s", uri)
        return file_path

    def read(self, category: str, slug: str) -> MemoryFile | None:
        """Read a memory file from disk."""
        uri = _uri_for(category, slug)
        file_path = self._root / uri
        if not file_path.exists():
            return None

        text = file_path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(text)
        return MemoryFile(
            category=meta.get("category", category),
            slug=slug,
            abstract=meta.get("abstract", ""),
            content=body.strip(),
        )

    def delete(self, category: str, slug: str) -> None:
        """Delete a memory file from disk and the index."""
        uri = _uri_for(category, slug)
        file_path = self._root / uri
        if file_path.exists():
            file_path.unlink()
        self._store.delete(uri)
        logger.info("Memory deleted: %s", uri)

    def list_all(self) -> list[MemoryFile]:
        """List all memory files from the index."""
        entries = self._store.all_abstracts()
        results: list[MemoryFile] = []
        for uri, abstract in entries:
            file_path = self._root / uri
            if not file_path.exists():
                continue
            text = file_path.read_text(encoding="utf-8")
            meta, body = _parse_frontmatter(text)
            parts = uri.rsplit("/", 1)
            slug = parts[-1].removesuffix(".md") if len(parts) > 1 else uri.removesuffix(".md")
            results.append(
                MemoryFile(
                    category=meta.get("category", ""),
                    slug=slug,
                    abstract=abstract,
                    content=body.strip(),
                )
            )
        return results

    def rebuild_index(self) -> None:
        """Rebuild the FTS5 index from all markdown files on disk.

        Walks the memory directory, parses frontmatter, and upserts each file.
        """
        count = 0
        for category, dir_prefix in _CATEGORY_DIRS.items():
            cat_path = self._root / dir_prefix
            if not cat_path.exists():
                continue
            for md_file in cat_path.glob("*.md"):
                text = md_file.read_text(encoding="utf-8")
                meta, body = _parse_frontmatter(text)
                slug = md_file.stem
                uri = _uri_for(meta.get("category", category), slug)
                self._store.upsert(
                    MemoryEntry(
                        uri=uri,
                        abstract=meta.get("abstract", body[:80]),
                        category=meta.get("category", category),
                        content=body.strip(),
                    )
                )
                count += 1
        logger.info("Index rebuilt: %d memories", count)
