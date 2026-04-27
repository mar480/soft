from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from arelle import XbrlConst

from .concept_index import format_qname

ARCROLES = {
    XbrlConst.parentChild: "parent_child",
    XbrlConst.all: "all",
    XbrlConst.notAll: "not_all",
    XbrlConst.hypercubeDimension: "hypercube_dimension",
    XbrlConst.dimensionDomain: "dimension_domain",
    XbrlConst.domainMember: "domain_member",
    XbrlConst.dimensionDefault: "dimension_default",
}


@dataclass(frozen=True)
class RoleRecord:
    role_uri: str
    definition: str | None
    used_on: list[str]
    arcroles_present: list[str]
    related_hypercubes: list[str]
    related_presentation_roots: list[str]


@dataclass(frozen=True)
class RelationshipRecord:
    role_uri: str
    arcrole: str
    arcrole_name: str
    from_qname: str | None
    to_qname: str | None
    target_role: str | None
    context_element: str | None
    closed: bool | None


@dataclass(frozen=True)
class RelationshipIndex:
    role_count: int
    hypercube_count: int
    dimension_count: int
    domain_member_relationship_count: int
    arcrole_counts: dict[str, int]
    roles: list[RoleRecord]
    relationships: list[RelationshipRecord]


def is_reportable_primary_item(concept: object | None) -> bool:
    if concept is None:
        return False
    if not getattr(concept, "isItem", False):
        return False
    if getattr(concept, "isDimensionItem", False) or getattr(concept, "isHypercubeItem", False):
        return False
    return True


def collect_presentation_primary_item_membership(model_xbrl: object) -> dict:
    presentation_set = model_xbrl.relationshipSet(XbrlConst.parentChild)
    role_entries: list[dict] = []
    unique_primary_items: dict[str, dict] = {}

    for role_uri in sorted(set(presentation_set.linkRoleUris)):
        role_relationships = model_xbrl.relationshipSet(XbrlConst.parentChild, role_uri).modelRelationships
        role_primary_items: dict[str, dict] = {}

        for relationship in role_relationships:
            for concept in (relationship.fromModelObject, relationship.toModelObject):
                qname = format_qname(getattr(concept, "qname", None))
                if not qname or not is_reportable_primary_item(concept):
                    continue

                record = {
                    "qname": qname,
                    "label": concept.label(
                        preferredLabel=XbrlConst.standardLabel,
                        lang="en-GB",
                        fallbackToQname=False,
                    )
                    or concept.label(
                        preferredLabel=XbrlConst.standardLabel,
                        lang="en",
                        fallbackToQname=False,
                    ),
                    "abstract": getattr(concept, "isAbstract", False),
                }
                role_primary_items[qname] = record
                unique_primary_items[qname] = record

        role_entries.append(
            {
                "role_uri": role_uri,
                "definition": _role_definition(model_xbrl, role_uri),
                "primary_item_count": len(role_primary_items),
                "primary_items": sorted(role_primary_items.values(), key=lambda item: item["qname"]),
            }
        )

    return {
        "primary_item_count": len(unique_primary_items),
        "primary_items": sorted(unique_primary_items.values(), key=lambda item: item["qname"]),
        "roles": role_entries,
    }


def _role_definition(model_xbrl: object, role_uri: str) -> str | None:
    role_types = model_xbrl.roleTypes.get(role_uri, [])
    if not role_types:
        return None
    return getattr(role_types[0], "definition", None)


def _role_used_on(model_xbrl: object, role_uri: str) -> list[str]:
    role_types = model_xbrl.roleTypes.get(role_uri, [])
    if not role_types:
        return []
    used_ons: set[str] = set()
    for role_type in role_types:
        used_ons.update(str(used_on) for used_on in getattr(role_type, "usedOns", set()))
    return sorted(used_ons)


