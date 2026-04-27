from __future__ import annotations

import re

_STOP_WORDS = {"hypercube", "table", "analysis", "disclosure"}


def split_camel_case(text: str) -> list[str]:
    return re.sub(r"(?<!^)(?=[A-Z])", " ", text).split()


def normalise_qname(qname: str) -> str:
    return qname.replace(":", "_").replace("-", "_").lower()


def topic_id_from_name(name: str) -> str:
    words = [w.lower() for w in split_camel_case(name)]
    filtered = [w for w in words if w not in _STOP_WORDS]
    return "_".join(filtered)
