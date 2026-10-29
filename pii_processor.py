"""
PII Detection and Anonymization Pipeline using Microsoft Presidio.
Masks detected PII entities with clean stars (*).
Does NOT skip or ignore text inside enclosures such as /[ ... /] or [ ... ].
"""

import re
import uuid
import logging
from typing import Tuple, List, Optional

logger = logging.getLogger(__name__)

# Lazy initialization of Presidio analyzer engine
_analyzer_engine = None

# Regex to find explicit enclosures like /[ text /] or \[ text \] so their contents are never skipped
ENCLOSURE_PATTERN = re.compile(r'/\[\s*([^\n\]]*?)\s*/\]')

# Markdown links and images like ![alt](path) or [title](url)
MD_LINK_OR_IMAGE_PATTERN = re.compile(r'!?\[[^\]\n]*\]\([^\)\n]+\)')

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
    Detects and masks PII in a single line with stars (*).
    Also ensures any text inside enclosures like /[ ... /] is evaluated and masked.
    """
    if not line.strip():
        return line, 0

    masked_count = 0
    working_line = line

    # 1. First: specifically check text inside enclosures like /[ ... /] to ensure it is NEVER skipped
    def handle_enclosure(match):
        nonlocal masked_count
        full_match = match.group(0)
        inner = match.group(1).strip()
        
        # If it's already masked with stars, leave it
        if not inner or re.fullmatch(r'[\*\s\-_.,]+', inner):
            return full_match

        # Run presidio on the inner content specifically
        try:
            inner_results = analyzer.analyze(
                text=inner,
                language="en",
                entities=[
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
                    "MEDICAL_LICENSE"
                ]
            )
        except Exception:
            inner_results = []

        if inner_results:
            masked_count += len(inner_results)
            # Mask the inner content with stars
            masked_inner = make_star_mask(inner)
            return f"/[{masked_inner}/]"
        return full_match

    working_line = ENCLOSURE_PATTERN.sub(handle_enclosure, working_line)

    # 2. Run Presidio Analyzer on the full line
    try:
        results = analyzer.analyze(
            text=working_line,
            language="en",
            entities=[
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
                "MEDICAL_LICENSE"
            ]
        )
    except Exception as e:
        logger.error(f"Presidio analyze error: {e}")
        return working_line, masked_count

    # Filter out entities that hit placeholders or are already stars
    valid_results = []
    for r in results:
        sub = working_line[r.start:r.end]
        if "PROTECTEDBLOCK" in sub or re.fullmatch(r'[\*\s\-_.,/\[\]]+', sub):
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
            # Replace with clean stars (*), preserving character length
            mask = make_star_mask(entity_val)
            working_line = working_line[:res.start] + mask + working_line[res.end:]
            masked_count += 1

    return working_line, masked_count


def mask_pii_markdown(content: str, language: str = "en") -> Tuple[str, int]:
    """
    Detects and masks PII in markdown text using Microsoft Presidio.
    Masks PII values cleanly with stars (*).
    Never skips enclosures such as /[ ... /].
    
    Protects:
      - Fenced code blocks (```...```)
      - Markdown images and links (![alt](path), [text](url))
      
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
            # Code block lines remain completely untouched
            processed_lines.append(line)
            continue

        # Temporarily protect markdown links/images on this line
        line_token_store = {}
        def protect_link(match):
            tid = f"PROTECTEDBLOCK{uuid.uuid4().hex[:8]}XYZ"
            line_token_store[tid] = match.group(0)
            return tid

        working_line = MD_LINK_OR_IMAGE_PATTERN.sub(protect_link, line)
        masked_line, count = mask_line_pii(working_line, analyzer)
        total_pii_count += count

        # Restore any links/images protected on this line
        for tid, orig in line_token_store.items():
            masked_line = masked_line.replace(tid, orig)

        processed_lines.append(masked_line)

    return "".join(processed_lines), total_pii_count
