# docx_utils.py
"""
Split-run-safe DOCX placeholder replacer.

Replaces placeholders like "[Company Name]" or "$[_____________]" even when
Word has split them across multiple runs (w:r) or text nodes (w:t).

Public:
    replace_placeholders_in_docx(template_path: str, dest_path: str, mapping: Dict[str,str]) -> None

Notes:
- Preserves all non-overlapping formatting by only editing the text spans that
  participate in each match.
- If multiple placeholders overlap (rare in legal docs), replacements run in
  deterministic order of 'mapping' keys then left-to-right within a paragraph.
"""
# docx_utils.py
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Dict, List, Tuple

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}

def _iter_text_nodes_in_para(p) -> List:
    """Return all <w:t> nodes inside a paragraph <w:p> (preserving order)."""
    return list(p.iterfind(".//w:t", namespaces=NS))

def _replace_span_across_nodes(nodes: List[ET.Element], start_idx: int, end_idx: int, replacement: str, positions: List[Tuple[int,int,int]]):
    """
    Replace the span [start_idx, end_idx) in the concatenated text of 'nodes' with 'replacement'.
    positions: list of tuples (node_index, offset_in_node, abs_index) for each character in the concat string.
    We rewrite only the affected node texts; collapsing the replaced area to the first node,
    preserving any prefix/suffix around the match.
    """
    if start_idx >= end_idx:
        return

    # Map absolute char index -> (node_index, offset_in_node)
    # positions[abs_i] = (node_idx, off_in_node, abs_i)
    (i0, o0, _abs0) = positions[start_idx]
    (i1, o1, _abs1) = positions[end_idx - 1]
    # end offset should point to 1-char past end within last node
    o1p = o1 + 1

    for idx, tnode in enumerate(nodes):
        text = tnode.text or ""
        if idx < i0 or idx > i1:
            # outside match range: no change
            continue
        if i0 == i1:
            # match contained within a single node
            new_text = text[:o0] + replacement + text[o1p:]
            tnode.text = new_text
        else:
            if idx == i0:
                # keep prefix then replacement
                tnode.text = text[:o0] + replacement
            elif idx == i1:
                # keep suffix
                tnode.text = text[o1p:]
            else:
                # fully covered middle node -> empty
                tnode.text = ""

def _replace_in_paragraph(p: ET.Element, mapping: Dict[str, str]) -> bool:
    """
    Perform split-run-safe replacements inside a single paragraph.
    Returns True if any replacement happened.
    """
    tnodes = _iter_text_nodes_in_para(p)
    if not tnodes:
        return False

    changed = False
    # Build concatenated string + positions map
    def build_concat():
        parts = []
        positions = []  # (node_index, offset_in_node, abs_index)
        abs_i = 0
        for i, t in enumerate(tnodes):
            s = t.text or ""
            parts.append(s)
            for j, _ch in enumerate(s):
                positions.append((i, j, abs_i))
                abs_i += 1
        return "".join(parts), positions

    # For stability, apply replacements label-by-label, and repeatedly search in updated text.
    for label, value in mapping.items():
        while True:
            concat, positions = build_concat()
            if not concat or label not in concat:
                break
            # Find first occurrence, replace, then loop again to catch later ones
            start = concat.find(label)
            end = start + len(label)
            _replace_span_across_nodes(tnodes, start, end, value, positions)
            changed = True

    return changed

def _replace_in_part_xml(xml_bytes: bytes, mapping: Dict[str, str]) -> bytes:
    """Run split-run-safe replacements in one XML part (document/header/footer/notes)."""
    root = ET.fromstring(xml_bytes)

    # Replace in paragraphs
    for p in root.iterfind(".//w:p", namespaces=NS):
        _replace_in_paragraph(p, mapping)

    # Serialize back
    return ET.tostring(root, encoding="utf-8", method="xml")

def replace_placeholders_in_docx(src_path: str, dst_path: str, mapping: Dict[str, str]) -> None:
    """
    Replace placeholders across the main document and common ancillary parts
    (headers, footers, footnotes, endnotes). Safe for split runs.
    """
    parts_to_touch_prefixes = (
        "word/document.xml",
        "word/header",    # header1.xml, header2.xml, ...
        "word/footer",    # footer1.xml, footer2.xml, ...
        "word/footnotes.xml",
        "word/endnotes.xml",
    )

    with zipfile.ZipFile(src_path, "r") as zin, zipfile.ZipFile(dst_path, "w") as zout:
        for item in zin.infolist():
            name = item.filename
            data = zin.read(name)
            if name == "word/document.xml" or name.startswith("word/header") or name.startswith("word/footer") or name in ("word/footnotes.xml", "word/endnotes.xml"):
                try:
                    new_data = _replace_in_part_xml(data, mapping)
                    zout.writestr(name, new_data)
                except Exception:
                    # If parsing fails, write original to be safe
                    zout.writestr(name, data)
            else:
                zout.writestr(name, data)

