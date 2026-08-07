from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EvidenceCheck:
    name: str
    passed: bool | None = None
    score: float | None = None
    severity: str = "medium"
    reason: str = ""
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ArtifactRef:
    type: str
    uri: str
    name: str = ""


@dataclass(frozen=True)
class EvidenceBundle:
    checks: tuple[EvidenceCheck, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    metrics: dict[str, float] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return bool(self.checks or self.artifacts or self.metrics)


@dataclass(frozen=True)
class SkillProfile:
    skill_id: str
    required_checks: tuple[str, ...] = ()
    critical_checks: tuple[str, ...] = ()
    min_evidence_score: float = 0.75
    high_confidence_requires_artifact_types: tuple[str, ...] = ()
