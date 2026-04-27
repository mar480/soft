from __future__ import annotations

from collections import defaultdict

from arelle import XbrlConst

from ..taxonomy.concept_index import format_qname
from ..taxonomy.relationship_index import is_reportable_primary_item
from .cube_model import CubeModel, DimensionModel
from .member_tree import MemberNode
from .topic_classifier import classify_topic, classify_topic_priority
from .topic_model import TopicModel


def _apply_topic_hierarchy(topic_id: str, topic_label: str) -> tuple[str, str]:
    if topic_id.startswith("financial_assets_"):
        return "financial_assets", "Financial Assets"
    if topic_id.startswith("financial_liabilities_"):
        return "financial_liabilities", "Financial Liabilities"
    return topic_id, topic_label


def _concept_label(concept: object | None) -> str | None:
    if concept is None:
        return None
    return concept.label(
        preferredLabel=XbrlConst.standardLabel,
        lang="en-GB",
        fallbackToQname=False,
    ) or concept.label(
        preferredLabel=XbrlConst.standardLabel,
        lang="en",
        fallbackToQname=False,
    )


def _role_definition(model_xbrl: object, role_uri: str) -> str | None:
    role_types = model_xbrl.roleTypes.get(role_uri, [])
    if not role_types:
        return None
    return getattr(role_types[0], "definition", None)


def _presentation_descendants(model_xbrl: object, role_uri: str, start_concept: object) -> dict[str, dict]:
    relationships = model_xbrl.relationshipSet(XbrlConst.parentChild, role_uri).fromModelObject(start_concept)
    descendants: dict[str, dict] = {}
    stack = [relationship.toModelObject for relationship in relationships]

    while stack:
        concept = stack.pop()
        qname = format_qname(getattr(concept, "qname", None))
        if not qname or qname in descendants:
            continue
        if is_reportable_primary_item(concept):
            descendants[qname] = {
                "qname": qname,
                "label": _concept_label(concept),
                "abstract": getattr(concept, "isAbstract", False),
                "source": "presentation_descendant",
            }
        for relationship in model_xbrl.relationshipSet(XbrlConst.parentChild, role_uri).fromModelObject(concept):
            stack.append(relationship.toModelObject)

    return descendants


def _member_children(model_xbrl: object, role_uri: str, concept: object, visited: set[tuple[str, str]]) -> list[MemberNode]:
    children: list[MemberNode] = []
    for relationship in model_xbrl.relationshipSet(XbrlConst.domainMember, role_uri).fromModelObject(concept):
        child = relationship.toModelObject
        qname = format_qname(getattr(child, "qname", None))
        if not qname:
            continue
        key = (role_uri, qname)
        if key in visited:
            continue
        visited.add(key)
        children.append(
            MemberNode(
                qname=qname,
                label=_concept_label(child),
                children=_member_children(model_xbrl, role_uri, child, visited),
            )
        )
    return children


def _dimension_model(model_xbrl: object, cube_role_uri: str, relationship: object) -> DimensionModel:
    dimension = relationship.toModelObject
    dimension_qname = format_qname(getattr(dimension, "qname", None)) or ""
    dimension_role_uri = relationship.targetRole or cube_role_uri

    default_member = None
    for default_relationship in model_xbrl.relationshipSet(XbrlConst.dimensionDefault, dimension_role_uri).fromModelObject(dimension):
        default_member = format_qname(getattr(default_relationship.toModelObject, "qname", None))
        if default_member:
            break

    domain_roots: list[str] = []
    member_tree: list[MemberNode] = []
    for domain_relationship in model_xbrl.relationshipSet(XbrlConst.dimensionDomain, dimension_role_uri).fromModelObject(dimension):
        domain = domain_relationship.toModelObject
        domain_qname = format_qname(getattr(domain, "qname", None))
        if not domain_qname:
            continue
        domain_roots.append(domain_qname)
        member_tree.append(
            MemberNode(
                qname=domain_qname,
                label=_concept_label(domain),
                children=_member_children(model_xbrl, dimension_role_uri, domain, {(dimension_role_uri, domain_qname)}),
            )
        )

    return DimensionModel(
        dimension_qname=dimension_qname,
        dimension_label=_concept_label(dimension),
        default_member=default_member,
        domain_roots=domain_roots,
        member_tree=member_tree,
    )


