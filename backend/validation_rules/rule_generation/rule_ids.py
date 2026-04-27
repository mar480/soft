from __future__ import annotations

_TOPIC_CODES = {
    "property_plant_equipment": "PPE",
    "creditors": "CREDITORS",
    "debtors": "DEBTORS",
    "operating_leases": "OPERATINGLEASES",
    "provisions": "PROVISIONS",
    "investments": "INVESTMENTS",
}


def topic_code(topic_id: str) -> str:
    if topic_id in _TOPIC_CODES:
        return _TOPIC_CODES[topic_id]
    return topic_id.replace("_", "").upper()


def build_rule_id(*, topic_id: str, rule_kind: str, index: int) -> str:
    return f"AUTO.{topic_code(topic_id)}.{rule_kind}.{index:03d}"
