"""
Custom Markdown generator from MinerU JSON with targeted PDF text matching and font-size based heading hierarchy.

Steps:
1. Extract candidate headings from JSON.
   - Ignore plain numbers (e.g., '27', '# 35', '100').
   - Ignore text with fewer than 3 alphabetic letters (e.g., 'C', 'II', 'A').
2. Scan the PDF specifically for the *particular heading text words* within the block's area.
   - Only consider spans matching the heading words, eliminating noise like drop-caps or stray numbers.
   - Extract the dominant font size.
3. Sort unique dominant font sizes descending -> H1, H2, H3, ...
4. Generate Markdown from the JSON:
   - Skip headers, footers, page numbers, and non-content elements.
   - Plain numbers or noise blocks are not rendered as headings.
   - Format legitimate headings with their calculated '#' level.
5. Normalize heading hierarchy:
   - Fix skipped levels (e.g. ## -> #### becomes ## -> ###).
"""

from __future__ import annotations

import re
import html
from collections import Counter
from typing import Dict, List, Tuple, Any, Optional, Set

try:
    import fitz  # PyMuPDF
except ImportError:
    import pymupdf as fitz

from docvortex.schema import (
    MiddleJson,
    BlockType,
    DocTitleBlock,
    ParagraphTitleBlock,
    TextBlock,
    RefTextBlock,
    ListBlock,
    TableBlock,
    EquationBlock,
    CodeBlock,
    PageAuxTextBlock,
    PageFootnoteBlock,
    IndexBlock,
    ContinuableTextBlockBase,
)
from docvortex.options import LatexDelimitersConfig
from docvortex.render.contracts import RenderMode
from docvortex.render._internal.markdown.renderer import (
    build_render_plan,
    _collect_markdown_anchor_targets,
    _PAGE_SEPARATOR,
)
from docvortex.render._internal.markdown.blocks import (
    render_joined_inline_contents,
    escape_standalone_marker_rule,
    escape_text_block_markdown_prefix,
    _prepend_markdown_anchor,
    visible_block_anchor,
    render_title_inline_content,
    _claim_markdown_anchor,
    _render_page_footnote,
    _render_equation,
    _render_list,
    _render_index,
    _render_table_block,
    _render_code_block,
)


def is_visual_block(block: Any) -> bool:
    """Checks whether a block represents an image or chart."""
    btype = getattr(block, "type", None)
    if btype in (BlockType.IMAGE, BlockType.CHART, "image", "chart"):
        return True
    return False


def extract_visual_block_annotations(block: Any, delimiters: LatexDelimitersConfig) -> List[str]:
    """Extracts caption/footnote annotations from an ImageBlock or ChartBlock."""
    from docvortex.render._internal.markdown.blocks import render_visual_annotation
    annotations = []
    content = getattr(block, "content", [])
    if isinstance(content, list):
        for child in content:
            child_type = getattr(child, "type", None)
            if child_type in (
                BlockType.IMAGE_CAPTION,
                BlockType.IMAGE_FOOTNOTE,
                BlockType.CHART_CAPTION,
                BlockType.CHART_FOOTNOTE,
                "image_caption",
                "image_footnote",
                "chart_caption",
                "chart_footnote",
            ):
                try:
                    text = render_visual_annotation(child, delimiters)
                    if text and text.strip():
                        annotations.append(text.strip())
                except Exception:
                    pass
    return annotations


def _get_page_clip_rect(page: fitz.Page, bbox: List[float]) -> Optional[fitz.Rect]:
    """Converts bbox (normalized or absolute) to a valid fitz.Rect with a slight inset."""
    if not bbox or len(bbox) != 4:
        return None
    x0, y0, x1, y1 = bbox
    w, h = page.rect.width, page.rect.height
    if max(x0, y0, x1, y1) <= 1.05:
        rect = fitz.Rect(x0 * w, y0 * h, x1 * w, y1 * h)
    else:
        rect = fitz.Rect(x0, y0, x1, y1)

    clip_rect = rect + (1, 1, -1, -1)
    if clip_rect.is_empty or not clip_rect.is_valid:
        clip_rect = rect
    return clip_rect


