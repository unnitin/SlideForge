"""
skills/dsl_parse.py — Parse DSL text or files into data models.

Wraps src.dsl.parser.SlideForgeParser.
"""

from src.dsl.models import PresentationNode
from src.dsl.parser import SlideForgeParser

_parser = SlideForgeParser()


def parse_text(dsl_text: str) -> PresentationNode:
    """Parse raw DSL text into a PresentationNode."""
    return _parser.parse(dsl_text)


def parse_file(path: str) -> PresentationNode:
    """Parse a .sdsl file into a PresentationNode."""
    return _parser.parse_file(path)
