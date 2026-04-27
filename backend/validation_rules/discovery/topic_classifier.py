from __future__ import annotations

import re

from ..taxonomy.labels import humanize_topic_label, strip_qname_prefix, topic_id_from_name

_TRAILING_VARIANT_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"^(?P<family>.+?)\s*-\s*Analysis\s*(?P<num>\d+)$", re.I), "analysis", "Analysis {num}"),
    (re.compile(r"^(?P<family>.+?)\s+Analysis\s*(?P<num>\d+)$", re.I), "analysis", "Analysis {num}"),
    (re.compile(r"^(?P<family>.+?)\s*-\s*Grouping\s*(?P<num>\d+)$", re.I), "grouping", "Grouping {num}"),
    (re.compile(r"^(?P<family>.+?)\s+Grouping\s*(?P<num>\d+)$", re.I), "grouping", "Grouping {num}"),
    (re.compile(r"^(?P<family>.+?)\s*-\s*(?P<label>Segments)$", re.I), "segments", "{label}"),
    (re.compile(r"^(?P<family>.+?)\s*-\s*(?P<label>Range)$", re.I), "range", "{label}"),
    (re.compile(r"^(?P<family>.+?)\s*-\s*(?P<label>Basic|Main|Full|Assets|Liabilities|Parents|Subsidiaries|Associates|Joint Ventures|Other Parties|Finance Leases|Equities)$", re.I), "variant", "{label}"),
]
_FAMILY_ALIASES = {
    "ppe": "Property Plant Equipment",
}
_DEPRIORITISED_FAMILIES = {
    "basic",
    "empty",
}


def _cleanup_source_text(text: str) -> str:
    cleaned = re.sub(r"^\d+\s*[-.:]\s*", "", text).strip()
    cleaned = cleaned.replace("[Hypercube]", "").replace("[hypercube]", "")
    cleaned = cleaned.replace("Hypercube -", "").replace("hypercube -", "")
    cleaned = cleaned.replace("[", " ").replace("]", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -")
    return cleaned


def classify_topic(*, cube_qname: str, cube_label: str | None, elr_definition: str | None, dimension_qnames: list[str]) -> dict:
    source_text = elr_definition or cube_label or strip_qname_prefix(cube_qname)
    cleaned = _cleanup_source_text(source_text)
    family_text = cleaned or strip_qname_prefix(cube_qname)
    occurrence_type = "base"
    variant_label: str | None = None

    for pattern, candidate_type, label_template in _TRAILING_VARIANT_PATTERNS:
        match = pattern.match(family_text)
        if not match:
            continue
        family_text = match.group("family").strip()
        occurrence_type = candidate_type
        variant_label = label_template.format(**{k: v for k, v in match.groupdict().items() if v is not None})
        break

    family_text = _FAMILY_ALIASES.get(family_text.lower(), family_text)

    family_topic_id = topic_id_from_name(family_text) or topic_id_from_name(strip_qname_prefix(cube_qname))
    family_topic_label = humanize_topic_label(family_text) or family_text or cube_qname

    validation = validate_occurrence_type(
        occurrence_type=occurrence_type,
        variant_label=variant_label,
        dimension_qnames=dimension_qnames,
    )

    return {
        "topic_id": family_topic_id,
        "topic_label": family_topic_label,
        "occurrence_type": occurrence_type,
        "variant_label": variant_label,
        "variant_validation": validation,
    }


def validate_occurrence_type(*, occurrence_type: str, variant_label: str | None, dimension_qnames: list[str]) -> dict:
    lowered_dimensions = [qname.lower() for qname in dimension_qnames]
    evidence: list[str] = []
    matches = True

    if occurrence_type == "analysis":
        matches = any("analysisdimension" in qname for qname in lowered_dimensions)
        evidence = [qname for qname in dimension_qnames if "analysisdimension" in qname.lower()]
    elif occurrence_type == "grouping":
        matches = any("group" in qname.lower() for qname in dimension_qnames)
        evidence = [qname for qname in dimension_qnames if "group" in qname.lower()]
    elif occurrence_type == "segments":
        matches = any("segment" in qname.lower() or "majorcustomers" in qname.lower() for qname in dimension_qnames)
        evidence = [qname for qname in dimension_qnames if "segment" in qname.lower() or "majorcustomers" in qname.lower()]
    elif occurrence_type == "range":
        matches = any("rangedimension" in qname.lower() for qname in lowered_dimensions)
        evidence = [qname for qname in dimension_qnames if "rangedimension" in qname.lower()]
    else:
        evidence = []

    return {
        "matches_dimension_content": matches,
        "evidence_dimensions": evidence,
        "validated_occurrence_types": ["analysis", "grouping", "segments", "range"],
        "variant_label": variant_label,
    }


def classify_topic_priority(*, topic_id: str, topic_label: str) -> tuple[str, str]:
    if topic_id in _DEPRIORITISED_FAMILIES:
        return "deprioritised", "shared_technical_family"
    if topic_label.lower() in _DEPRIORITISED_FAMILIES:
        return "deprioritised", "shared_technical_family"
    return "normal", "disclosure_family"