def scan_visual_block_heading_sizes(pdf_page: fitz.Page, bbox: List[float]) -> Set[float]:
    """
    Scans selectable text inside a visual block for candidate heading font sizes.
    Lines must satisfy heading candidate criteria (e.g. >= 3 letters, not numbers).
    """
    found_sizes: Set[float] = set()
    clip_rect = _get_page_clip_rect(pdf_page, bbox)
    if not clip_rect:
        return found_sizes

    try:
        text_dict = pdf_page.get_text("dict", clip=clip_rect)
        for block in text_dict.get("blocks", []):
            for line in block.get("lines", []):
                line_spans = line.get("spans", [])
                line_text = " ".join(s.get("text", "").strip() for s in line_spans if s.get("text", "").strip()).strip()
                if not is_valid_heading_candidate(line_text):
                    continue

                # Get the dominant font size of this candidate line
                size_counts = Counter()
                for span in line_spans:
                    stext = span.get("text", "").strip()
                    ssize = round(float(span.get("size", 0)), 1)
                    if stext and ssize > 0:
                        size_counts[ssize] += len(stext)

                if size_counts:
                    dom_size, _ = size_counts.most_common(1)[0]
                    if dom_size > 0:
                        found_sizes.add(dom_size)
    except Exception:
        pass

    return found_sizes


def extract_superimposed_elements_from_pdf(
    pdf_page: fitz.Page,
    bbox: List[float],
    font_level_map: Dict[float, int],
) -> List[str]:
    """
    Extracts selectable digital text located inside a visual block's bounding box.
    Runs each line through the heading inference process:
    - If a line's dominant font size matches the heading map, it is rendered as '# Heading'.
    - Otherwise, it is rendered as body text.
    Consecutive body lines within the same block are grouped into paragraphs.
    """
    clip_rect = _get_page_clip_rect(pdf_page, bbox)
    if not clip_rect:
        return []

    elements: List[str] = []

    try:
        text_dict = pdf_page.get_text("dict", clip=clip_rect)
        for block in text_dict.get("blocks", []):
            current_paragraph_lines: List[str] = []

            for line in block.get("lines", []):
                line_spans = line.get("spans", [])
                line_text = " ".join(s.get("text", "").strip() for s in line_spans if s.get("text", "").strip()).strip()
                if not line_text:
                    continue

                # Determine dominant font size for this line
                size_counts = Counter()
                for span in line_spans:
                    stext = span.get("text", "").strip()
                    ssize = round(float(span.get("size", 0)), 1)
                    if stext and ssize > 0:
                        size_counts[ssize] += len(stext)

                dom_size = size_counts.most_common(1)[0][0] if size_counts else None

                # Check if this line is an inferred heading
                if (
                    dom_size is not None
                    and dom_size in font_level_map
                    and is_valid_heading_candidate(line_text)
                ):
                    # Flush any accumulated body lines into a paragraph
                    if current_paragraph_lines:
                        elements.append(" ".join(current_paragraph_lines))
                        current_paragraph_lines = []

                    level = font_level_map[dom_size]
                    level = min(max(level, 1), 6)
                    elements.append(f"{'#' * level} {line_text}")
                else:
                    current_paragraph_lines.append(line_text)

            if current_paragraph_lines:
                elements.append(" ".join(current_paragraph_lines))

    except Exception:
        pass

    return elements


def extract_raw_text(block: Any) -> str:
    """Extracts raw plain text string from a block."""
    content = getattr(block, "content", None)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(item.get("content", item.get("text", "")))
            elif hasattr(item, "content"):
                parts.append(str(item.content))
        return " ".join(parts).strip()
    return ""


def is_valid_heading_candidate(text: str) -> bool:
    """
    Validates whether a candidate heading text should be used for heading level determination:
    - Rejects plain numbers or symbols (e.g., '27', '# 35', '100', '3.1').
    - Rejects text with fewer than 3 alphabetic letters (e.g., 'C', 'II', 'A').
    """
    if not text:
        return False

    clean = text.strip()

    # Reject plain numbers, decimals, or number-only symbols
    stripped_num = re.sub(r'^[#\s\.\-\–\—\(\)\[\]]+', '', clean)
    stripped_num = re.sub(r'[#\s\.\-\–\—\(\)\[\]]+$', '', stripped_num)
    if re.fullmatch(r'^\d+([.,]\d+)*$', stripped_num):
        return False

    # Extract all alphabetic letters
    letters = re.findall(r'[a-zA-Z]', clean)
    if len(letters) < 3:
        return False

    return True


