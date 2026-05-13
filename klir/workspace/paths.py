"""Central path resolution for the workspace layout.

This module is the SINGLE SOURCE OF TRUTH for all paths in the framework.
Every path the framework needs is either a field or property of ``KlirPaths``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# klir/workspace/paths.py -> klir/workspace -> klir
_PKG_DIR = Path(__file__).resolve().parent.parent


def _default_home_defaults() -> Path:
    return _PKG_DIR / "_home_defaults"


def _default_framework_root() -> Path:
    return _PKG_DIR.parent


@dataclass(frozen=True)
class KlirPaths:
    """Resolved, immutable paths for the workspace layout.

    All framework paths are derived from three roots:

    - ``klir_home``:    User data directory (default ``~/.klir``).
    - ``home_defaults``:  Bundled template that mirrors ``klir_home`` (package-internal).
    - ``framework_root``: Repository root (for config.example.json).
    """

    klir_home: Path
    home_defaults: Path = field(default_factory=_default_home_defaults)
    framework_root: Path = field(default_factory=_default_framework_root)

    # -- User data paths (inside klir_home) --

    @property
    def workspace(self) -> Path:
        return self.klir_home / "workspace"

    @property
    def config_dir(self) -> Path:
        return self.klir_home / "config"

    @property
    def config_path(self) -> Path:
        return self.config_dir / "config.json"

    @property
    def cron_jobs_path(self) -> Path:
        return self.klir_home / "cron_jobs.json"

    @property
    def webhooks_path(self) -> Path:
        return self.klir_home / "webhooks.json"

    @property
    def logs_dir(self) -> Path:
        return self.klir_home / "logs"

    @property
    def cron_tasks_dir(self) -> Path:
        return self.workspace / "cron_tasks"

    @property
    def cron_state_dir(self) -> Path:
        return self.klir_home / "cron-state"

    def cron_job_state_dir(self, job_id: str) -> Path:
        return self.cron_state_dir / job_id

    @property
    def tools_dir(self) -> Path:
        return self.workspace / "tools"

    @property
    def output_to_user_dir(self) -> Path:
        return self.workspace / "output_to_user"

    @property
    def telegram_files_dir(self) -> Path:
        return self.workspace / "telegram_files"

    @property
    def api_files_dir(self) -> Path:
        return self.workspace / "api_files"

    @property
    def memory_system_dir(self) -> Path:
        return self.workspace / "memory_system"

    @property
    def skills_dir(self) -> Path:
        return self.workspace / "skills"

    @property
    def bundled_skills_dir(self) -> Path:
        """Package-internal skill directory (read-only, ships with klir)."""
        return self.home_defaults / "workspace" / "skills"

    @property
    def tasks_dir(self) -> Path:
        """Per-task metadata folders (TASKMEMORY.md etc.)."""
        return self.workspace / "tasks"

    @property
    def named_sessions_path(self) -> Path:
        return self.klir_home / "named_sessions.json"

    @property
    def startup_state_path(self) -> Path:
        return self.klir_home / "startup_state.json"

    @property
    def inflight_turns_path(self) -> Path:
        return self.klir_home / "inflight_turns.json"

    @property
    def server_state_path(self) -> Path:
        return self.klir_home / "server_state.json"

    @property
    def db_path(self) -> Path:
        return self.klir_home / "klir.db"

    @property
    def env_file(self) -> Path:
        """User-managed ``.env`` for external API secrets."""
        return self.klir_home / ".env"

    @property
    def mainmemory_path(self) -> Path:
        return self.memory_system_dir / "MAINMEMORY.md"

    @property
    def memory_index_path(self) -> Path:
        return self.memory_system_dir / "index.db"

    # -- Framework paths (bundled with package or repo root) --

    @property
    def config_example_path(self) -> Path:
        """Config example: repo root (dev) or package-bundled (installed)."""
        repo_path = self.framework_root / "config.example.json"
        if repo_path.is_file():
            return repo_path
        return _PKG_DIR / "_config_example.json"


def resolve_paths(
    klir_home: str | Path | None = None,
    *,
    framework_root: str | Path | None = None,
    home_defaults: str | Path | None = None,
) -> KlirPaths:
    """Build KlirPaths from explicit values, env vars, or defaults.

    Args:
        klir_home: User data directory. Falls back to ``$KLIR_HOME`` or ``~/.klir``.
        framework_root: Repository root. Falls back to ``$KLIR_FRAMEWORK_ROOT``.
        home_defaults: Template directory. Falls back to ``klir/_home_defaults/``.
    """
    if klir_home is not None:
        home = Path(klir_home).expanduser().resolve()
    else:
        home = (
            Path(
                os.environ.get("KLIR_HOME", str(Path.home() / ".klir")),
            )
            .expanduser()
            .resolve()
        )

    if framework_root is not None:
        fw = Path(framework_root).expanduser().resolve()
    else:
        env_fw = os.environ.get("KLIR_FRAMEWORK_ROOT")
        fw = Path(env_fw).resolve() if env_fw else _default_framework_root()

    hd = Path(home_defaults).resolve() if home_defaults is not None else _default_home_defaults()

    return KlirPaths(klir_home=home, home_defaults=hd, framework_root=fw)
