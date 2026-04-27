from __future__ import annotations

import re

_STOP_WORDS = {
    "hypercube",
    "table",
    "analysis",
    "disclosure",
    "grouping",
    "heading",
    "primary",
    "items",
    "dimension",
    "member",
    "members",
    "default",
}


def split_camel_case(text: str) -> list[str]:
    normalised = text.replace("-", " ").replace("_", " ")
    return re.sub(r"(?<!^)(?=[A-Z])", " ", normalised).split()


def normalise_qname(qname: str) -> str:
    return qname.replace(":", "_").replace("-", "_").lower()


def topic_id_from_name(name: str) -> str:
    words = [w.lower() for w in split_camel_case(name)]
    filtered = [w for w in words if w not in _STOP_WORDS]
    return "_".join(filtered)


def strip_qname_prefix(qname: str) -> str:
    return qname.split(":", 1)[1] if ":" in qname else qname


def humanize_topic_label(name: str) -> str:
    words = [word for word in split_camel_case(name) if word.lower() not in _STOP_WORDS]
    return " ".join(words).strip()