def get_dominant_font_size_for_text(page: fitz.Page, bbox: List[float], heading_text: str) -> Optional[float]:
    """
    Scans the PDF page around bbox specifically for spans that contain words of `heading_text`.
    Eliminates decorative letters (e.g. drop-cap 'C') or large standalone numbers (e.g. '3', '60 pt')
    that happen to share the bounding box.
    """
    if not bbox or len(bbox) != 4:
        return None

    # Tokenize heading into lowercase alpha-numeric words with length >= 2
    heading_words = set(w.lower() for w in re.findall(r'[a-zA-Z0-9]{2,}', heading_text))
    if not heading_words:
        return None

    x0, y0, x1, y1 = bbox
    w, h = page.rect.width, page.rect.height

    if max(x0, y0, x1, y1) <= 1.05:
        rect = fitz.Rect(x0 * w, y0 * h, x1 * w, y1 * h)
    else:
        rect = fitz.Rect(x0, y0, x1, y1)

    # Slight padding to ensure boundary character inclusion
    rect = rect + (-2, -2, 2, 2)

    text_dict = page.get_text("dict", clip=rect)
    size_counts = Counter()

    for block in text_dict.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                span_text = span.get("text", "").strip()
                span_size = round(float(span.get("size", 0)), 1)
                if not span_text or span_size <= 0:
                    continue

                # Tokenize span into words
                span_words = set(w.lower() for w in re.findall(r'[a-zA-Z0-9]{2,}', span_text))

                # ONLY count font sizes of spans that match heading words
                matching_words = span_words & heading_words
                if matching_words:
                    # Weight by number of matched characters
                    match_len = sum(len(w) for w in matching_words)
                    size_counts[span_size] += match_len

    if not size_counts:
        # Fallback: if clip did not capture due to slight bbox discrepancy, search page for first 3 heading words
        sample_words = [w for w in re.findall(r'[a-zA-Z]{3,}', heading_text)][:3]
        if sample_words:
            query = " ".join(sample_words)
            hits = page.search_for(query)
            for hit in hits:
                hit_dict = page.get_text("dict", clip=hit + (-1, -1, 1, 1))
                for block in hit_dict.get("blocks", []):
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            s_size = round(float(span.get("size", 0)), 1)
                            if s_size > 0:
                                size_counts[s_size] += len(span.get("text", ""))

    if not size_counts:
        return None

    dominant_size, _ = size_counts.most_common(1)[0]
    return dominant_size


def is_title_block(block: Any) -> bool:
    """Checks whether a block represents a title/heading in various schema formats."""
    if isinstance(block, (DocTitleBlock, ParagraphTitleBlock)):
        return True
    btype = getattr(block, "type", None)
    if btype in (BlockType.DOC_TITLE, BlockType.PARAGRAPH_TITLE, "title", "doc_title", "paragraph_title"):
        return True
    subtype = getattr(block, "sub_type", None)
    if subtype in ("title", "doc_title", "paragraph_title"):
        return True
    return False


def is_skipped_aux_block(block: Any) -> bool:
    """Checks if a block is a header, footer, page number, or aside text to skip."""
    if isinstance(block, PageAuxTextBlock):
        return True
    btype = getattr(block, "type", None)
    if btype in (
        BlockType.HEADER,
        BlockType.FOOTER,
        BlockType.PAGE_NUMBER,
        BlockType.ASIDE_TEXT,
        "header",
        "footer",
        "page_number",
        "page_no",
        "aside_text",
        "aside",
    ):
        return True
    return False


