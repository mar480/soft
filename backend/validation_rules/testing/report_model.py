from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class ReportContext:
    context_id: str
    entity: str
    period_type: str
    instant: str
    start_date: str
    end_date: str
    dimensions: dict[str, str]

    def to_dict(self) -> dict:
        return {
            "context_id": self.context_id,
            "entity": self.entity,
            "period": {
                "type": self.period_type,
                "instant": self.instant,
                "start_date": self.start_date,
                "end_date": self.end_date,
            },
            "dimensions": dict(sorted(self.dimensions.items())),
        }


@dataclass
class ReportFact:
    fact_id: str
    tag: str
    concept_qname: str | None
    context_id: str | None
    unit: str | None
    decimals: str | None
    value: str
    attributes: dict[str, str] = field(default_factory=dict)

    def numeric_value(self) -> float | None:
        try:
            return float(self.value.replace(",", ""))
        except (ValueError, AttributeError):
            return None

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["attributes"] = dict(sorted(self.attributes.items()))
        return payload


@dataclass
class ReportModel:
    source_path: str
    contexts: dict[str, ReportContext]
    facts: list[ReportFact]

    def to_dict(self) -> dict:
        return {
            "source_path": self.source_path,
            "context_count": len(self.contexts),
            "fact_count": len(self.facts),
            "contexts": [context.to_dict() for _, context in sorted(self.contexts.items())],
            "facts": [fact.to_dict() for fact in self.facts],
        }
