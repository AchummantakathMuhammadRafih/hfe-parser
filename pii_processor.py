"""
PII Detection and Anonymization Pipeline using Microsoft Presidio and targeted Regex.
Masks detected PII entities with clean stars (*).
Does NOT skip or ignore text inside enclosures such as /[ ... /], [ ... ], or markdown links/images.
"""

import re
import logging
from typing import Tuple, List, Optional

logger = logging.getLogger(__name__)

# Lazy initialization of Presidio analyzer engine
_analyzer_engine = None

# Regex to find explicit enclosures like /[ text /] or \[ text \] so their contents are never skipped
ENCLOSURE_PATTERN = re.compile(r'/\[\s*([^\n\]]*?)\s*/\]')

# Markdown links [text](url) and images ![alt](url)
MD_LINK_OR_IMAGE_PATTERN = re.compile(r'(!?\[)(.*?)(\]\()([^\)\n]+)(\))')

# HTML / Autolinks like <email@domain.com> or <a href="...">...</a>
HTML_AUTOLINK_PATTERN = re.compile(r'<([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)>')
HTML_MAILTO_PATTERN = re.compile(r'(<a\b[^>]*href=["\']mailto:)([^"\'>]+)(["\'][^>]*>)(.*?)(</a>)', re.IGNORECASE)

# Standalone email and phone patterns for reliable fallback detection
EMAIL_REGEX = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')
# Matches international / local phone numbers like +1 555-0199, +* *** *** ****, (123) 456-7890
PHONE_REGEX = re.compile(r'(?:(?:\+|00)\d{1,3}[\s.-]*)?(?:\(?\d{2,4}\)?[\s.-]*)?\d{3,4}[\s.-]*\d{3,4}(?:[\s.-]*\d{1,9})?')

TARGET_ENTITIES = [
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "CRYPTO",
    "IBAN_CODE",
    "IP_ADDRESS",
    "US_BANK_NUMBER",
    "US_DRIVER_LICENSE",
    "US_ITIN",
    "US_PASSPORT",
    "US_SSN",
    "MEDICAL_LICENSE",
    "LOCATION",
]

