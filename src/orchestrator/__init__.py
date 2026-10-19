"""Model-neutral agent orchestration (Cycle 2).

Models are untrusted text generators behind ModelProvider (str -> str). Their output is
parsed into typed proposals; actions reach handlers only via an ActorGateway onto
ControlPlane.execute.
"""

from .proposal import ActionProposal, FinalProposal, ProposalError, parse_proposal
from .provider import MockProvider, ModelProvider, ProviderError, ProviderTimeout, ProviderUnavailable
from .runner import AgentRunner, PendingApproval, RunConfig, RunResult, RunState, operation_id_for

__all__ = [
    "ActionProposal", "FinalProposal", "ProposalError", "parse_proposal", "MockProvider",
    "ModelProvider", "ProviderError", "ProviderTimeout", "ProviderUnavailable", "AgentRunner",
    "PendingApproval", "RunConfig", "RunResult", "RunState", "operation_id_for",
]