def discover_cubes(model_xbrl: object) -> list[CubeModel]:
    cubes: list[CubeModel] = []
    for arcrole in (XbrlConst.all, XbrlConst.notAll):
        relationship_set = model_xbrl.relationshipSet(arcrole)
        for role_uri in sorted(set(relationship_set.linkRoleUris)):
            for relationship in model_xbrl.relationshipSet(arcrole, role_uri).modelRelationships:
                primary_item = relationship.fromModelObject
                cube = relationship.toModelObject
                primary_items: dict[str, dict] = {}

                primary_qname = format_qname(getattr(primary_item, "qname", None))
                if primary_qname and is_reportable_primary_item(primary_item):
                    primary_items[primary_qname] = {
                        "qname": primary_qname,
                        "label": _concept_label(primary_item),
                        "abstract": getattr(primary_item, "isAbstract", False),
                        "source": "cube_attachment",
                    }

                primary_items.update(_presentation_descendants(model_xbrl, role_uri, primary_item))

                dimensions = [
                    _dimension_model(model_xbrl, role_uri, dimension_relationship)
                    for dimension_relationship in model_xbrl.relationshipSet(XbrlConst.hypercubeDimension, role_uri).fromModelObject(cube)
                ]

                cubes.append(
                    CubeModel(
                        cube_qname=format_qname(getattr(cube, "qname", None)) or "",
                        cube_label=_concept_label(cube),
                        elr=role_uri,
                        elr_definition=_role_definition(model_xbrl, role_uri),
                        source_family_topic_id="",
                        source_family_topic_label="",
                        family_topic_id="",
                        family_topic_label="",
                        occurrence_type="base",
                        variant_label=None,
                        variant_validation={},
                        closed=(relationship.arcElement.get("{http://xbrl.org/2005/xbrldt}closed", "false").lower() == "true"),
                        primary_items=sorted(primary_items.values(), key=lambda item: item["qname"]),
                        dimensions=dimensions,
                    )
                )
    return cubes


def discover_topics(*, model_xbrl: object, taxonomy_year: int, entrypoint: str) -> list[TopicModel]:
    topic_groups: dict[str, list[CubeModel]] = defaultdict(list)
    topic_labels: dict[str, str] = {}

    for cube in discover_cubes(model_xbrl):
        classification = classify_topic(
            cube_qname=cube.cube_qname,
            cube_label=cube.cube_label,
            elr_definition=cube.elr_definition,
            dimension_qnames=[dimension.dimension_qname for dimension in cube.dimensions],
        )
        cube.source_family_topic_id = classification["topic_id"]
        cube.source_family_topic_label = classification["topic_label"]
        family_topic_id, family_topic_label = _apply_topic_hierarchy(
            classification["topic_id"],
            classification["topic_label"],
        )
        cube.family_topic_id = family_topic_id
        cube.family_topic_label = family_topic_label
        cube.occurrence_type = classification["occurrence_type"]
        cube.variant_label = classification["variant_label"]
        cube.variant_validation = classification["variant_validation"]
        topic_groups[family_topic_id].append(cube)
        topic_labels.setdefault(family_topic_id, family_topic_label)

    topics = [
        TopicModel(
            topic_id=topic_id,
            topic_label=topic_labels[topic_id],
            taxonomy_year=taxonomy_year,
            entrypoint=entrypoint,
            priority=classify_topic_priority(topic_id=topic_id, topic_label=topic_labels[topic_id])[0],
            topic_kind=classify_topic_priority(topic_id=topic_id, topic_label=topic_labels[topic_id])[1],
            hypercubes=sorted(
                cubes,
                key=lambda cube: (
                    cube.family_topic_label,
                    cube.occurrence_type,
                    cube.variant_label or "",
                    cube.elr_definition or cube.cube_label or cube.cube_qname,
                ),
            ),
            candidate_rules=[],
        )
        for topic_id, cubes in topic_groups.items()
    ]
    return sorted(topics, key=lambda topic: topic.topic_id)
