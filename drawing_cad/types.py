from dataclasses import dataclass, field
from typing import Protocol

import numpy as np


@dataclass
class DrawingPage:
    image: np.ndarray
    page_number: int
    page_count: int
    texts: list[dict] = field(default_factory=list)
    paths: list[dict] = field(default_factory=list)
    segments: list[dict] = field(default_factory=list)
    preprocessing: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class InputAdapter(Protocol):
    """Future DXF/DWG readers must produce the same normalized pixel-space evidence."""
    def read(self, path, page: int, dpi: int) -> DrawingPage: ...


class FeatureRecognizer(Protocol):
    """Recognizers select geometry-supported feature plans, never filename templates."""
    name: str
    def recognize(self, geometry: dict) -> dict: ...


class ExportAdapter(Protocol):
    """Future MuJoCo adapters receive a validated solid and explicit millimetre units."""
    def export(self, solid, document: dict, output): ...
