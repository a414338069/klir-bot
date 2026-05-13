"""Multi-agent architecture: supervisor, bus, and inter-agent communication."""

from klir.multiagent.bus import InterAgentBus
from klir.multiagent.health import AgentHealth
from klir.multiagent.models import SubAgentConfig
from klir.multiagent.supervisor import AgentSupervisor

__all__ = ["AgentHealth", "AgentSupervisor", "InterAgentBus", "SubAgentConfig"]
