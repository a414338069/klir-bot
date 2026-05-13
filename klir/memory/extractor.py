"""Memory extraction: parses LLM responses into structured memory candidates.

The extraction prompt is sent to the active CLI provider at session end.
This module handles parsing the response into ``MemoryCandidate`` objects
that can be deduplicated and written to disk.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_VALID_CATEGORIES = frozenset(
    {
        "profile",
        "preferences",
        "entities",
        "events",
        "cases",
        "patterns",
    }
)

_MEMORY_BLOCK_RE = re.compile(
    r"```memory\s*\n"
    r"abstract:\s*(.+?)\n"
    r"category:\s*(.+?)\n"
    r"---\s*\n"
    r"(.*?)"
    r"```",
    re.DOTALL,
)


def _slugify(text: str) -> str:
    """Convert text to a filesystem-safe slug."""
    slug = re.sub(r"[^\w\s-]", "", text.lower().strip())
    slug = re.sub(r"[\s_]+", "-", slug)
    return slug[:60]


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    """A memory extracted from an LLM response, pending dedup and storage."""

    abstract: str
    category: str
    content: str
    slug: str


def parse_extraction_response(response: str) -> list[MemoryCandidate]:
    """Parse ```memory``` blocks from an LLM extraction response.

    Returns a list of MemoryCandidate objects.
    """
    candidates: list[MemoryCandidate] = []

    for match in _MEMORY_BLOCK_RE.finditer(response):
        abstract = match.group(1).strip()
        category = match.group(2).strip().lower()
        content = match.group(3).strip()

        if not abstract or not content:
            continue

        if category not in _VALID_CATEGORIES:
            logger.warning("Invalid memory category '%s', defaulting to 'cases'", category)
            category = "cases"

        candidates.append(
            MemoryCandidate(
                abstract=abstract,
                category=category,
                content=content,
                slug=_slugify(abstract),
            )
        )

    return candidates


# The extraction prompt template. Sent to the CLI provider with the
# session summary as context.
EXTRACTION_PROMPT = """You are analyzing a conversation to extract long-term memories.

Review the conversation below and extract any durable knowledge worth remembering
for future sessions. Output each memory as a ```memory``` block:

```memory
abstract: One-line summary of this memory
category: one of: profile, preferences, entities, events, cases, patterns
---
Detailed content (2-5 lines). Focus on facts, decisions, and patterns.
```

Categories:
- profile: About the user (role, expertise, background)
- preferences: User's preferred tools, styles, approaches
- entities: Projects, repos, teams, services the user works with
- events: Specific incidents, decisions with dates
- cases: Problem+solution pairs (debugging, fixes)
- patterns: Reusable workflows or conventions

If nothing is worth remembering, respond with: "No memories to extract."

Conversation summary:
{summary}
"""

EXCHANGE_EXTRACTION_PROMPT = """You are analyzing conversation exchanges to extract long-term memories.

Review the message exchanges below and extract any durable knowledge worth remembering
for future sessions. Focus on:
- User preferences revealed through their requests
- Project/codebase facts mentioned in responses
- Patterns in how the user works
- Decisions made and their reasoning
- Problem+solution pairs

Output each memory as a ```memory``` block:

```memory
abstract: One-line summary of this memory
category: one of: profile, preferences, entities, events, cases, patterns
---
Detailed content (2-5 lines). Focus on facts, decisions, and patterns.
```

Categories:
- profile: About the user (role, expertise, background)
- preferences: User's preferred tools, styles, approaches
- entities: Projects, repos, teams, services the user works with
- events: Specific incidents, decisions with dates
- cases: Problem+solution pairs (debugging, fixes)
- patterns: Reusable workflows or conventions

If nothing is worth remembering, respond with: "No memories to extract."

Conversation exchanges:
{exchanges}
"""


def format_exchanges(exchanges: list[tuple[str, str]]) -> str:
    """Format message+response pairs for the extraction prompt.

    Each tuple is (user_message, assistant_response).
    Truncates long responses to keep the prompt bounded.
    """
    max_response_chars = 1000
    parts: list[str] = []
    for i, (msg, resp) in enumerate(exchanges, 1):
        truncated = resp[:max_response_chars] + "..." if len(resp) > max_response_chars else resp
        parts.append(f"### Exchange {i}\n**User:** {msg}\n**Assistant:** {truncated}")
    return "\n\n".join(parts)