def _relationship_record(role_uri: str, arcrole_uri: str, relationship: Any) -> RelationshipRecord:
    arc_element = getattr(relationship, "arcElement", None)
    closed_raw = arc_element.get("{http://xbrl.org/2005/xbrldt}closed") if arc_element is not None else None
    return RelationshipRecord(
        role_uri=role_uri,
        arcrole=arcrole_uri,
        arcrole_name=ARCROLES[arcrole_uri],
        from_qname=format_qname(getattr(getattr(relationship, "fromModelObject", None), "qname", None)),
        to_qname=format_qname(getattr(getattr(relationship, "toModelObject", None), "qname", None)),
        target_role=arc_element.get("{http://xbrl.org/2005/xbrldt}targetRole") if arc_element is not None else None,
        context_element=arc_element.get("{http://xbrl.org/2005/xbrldt}contextElement") if arc_element is not None else None,
        closed=None if closed_raw is None else closed_raw.lower() == "true",
    )


def build_relationship_index(model_xbrl: object) -> RelationshipIndex:
    role_uris: set[str] = set()
    counts: dict[str, int] = {value: 0 for value in ARCROLES.values()}
    relationships: list[RelationshipRecord] = []
    role_arcroles: dict[str, set[str]] = {}
    role_hypercubes: dict[str, set[str]] = {}
    role_presentation_roots: dict[str, set[str]] = {}
    unique_hypercubes: set[str] = set()
    unique_dimensions: set[str] = set()

    for arcrole_uri, arcrole_name in ARCROLES.items():
        relationship_set = model_xbrl.relationshipSet(arcrole_uri)
        link_roles = sorted(set(relationship_set.linkRoleUris))
        for role_uri in link_roles:
            role_uris.add(role_uri)
            role_arcroles.setdefault(role_uri, set()).add(arcrole_name)
            role_relationships = model_xbrl.relationshipSet(arcrole_uri, role_uri).modelRelationships
            for relationship in role_relationships:
                relationships.append(_relationship_record(role_uri, arcrole_uri, relationship))
                counts[arcrole_name] += 1

                if arcrole_name in {"all", "not_all"}:
                    hypercube_qname = format_qname(getattr(getattr(relationship, "toModelObject", None), "qname", None))
                    if hypercube_qname:
                        role_hypercubes.setdefault(role_uri, set()).add(hypercube_qname)
                        unique_hypercubes.add(hypercube_qname)
                elif arcrole_name == "hypercube_dimension":
                    dimension_qname = format_qname(getattr(getattr(relationship, "toModelObject", None), "qname", None))
                    if dimension_qname:
                        unique_dimensions.add(dimension_qname)

    presentation_set = model_xbrl.relationshipSet(XbrlConst.parentChild)
    for role_uri in sorted(set(presentation_set.linkRoleUris)):
        role_uris.add(role_uri)
        role_arcroles.setdefault(role_uri, set()).add(ARCROLES[XbrlConst.parentChild])
        role_relationships = model_xbrl.relationshipSet(XbrlConst.parentChild, role_uri).modelRelationships
        from_qnames: set[str] = set()
        to_qnames: set[str] = set()
        for relationship in role_relationships:
            from_qname = format_qname(getattr(getattr(relationship, "fromModelObject", None), "qname", None))
            to_qname = format_qname(getattr(getattr(relationship, "toModelObject", None), "qname", None))
            if from_qname:
                from_qnames.add(from_qname)
            if to_qname:
                to_qnames.add(to_qname)
        role_presentation_roots[role_uri] = from_qnames - to_qnames

    roles = [
        RoleRecord(
            role_uri=role_uri,
            definition=_role_definition(model_xbrl, role_uri),
            used_on=_role_used_on(model_xbrl, role_uri),
            arcroles_present=sorted(role_arcroles.get(role_uri, set())),
            related_hypercubes=sorted(role_hypercubes.get(role_uri, set())),
            related_presentation_roots=sorted(role_presentation_roots.get(role_uri, set())),
        )
        for role_uri in sorted(role_uris)
    ]

    return RelationshipIndex(
        role_count=len(roles),
        hypercube_count=len(unique_hypercubes),
        dimension_count=len(unique_dimensions),
        domain_member_relationship_count=counts["domain_member"],
        arcrole_counts=counts,
        roles=roles,
        relationships=relationships,
    )


def write_relationship_stats(stats: RelationshipIndex, output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(asdict(stats), indent=2), encoding="utf-8")
