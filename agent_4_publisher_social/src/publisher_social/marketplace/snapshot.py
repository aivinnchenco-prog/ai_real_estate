from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET


def parse_bounds(bounds: str) -> tuple[int, int, int, int] | None:
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not match:
        return None
    return tuple(int(value) for value in match.groups())  # type: ignore[return-value]


def bounds_center(bounds: tuple[int, int, int, int]) -> tuple[int, int]:
    x1, y1, x2, y2 = bounds
    return (x1 + x2) // 2, (y1 + y2) // 2


@dataclass(frozen=True)
class UINode:
    node_id: str
    class_name: str
    text: str
    content_desc: str
    resource_id: str
    bounds: tuple[int, int, int, int] | None
    clickable: bool
    editable: bool
    focusable: bool
    input_type: str
    y1: int = 0

    @property
    def label(self) -> str:
        return (self.content_desc or self.text or "").strip()


@dataclass
class UISnapshot:
    xml: str
    nodes: list[UINode]
    activity: str = ""
    package: str = ""
    width: int = 0
    height: int = 0
    captured_at: float = field(default_factory=time.time)
    snapshot_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def refresh_from_device(self, device) -> UISnapshot:
        """Replace snapshot contents from a live device (never reuse stale tree)."""
        xml = ""
        try:
            xml = device.dump_hierarchy() or ""
        except Exception:
            xml = self.xml
        activity = ""
        package = ""
        width = 0
        height = 0
        try:
            info = device.app_current() or {}
            activity = str(info.get("activity") or "")
            package = str(info.get("package") or "")
        except Exception:
            pass
        try:
            width, height = device.window_size()
        except Exception:
            pass
        self.xml = xml
        self.nodes = parse_ui_nodes(xml)
        self.activity = activity
        self.package = package
        self.width = width
        self.height = height
        self.captured_at = time.time()
        self.snapshot_id = uuid.uuid4().hex[:12]
        return self

    def text_present(self, *needles: str) -> bool:
        hay = self.xml.lower()
        return any(needle.lower() in hay for needle in needles if needle)

    def find_edittexts(self) -> list[UINode]:
        return [node for node in self.nodes if "edittext" in node.class_name.lower()]


def parse_ui_nodes(xml: str) -> list[UINode]:
    nodes: list[UINode] = []
    if not xml:
        return nodes
    index = 0
    for element in ET.fromstring(xml).iter("node"):
        attrs = element.attrib
        bounds = parse_bounds(attrs.get("bounds", ""))
        y1 = bounds[1] if bounds else 0
        nodes.append(
            UINode(
                node_id=f"node_{index}",
                class_name=attrs.get("class") or "",
                text=(attrs.get("text") or "").strip(),
                content_desc=(attrs.get("content-desc") or "").strip(),
                resource_id=(attrs.get("resource-id") or "").strip(),
                bounds=bounds,
                clickable=attrs.get("clickable") == "true",
                editable=attrs.get("editable") == "true",
                focusable=attrs.get("focusable") == "true",
                input_type=(attrs.get("input-type") or "").strip(),
                y1=y1,
            )
        )
        index += 1
    return nodes


def snapshot_from_xml(xml: str, *, width: int = 720, height: int = 1600) -> UISnapshot:
    return UISnapshot(
        xml=xml,
        nodes=parse_ui_nodes(xml),
        width=width,
        height=height,
    )
