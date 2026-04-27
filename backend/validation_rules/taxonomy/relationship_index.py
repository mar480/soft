from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json
import xml.etree.ElementTree as ET

LINK_NS = "http://www.xbrl.org/2003/linkbase"
XLINK_NS = "http://www.w3.org/1999/xlink"

ARCROLES_OF_INTEREST = {
    "http://www.xbrl.org/2003/arcrole/parent-child": "parent_child",
    "http://xbrl.org/int/dim/arcrole/all": "all",
    "http://xbrl.org/int/dim/arcrole/notAll": "not_all",
    "http://xbrl.org/int/dim/arcrole/hypercube-dimension": "hypercube_dimension",
    "http://xbrl.org/int/dim/arcrole/dimension-domain": "dimension_domain",
    "http://xbrl.org/int/dim/arcrole/domain-member": "domain_member",
    "http://xbrl.org/int/dim/arcrole/dimension-default": "dimension_default",
}


@dataclass(frozen=True)
class RoleDefinitionRecord:
    role_uri: str
    definition: str


@dataclass(frozen=True)
class RelationshipStats:
    role_count: int
    hypercube_count: int
    dimension_count: int
    domain_member_relationship_count: int
    arcrole_counts: dict[str, int]
    role_definitions: list[RoleDefinitionRecord]


def build_relationship_stats(files: set[Path]) -> RelationshipStats:
    roles: set[str] = set()
    role_definitions: dict[str, str] = {}
    hypercubes = 0
    dimensions = 0
    domain_members = 0
    counts: dict[str, int] = {v: 0 for v in ARCROLES_OF_INTEREST.values()}

    for xml_file in sorted(files):
        if xml_file.suffix.lower() not in {".xsd", ".xml"}:
            continue
        try:
            root = ET.parse(xml_file).getroot()
        except ET.ParseError:
            continue

        for role_type in root.findall(f".//{{{LINK_NS}}}roleType"):
            role_uri = role_type.attrib.get("roleURI")
            if not role_uri:
                continue
            def_el = role_type.find(f"{{{LINK_NS}}}definition")
            definition = (def_el.text or "").strip() if def_el is not None else ""
            roles.add(role_uri)
            if definition:
                role_definitions[role_uri] = definition

        for ext_link in root.findall(f".//{{{LINK_NS}}}definitionLink") + root.findall(f".//{{{LINK_NS}}}presentationLink"):
            role = ext_link.attrib.get(f"{{{XLINK_NS}}}role")
            if role:
                roles.add(role)

        for arc in root.findall(f".//{{{LINK_NS}}}definitionArc") + root.findall(f".//{{{LINK_NS}}}presentationArc"):
            arcrole = arc.attrib.get(f"{{{XLINK_NS}}}arcrole")
            if arcrole not in ARCROLES_OF_INTEREST:
                continue
            key = ARCROLES_OF_INTEREST[arcrole]
            counts[key] += 1
            if key == "all":
                hypercubes += 1
            elif key == "hypercube_dimension":
                dimensions += 1
            elif key == "domain_member":
                domain_members += 1

    return RelationshipStats(
        role_count=len(roles),
        hypercube_count=hypercubes,
        dimension_count=dimensions,
        domain_member_relationship_count=domain_members,
        arcrole_counts=counts,
        role_definitions=[RoleDefinitionRecord(role_uri=k, definition=v) for k, v in sorted(role_definitions.items())],
    )


def write_relationship_stats(stats: RelationshipStats, output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(asdict(stats), indent=2), encoding="utf-8")
