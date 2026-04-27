from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class MemberNode:
    qname: str
    label: str | None
    children: list["MemberNode"]

    def to_dict(self) -> dict:
        return {
            "qname": self.qname,
            "label": self.label,
            "children": [child.to_dict() for child in self.children],
        }
