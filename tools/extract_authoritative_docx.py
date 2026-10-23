"""Extract authoritative DOCX files without flattening table structure.

For each DOCX, this script writes:
* a readable Markdown file with headings and tables; and
* an audit text file with explicit paragraph/table/row/cell markers.

It validates that every text node in word/document.xml is visited in the
same order during extraction. The documents in this project contain no
headers, footers, notes, comments, or drawings; that is checked separately
and recorded in the generated manifest.
"""

from __future__ import annotations

import hashlib
import html
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"


@dataclass
class ExtractionStats:
    paragraphs: int = 0
    tables: int = 0
    rows: int = 0
    cells: int = 0
    text_nodes: list[str] = field(default_factory=list)


def local_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def paragraph_style(paragraph: ET.Element) -> str:
    style = paragraph.find(f"./{W}pPr/{W}pStyle")
    return style.get(f"{W}val", "") if style is not None else ""


def is_numbered(paragraph: ET.Element) -> bool:
    return paragraph.find(f"./{W}pPr/{W}numPr") is not None


def paragraph_text(paragraph: ET.Element, stats: ExtractionStats) -> str:
    parts: list[str] = []
    for item in paragraph.iter():
        name = local_name(item)
        if name == "t":
            value = item.text or ""
            parts.append(value)
            stats.text_nodes.append(value)
        elif name == "tab":
            parts.append("\t")
        elif name in {"br", "cr"}:
            parts.append("\n")
    return "".join(parts)


def direct_children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element if local_name(child) == name]


def cell_blocks(cell: ET.Element) -> list[ET.Element]:
    blocks: list[ET.Element] = []
    for child in cell:
        name = local_name(child)
        if name in {"p", "tbl"}:
            blocks.append(child)
        elif name not in {"tcPr"}:
            blocks.extend(find_blocks(child))
    return blocks


def find_blocks(container: ET.Element) -> list[ET.Element]:
    blocks: list[ET.Element] = []
    for child in container:
        name = local_name(child)
        if name in {"p", "tbl"}:
            blocks.append(child)
        elif name != "sectPr":
            blocks.extend(find_blocks(child))
    return blocks


def cell_text(cell: ET.Element, stats: ExtractionStats) -> str:
    parts: list[str] = []
    for block in cell_blocks(cell):
        if local_name(block) == "p":
            stats.paragraphs += 1
            value = paragraph_text(block, stats)
            if value:
                parts.append(value)
        else:
            nested_rows = table_rows(block, stats)
            for row in nested_rows:
                parts.append(" | ".join(row))
    return "\n".join(parts)


def table_rows(table: ET.Element, stats: ExtractionStats) -> list[list[str]]:
    stats.tables += 1
    rows: list[list[str]] = []
    for row in direct_children(table, "tr"):
        stats.rows += 1
        values: list[str] = []
        for cell in direct_children(row, "tc"):
            stats.cells += 1
            values.append(cell_text(cell, stats))
        rows.append(values)
    return rows


def escape_markdown_cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def markdown_paragraph(paragraph: ET.Element, text: str) -> str:
    style = paragraph_style(paragraph)
    normalized_style = re.sub(r"[^a-z0-9]", "", style.lower())
    heading_match = re.search(r"heading(\d+)", normalized_style)
    if heading_match:
        level = min(max(int(heading_match.group(1)), 1), 6)
        return f"{'#' * level} {text}"
    if normalized_style in {"title", "documenttitle"}:
        return f"# {text}"
    if normalized_style in {"subtitle"}:
        return f"## {text}"
    if is_numbered(paragraph) or "list" in normalized_style:
        return f"- {text}"
    return text


def render_table_markdown(rows: list[list[str]]) -> list[str]:
    if not rows:
        return ["<!-- Empty table -->"]
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    output = [
        "| " + " | ".join(escape_markdown_cell(value) for value in padded[0]) + " |",
        "| " + " | ".join("---" for _ in range(width)) + " |",
    ]
    for row in padded[1:]:
        output.append("| " + " | ".join(escape_markdown_cell(value) for value in row) + " |")
    return output


def package_features(archive: zipfile.ZipFile, document_xml: bytes) -> dict[str, int | bool]:
    names = archive.namelist()
    return {
        "drawings": document_xml.count(b"<w:drawing"),
        "headers": len([name for name in names if re.fullmatch(r"word/header\d+\.xml", name)]),
        "footers": len([name for name in names if re.fullmatch(r"word/footer\d+\.xml", name)]),
        "footnotes": "word/footnotes.xml" in names,
        "endnotes": "word/endnotes.xml" in names,
        "comments": "word/comments.xml" in names,
        "media": len([name for name in names if name.startswith("word/media/")]),
    }


