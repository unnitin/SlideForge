"""
skills/dsl_serialize.py — Serialize data models back to DSL text.

Wraps src.dsl.serializer.SlideForgeSerializer.
"""

from src.dsl.models import PresentationNode, SlideNode
from src.dsl.serializer import SlideForgeSerializer

_serializer = SlideForgeSerializer()


def serialize(presentation: PresentationNode) -> str:
    """Serialize a full presentation to .sdsl text."""
    return _serializer.serialize(presentation)


def serialize_slide(slide: SlideNode) -> str:
    """Serialize a single slide to DSL text."""
    return _serializer.serialize_slide(slide)
