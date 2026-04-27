from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class CandidateRule:
    id: str
    type: str
    topic: str
    severity: str
    confidence: str
    requires_review: bool
    payload: dict

    def to_dict(self) -> dict:
        base = asdict(self)
        payload = base.pop("payload")
        base.update(payload)
        return base
