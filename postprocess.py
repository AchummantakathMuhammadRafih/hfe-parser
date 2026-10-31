import re
import os
import zipfile
from typing import List, Tuple, Optional

# Match ATX headings: 1 to 6 '#' followed by whitespace and title text
HEADING_REGEX = re.compile(r'^(#{1,})\s+(.+)$')

def is_fenced_code_boundary(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("```") or stripped.startswith("~~~")

def is_noise_markdown_line(line: str) -> bool:
    """
    Checks if a markdown line is noise:
    1. Plain numeric line (e.g. '4', '44', '# 44', '  27  ', '2.1').
    2. Short noise text with fewer than 3 alphabetic letters (e.g. 'II', 'C', '## A').
    Preserves markdown tables, blockquotes, list items, code fences, and horizontal rules.
    """
    s = line.strip()
    if not s:
        return False

    # Never treat markdown structural markers or code fences as noise
    if s.startswith(('|', '>', '```', '~~~', '<')):
        return False

    # Horizontal rules (e.g. ---, ***, ___)
    if re.fullmatch(r'[-*_]{3,}', s):
        return False

    # List items (- item, * item, 1. item)
    if re.match(r'^[-*+]\s+', s) or re.match(r'^\d+\.\s+', s):
        item_text = re.sub(r'^[-*+]\s+', '', s)
        item_text = re.sub(r'^\d+\.\s+', '', item_text).strip()
        letters = re.findall(r'[a-zA-Z]', item_text)
        digits = re.findall(r'\d', item_text)
        if not letters and not digits:
            return True
        return False

    # Check headings or standalone text line
    m = HEADING_REGEX.match(s)
    text = m.group(2).strip() if m else s

    # Strip surrounding punct / symbols
    stripped = re.sub(r'^[#\s\.\-\–\—\(\)\[\]/]+', '', text)
    stripped = re.sub(r'[#\s\.\-\–\—\(\)\[\]/]+$', '', stripped)

    # 1. Plain numeric line (e.g. '4', '44', '# 44')
    if re.fullmatch(r'^\d+([.,]\d+)*$', stripped):
        return True

    # 2. Text with fewer than 3 alphabetic letters
    letters = re.findall(r'[a-zA-Z]', text)
    if len(letters) < 3 and not re.search(r'\d', text):
        return True

    return False

def filter_document_noise_lines(content: str) -> Tuple[str, int]:
    """
    Filters out noise lines (e.g., '# 44', '4', 'II', '< 3 letters') BEFORE any
    other pipeline processing (heading normalization, merging, redaction, stripping).
    Preserves content inside code fences.
    """
    lines = content.splitlines(keepends=True)
    if not lines:
        return content, 0

    filtered_lines = []
    removed_count = 0
    in_code_block = False

    for line in lines:
        if is_fenced_code_boundary(line):
            in_code_block = not in_code_block
            filtered_lines.append(line)
            continue

        if in_code_block:
            filtered_lines.append(line)
            continue

        if is_noise_markdown_line(line):
            removed_count += 1
            continue

        filtered_lines.append(line)

    result = "".join(filtered_lines)
    # Collapse 3+ consecutive newlines into 2 (single blank line between paragraphs)
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result, removed_count

def clean_markdown_heading_hierarchy(content: str) -> Tuple[str, int]:
    lines = content.splitlines(keepends=True)
    if not lines:
        return content, 0

    adjustment_count = 0
    in_code_block = False

    tokens = []
    for line in lines:
        stripped = line.strip()
        if is_fenced_code_boundary(line):
            in_code_block = not in_code_block
            tokens.append({'type': 'code', 'line': line})
            continue

        if in_code_block:
            tokens.append({'type': 'code', 'line': line})
            continue

        if not stripped:
            tokens.append({'type': 'blank', 'line': line})
            continue

        m = HEADING_REGEX.match(stripped)
        if m:
            tokens.append({'type': 'heading', 'level': len(m.group(1)), 'title': m.group(2).strip(), 'line': line})
        else:
            tokens.append({'type': 'text', 'line': line})

    h_indices = [idx for idx, t in enumerate(tokens) if t['type'] == 'heading']
    if not h_indices:
        return content, 0

    groups = []
    curr_group = [0]
    for k in range(1, len(h_indices)):
        prev_idx = h_indices[k - 1]
        this_idx = h_indices[k]
        only_blanks = all(tokens[x]['type'] == 'blank' for x in range(prev_idx + 1, this_idx))
        if only_blanks:
            curr_group.append(k)
        else:
            groups.append(curr_group)
            curr_group = [k]
    groups.append(curr_group)

    def get_subtree_end(h_list_idx: int) -> int:
        parent_lvl = tokens[h_indices[h_list_idx]]['level']
        for k in range(h_list_idx + 1, len(h_indices)):
            if tokens[h_indices[k]]['level'] <= parent_lvl:
                return k
        return len(h_indices)

    for grp in groups:
        if len(grp) <= 1:
            continue

        changed = True
        while changed:
            changed = False
            for pos in range(len(grp) - 1):
                idx_curr = h_indices[grp[pos]]
                idx_next = h_indices[grp[pos + 1]]
                if tokens[idx_curr]['level'] > tokens[idx_next]['level']:
                    tokens[idx_curr]['level'] = tokens[idx_next]['level']
                    tokens[idx_curr]['line'] = ('#' * tokens[idx_curr]['level']) + ' ' + tokens[idx_curr]['title'] + chr(10)
                    adjustment_count += 1
                    changed = True

        for pos in range(len(grp) - 1):
            idx_curr = h_indices[grp[pos]]
            idx_next = h_indices[grp[pos + 1]]
            curr_lvl = tokens[idx_curr]['level']
            next_lvl = tokens[idx_next]['level']

            if next_lvl <= curr_lvl:
                target_lvl = curr_lvl + 1
                delta = target_lvl - next_lvl
                if delta > 0:
                    sub_end = get_subtree_end(grp[pos + 1])
                    for k in range(grp[pos + 1], sub_end):
                        t_idx = h_indices[k]
                        tokens[t_idx]['level'] += delta
                        tokens[t_idx]['line'] = ('#' * tokens[t_idx]['level']) + ' ' + tokens[t_idx]['title'] + chr(10)
                        adjustment_count += 1

    result = ''.join(t['line'] for t in tokens)
    return result, adjustment_count



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


def sanitize_duplicate_heading_title(title: str) -> str:
    """
    Sanitizes duplicate patterns in headings like:
      'CHANGING LEADERSHIP BEHAVIOUR CHANGING LEADERSHIP BEHAVIOUR'
      -> 'CHANGING LEADERSHIP BEHAVIOUR'
      'IMPROVING INTERACTIONS WITH TECHNOLOGY IMPROVING INTERACTIONS WITH TECHNOLOGY'
      -> 'IMPROVING INTERACTIONS WITH TECHNOLOGY'
      'CHANGING - LEADERSHIP - BEHAVIOUR - CHANGING LEADERSHIP BEHAVIOUR'
      -> 'CHANGING LEADERSHIP BEHAVIOUR'
    Supports both space-delimited and hyphen-delimited merged headings.
    """
    from difflib import SequenceMatcher

    def norm(s: str) -> str:
        return re.sub(r'[^a-zA-Z0-9]', '', s).lower()

    def check_split(parts: List[str]) -> Optional[str]:
        candidates = []
        for split_idx in range(1, len(parts)):
            prefix = ' '.join(parts[:split_idx])
            suffix = ' '.join(parts[split_idx:])

            np = norm(prefix)
            ns = norm(suffix)

            if not np or not ns:
                continue

            # Exact match of normalized text
            if np == ns:
                return suffix

            # High similarity match (tolerates OCR typos/ligatures, e.g. 'STA' vs 'STAFF')
            ratio = SequenceMatcher(None, np, ns).ratio()
            if ratio >= 0.85 and min(len(np), len(ns)) >= 8:
                candidates.append((ratio, abs(len(np) - len(ns)), suffix if len(suffix) >= len(prefix) else prefix))

        if candidates:
            # Prefer highest similarity match, then closest balance
            candidates.sort(key=lambda x: (-x[0], x[1]))
            return candidates[0][2]
        return None

    # 1. Hyphen-delimited check (e.g. from existing hyphens or hyphenated headings)
    hyphen_parts = [p.strip() for p in re.split(r'\s+-\s+', title) if p.strip()]
    if len(hyphen_parts) > 1:
        res = check_split(hyphen_parts)
        if res:
            return res

    # 2. Word/space-delimited check (e.g. from space-merged consecutive headings)
    word_parts = [p.strip() for p in title.split() if p.strip()]
    if len(word_parts) > 1:
        res = check_split(word_parts)
        if res:
            return res

    return title


def sanitize_duplicate_markdown_headings(content: str) -> Tuple[str, int]:
    """
    Pipeline step to scan and sanitize duplicated text inside headings throughout markdown.
    Preserves code blocks untouched.
    """
    lines = content.splitlines(keepends=True)
    if not lines:
        return content, 0

    sanitized_count = 0
    in_code_block = False
    output_lines = []

    for line in lines:
        stripped = line.strip()
        if is_fenced_code_boundary(line):
            in_code_block = not in_code_block
            output_lines.append(line)
            continue

        if in_code_block:
            output_lines.append(line)
            continue

        m = HEADING_REGEX.match(stripped)
        if m:
            level_str = m.group(1)
            title = m.group(2).strip()
            cleaned_title = sanitize_duplicate_heading_title(title)
            if cleaned_title != title:
                sanitized_count += 1
                output_lines.append(f"{level_str} {cleaned_title}\n")
            else:
                output_lines.append(line)
        else:
            output_lines.append(line)

    return "".join(output_lines), sanitized_count


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

                    # Pipeline Step 1: UPFRONT Noise filtering
                    # Filters out stray numbers, isolated letters, and noise lines before anything else
                    denoised_text, noise_removed = filter_document_noise_lines(text)
                    if noise_removed > 0:
                        print(f"[Post-Process] Noise filtered ({noise_removed} noise lines dropped).")

                    # Pipeline Step 2: Heading hierarchy normalization
                    # 2a: Smooth skipped heading levels (e.g., # -> ### becomes # -> ##)
                    from custom_markdown import normalize_heading_skipped_levels
                    smoothed = normalize_heading_skipped_levels(denoised_text)

                    # 2b: Merge consecutive headings without body text (e.g. ## H1 followed by ## H2 -> ## H1 H2)
                    cleaned, count = clean_markdown_heading_hierarchy(smoothed)
                    total_merges += count

                    # Pipeline Step 3: Presidio PII detection and enclosure masking
                    try:
                        redacted_text, pii_count = mask_pii_markdown(cleaned)
                        total_pii += pii_count
                    except Exception:
                        redacted_text = cleaned

                    # Pipeline Step 4: Remove images from the markdown
                    images_removed_text, img_count = remove_markdown_images(redacted_text)
                    total_images_removed += img_count

                    # Pipeline Step 5: Sanitize duplicate headers (just before zipping)
                    # e.g. '# CHANGING LEADERSHIP BEHAVIOUR CHANGING LEADERSHIP BEHAVIOUR' -> '# CHANGING LEADERSHIP BEHAVIOUR'
                    final_text, dups_sanitized = sanitize_duplicate_markdown_headings(images_removed_text)
                    if dups_sanitized > 0:
                        print(f"[Post-Process] Heading duplicates sanitized ({dups_sanitized} headings cleaned).")

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