def detect_base_body_font_size(doc: fitz.Document) -> float:
    """
    Detects the dominant body text font size across the document by character count.
    Body text typically represents >50% of characters in any standard document.
    """
    font_char_counts = Counter()
    for page in doc:
        try:
            td = page.get_text("dict")
            for b in td.get("blocks", []):
                for l in b.get("lines", []):
                    for s in l.get("spans", []):
                        t = s.get("text", "").strip()
                        sz = round(float(s.get("size", 0)), 1)
                        if t and sz > 0:
                            font_char_counts[sz] += len(t)
        except Exception:
            continue

    if font_char_counts:
        base_size, _ = font_char_counts.most_common(1)[0]
        return base_size
    return 10.0  # Fallback standard


def build_heading_font_map(middle_json: MiddleJson, pdf_path: str) -> Dict[float, int]:
    """
    Step 1: Iterate through candidate heading blocks.
    - Filters out plain numbers and short noise (< 3 letters).
    - Detects document's base body font size dynamically.
    - Discards any candidate font size <= base_body_size + 0.5 (filters out body text, footnotes, chart noise).
    - Scans the PDF area for the PARTICULAR heading text words only.
    - Sorts unique dominant font sizes descending: largest -> Level 1 (#), next -> Level 2 (##), etc.
    """
    candidate_sizes: Set[float] = set()

    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        print(f"[CustomMarkdown] Could not open PDF {pdf_path}: {e}")
        return {}

    try:
        base_body_size = detect_base_body_font_size(doc)
        # Genuine headings must be strictly larger than body text (noise threshold)
        noise_threshold = round(base_body_size + 0.8, 1)
        print(f"[CustomMarkdown] Base body font size detected: {base_body_size} pt (Heading threshold > {noise_threshold} pt)")

        for page_elem in middle_json.pages:
            page_idx = page_elem.page_idx
            if page_idx < 0 or page_idx >= len(doc):
                continue

            pdf_page = doc[page_idx]
            for block in page_elem.blocks:
                if is_title_block(block):
                    raw_text = extract_raw_text(block)
                    if not is_valid_heading_candidate(raw_text):
                        # Skip plain numbers (e.g. 27, 35) or noise (< 3 letters)
                        continue

                    bbox = getattr(block, "bbox", None)
                    if bbox:
                        dom_size = get_dominant_font_size_for_text(pdf_page, bbox, raw_text)
                        if dom_size is not None and dom_size > noise_threshold:
                            candidate_sizes.add(dom_size)
                elif is_visual_block(block):
                    bbox = getattr(block, "bbox", None)
                    if bbox:
                        visual_heading_sizes = scan_visual_block_heading_sizes(pdf_page, bbox)
                        for vs in visual_heading_sizes:
                            if vs > noise_threshold:
                                candidate_sizes.add(vs)
    finally:
        doc.close()

    # Sort descending: largest font size -> Level 1 (#), 2nd largest -> Level 2 (##), etc.
    sorted_sizes = sorted(candidate_sizes, reverse=True)
    font_level_map: Dict[float, int] = {}
    for idx, size in enumerate(sorted_sizes):
        font_level_map[size] = min(idx + 1, 6)

    print(f"[CustomMarkdown] Filtered genuine heading font sizes: {sorted_sizes} -> Level Map: {font_level_map}")
    return font_level_map


def _render_custom_title(
    block: DocTitleBlock | ParagraphTitleBlock,
    delimiters: LatexDelimitersConfig,
    font_level_map: Dict[float, int],
    pdf_doc: Optional[fitz.Document],
    page_idx: int,
    emitted_anchors: Optional[set] = None,
) -> str:
    """
    Renders a title block with the heading level determined by its dominant font size.
    If the block is a plain number or < 3 letters, renders it as standard text without '#'.
    """
    raw_text = extract_raw_text(block)

    # If it's a plain number (e.g. '28') or noise, do not render as a markdown heading '#'
    if not is_valid_heading_candidate(raw_text):
        content_text = render_title_inline_content(block, delimiters)
        return escape_standalone_marker_rule(escape_text_block_markdown_prefix(content_text))

    level = None

    if pdf_doc and 0 <= page_idx < len(pdf_doc) and font_level_map:
        bbox = getattr(block, "bbox", None)
        if bbox:
            dom_size = get_dominant_font_size_for_text(pdf_doc[page_idx], bbox, raw_text)
            if dom_size in font_level_map:
                level = font_level_map[dom_size]

    # If font size is below noise threshold and not in genuine heading map,
    # render it as regular body text rather than forcing it as a heading
    if level is None:
        content_text = render_title_inline_content(block, delimiters)
        return escape_standalone_marker_rule(escape_text_block_markdown_prefix(content_text))

    level = min(max(level, 1), 6)
    title_text = render_title_inline_content(block, delimiters)
    title = f"{'#' * level} {title_text}"

    anchor = _claim_markdown_anchor(visible_block_anchor(block), emitted_anchors)
    if not anchor:
        return title
    return f'<a id="{html.escape(anchor, quote=True)}"></a>\n{title}'