def extract_document(source: Path, output_dir: Path) -> dict[str, object]:
    with zipfile.ZipFile(source) as archive:
        xml_bytes = archive.read("word/document.xml")
        features = package_features(archive, xml_bytes)
        auxiliary_parts = [
            name
            for name in archive.namelist()
            if re.fullmatch(r"word/(header|footer)\d+\.xml", name)
        ]
        part_bytes = {
            **{name: archive.read(name) for name in sorted(auxiliary_parts)},
            "word/document.xml": xml_bytes,
        }

    document_root = ET.fromstring(xml_bytes)
    body = document_root.find(f"./{W}body")
    if body is None:
        raise ValueError(f"No document body found in {source}")

    header_parts = sorted(name for name in auxiliary_parts if "/header" in name)
    footer_parts = sorted(name for name in auxiliary_parts if "/footer" in name)
    ordered_parts: list[tuple[str, ET.Element]] = [
        *[(name, ET.fromstring(part_bytes[name])) for name in header_parts],
        ("word/document.xml", body),
        *[(name, ET.fromstring(part_bytes[name])) for name in footer_parts],
    ]

    source_nodes: list[str] = []
    stats = ExtractionStats()
    markdown: list[str] = [f"<!-- Extracted from: {source.name} -->", ""]
    audit: list[str] = [f"SOURCE: {source.name}", ""]
    top_level_table = 0
    block_number = 0

    for part_name, container in ordered_parts:
        source_nodes.extend((node.text or "") for node in container.iter(f"{W}t"))
        part_label = (
            "DOCUMENT BODY"
            if part_name == "word/document.xml"
            else part_name.removeprefix("word/").removesuffix(".xml").upper()
        )
        audit.extend([f"[PART {part_label}]", ""])
        markdown.extend([f"<!-- {part_label} -->", ""])

        for block in find_blocks(container):
            block_number += 1
            if local_name(block) == "p":
                stats.paragraphs += 1
                value = paragraph_text(block, stats)
                style = paragraph_style(block) or "Normal"
                audit.append(f"[BLOCK {block_number:04d} PARAGRAPH style={style}]")
                audit.append(value)
                audit.append("")
                if value:
                    markdown.extend([markdown_paragraph(block, value), ""])
            else:
                top_level_table += 1
                rows = table_rows(block, stats)
                audit.append(f"[BLOCK {block_number:04d} TABLE {top_level_table:03d}]")
                for row_index, row in enumerate(rows, start=1):
                    audit.append(f"  [ROW {row_index:03d}]")
                    for cell_index, value in enumerate(row, start=1):
                        audit.append(f"    [CELL {cell_index:03d}] {value}")
                audit.extend([f"[END TABLE {top_level_table:03d}]", ""])
                markdown.extend(render_table_markdown(rows))
                markdown.append("")

    exact_text_node_match = source_nodes == stats.text_nodes
    if not exact_text_node_match:
        raise ValueError(
            f"Text-node verification failed for {source.name}: "
            f"source={len(source_nodes)}, extracted={len(stats.text_nodes)}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = output_dir / f"{source.stem}.full.md"
    audit_path = output_dir / f"{source.stem}.full.txt"
    markdown_path.write_text("\n".join(markdown).rstrip() + "\n", encoding="utf-8")
    audit_path.write_text("\n".join(audit).rstrip() + "\n", encoding="utf-8")

    source_text = "".join(source_nodes)
    return {
        "source": source.name,
        "source_bytes": source.stat().st_size,
        "paragraphs": stats.paragraphs,
        "tables": stats.tables,
        "rows": stats.rows,
        "cells": stats.cells,
        "text_nodes": len(source_nodes),
        "text_characters": len(source_text),
        "text_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        "exact_text_node_match": exact_text_node_match,
        "markdown": markdown_path.name,
        "audit_text": audit_path.name,
        **features,
    }


def write_manifest(results: list[dict[str, object]], output_dir: Path) -> None:
    lines = [
        "# Authoritative Document Extraction Manifest",
        "",
        "Each source was read directly from `word/document.xml`. Every text node was",
        "visited in source order and compared with the extraction sequence. Tables are",
        "preserved in Markdown and in explicit row/cell form in the audit text files.",
        "",
        "| Source | Paragraphs | Tables | Rows | Cells | Text nodes | Characters | Exact match |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in results:
        lines.append(
            f"| {item['source']} | {item['paragraphs']} | {item['tables']} | "
            f"{item['rows']} | {item['cells']} | {item['text_nodes']} | "
            f"{item['text_characters']} | {'Yes' if item['exact_text_node_match'] else 'No'} |"
        )

    lines.extend(["", "## Package feature check", ""])
    for item in results:
        lines.extend(
            [
                f"### {item['source']}",
                "",
                f"- Drawings: {item['drawings']}",
                f"- Media files: {item['media']}",
                f"- Headers: {item['headers']}",
                f"- Footers: {item['footers']}",
                f"- Footnotes: {item['footnotes']}",
                f"- Endnotes: {item['endnotes']}",
                f"- Comments: {item['comments']}",
                f"- Text SHA-256: `{item['text_sha256']}`",
                f"- Markdown: `{item['markdown']}`",
                f"- Audit text: `{item['audit_text']}`",
                "",
            ]
        )

    (output_dir / "EXTRACTION_MANIFEST.md").write_text(
        "\n".join(lines).rstrip() + "\n", encoding="utf-8"
    )


def main() -> None:
    source_dir = Path("authoritative documents")
    output_dir = source_dir / "extracted"
    sources = sorted(source_dir.glob("*.docx"))
    if not sources:
        raise SystemExit(f"No DOCX files found in {source_dir.resolve()}")
    results = [extract_document(source, output_dir) for source in sources]
    write_manifest(results, output_dir)
    print(f"Extracted {len(results)} documents to {output_dir.resolve()}")
    for item in results:
        print(
            f"{item['source']}: {item['paragraphs']} paragraphs, "
            f"{item['tables']} tables, {item['text_nodes']} text nodes, exact match"
        )


if __name__ == "__main__":
    main()
