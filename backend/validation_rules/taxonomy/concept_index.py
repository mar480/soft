from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json
import xml.etree.ElementTree as ET

XSD_NS = "http://www.w3.org/2001/XMLSchema"
XBRLI_NS = "http://www.xbrl.org/2003/instance"


@dataclass(frozen=True)
class ConceptRecord:
    concept_qname: str
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
    source_schema: str


def _is_numeric_type(data_type: str | None) -> bool:
    if not data_type:
        return False
    lowered = data_type.lower()
    return any(marker in lowered for marker in ("integer", "decimal", "monetary", "shares", "percent", "pure"))


def _extract_ns_prefixes(xml_file: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for event, payload in ET.iterparse(xml_file, events=("start-ns",)):
        prefix, uri = payload
        if prefix:
            out[uri] = prefix
    return out


def _preferred_prefix(uri: str | None, known: dict[str, str]) -> str | None:
    if not uri:
        return None
    return known.get(uri)


def _prefixed_qname(prefix: str | None, local_name: str) -> str:
    return f"{prefix}:{local_name}" if prefix else local_name


def build_concept_index(schema_files: set[Path]) -> list[ConceptRecord]:
    concepts: list[ConceptRecord] = []
    ns_map: dict[str, str] = {}

    for schema_file in sorted(schema_files):
        if schema_file.suffix.lower() != ".xsd":
            continue
        try:
            ns_map.update(_extract_ns_prefixes(schema_file))
        except ET.ParseError:
            continue

    for schema_file in sorted(schema_files):
        if schema_file.suffix.lower() != ".xsd":
            continue

        try:
            root = ET.parse(schema_file).getroot()
        except ET.ParseError:
            continue

        target_ns = root.attrib.get("targetNamespace")
        prefix = _preferred_prefix(target_ns, ns_map)

        for el in root.findall(f"{{{XSD_NS}}}element"):
            local_name = el.attrib.get("name")
            if not local_name:
                continue

            data_type = el.attrib.get("type")
            period_type = el.attrib.get(f"{{{XBRLI_NS}}}periodType")
            balance = el.attrib.get(f"{{{XBRLI_NS}}}balance")
            substitution_group = el.attrib.get("substitutionGroup")
            abstract = el.attrib.get("abstract", "false").lower() == "true"

            concepts.append(
                ConceptRecord(
                    concept_qname=_prefixed_qname(prefix, local_name),
                    namespace=target_ns,
                    local_name=local_name,
                    period_type=period_type,
                    balance=balance,
                    data_type=data_type,
                    abstract=abstract,
                    substitution_group=substitution_group,
                    is_numeric=_is_numeric_type(data_type),
                    is_monetary="monetary" in (data_type or "").lower(),
                    is_duration=period_type == "duration",
                    is_instant=period_type == "instant",
                    source_schema=str(schema_file),
                )
            )

    return concepts


def write_concept_index(concepts: list[ConceptRecord], output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps([asdict(c) for c in concepts], indent=2), encoding="utf-8")
