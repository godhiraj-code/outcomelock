"""OutcomeLock: deterministic stale-work protection for autonomous agents."""

from .__about__ import __version__
from .core import Decision, evaluate
from .store import EvidenceStore, Observation

__all__ = ["Decision", "EvidenceStore", "Observation", "__version__", "evaluate"]
