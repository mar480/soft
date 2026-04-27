from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json
import xml.etree.ElementTree as ET

XSD_NS = "http://www.w3.org/2001/XMLSchema"
XBRLI_NS = "http://www.xbrl.org/2003/instance"


@dataclass(frozen=True)
class ConceptRecord:
    qname: str
    namespace: str | None
    local_name: str
    period_type: str | None
    balance: str | None
    data_type: str | None
    abstract: bool
    substitution_group: str | None
    is_numeric: bool
    is_monetary: bool
    is_duration: bool
    is_instant: bool


def _is_numeric_type(data_type: str | None) -> bool:
    if not data_type:
        return False
    numeric_markers = ("monetary", "integer", "decimal", "shares", "percent", "pure")
    lowered = data_type.lower()
    return any(marker in lowered for marker in numeric_markers)


def _iter_schema_files(root_dir: Path) -> list[Path]:
    return sorted(root_dir.rglob("*.xsd"))


def build_concept_index(schema_root: Path) -> list[ConceptRecord]:
    concepts: list[ConceptRecord] = []

    for schema_file in _iter_schema_files(schema_root):
        try:
            root = ET.parse(schema_file).getroot()
        except ET.ParseError:
            continue

        target_ns = root.attrib.get("targetNamespace")
        for el in root.findall(f"{{{XSD_NS}}}element"):
            local_name = el.attrib.get("name")
            if not local_name:
                continue

            data_type = el.attrib.get("type")
            period_type = el.attrib.get(f"{{{XBRLI_NS}}}periodType")
            balance = el.attrib.get(f"{{{XBRLI_NS}}}balance")
            substitution_group = el.attrib.get("substitutionGroup")
            abstract = el.attrib.get("abstract", "false").lower() == "true"
            is_numeric = _is_numeric_type(data_type)

            concepts.append(
                ConceptRecord(
                    qname=f"{{{target_ns}}}{local_name}" if target_ns else local_name,
                    namespace=target_ns,
                    local_name=local_name,
                    period_type=period_type,
                    balance=balance,
                    data_type=data_type,
                    abstract=abstract,
                    substitution_group=substitution_group,
                    is_numeric=is_numeric,
                    is_monetary=(data_type or "").lower().find("monetary") >= 0,
                    is_duration=period_type == "duration",
                    is_instant=period_type == "instant",
                )
            )

    return concepts


def write_concept_index(concepts: list[ConceptRecord], output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(concept) for concept in concepts]
    output_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
