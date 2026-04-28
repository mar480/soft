from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .report_model import ReportContext, ReportFact, ReportModel


NS = {
    "ix": "http://www.xbrl.org/2013/inlineXBRL",
    "xbrli": "http://www.xbrl.org/2003/instance",
    "xbrldi": "http://xbrl.org/2006/xbrldi",
}

KNOWN_FACT_ATTRS = {"id", "name", "contextRef", "unitRef", "decimals"}


def _load_xml(path: Path) -> ET.Element:
    return ET.fromstring(path.read_text(encoding="utf-8"))


def _strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _collect_contexts(root: ET.Element) -> dict[str, ReportContext]:
    contexts: dict[str, ReportContext] = {}
    for context in root.findall(".//xbrli:context", NS):
        context_id = context.attrib.get("id")
        if not context_id:
            continue
        identifier = context.findtext("./xbrli:entity/xbrli:identifier", default="", namespaces=NS)
        instant = context.findtext("./xbrli:period/xbrli:instant", default="", namespaces=NS)
        dimensions: dict[str, str] = {}
        for member in context.findall(".//xbrldi:explicitMember", NS):
            dimension_name = member.attrib.get("dimension")
            if dimension_name:
                dimensions[dimension_name] = (member.text or "").strip()
        contexts[context_id] = ReportContext(
            context_id=context_id,
            entity=identifier,
            period_type="instant",
            instant=instant,
            dimensions=dimensions,
        )
    return contexts


def _collect_units(root: ET.Element) -> dict[str, str]:
    units: dict[str, str] = {}
    for unit in root.findall(".//xbrli:unit", NS):
        unit_id = unit.attrib.get("id")
        if not unit_id:
            continue
        measure = unit.findtext("./xbrli:measure", default="", namespaces=NS)
        units[unit_id] = measure.strip()
    return units


def _collect_facts(root: ET.Element, *, units: dict[str, str]) -> list[ReportFact]:
    facts: list[ReportFact] = []
    fact_nodes = root.findall(".//ix:nonFraction", NS) + root.findall(".//ix:nonNumeric", NS)
    for index, fact in enumerate(fact_nodes, start=1):
        unit_ref = fact.attrib.get("unitRef")
        extra_attrs = {
            key: value
            for key, value in fact.attrib.items()
            if key not in KNOWN_FACT_ATTRS
        }
        facts.append(
            ReportFact(
                fact_id=fact.attrib.get("id") or f"fact_{index:03d}",
                tag=_strip_namespace(fact.tag),
                concept_qname=fact.attrib.get("name"),
                context_id=fact.attrib.get("contextRef"),
                unit=units.get(unit_ref, unit_ref),
                decimals=fact.attrib.get("decimals"),
                value=(fact.text or "").strip(),
                attributes=extra_attrs,
            )
        )
    return facts


def load_report_model(path: str | Path) -> ReportModel:
    source_path = Path(path)
    root = _load_xml(source_path)
    contexts = _collect_contexts(root)
    units = _collect_units(root)
    facts = _collect_facts(root, units=units)
    return ReportModel(source_path=str(source_path), contexts=contexts, facts=facts)