def render_custom_markdown(
    middle_json: MiddleJson,
    pdf_path: str,
    *,
    mode: RenderMode = RenderMode.DEFAULT,
    latex_delimiters: Optional[LatexDelimitersConfig] = None,
) -> str:
    """
    Renders clean Markdown from middle_json and pdf_path:
    1. Detects heading levels by dominant font size from PDF for particular heading text words only.
    2. Filters out plain numbers and noise from heading hierarchy.
    3. Filters out headers, footers, page numbers, and images.
    4. Normalizes skipped heading hierarchy levels.
    """
    delimiters = latex_delimiters or LatexDelimitersConfig()

    # Step 1: Scan PDF for dominant font sizes of valid headings
    font_level_map = build_heading_font_map(middle_json, pdf_path)

    pdf_doc = None
    try:
        pdf_doc = fitz.open(pdf_path)
    except Exception:
        pass

    try:
        # Step 2: Build render plan using docvortex
        planned_pages = build_render_plan(middle_json, mode)
        anchor_targets = _collect_markdown_anchor_targets(middle_json)
        emitted_anchors: set[str] = set()
        rendered_pages = []

        for page_planned in planned_pages:
            rendered_blocks = []
            for planned in page_planned:
                block = planned.block

                # Skip header, footer, page_number, aside_text
                if is_skipped_aux_block(block):
                    continue

                # Handle images and charts:
                # Discard the image asset, but run superimposed digital text through
                # heading inference and preserve any captions/annotations.
                if is_visual_block(block):
                    visual_elements: List[str] = []
                    # 1. Recover superimposed digital text inside the visual bounding box
                    # and infer heading levels for lines matching candidate heading font sizes
                    if pdf_doc and 0 <= planned.page_idx < len(pdf_doc):
                        bbox = getattr(block, "bbox", None)
                        if bbox:
                            inferred_elements = extract_superimposed_elements_from_pdf(
                                pdf_doc[planned.page_idx],
                                bbox,
                                font_level_map,
                            )
                            visual_elements.extend(inferred_elements)

                    # 2. Extract any captions or footnotes attached to the block
                    annotations = extract_visual_block_annotations(block, delimiters)
                    for ann in annotations:
                        if ann not in visual_elements:
                            visual_elements.append(ann)

                    for elem in visual_elements:
                        # If the element is an inferred ATX heading (starts with '#'), do not escape the '#' prefix!
                        if re.match(r'^#{1,6}\s+', elem):
                            rendered = elem
                        else:
                            rendered = escape_standalone_marker_rule(escape_text_block_markdown_prefix(elem))
                        if rendered:
                            rendered_blocks.append(rendered)
                    continue

                # Custom Title rendering with targeted font-size level mapping
                if is_title_block(block):
                    rendered = _render_custom_title(
                        block,
                        delimiters,
                        font_level_map,
                        pdf_doc,
                        planned.page_idx,
                        emitted_anchors=emitted_anchors,
                    )
                    if rendered:
                        rendered_blocks.append(rendered)
                    continue

                if isinstance(block, TextBlock):
                    content = render_joined_inline_contents(
                        planned.text_contents or [block.content], delimiters
                    )
                    rendered = escape_standalone_marker_rule(escape_text_block_markdown_prefix(content))
                    block_rendered = _prepend_markdown_anchor(rendered, visible_block_anchor(block), emitted_anchors)
                    if block_rendered:
                        rendered_blocks.append(block_rendered)
                    continue

                if isinstance(block, RefTextBlock):
                    content = render_joined_inline_contents(
                        planned.text_contents or [block.content], delimiters
                    )
                    block_rendered = escape_standalone_marker_rule(content)
                    if block_rendered:
                        rendered_blocks.append(block_rendered)
                    continue

                if isinstance(block, PageFootnoteBlock):
                    block_rendered = _render_page_footnote(block, delimiters, emitted_anchors)
                    if block_rendered:
                        rendered_blocks.append(block_rendered)
                    continue

                if isinstance(block, EquationBlock):
                    block_rendered = _render_equation(block, delimiters, "", None)
                    if block_rendered:
                        rendered_blocks.append(block_rendered)
                    continue

                if isinstance(block, ListBlock):
                    block_rendered = _render_list(block, delimiters)
                    if block_rendered:
                        rendered_blocks.append(block_rendered)
                    continue

                if isinstance(block, IndexBlock):
                    block_rendered = _render_index(block, delimiters, anchor_targets=anchor_targets)
                    if block_rendered:
                        rendered_blocks.append(block_rendered)
                    continue

                if isinstance(block, TableBlock):
                    block_rendered = _render_table_block(block, delimiters, "", None, emitted_anchors=emitted_anchors)
                    if block_rendered:
                        rendered_blocks.append(block_rendered)
                    continue

                if isinstance(block, CodeBlock):
                    block_rendered = _render_code_block(block, delimiters, emitted_anchors=emitted_anchors)
                    if block_rendered:
                        rendered_blocks.append(block_rendered)
                    continue

            rendered_page_text = "\n\n".join(rb for rb in rendered_blocks if rb.strip())
            rendered_pages.append(rendered_page_text)

        if mode is RenderMode.FULL:
            raw_markdown = _PAGE_SEPARATOR.join(rendered_pages)
        else:
            raw_markdown = "\n\n".join(p for p in rendered_pages if p.strip())

    finally:
        if pdf_doc:
            pdf_doc.close()

    # Step 3: Normalize skipped heading levels
    normalized_markdown = normalize_heading_skipped_levels(raw_markdown)
    return normalized_markdown


