"""
src/renderer/format_plugins.py — Output Format Converter Plugin System (STUB)

TODO (Claude Code Phase 3):
    - FormatConverter protocol
    - EE4PConverter (requires .ee4p format spec)
    - PDFConverter (via LibreOffice soffice)
    - Plugin registry and discovery

See specs/RENDERER_SPEC.md for full specification.
"""

from pathlib import Path
from typing import Protocol


class FormatConverter(Protocol):
    """Plugin interface for output format converters."""

    def can_convert(self, target_format: str) -> bool: ...
    def convert(self, pptx_path: Path, output_path: Path) -> Path: ...


class PDFConverter:
    """Converts .pptx → .pdf via LibreOffice."""

    def can_convert(self, target_format: str) -> bool:
        return target_format.lower() == "pdf"

    def convert(self, pptx_path: Path, output_path: Path) -> Path:
        import subprocess

        subprocess.run(
            [
                "soffice",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(output_path.parent),
                str(pptx_path),
            ],
            check=True,
        )
        return output_path


class EE4PConverter:
    """
    Converts .pptx → .ee4p

    Requires .ee4p format specification or vendor CLI tool.
    """

    def can_convert(self, target_format: str) -> bool:
        return target_format.lower() == "ee4p"

    def convert(self, pptx_path: Path, output_path: Path) -> Path:
        raise NotImplementedError(
            "EE4P conversion requires format specification or vendor tool. "
            "Provide the .ee4p spec to implement this converter."
        )


def get_converter(target_format: str) -> FormatConverter:
    """Get the appropriate converter for a target format."""
    converters: list[FormatConverter] = [PDFConverter(), EE4PConverter()]
    for c in converters:
        if c.can_convert(target_format):
            return c
    raise ValueError(f"No converter available for format: {target_format}")
