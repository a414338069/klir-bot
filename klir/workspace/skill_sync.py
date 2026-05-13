"""Cross-platform skill directory sync between klir workspace and CLI tools.

Provides multi-way symlink synchronization so skills installed via Claude Code,
Codex CLI, Gemini CLI, or the klir workspace are visible to all agents.

Includes bundled-skill linking (package → workspace), sync-time external-symlink
protection, and cleanup of klir-created links on shutdown.

Sync runs once during ``init_workspace`` and periodically as a background task.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path

from klir.workspace.paths import KlirPaths

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"

_SKIP_DIRS: frozenset[str] = frozenset(
    {".claude", ".system", ".git", ".venv", "__pycache__", "node_modules"}
)

_SKILL_SYNC_INTERVAL = 30.0


def _is_under(child: Path, parent: Path) -> bool:
    """Return ``True`` if *child* is located under *parent* directory."""
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    else:
        return True


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _discover_skills(base: Path) -> dict[str, Path]:
    """Scan a skills directory and return ``{name: path}`` for valid entries.

    Skips hidden/internal directories and broken symlinks.
    Only includes subdirectories (plain files are ignored).
    """
    if not base.is_dir():
        return {}
    skills: dict[str, Path] = {}
    for entry in sorted(base.iterdir()):
        if entry.name.startswith(".") or entry.name in _SKIP_DIRS:
            continue
        if entry.is_symlink():
            if entry.exists():
                skills[entry.name] = entry
            continue
        if entry.is_dir():
            skills[entry.name] = entry
    return skills


def _cli_skill_dirs() -> dict[str, Path]:
    """Return skill directories for installed CLIs.

    Only includes CLIs whose home directory exists on disk.
    Uses the same detection pattern as ``cli/auth.py``.
    """
    dirs: dict[str, Path] = {}
    claude_home = Path.home() / ".claude"
    if claude_home.is_dir():
        dirs["claude"] = claude_home / "skills"
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    if codex_home.is_dir():
        dirs["codex"] = codex_home / "skills"
    gemini_home = Path.home() / ".gemini"
    if gemini_home.is_dir():
        dirs["gemini"] = gemini_home / "skills"
    opencode_base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    opencode_home = opencode_base / "opencode"
    if opencode_home.is_dir():
        dirs["opencode"] = opencode_home / "skills"
    return dirs


# ---------------------------------------------------------------------------
# Canonical resolution
# ---------------------------------------------------------------------------


def _resolve_canonical(
    name: str,
    *registries: dict[str, Path],
) -> Path | None:
    """Find the canonical (real, non-symlink) path for a skill.

    Priority follows argument order (typically klir > claude > codex > gemini).
    Falls back to resolving the first valid symlink if no real dir exists.
    """
    for registry in registries:
        entry = registry.get(name)
        if entry is not None and not entry.is_symlink():
            return entry
    for registry in registries:
        entry = registry.get(name)
        if entry is not None and entry.is_symlink() and entry.exists():
            return entry.resolve()
    return None


# ---------------------------------------------------------------------------
# Cross-platform symlink creation
# ---------------------------------------------------------------------------


def _create_dir_link(link_path: Path, target: Path) -> None:
    """Create a directory symlink with Windows junction fallback.

    Linux/macOS/WSL: standard ``os.symlink``.
    Windows: tries ``os.symlink`` (requires Developer Mode or admin),
    then falls back to NTFS junction via ``mklink /J`` (no admin needed).
    """
    if not _IS_WINDOWS:
        link_path.symlink_to(target)
        return

    try:
        link_path.symlink_to(target, target_is_directory=True)
    except OSError:
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link_path), str(target)],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            msg = f"Failed to create symlink or junction: {link_path} -> {target}"
            raise OSError(msg) from None


def _ensure_link(link_path: Path, target: Path) -> bool:
    """Idempotently ensure *link_path* is a symlink to *target*.

    Returns ``True`` if a new link was created, ``False`` if already correct
    or if *link_path* is a real directory (never destroyed).
    """
    if link_path.exists() and not link_path.is_symlink():
        return False
    if link_path.is_symlink():
        if link_path.resolve() == target.resolve():
            return False
        link_path.unlink()
    _create_dir_link(link_path, target)
    return True


# ---------------------------------------------------------------------------
# Broken link cleanup
# ---------------------------------------------------------------------------


def _clean_broken_links(directory: Path) -> int:
    """Remove broken symlinks in *directory*. Returns count removed."""
    if not directory.is_dir():
        return 0
    removed = 0
    for entry in directory.iterdir():
        if entry.is_symlink() and not entry.exists():
            entry.unlink()
            removed += 1
    return removed


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _should_skip_link(dest: Path, sync_roots: frozenset[Path]) -> bool:
    """Return ``True`` if *dest* should be left alone during symlink sync."""
    if dest.exists() and not dest.is_symlink():
        return True
    if dest.is_symlink() and dest.exists():
        resolved = dest.resolve()
        return not any(_is_under(resolved, root) for root in sync_roots)
    return False


def _link_skill_everywhere(
    skill_name: str,
    canonical: Path,
    all_dirs: dict[str, Path],
) -> None:
    """Create symlinks for *skill_name* in every location that lacks it.

    Preserves existing symlinks that point outside the known sync directories
    (user-managed external links are never touched).
    """
    sync_roots = frozenset(d.resolve() for d in all_dirs.values() if d.is_dir())
    for loc_name, base_dir in all_dirs.items():
        if not base_dir.is_dir():
            base_dir.mkdir(parents=True, exist_ok=True)
        dest = base_dir / skill_name
        if dest == canonical:
            continue
        if _should_skip_link(dest, sync_roots):
            continue
        try:
            if _ensure_link(dest, canonical):
                logger.info("Skill link created: %s -> %s", dest, canonical)
        except OSError:
            logger.warning("Failed to sync skill %s in %s", skill_name, loc_name, exc_info=True)


def sync_skills(paths: KlirPaths) -> None:
    """Multi-way skill directory sync: klir workspace <-> CLI skill dirs.

    Syncs between klir workspace, ~/.claude/skills, ~/.codex/skills,
    and ~/.gemini/skills.

    Safety guarantees:
    - Real directories are never overwritten or removed.
    - Existing valid symlinks pointing elsewhere are left alone.
    - Internal directories (.system, .claude, .git, .venv) are skipped.
    """
    cli_dirs = _cli_skill_dirs()
    all_dirs: dict[str, Path] = {"klir": paths.skills_dir, **cli_dirs}

    registries = {name: _discover_skills(d) for name, d in all_dirs.items()}

    all_names: set[str] = set()
    for reg in registries.values():
        all_names.update(reg.keys())

    # Priority order: klir > claude > codex > gemini
    priority = ("klir", "claude", "codex", "gemini", "opencode")
    for skill_name in sorted(all_names):
        canonical = _resolve_canonical(
            skill_name,
            *(registries.get(n, {}) for n in priority),
        )
        if canonical is not None:
            _link_skill_everywhere(skill_name, canonical, all_dirs)

    for base_dir in all_dirs.values():
        removed = _clean_broken_links(base_dir)
        if removed:
            logger.info("Cleaned %d broken skill link(s) in %s", removed, base_dir)


def _iter_bundled_entries(paths: KlirPaths) -> list[tuple[Path, Path]]:
    """Return ``(source, target)`` pairs for each bundled skill."""
    bundled = paths.bundled_skills_dir
    if not bundled.is_dir():
        return []
    target_dir = paths.skills_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    pairs: list[tuple[Path, Path]] = []
    for entry in sorted(bundled.iterdir()):
        if not entry.is_dir() or entry.name.startswith(".") or entry.name in _SKIP_DIRS:
            continue
        pairs.append((entry, target_dir / entry.name))
    return pairs


def sync_bundled_skills(paths: KlirPaths) -> None:
    """Sync bundled skills from the package into the klir workspace.

    Creates symlinks from ``~/.klir/workspace/skills/<name>`` to the
    package's ``_home_defaults/workspace/skills/<name>`` so bundled skills
    stay up-to-date with the installed klir version.

    Real directories are never overwritten (preserves user modifications
    from older Zone 3 copies or manually created skills with the same name).
    """
    for source, target in _iter_bundled_entries(paths):
        if target.exists() and not target.is_symlink():
            continue
        if target.is_symlink():
            if target.resolve() == source.resolve():
                continue
            target.unlink()
        try:
            _create_dir_link(target, source)
            logger.info("Bundled skill linked: %s -> %s", target, source)
        except OSError:
            logger.warning("Failed to link bundled skill %s", source.name, exc_info=True)


def cleanup_klir_links(paths: KlirPaths) -> int:
    """Remove symlinks created by klir in CLI skill directories.

    Only removes symlinks whose resolved target is under the klir workspace
    skills directory or the bundled skills directory.  Everything else
    (real directories, user-managed symlinks) is left untouched.

    Returns the total count of removed links.
    """
    managed_roots = [paths.skills_dir]
    bundled = paths.bundled_skills_dir
    if bundled.is_dir():
        managed_roots.append(bundled)

    removed = 0
    for cli_dir in _cli_skill_dirs().values():
        if not cli_dir.is_dir():
            continue
        for entry in cli_dir.iterdir():
            if not entry.is_symlink():
                continue
            try:
                resolved = entry.resolve()
            except OSError:
                continue
            if any(_is_under(resolved, root) for root in managed_roots):
                entry.unlink()
                removed += 1
                logger.info("Removed klir skill link: %s", entry)

    if removed:
        logger.info("Cleaned up %d klir skill link(s) from CLI directories", removed)
    return removed


async def watch_skill_sync(
    paths: KlirPaths,
    *,
    interval: float = _SKILL_SYNC_INTERVAL,
) -> None:
    """Continuously sync skill directories across all agents.

    Runs ``sync_skills`` in a thread every *interval* seconds.
    Follows the same pattern as ``watch_rule_files``.
    """
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(sync_skills, paths)
        except Exception:
            logger.exception("Skill sync failed")
