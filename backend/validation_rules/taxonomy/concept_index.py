from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json
from typing import Any

from arelle import XbrlConst


def format_qname(qname: Any) -> str | None:
    if qname is None:
        return None
    prefix = getattr(qname, "prefix", None)
    local_name = getattr(qname, "localName", None)
    if prefix and local_name:
        return f"{prefix}:{local_name}"
    if local_name:
        return local_name
    return str(qname)


@dataclass(frozen=True)
class ConceptRecord:
    qname: str
    prefix: str | None
    namespace: str | None
    local_name: str
    standard_label: str | None
    verbose_label: str | None
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


def _concept_sort_key(record: ConceptRecord) -> tuple[str, str]:
    return (record.namespace or "", record.local_name)


def build_concept_index(model_xbrl: object) -> list[ConceptRecord]:
    concepts: dict[str, ConceptRecord] = {}

    for qname, concept in model_xbrl.qnameConcepts.items():
        namespace = getattr(qname, "namespaceURI", None)
        if namespace and ("w3.org" in namespace or "xbrl.org/2003" in namespace):
            continue

        model_document = getattr(concept, "modelDocument", None)
        if model_document is None:
            continue
        if not (getattr(concept, "isItem", False) or getattr(concept, "isTuple", False)):
            continue

        data_type_qname = getattr(getattr(concept, "type", None), "qname", None)
        data_type = format_qname(data_type_qname)
        period_type = getattr(concept, "periodType", None)

        record = ConceptRecord(
            qname=format_qname(qname) or str(qname),
            prefix=getattr(qname, "prefix", None),
            namespace=namespace,
            local_name=getattr(qname, "localName", str(qname)),
            standard_label=concept.label(
                preferredLabel=XbrlConst.standardLabel,
                lang="en-GB",
                fallbackToQname=False,
            )
            or concept.label(
                preferredLabel=XbrlConst.standardLabel,
                lang="en",
                fallbackToQname=False,
            ),
            verbose_label=concept.label(
                preferredLabel=XbrlConst.verboseLabel,
                lang="en-GB",
                fallbackToQname=False,
            )
            or concept.label(
                preferredLabel=XbrlConst.verboseLabel,
                lang="en",
                fallbackToQname=False,
            ),
            period_type=period_type,
            balance=getattr(concept, "balance", None),
            data_type=data_type,
            abstract=getattr(concept, "isAbstract", False),
            substitution_group=format_qname(getattr(concept, "substitutionGroupQname", None)),
            is_numeric=getattr(concept, "isNumeric", False) or _is_numeric_type(data_type),
            is_monetary=(data_type or "").lower().find("monetary") >= 0,
            is_duration=period_type == "duration",
            is_instant=period_type == "instant",
        )
        concepts[record.qname] = record

    return sorted(concepts.values(), key=_concept_sort_key)


def write_concept_index(concepts: list[ConceptRecord], output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(concept) for concept in concepts]
    output_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
