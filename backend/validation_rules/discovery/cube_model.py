from __future__ import annotations

from dataclasses import dataclass

from .member_tree import MemberNode


@dataclass
class DimensionModel:
    dimension_qname: str
    dimension_label: str | None
    default_member: str | None
    domain_roots: list[str]
    member_tree: list[MemberNode]

    def to_dict(self) -> dict:
        return {
            "dimension_qname": self.dimension_qname,
            "dimension_label": self.dimension_label,
            "default_member": self.default_member,
            "domain_roots": self.domain_roots,
            "member_tree": [node.to_dict() for node in self.member_tree],
        }


@dataclass
class CubeModel:
    cube_qname: str
    cube_label: str | None
    elr: str
    elr_definition: str | None
    source_family_topic_id: str
    source_family_topic_label: str
    family_topic_id: str
    family_topic_label: str
    occurrence_type: str
    variant_label: str | None
    variant_validation: dict
    closed: bool
    primary_items: list[dict]
    dimensions: list[DimensionModel]

    def to_dict(self) -> dict:
        return {
            "cube_qname": self.cube_qname,
            "cube_label": self.cube_label,
            "elr": self.elr,
            "elr_definition": self.elr_definition,
            "source_family_topic_id": self.source_family_topic_id,
            "source_family_topic_label": self.source_family_topic_label,
            "family_topic_id": self.family_topic_id,
            "family_topic_label": self.family_topic_label,
            "occurrence_type": self.occurrence_type,
            "variant_label": self.variant_label,
            "variant_validation": self.variant_validation,
            "closed": self.closed,
            "primary_items": self.primary_items,
            "dimensions": [dimension.to_dict() for dimension in self.dimensions],
        }
