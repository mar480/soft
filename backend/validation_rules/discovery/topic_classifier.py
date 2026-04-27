from __future__ import annotations

import re

from ..taxonomy.labels import humanize_topic_label, strip_qname_prefix, topic_id_from_name


def _cleanup_source_text(text: str) -> str:
    cleaned = re.sub(r"^\d+\s*[-.:]\s*", "", text).strip()
    cleaned = cleaned.replace("[Hypercube]", "").replace("[hypercube]", "")
    cleaned = cleaned.replace("Hypercube -", "").replace("hypercube -", "")
    cleaned = cleaned.replace("[", " ").replace("]", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -")
    return cleaned


def classify_topic(*, cube_qname: str, cube_label: str | None, elr_definition: str | None) -> tuple[str, str]:
    source_text = cube_label or elr_definition or strip_qname_prefix(cube_qname)
    cleaned = _cleanup_source_text(source_text)
    topic_id = topic_id_from_name(cleaned or strip_qname_prefix(cube_qname))
    topic_label = humanize_topic_label(cleaned or strip_qname_prefix(cube_qname)) or cleaned or cube_qname
    return topic_id or topic_id_from_name(strip_qname_prefix(cube_qname)), topic_label
