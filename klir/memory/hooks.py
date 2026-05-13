"""Memory retrieval hook: searches the FTS5 index and builds context for injection.

This module provides the bridge between the MemoryStore and the
MessageHookRegistry. The ``build_memory_context`` function is the
single entry point for retrieving relevant memories for a prompt.
"""

from __future__ import annotations

import logging

from klir.memory.store import MemoryStore

logger = logging.getLogger(__name__)

# Maximum characters of memory context to inject per message
_MAX_CONTEXT_CHARS = 6000
# Number of BM25 results to retrieve
_SEARCH_LIMIT = 8
# Number of top results that get full content preview
_DETAIL_LIMIT = 3
# Number of profile entries to include on new sessions
_PROFILE_LIMIT = 3


def build_memory_context(
    store: MemoryStore,
    prompt: str,
    *,
    is_new_session: bool = False,
) -> str:
    """Build a memory context string for injection into the CLI prompt.

    On new sessions, always includes profile memories.
    On every call, searches for prompt-relevant memories via BM25.

    Returns a formatted string ready for prompt injection, or empty string
    if no relevant memories are found.
    """
    if store.count() == 0:
        return ""

    sections: list[str] = []
    seen_uris: set[str] = set()
    budget = _MAX_CONTEXT_CHARS

    # L0: Always load profile on new sessions
    if is_new_session:
        budget = _collect_profiles(store, sections, seen_uris, budget)

    # L1/L2: BM25 search for relevant memories
    if prompt.strip():
        _collect_search_results(store, prompt, sections, seen_uris, budget)

    if not sections:
        return ""

    header = "## Recalled Memories\nRelevant context from long-term memory:\n"
    return header + "\n\n".join(sections)


def _collect_profiles(
    store: MemoryStore,
    sections: list[str],
    seen_uris: set[str],
    budget: int,
) -> int:
    """Append profile entries to sections, return remaining budget."""
    profiles = store.list_category("profile")
    for entry in profiles[:_PROFILE_LIMIT]:
        block = f"**[{entry.uri}]** {entry.abstract}\n{entry.content}"
        if len(block) > budget:
            break
        sections.append(block)
        seen_uris.add(entry.uri)
        budget -= len(block)
    return budget


def _collect_search_results(
    store: MemoryStore,
    prompt: str,
    sections: list[str],
    seen_uris: set[str],
    budget: int,
) -> None:
    """Append BM25 search results to sections using progressive disclosure.

    Top results get full abstract + content preview.
    Lower-ranked results get abstract only (index entries).
    """
    results = store.search(prompt, limit=_SEARCH_LIMIT)
    detail_count = 0
    for entry in results:
        if entry.uri in seen_uris:
            continue

        # Top results: full content preview
        if detail_count < _DETAIL_LIMIT:
            content_preview = entry.content[:500] if len(entry.content) > 500 else entry.content
            block = f"**[{entry.uri}]** {entry.abstract}\n{content_preview}"
            if len(block) > budget:
                block = f"**[{entry.uri}]** {entry.abstract}"
                if len(block) > budget:
                    break
            detail_count += 1
        else:
            # Lower-ranked: abstract only (progressive disclosure)
            block = f"- [{entry.uri}] {entry.abstract}"
            if len(block) > budget:
                break

        sections.append(block)
        seen_uris.add(entry.uri)
        budget -= len(block)


class MemoryRetrievalHook:
    """Stateful hook that injects retrieved memories into prompts.

    Unlike the static ``MessageHook`` dataclass, this needs a reference
    to the ``MemoryStore`` instance.  It exposes the same ``(condition, suffix)``
    interface but computes the suffix dynamically.
    """

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def apply(self, prompt: str, *, is_new_session: bool = False) -> str:
        """Apply memory retrieval to a prompt.

        Returns the prompt with memory context appended, or unchanged
        if no relevant memories are found.
        """
        ctx = build_memory_context(self._store, prompt, is_new_session=is_new_session)
        if not ctx:
            return prompt
        return prompt + "\n\n" + ctx
