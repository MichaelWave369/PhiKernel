"""TIEKAT Sovereign Coherence Kernel for PhiKernel.

This package adds the first foundational merge for PhiKernel v0.2.0:
- canonical claim intake
- weighted quorum resolution
- temporal history and drift
- evidence packet construction
- dispute / branch preservation
- policy gate evaluation

The modules are intentionally local-first and file-backed so they can be
integrated into the existing PhiKernel shell and heart surfaces incrementally.
"""

from .claims import KernelClaim, CompositeKey, make_composite_key
from .quorum import QuorumResult, QuorumEngine
from .temporal import ResolutionHistoryStore, TemporalSummary
from .evidence import EvidencePacket, build_evidence_packet
from .disputes import DisputeRecord, DisputeStore
from .policy import PolicyProfile, PolicyDecision, PolicyGate

__all__ = [
    "CompositeKey",
    "KernelClaim",
    "make_composite_key",
    "QuorumResult",
    "QuorumEngine",
    "ResolutionHistoryStore",
    "TemporalSummary",
    "EvidencePacket",
    "build_evidence_packet",
    "DisputeRecord",
    "DisputeStore",
    "PolicyProfile",
    "PolicyDecision",
    "PolicyGate",
]
