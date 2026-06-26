from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping, Sequence

import cv2
import numpy as np


COLORS = {
    "black",
    "blue",
    "brown",
    "gray",
    "green",
    "orange",
    "pink",
    "purple",
    "red",
    "white",
    "yellow",
}

OBJECT_ALIASES: dict[str, tuple[str, ...]] = {
    "bag": ("backpack", "handbag", "suitcase"),
    "bags": ("backpack", "handbag", "suitcase"),
    "cellphone": ("cell phone",),
    "cell phone": ("cell phone",),
    "earphone": (),
    "earphones": (),
    "earbud": (),
    "earbuds": (),
    "airpod": (),
    "airpods": (),
    "mobile": ("cell phone",),
    "mobile phone": ("cell phone",),
    "phone": ("cell phone",),
    "phones": ("cell phone",),
    "smartphone": ("cell phone",),
    "t shirt": (),
    "tshirt": (),
}

UNSUPPORTED_MESSAGES = {
    "airpod": "AirPods are too small for the standard model. A close camera and custom model are required.",
    "airpods": "AirPods are too small for the standard model. A close camera and custom model are required.",
    "earbud": "Earbuds are too small for the standard model. A close camera and custom model are required.",
    "earbuds": "Earbuds are too small for the standard model. A close camera and custom model are required.",
    "earphone": "Earphones are not included in the standard model. A custom model is required.",
    "earphones": "Earphones are not included in the standard model. A custom model is required.",
}


@dataclass(frozen=True)
class SearchSpec:
    query: str
    mode: str
    target: str
    class_ids: tuple[int, ...] = ()
    color: str | None = None
    supported: bool = True
    message: str = ""

    @property
    def active(self) -> bool:
        return self.supported and self.mode != "inactive"


def inactive_search() -> SearchSpec:
    return SearchSpec(
        query="",
        mode="inactive",
        target="",
        message="Enter an object or shirt color to search the live camera.",
    )


def _normalize_query(query: str) -> str:
    text = query.lower().strip()
    text = re.sub(r"[-_]+", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_search_query(
    query: str,
    class_names: Mapping[int, str],
) -> SearchSpec:
    normalized = _normalize_query(query)
    if not normalized:
        return inactive_search()

    shirt_words = {"shirt", "tshirt", "top", "clothes", "clothing"}
    words = set(normalized.split())
    requested_color = next((color for color in COLORS if color in words), None)
    if requested_color and words.intersection(shirt_words):
        return SearchSpec(
            query=query.strip(),
            mode="shirt_color",
            target=f"{requested_color} shirt",
            class_ids=(0,),
            color=requested_color,
            message=f"Looking for people wearing a {requested_color} shirt.",
        )

    wants_person_association = bool(
        re.search(r"\b(holding|using|carrying|with)\b", normalized)
    )
    cleaned = re.sub(
        r"\b(find|detect|search|look|looking|for|show|me|a|an|the|any|"
        r"person|people|someone|who|is|are|holding|using|carrying|with)\b",
        " ",
        normalized,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    target_text = cleaned or normalized

    for unsupported, message in UNSUPPORTED_MESSAGES.items():
        if unsupported in target_text:
            return SearchSpec(
                query=query.strip(),
                mode="unsupported",
                target=unsupported,
                supported=False,
                message=message,
            )

    available = {name.lower(): class_id for class_id, name in class_names.items()}
    canonical_names: Sequence[str] = OBJECT_ALIASES.get(
        target_text, (target_text,)
    )
    class_ids = tuple(
        available[name]
        for name in canonical_names
        if name in available
    )

    if not class_ids:
        partial = [
            (class_id, name)
            for name, class_id in available.items()
            if target_text == name
            or target_text in name
            or name in target_text
        ]
        class_ids = tuple(class_id for class_id, _ in partial)
        canonical_names = tuple(name for _, name in partial)

    if not class_ids:
        examples = "phone, backpack, bottle, laptop, person, or red shirt"
        return SearchSpec(
            query=query.strip(),
            mode="unsupported",
            target=target_text,
            supported=False,
            message=f'"{query.strip()}" is not supported by this model. Try {examples}.',
        )

    target_label = (
        " or ".join(canonical_names)
        if len(canonical_names) > 1
        else canonical_names[0]
    )
    if wants_person_association and 0 not in class_ids:
        return SearchSpec(
            query=query.strip(),
            mode="person_with_object",
            target=target_label,
            class_ids=class_ids,
            message=f"Looking for a person with {target_label}.",
        )
    return SearchSpec(
        query=query.strip(),
        mode="object",
        target=target_label,
        class_ids=class_ids,
        message=f"Looking for {target_label}.",
    )


def estimate_shirt_color(
    frame: np.ndarray,
    bbox: tuple[int, int, int, int],
) -> tuple[str, float]:
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = bbox
    box_width = max(1, x2 - x1)
    box_height = max(1, y2 - y1)
    left = max(0, min(width, round(x1 + box_width * 0.18)))
    right = max(0, min(width, round(x2 - box_width * 0.18)))
    top = max(0, min(height, round(y1 + box_height * 0.18)))
    bottom = max(0, min(height, round(y1 + box_height * 0.58)))
    crop = frame[top:bottom, left:right]
    if crop.size == 0 or crop.shape[0] < 4 or crop.shape[1] < 4:
        return "unknown", 0.0

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0]
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    total = float(hue.size)

    masks = {
        "black": value < 55,
        "white": (saturation < 45) & (value > 185),
        "gray": (saturation < 55) & (value >= 55) & (value <= 185),
        "brown": (hue >= 5) & (hue <= 20) & (saturation >= 70) & (value < 165),
        "red": ((hue <= 9) | (hue >= 170)) & (saturation >= 60) & (value >= 55),
        "orange": (hue >= 10) & (hue <= 20) & (saturation >= 70) & (value >= 165),
        "yellow": (hue >= 21) & (hue <= 35) & (saturation >= 60) & (value >= 70),
        "green": (hue >= 36) & (hue <= 85) & (saturation >= 45) & (value >= 45),
        "blue": (hue >= 86) & (hue <= 130) & (saturation >= 50) & (value >= 45),
        "purple": (hue >= 131) & (hue <= 155) & (saturation >= 45) & (value >= 45),
        "pink": (hue >= 156) & (hue <= 169) & (saturation >= 35) & (value >= 80),
    }
    scores = {
        color: float(np.count_nonzero(mask)) / total
        for color, mask in masks.items()
    }
    color, score = max(scores.items(), key=lambda item: item[1])
    if score < 0.12:
        return "unknown", score
    return color, score


def object_inside_person(
    object_bbox: tuple[int, int, int, int],
    person_bbox: tuple[int, int, int, int],
) -> bool:
    ox1, oy1, ox2, oy2 = object_bbox
    px1, py1, px2, py2 = person_bbox
    margin_x = max(8, round((px2 - px1) * 0.12))
    margin_y = max(8, round((py2 - py1) * 0.08))
    center_x = (ox1 + ox2) / 2
    center_y = (oy1 + oy2) / 2
    return (
        px1 - margin_x <= center_x <= px2 + margin_x
        and py1 - margin_y <= center_y <= py2 + margin_y
    )
