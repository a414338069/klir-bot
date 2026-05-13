"""Memory extraction observer: extracts memories when sessions go stale.

This observer hooks into session staleness detection and runs the
extraction prompt through the CLI provider to produce structured
memory entries.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from klir.memory.extractor import (
    EXCHANGE_EXTRACTION_PROMPT,
    EXTRACTION_PROMPT,
    MemoryCandidate,
    format_exchanges,
    parse_extraction_response,
)
from klir.memory.files import MemoryFile, MemoryFileManager
from klir.memory.store import MemoryStore

logger = logging.getLogger(__name__)


def _abstracts_match(a: str, b: str) -> bool:
    """Check if two abstracts are similar enough to be duplicates."""
    a_norm = a.strip().lower()
    b_norm = b.strip().lower()
    if a_norm == b_norm:
        return True
    # Simple containment check
    return a_norm in b_norm or b_norm in a_norm


def _dedup_and_store(
    candidates: list[MemoryCandidate],
    store: MemoryStore,
    files: MemoryFileManager,
) -> int:
    """Deduplicate candidates and write new ones to disk.

    Returns the number of new memories stored.
    """
    stored = 0
    for candidate in candidates:
        existing = store.search(candidate.abstract, limit=1)
        if existing and _abstracts_match(existing[0].abstract, candidate.abstract):
            logger.debug(
                "Dedup: skipping '%s' (matches '%s')",
                candidate.abstract,
                existing[0].abstract,
            )
            continue

        try:
            files.write(
                MemoryFile(
                    category=candidate.category,
                    slug=candidate.slug,
                    abstract=candidate.abstract,
                    content=candidate.content,
                )
            )
        except Exception:
            logger.exception("Failed to write memory '%s'", candidate.abstract)
            continue
        stored += 1
    return stored


async def extract_and_store(
    summary: str,
    store: MemoryStore,
    files: MemoryFileManager,
    run_extraction: Callable[[str], Awaitable[str]],
) -> int:
    """Run memory extraction and store results.

    Args:
        summary: Conversation summary text to extract from.
        store: The FTS5 memory store for dedup checks.
        files: The file manager for writing memory files.
        run_extraction: Async callable that sends the extraction prompt
            to a CLI provider and returns the response text.

    Returns:
        Number of new memories stored.
    """
    prompt = EXTRACTION_PROMPT.format(summary=summary)

    try:
        response = await run_extraction(prompt)
    except Exception:
        logger.exception("Memory extraction CLI call failed")
        return 0

    candidates = parse_extraction_response(response)
    if not candidates:
        logger.info("No memories extracted from session")
        return 0

    stored = _dedup_and_store(candidates, store, files)
    logger.info("Memory extraction complete: %d/%d stored", stored, len(candidates))
    return stored


async def extract_from_exchanges(
    exchanges: list[tuple[str, str]],
    store: MemoryStore,
    files: MemoryFileManager,
    run_extraction: Callable[[str], Awaitable[str]],
) -> int:
    """Extract memories from message+response pairs.

    Uses richer conversation context instead of a pre-built summary.

    Args:
        exchanges: List of (user_message, assistant_response) tuples.
        store: The FTS5 memory store for dedup checks.
        files: The file manager for writing memory files.
        run_extraction: Async callable that sends the extraction prompt
            to a CLI provider and returns the response text.

    Returns:
        Number of new memories stored.
    """
    if not exchanges:
        return 0

    formatted = format_exchanges(exchanges)
    prompt = EXCHANGE_EXTRACTION_PROMPT.format(exchanges=formatted)

    try:
        response = await run_extraction(prompt)
    except Exception:
        logger.exception("Memory extraction from exchanges failed")
        return 0

    candidates = parse_extraction_response(response)
    if not candidates:
        logger.info("No memories extracted from exchanges")
        return 0

    stored = _dedup_and_store(candidates, store, files)
    logger.info("Exchange extraction complete: %d/%d stored", stored, len(candidates))
    return stored