def normalize_heading_skipped_levels(markdown_text: str) -> str:
    """
    Normalizes heading hierarchy throughout markdown so that skipped intermediate levels
    are smoothly stepped down without gaps (e.g. # -> ### becomes # -> ##), while keeping
    identical original levels consistent within the same parent scope.

    Example:
      # OPTIMISING - PRODUCT IMAGES
      ### THE PROBLEM   -> becomes ## THE PROBLEM
      ... body paragraph ...
      ### THE SOLUTION  -> becomes ## THE SOLUTION (sibling of THE PROBLEM!)
      #### Sub-item     -> becomes ### Sub-item

    Preserves code fences (``` or ~~~).
    """
    heading_pattern = re.compile(r'^(#{1,6})\s+(.+)$')
    lines = markdown_text.splitlines(keepends=True)
    output_lines = []
    in_code_block = False

    # Heading hierarchy stack: records {'orig': original_level, 'new': mapped_level}
    heading_stack: List[Dict[str, int]] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_code_block = not in_code_block
            output_lines.append(line)
            continue

        if in_code_block:
            output_lines.append(line)
            continue

        m = heading_pattern.match(stripped)
        if m:
            orig_level = len(m.group(1))
            title = m.group(2)

            # Pop any previous headings that are deeper than or equal to current orig_level
            while heading_stack and heading_stack[-1]["orig"] >= orig_level:
                heading_stack.pop()

            if not heading_stack:
                # Top-level heading
                new_level = 1
            else:
                # Sibling or child under parent
                parent_new = heading_stack[-1]["new"]
                new_level = parent_new + 1

            new_level = min(max(new_level, 1), 6)
            heading_stack.append({"orig": orig_level, "new": new_level})
            output_lines.append(f"{'#' * new_level} {title}\n")
        else:
            output_lines.append(line)

    return "".join(output_lines)
