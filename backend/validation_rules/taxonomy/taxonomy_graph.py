from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET

XSD_NS = "http://www.w3.org/2001/XMLSchema"
LINK_NS = "http://www.xbrl.org/2003/linkbase"
XLINK_NS = "http://www.w3.org/1999/xlink"


def _schema_refs(schema_file: Path) -> list[Path]:
    refs: list[Path] = []
    try:
        root = ET.parse(schema_file).getroot()
    except ET.ParseError:
        return refs

    for el in root.findall(f"{{{XSD_NS}}}import") + root.findall(f"{{{XSD_NS}}}include"):
        location = el.attrib.get("schemaLocation")
        if location and not location.startswith("http"):
            refs.append((schema_file.parent / location).resolve())

    for ref in root.findall(f".//{{{LINK_NS}}}linkbaseRef"):
        href = ref.attrib.get(f"{{{XLINK_NS}}}href")
        if href and not href.startswith("http"):
            refs.append((schema_file.parent / href).resolve())

    return refs


def discover_reachable_files(entrypoint_xsd: Path) -> set[Path]:
    seen: set[Path] = set()
    queue = [entrypoint_xsd.resolve()]

    while queue:
        current = queue.pop(0)
        if current in seen or not current.exists():
            continue
        seen.add(current)

        if current.suffix.lower() == ".xsd":
            for nxt in _schema_refs(current):
                if nxt not in seen:
                    queue.append(nxt)

    return seen
