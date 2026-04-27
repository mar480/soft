from __future__ import annotations

from dataclasses import dataclass

from .cube_model import CubeModel


@dataclass
class TopicModel:
    topic_id: str
    topic_label: str
    taxonomy_year: int
    entrypoint: str
    hypercubes: list[CubeModel]
    candidate_rules: list[dict]

    def to_dict(self) -> dict:
        primary_items = {
            item["qname"]
            for cube in self.hypercubes
            for item in cube.primary_items
        }
        dimensions = {
            dimension.dimension_qname
            for cube in self.hypercubes
            for dimension in cube.dimensions
        }
        return {
            "topic_id": self.topic_id,
            "topic_label": self.topic_label,
            "taxonomy_year": self.taxonomy_year,
            "entrypoint": self.entrypoint,
            "hypercube_count": len(self.hypercubes),
            "primary_item_count": len(primary_items),
            "dimension_count": len(dimensions),
            "dimensions": sorted(dimensions),
            "hypercubes": [cube.to_dict() for cube in self.hypercubes],
            "candidate_rules": self.candidate_rules,
        }