'''
# docx_utils.py
"""
Split-run-safe DOCX placeholder replacer.

Replaces placeholders like "[Company Name]" or "$[_____________]" even when
Word has split them across multiple runs (w:r) or text nodes (w:t).

Public:
    replace_placeholders_in_docx(template_path: str, dest_path: str, mapping: Dict[str,str]) -> None

Notes:
- Preserves all non-overlapping formatting by only editing the text spans that
  participate in each match.
- If multiple placeholders overlap (rare in legal docs), replacements run in
  deterministic order of 'mapping' keys then left-to-right within a paragraph.
"""

from typing import Dict, List, Tuple
import zipfile
import xml.etree.ElementTree as ET

DOCX_MAIN = "word/document.xml"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

def _iter_paragraphs(root):
    ns = {'w': W_NS}
    for p in root.iterfind('.//w:p', namespaces=ns):
        yield p

def _get_runs_with_text(p):
    """Return a list of (t_node, text) in order for a paragraph."""
    ns = {'w': W_NS}
    out = []
    for r in p.iterfind('.//w:r', namespaces=ns):
        t = r.find('.//w:t', namespaces=ns)
        if t is not None:
            out.append((t, t.text or ""))
    return out

def _rebuild_concat_map(runs: List[Tuple[ET.Element, str]]):
    """Return concat string and a list of (node, start, end) positions."""
    concat = []
    posmap = []
    cursor = 0
    for node, text in runs:
        s = cursor
        concat.append(text)
        cursor += len(text)
        posmap.append((node, s, cursor))
    return "".join(concat), posmap

def _replace_span_across_runs(runs: List[Tuple[ET.Element, str]], start: int, end: int, value: str):
    """
    Replace the span [start:end) in the concatenated run text with 'value',
    modifying the minimal set of run texts in-place.

    Strategy:
      - Recompute mapping after each replacement; operate left-to-right
      - First overlapping run: splice in 'value'
      - All subsequent overlapping runs: delete overlapped segment
    """
    # Build mapping
    concat, posmap = _rebuild_concat_map(runs)
    if start < 0 or end > len(concat) or start >= end:
        return runs  # no-op

    # Identify overlapping indices
    first_idx = None
    last_idx = None
    for i, (node, s, e) in enumerate(posmap):
        if e <= start:
            continue
        if s >= end and first_idx is not None:
            break
        if (s < end) and (e > start):
            if first_idx is None:
                first_idx = i
            last_idx = i

    if first_idx is None:
        return runs  # nothing to do

    # Modify texts
    new_runs = []
    for i, (node, text) in enumerate(runs):
        s, e = posmap[i][1], posmap[i][2]
        if i < first_idx or i > last_idx:
            new_runs.append((node, text))
            continue

        # Overlap with [start:end)
        local_start = max(0, start - s)
        local_end = min(len(text), end - s)

        if i == first_idx:
            # Replace overlapped slice with value
            new_text = text[:local_start] + value + text[local_end:]
        else:
            # Remove overlapped slice entirely
            new_text = text[:local_start] + text[local_end:]

        new_runs.append((node, new_text))

    # Write back to nodes
    for (node, _old), (_node2, new_text) in zip(runs, new_runs):
        node.text = new_text

    return new_runs

def _replace_all_in_paragraph(p, repls: List[Tuple[str, str]]):
    """
    Replace all occurrences of any label in 'repls' within paragraph p.
    repls: list of (label, value)
    """
    runs = _get_runs_with_text(p)
    if not runs:
        return

    # Process each label separately for determinism
    for (label, value) in repls:
        # Keep scanning from left to right, updating runs map after each replacement
        while True:
            concat, posmap = _rebuild_concat_map(runs)
            idx = concat.find(label)
            if idx == -1:
                break
            start, end = idx, idx + len(label)
            runs = _replace_span_across_runs(runs, start, end, value)

def replace_placeholders_in_docx(template_path: str, dest_path: str, mapping: Dict[str, str]) -> None:
    """
    Split-run-safe replacement. Opens the DOCX, finds all placeholders in each paragraph
    even if split across runs, replaces them with 'mapping' values, and writes out a new DOCX.
    """
    with zipfile.ZipFile(template_path) as z:
        xml_data = z.read(DOCX_MAIN)
        other_files = {name: z.read(name) for name in z.namelist() if name != DOCX_MAIN}

    root = ET.fromstring(xml_data)

    # Prepare replacements list once (preserve input order)
    repls = list(mapping.items())

    for p in _iter_paragraphs(root):
        _replace_all_in_paragraph(p, repls)

    xml_out = ET.tostring(root, encoding="utf-8")
    with zipfile.ZipFile(dest_path, "w", compression=zipfile.ZIP_DEFLATED) as newz:
        newz.writestr(DOCX_MAIN, xml_out)
        for name, data in other_files.items():
            newz.writestr(name, data)
'''