"""Multi-instance opencode serve process manager.

Manages lifecycle of ``opencode serve`` subprocesses — one per project
directory.  Allocates unique ports, health-checks instances, and supports
state save/restore across manager restarts.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import time
from dataclasses import dataclass, field

import aiohttp

logger = logging.getLogger(__name__)

_server_manager: OpenCodeServerManager | None = None


def get_server_manager() -> OpenCodeServerManager:
    """Return the shared :class:`OpenCodeServerManager` singleton.

    Creates one lazily on first call.  All :class:`OpenCodeHTTPCLI`
    instances share the same manager so server state is consistent
    across the process lifetime.
    """
    global _server_manager
    if _server_manager is None:
        _server_manager = OpenCodeServerManager()
    return _server_manager


@dataclass(slots=True)
class ServerInstance:
    """A running ``opencode serve`` instance bound to a project directory.

    Attributes:
        project_path: Absolute path to the project working directory.
        port: Port number the ``opencode serve`` HTTP server is bound to.
        process: Subprocess handle, or ``None`` for externally-managed instances
                 restored from prior state where the original handle was lost.
        started_at: UNIX timestamp when the server was started.
        base_url: HTTP base URL for the server (e.g. ``http://localhost:4097``).
    """

    project_path: str
    port: int
    started_at: float = field(default_factory=time.time)
    base_url: str = ""
    process: asyncio.subprocess.Process | None = None


class OpenCodeServerManager:
    """Multi-instance manager for ``opencode serve`` subprocesses.

    Maintains a mapping of project directory → running server instance.
    Each server gets a unique port allocated from the range
    ``BASE_PORT`` … ``BASE_PORT + MAX_INSTANCES``.

    Usage::

        mgr = OpenCodeServerManager()
        instance = await mgr.get_or_create("/path/to/project")
        print(f"Server at {instance.base_url}")

        await mgr.shutdown("/path/to/project")
        await mgr.shutdown_all()
    """

    BASE_PORT = 4097
    """Starting port for managed instances.  4096 is reserved for the user's
    default ``opencode serve`` instance."""

    MAX_INSTANCES = 5
    """Maximum number of concurrently managed server instances."""

    STARTUP_TIMEOUT = 30
    """Maximum seconds to wait for a newly-launched server to become healthy."""

    SHUTDOWN_WAIT = 5
    """Grace period in seconds after SIGTERM before sending SIGKILL."""

    def __init__(self) -> None:
        self._instances: dict[str, ServerInstance] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_or_create(self, project_path: str) -> ServerInstance:
        """Return an existing healthy instance for *project_path*, or launch one.

        If an instance already exists for *project_path*, its health is
        checked first.  A healthy instance is returned immediately.  An
        unhealthy instance is discarded and replaced.

        Raises:
            RuntimeError: If no free port is available in the allocated range.
            TimeoutError: If the launched server does not become healthy within
                          :attr:`STARTUP_TIMEOUT` seconds.
        """
        # 1. Check existing instance
        existing = self._instances.get(project_path)
        if existing is not None:
            if await self._wait_for_healthy(existing.base_url, timeout=5):
                return existing
            # Instance is dead — clean up
            logger.warning(
                "Existing instance for %s at %s is unhealthy, replacing",
                project_path, existing.base_url,
            )
            del self._instances[project_path]

        # 2. Allocate a free port
        port = self._allocate_port()
        base_url = f"http://localhost:{port}"

        # 3. Launch server
        logger.info("Starting opencode serve for %s on port %d", project_path, port)
        process = await self._start_server(project_path, port)

        # 4. Wait for health
        try:
            healthy = await self._wait_for_healthy(base_url, timeout=self.STARTUP_TIMEOUT)
        except Exception:
            # Cleanup if health check itself fails
            with contextlib.suppress(OSError):
                process.kill()
                await process.wait()
            raise

        if not healthy:
            # Startup timed out — kill the failed process
            logger.error(
                "Server for %s did not become healthy within %ds",
                project_path, self.STARTUP_TIMEOUT,
            )
            with contextlib.suppress(OSError):
                process.kill()
                await process.wait()
            raise TimeoutError(
                f"opencode serve for {project_path} did not become "
                f"healthy at {base_url} within {self.STARTUP_TIMEOUT}s"
            )

        # 5. Store and return
        instance = ServerInstance(
            project_path=project_path,
            port=port,
            process=process,
            base_url=base_url,
        )
        self._instances[project_path] = instance
        logger.info(
            "Server ready: project=%s port=%d base_url=%s",
            project_path, port, base_url,
        )
        return instance

    def get_instance(self, project_path: str) -> ServerInstance | None:
        """Return the managed instance for *project_path*, or ``None``."""
        return self._instances.get(project_path)

    def list_instances(self) -> list[ServerInstance]:
        """Return all currently managed server instances."""
        return list(self._instances.values())

    async def shutdown(self, project_path: str) -> None:
        """Gracefully shut down and remove the instance for *project_path*.

        Sends SIGTERM, waits up to :attr:`SHUTDOWN_WAIT` seconds, then sends
        SIGKILL if the process is still alive.  Idempotent — does nothing if
        no instance is registered for *project_path*.
        """
        instance = self._instances.pop(project_path, None)
        if instance is None:
            return

        logger.info(
            "Shutting down server: project=%s port=%d",
            project_path, instance.port,
        )

        if instance.process is None:
            # Restored instance without a process handle — nothing to kill.
            return

        process = instance.process
        if process.returncode is not None:
            # Already exited.
            return

        try:
            process.terminate()
        except ProcessLookupError:
            return

        try:
            await asyncio.wait_for(process.wait(), timeout=self.SHUTDOWN_WAIT)
        except TimeoutError:
            logger.warning(
                "Server for %s did not exit after SIGTERM, sending SIGKILL",
                project_path,
            )
            try:
                process.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=self.SHUTDOWN_WAIT)
            except TimeoutError:
                logger.error(
                    "Server for %s did not exit even after SIGKILL",
                    project_path,
                )

    async def shutdown_all(self) -> None:
        """Shut down all managed instances concurrently."""
        paths = list(self._instances.keys())
        if not paths:
            return
        logger.info("Shutting down %d server instance(s)", len(paths))
        await asyncio.gather(*(self.shutdown(path) for path in paths))

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def save_state(self) -> dict:
        """Serialize manager state for later restoration.

        Returns:
            dict keyed by project path, each value containing ``port`` and
            ``started_at``.  Only instances with a live process are included.
        """
        state: dict = {}
        for instance in self._instances.values():
            if instance.process is not None and instance.process.returncode is None:
                state[instance.project_path] = {
                    "port": instance.port,
                    "started_at": instance.started_at,
                }
        return state

    async def restore_from_state(self, state: dict) -> None:
        """Restore manager state from a previously-saved snapshot.

        For each project path in *state*:
        - If the server is still alive at the recorded port, a ``ServerInstance``
          is created in the registry (without a process handle — the process was
          started in a previous lifecycle).
        - If the server is dead, :meth:`get_or_create` is called to launch a
          fresh instance.
        """
        for project_path, info in state.items():
            port = info["port"]
            base_url = f"http://localhost:{port}"

            is_alive = await self._wait_for_healthy(base_url, timeout=5)

            if is_alive:
                logger.info(
                    "Restored existing server: project=%s port=%d",
                    project_path, port,
                )
                self._instances[project_path] = ServerInstance(
                    project_path=project_path,
                    port=port,
                    started_at=info.get("started_at", time.time()),
                    base_url=base_url,
                    # process=None — handle was lost across restarts.
                )
            else:
                logger.info(
                    "Saved server dead, re-creating: project=%s",
                    project_path,
                )
                await self.get_or_create(project_path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _allocate_port(self) -> int:
        """Find a free port in the configured range.

        Scans :attr:`BASE_PORT` through ``BASE_PORT + MAX_INSTANCES`` (the
        extra slot provides a buffer for races).  Checks both the internal
        instance registry and the OS socket layer.

        Returns:
            An available port number.

        Raises:
            RuntimeError: If all ports in the range are occupied.
        """
        assigned_ports = {inst.port for inst in self._instances.values()}
        for port in range(self.BASE_PORT, self.BASE_PORT + self.MAX_INSTANCES + 1):
            if port in assigned_ports:
                continue
            if _is_port_free(port):
                logger.debug("Allocated port %d", port)
                return port

        raise RuntimeError(
            f"No free port in range {self.BASE_PORT}–"
            f"{self.BASE_PORT + self.MAX_INSTANCES}. "
            f"Currently managing {len(self._instances)} instance(s)."
        )

    async def _start_server(
        self, project_path: str, port: int,
    ) -> asyncio.subprocess.Process:
        """Launch ``opencode serve`` as a subprocess.

        Args:
            project_path: Working directory for the server.
            port: TCP port to bind the HTTP server.

        Returns:
            The subprocess handle.
        """
        return await asyncio.create_subprocess_exec(
            "opencode", "serve", "--port", str(port),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_path,
        )

    async def _wait_for_healthy(
        self, base_url: str, timeout: float = 30,
    ) -> bool:
        """Poll the health endpoint until it responds 200 or *timeout* elapses.

        Args:
            base_url: Base URL of the server (e.g. ``http://localhost:4097``).
            timeout: Maximum seconds to wait.

        Returns:
            ``True`` if the server responded with HTTP 200, ``False`` otherwise.
        """
        health_url = f"{base_url}/global/health"
        deadline = time.monotonic() + timeout

        async with aiohttp.ClientSession() as session:
            while time.monotonic() < deadline:
                try:
                    async with session.get(health_url) as resp:
                        if resp.status == 200:
                            return True
                        logger.debug(
                            "Health check %s returned %d, retrying…",
                            health_url, resp.status,
                        )
                except (aiohttp.ClientError, OSError) as exc:
                    logger.debug("Health check %s failed: %s", health_url, exc)

                await asyncio.sleep(1.0)

        return False


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _is_port_free(port: int) -> bool:
    """Check whether *port* is available for binding on localhost.

    Returns:
        ``True`` if the port can be bound, ``False`` if it is already in use.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False