def is_fenced_code_boundary(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("```") or stripped.startswith("~~~")

def get_presidio_analyzer():
    global _analyzer_engine
    if _analyzer_engine is None:
        try:
            from presidio_analyzer import AnalyzerEngine
            _analyzer_engine = AnalyzerEngine()
        except Exception as e:
            logger.warning(f"Failed to load Presidio AnalyzerEngine: {e}")
            _analyzer_engine = None
    return _analyzer_engine


def make_star_mask(text: str) -> str:
    """
    Replaces alphanumeric characters with stars (*), preserving spaces and punctuation.
    """
    return re.sub(r'\w', '*', text)


def mask_line_pii(line: str, analyzer) -> Tuple[str, int]:
    """
    Detects and masks PII in a line with stars (*).
    Evaluates:
      1. Enclosures /[ ... /]
      2. Direct fallback regex for emails and phone numbers (ensures zero omissions)
      3. Presidio NLP analyzer for PERSON, LOCATION, and other sensitive entities
    """
    if not line.strip():
        return line, 0

    masked_count = 0
    working_line = line

    # 1. Check text inside enclosures like /[ ... /]
    def handle_enclosure(match):
        nonlocal masked_count
        full_match = match.group(0)
        inner = match.group(1).strip()
        
        if not inner or re.fullmatch(r'[\*\s\-_.,]+', inner):
            return full_match

        try:
            inner_results = analyzer.analyze(
                text=inner,
                language="en",
                entities=TARGET_ENTITIES
            )
        except Exception:
            inner_results = []

        if inner_results:
            masked_count += len(inner_results)
            masked_inner = make_star_mask(inner)
            return f"/[{masked_inner}/]"
        return full_match

    working_line = ENCLOSURE_PATTERN.sub(handle_enclosure, working_line)

    # 2. Targeted regex check for emails (catches any missed by NLP or formatting boundaries)
    def mask_email_match(match):
        nonlocal masked_count
        masked_count += 1
        return make_star_mask(match.group(0))

    working_line = EMAIL_REGEX.sub(mask_email_match, working_line)

    # 3. Presidio Analyzer on the working line
    try:
        results = analyzer.analyze(
            text=working_line,
            language="en",
            entities=TARGET_ENTITIES
        )
    except Exception as e:
        logger.error(f"Presidio analyze error: {e}")
        return working_line, masked_count

    # Filter out entities that are already stars or whitespace/punct
    valid_results = []
    for r in results:
        sub = working_line[r.start:r.end]
        if re.fullmatch(r'[\*\s\-_.,/\[\]]+', sub):
            continue
        valid_results.append(r)

    # Sort reverse by start offset to safely replace in-place
    valid_results.sort(key=lambda x: x.start, reverse=True)

    filtered_spans = []
    for res in valid_results:
        overlaps = False
        for (s, e) in filtered_spans:
            if not (res.end <= s or res.start >= e):
                overlaps = True
                break
        if not overlaps:
            filtered_spans.append((res.start, res.end))
            entity_val = working_line[res.start:res.end]
            mask = make_star_mask(entity_val)
            working_line = working_line[:res.start] + mask + working_line[res.end:]
            masked_count += 1

    return working_line, masked_count


def mask_markdown_links_and_tags(line: str, analyzer) -> Tuple[str, int]:
    """
    Carefully inspects markdown links [text](url) and HTML mailto/autolinks,
    masking sensitive information inside the link text AND the url/destination.
    """
    total_count = 0

    # 1. HTML Autolinks: <email@domain.com>
    def mask_html_autolink(match):
        nonlocal total_count
        email = match.group(1)
        total_count += 1
        return f"<{make_star_mask(email)}>"

    line = HTML_AUTOLINK_PATTERN.sub(mask_html_autolink, line)

    # 2. HTML <a> tags with mailto:
    def mask_html_mailto(match):
        nonlocal total_count
        prefix = match.group(1)
        target = match.group(2)
        mid = match.group(3)
        inner = match.group(4)
        close = match.group(5)

        masked_target = make_star_mask(target)
        masked_inner, c = mask_line_pii(inner, analyzer)
        total_count += 1 + c
        return f"{prefix}{masked_target}{mid}{masked_inner}{close}"

    line = HTML_MAILTO_PATTERN.sub(mask_html_mailto, line)

    # 3. Markdown Links: [anchor text](url) and images ![alt text](url)
    def mask_md_link(match):
        nonlocal total_count
        open_bracket = match.group(1)  # '[' or '!['
        anchor_text = match.group(2)
        mid = match.group(3)           # ']('
        url = match.group(4)
        close = match.group(5)         # ')'

        # Mask anchor text if it contains PII (e.g. name, email, phone)
        masked_anchor, c1 = mask_line_pii(anchor_text, analyzer)

        # Mask URL if it contains mailto: or direct email/phone
        masked_url = url
        if url.lower().startswith("mailto:"):
            email_part = url[7:]
            masked_url = "mailto:" + make_star_mask(email_part)
            total_count += 1
        elif url.lower().startswith("tel:"):
            tel_part = url[4:]
            masked_url = "tel:" + make_star_mask(tel_part)
            total_count += 1
        elif EMAIL_REGEX.search(url):
            masked_url = EMAIL_REGEX.sub(lambda m: make_star_mask(m.group(0)), url)
            total_count += 1

        total_count += c1
        return f"{open_bracket}{masked_anchor}{mid}{masked_url}{close}"

    line = MD_LINK_OR_IMAGE_PATTERN.sub(mask_md_link, line)
    return line, total_count


def mask_pii_markdown(content: str, language: str = "en") -> Tuple[str, int]:
    """
    Detects and masks PII across markdown text using Microsoft Presidio and targeted regex.
    Masks PII values cleanly with stars (*).
    Never skips enclosures such as /[ ... /] or markdown links/images.
    
    Protects:
      - Fenced code blocks (```...```)
      
    Returns:
      (masked_content, total_pii_count)
    """
    if not content or not content.strip():
        return content, 0

    analyzer = get_presidio_analyzer()
    if analyzer is None:
        return content, 0

    lines = content.splitlines(keepends=True)
    processed_lines = []
    total_pii_count = 0
    in_code_block = False

    for line in lines:
        if is_fenced_code_boundary(line):
            in_code_block = not in_code_block
            processed_lines.append(line)
            continue

        if in_code_block:
            # Code block lines remain untouched
            processed_lines.append(line)
            continue

        # Step A: Mask sensitive information inside markdown links, mailto targets, and HTML tags
        line_with_masked_links, link_pii_count = mask_markdown_links_and_tags(line, analyzer)
        total_pii_count += link_pii_count

        # Step B: Mask remaining PII across the line
        masked_line, count = mask_line_pii(line_with_masked_links, analyzer)
        total_pii_count += count

        processed_lines.append(masked_line)

    return "".join(processed_lines), total_pii_count
