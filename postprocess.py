import re
import os
import zipfile
from typing import List, Tuple, Optional

# Match ATX headings: 1 to 6 '#' followed by whitespace and title text
HEADING_REGEX = re.compile(r'^(#{1,6})\s+(.+)$')

def is_fenced_code_boundary(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("```") or stripped.startswith("~~~")

def clean_markdown_heading_hierarchy(content: str) -> Tuple[str, int]:
    """
    Cleans consecutive markdown headings that have no body paragraph between them.

    Rules:
      - Case 1: L1 == L2 (e.g. ## H1 followed by ## H2 without text)
        -> Merged as: ## H1 - H2
      - Case 2: L1 > L2 (e.g. ### H1 followed by ## H2 without text)
        -> Merged as: ## H1 - H2 (takes min(L1, L2), the higher hierarchy level)
      - Case 3: L1 < L2 (e.g. ## H1 followed by ### H2 without text)
        -> Kept as-is (valid parent-child hierarchy)
      - Content inside code blocks (```) is never touched.

    Returns:
      (cleaned_content, merge_count)
    """
    lines = content.splitlines(keepends=True)
    if not lines:
        return content, 0

    merged_count = 0
    in_code_block = False

    # First pass: identify tokens (code_line, blank_line, heading, text_line)
    # Each item: dict(type="heading"|"blank"|"text"|"code", line=..., level=..., text=...)
    tokens = []
    for line in lines:
        stripped = line.strip()
        if is_fenced_code_boundary(line):
            in_code_block = not in_code_block
            tokens.append({"type": "code", "line": line})
            continue

        if in_code_block:
            tokens.append({"type": "code", "line": line})
            continue

        if not stripped:
            tokens.append({"type": "blank", "line": line})
            continue

        m = HEADING_REGEX.match(stripped)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            tokens.append({"type": "heading", "level": level, "title": title, "line": line})
        else:
            tokens.append({"type": "text", "line": line})

    # Second pass: scan for consecutive headings
    # If heading1 is followed only by blanks and then heading2:
    #   if L1 >= L2: merge heading2 into heading1 with min(L1, L2)
    i = 0
    while i < len(tokens):
        if tokens[i]["type"] == "heading":
            # Look ahead past any blanks
            j = i + 1
            while j < len(tokens) and tokens[j]["type"] == "blank":
                j += 1

            if j < len(tokens) and tokens[j]["type"] == "heading":
                l1 = tokens[i]["level"]
                l2 = tokens[j]["level"]

                # Case 1 (L1 == L2) or Case 2 (L1 > L2)
                if l1 >= l2:
                    target_level = min(l1, l2)
                    new_title = f"{tokens[i]['title']} - {tokens[j]['title']}"
                    hashes = "#" * target_level
                    tokens[i]["level"] = target_level
                    tokens[i]["title"] = new_title
                    tokens[i]["line"] = f"{hashes} {new_title}\n"

                    # Remove intermediate blanks and heading2
                    del tokens[i + 1 : j + 1]
                    merged_count += 1
                    # Do not advance i, in case a 3rd consecutive heading follows
                    continue

        i += 1

    # Reconstruct document
    result = "".join(t["line"] for t in tokens)
    return result, merged_count


# Regex patterns for markdown images and HTML images
MD_IMAGE_REGEX = re.compile(r'!\[.*?\]\([^\)\n]+\)')
HTML_IMAGE_REGEX = re.compile(r'<img\b[^>]*\/?>', re.IGNORECASE)
IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.svg')

def remove_markdown_images(content: str) -> Tuple[str, int]:
    """
    Strips image references from markdown while preserving code blocks and standard links.
    Also collapses superfluous empty lines created by removing standalone image lines.

    Returns:
      (cleaned_content, removed_count)
    """
    lines = content.splitlines(keepends=True)
    if not lines:
        return content, 0

    removed_count = 0
    in_code_block = False
    processed_lines = []

    for line in lines:
        if is_fenced_code_boundary(line):
            in_code_block = not in_code_block
            processed_lines.append(line)
            continue

        if in_code_block:
            processed_lines.append(line)
            continue

        # Count images in this line
        md_matches = len(MD_IMAGE_REGEX.findall(line))
        html_matches = len(HTML_IMAGE_REGEX.findall(line))
        count_on_line = md_matches + html_matches

        if count_on_line > 0:
            removed_count += count_on_line
            # Strip markdown and html image tags
            new_line = MD_IMAGE_REGEX.sub('', line)
            new_line = HTML_IMAGE_REGEX.sub('', new_line)
            # If the line was only an image and is now just whitespace, leave an empty line
            if not new_line.strip() and line.strip():
                processed_lines.append('\n')
            else:
                processed_lines.append(new_line)
        else:
            processed_lines.append(line)

    result = "".join(processed_lines)
    # Collapse 3+ consecutive newlines into 2 (single blank line between paragraphs)
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result, removed_count


def postprocess_zip_markdown(zip_path: str, output_dir: Optional[str] = None) -> Tuple[int, int, int]:
    """
    Extracts markdown files from the zip archive:
      1. Cleans and normalizes heading hierarchy.
      2. Runs Presidio PII detection and masking pipeline with stars inside enclosures (/[****/]).
      3. Strips image references from markdown and excludes image assets from the zip archive (Option B).
    Rewrites the zip archive with the processed markdown, and extracts
    the final markdown to output_dir for easy preview/access.

    Returns:
      (total_heading_merges, total_pii_masked, total_images_removed)
    """
    if not os.path.exists(zip_path):
        return 0, 0, 0

    from pii_processor import mask_pii_markdown

    temp_zip_path = zip_path + ".tmp"
    total_merges = 0
    total_pii = 0
    total_images_removed = 0

    with zipfile.ZipFile(zip_path, "r") as zin:
        with zipfile.ZipFile(temp_zip_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                filename_lower = item.filename.lower()

                # Include ONLY markdown files in the final zip archive
                if not filename_lower.endswith(".md"):
                    continue

                data = zin.read(item.filename)
                try:
                    text = data.decode("utf-8", errors="replace")

                    # Pipeline Step 1: Heading hierarchy normalization
                    cleaned, count = clean_markdown_heading_hierarchy(text)
                    total_merges += count

                    # Pipeline Step 2: Presidio PII detection and enclosure masking
                    try:
                        redacted_text, pii_count = mask_pii_markdown(cleaned)
                        total_pii += pii_count
                    except Exception:
                        redacted_text = cleaned

                    # Pipeline Step 3: Remove images from the markdown
                    final_text, img_count = remove_markdown_images(redacted_text)
                    total_images_removed += img_count

                    # Save to zip (only the final processed markdown)
                    zout.writestr(item, final_text.encode("utf-8"))

                    # Also save to output_dir if provided
                    if output_dir:
                        out_md_path = os.path.join(output_dir, os.path.basename(item.filename))
                        with open(out_md_path, "w", encoding="utf-8", errors="replace") as f:
                            f.write(final_text)

                except Exception:
                    zout.writestr(item, data)

    # Replace original zip with updated zip
    if os.path.exists(temp_zip_path):
        os.replace(temp_zip_path, zip_path)

    return total_merges, total_pii, total_images_removed

