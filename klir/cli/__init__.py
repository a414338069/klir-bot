"""CLI layer: provider abstraction, process tracking, streaming."""

from klir.cli.auth import AuthResult as AuthResult
from klir.cli.auth import AuthStatus as AuthStatus
from klir.cli.auth import check_all_auth as check_all_auth
from klir.cli.base import BaseCLI as BaseCLI
from klir.cli.base import CLIConfig as CLIConfig
from klir.cli.coalescer import CoalesceConfig as CoalesceConfig
from klir.cli.coalescer import StreamCoalescer as StreamCoalescer
from klir.cli.factory import create_cli as create_cli
from klir.cli.process_registry import ProcessRegistry as ProcessRegistry
from klir.cli.service import CLIService as CLIService
from klir.cli.service import CLIServiceConfig as CLIServiceConfig
from klir.cli.types import AgentRequest as AgentRequest
from klir.cli.types import AgentResponse as AgentResponse
from klir.cli.types import CLIResponse as CLIResponse

__all__ = [
    "AgentRequest",
    "AgentResponse",
    "AuthResult",
    "AuthStatus",
    "BaseCLI",
    "CLIConfig",
    "CLIResponse",
    "CLIService",
    "CLIServiceConfig",
    "CoalesceConfig",
    "ProcessRegistry",
    "StreamCoalescer",
    "check_all_auth",
    "create_cli",
]
