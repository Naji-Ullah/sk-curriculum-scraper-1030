"""Extract Broad Areas of Learning and Cross-curricular Competencies from curriculum PDFs."""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

import fitz  # pymupdf
import httpx

from app.curriculum_config import BASE_URL, CORE_FRENCH_K9, CURRICULA

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("scraped_data")

# Canonical titles (used as output keys)
BAL_TITLES = [
    "Lifelong Learners",
    "Sense of Self, Community, and Place",
    "Engaged Citizens",
]

CCC_TITLES = [
    "Developing Thinking",
    "Developing Identity and Interdependence",
    "Developing Literacies",
    "Developing Social Responsibility",
]

# Patterns that match title variations across PDFs
BAL_PATTERNS = [
    (re.compile(r"Lifelong Learners"), "Lifelong Learners"),
    (re.compile(r"Sense of Self,? Community,? and Place"), "Sense of Self, Community, and Place"),
    (re.compile(r"Engaged Citizens"), "Engaged Citizens"),
]

CCC_PATTERNS = [
    (re.compile(r"Developing Thinking"), "Developing Thinking"),
    (re.compile(r"Developing Identity and Interdependence"), "Developing Identity and Interdependence"),
    (re.compile(r"Developing Literacies"), "Developing Literacies"),
    (re.compile(r"Developing Social Responsibility"), "Developing Social Responsibility"),
]


def _clean_paragraph(text: str) -> str:
    """Clean extracted PDF paragraph text."""
    # Remove "(Related to the following Goals of Education: ...)" blocks
    text = re.sub(r"\(Related to the following Goals of Education:.*?\)", "", text, flags=re.DOTALL)
    # Remove "(Related to CEL(s) of ...)" blocks
    text = re.sub(r"\(Related to CELs? of .*?\)", "", text, flags=re.DOTALL)
    # Remove K-12 Goals sidebar text
    text = re.sub(r"K-12 Goals for .*?(?=\n[A-Z]|\Z)", "", text, flags=re.DOTALL)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    # Remove leading colons / bullets from badly extracted text
    text = re.sub(r"^[\s:•*]+", "", text).strip()
    return text


def _find_content_pages(doc: fitz.Document, section_name: str, content_markers: list[str]) -> str:
    """Find and concatenate pages containing a section's actual content (not TOC)."""
    pages_text = []
    collecting = False

    for i in range(min(30, doc.page_count)):
        text = doc[i].get_text()
        if not collecting:
            if section_name in text and any(m in text for m in content_markers) and "Table of Contents" not in text:
                collecting = True
                pages_text.append(text)
        else:
            has_subsection = any(pat.search(text) for pat, _ in BAL_PATTERNS + CCC_PATTERNS)
            has_stop_heading = any(heading in text for heading in [
                "Outcomes and Indicators",
                "Curriculum Outcomes",
                "Teaching Resources",
                "Assessment and Evaluation",
            ]) and section_name not in text
            if has_stop_heading and not has_subsection:
                break
            pages_text.append(text)
            if len(pages_text) >= 5:
                break

    return "\n".join(pages_text)


def _find_sections(content: str, patterns: list[tuple[re.Pattern, str]]) -> list[dict[str, str]]:
    """Find all section headings in text and extract content between them."""
    # Find all heading positions
    headings: list[tuple[int, int, str]] = []
    for pat, canonical_title in patterns:
        for m in pat.finditer(content):
            headings.append((m.start(), m.end(), canonical_title))

    # Sort by position in text
    headings.sort(key=lambda x: x[0])

    # Deduplicate: keep only the last occurrence of each title
    # (first occurrences may be in TOC or intro text)
    seen_titles: dict[str, int] = {}
    for idx, (start, end, title) in enumerate(headings):
        seen_titles[title] = idx
    headings = [headings[i] for i in sorted(seen_titles.values())]

    # Also find stop markers that indicate end of the whole section
    stop_patterns = [
        re.compile(r"K-12 Aim and Goals"),
        re.compile(r"Aim and Goals of"),
        re.compile(r"An Effective .* Program"),
        re.compile(r"Cross-curricular Competencies\b"),
        re.compile(r"\*A sense of place"),
    ]

    results = []
    for i, (start, end, title) in enumerate(headings):
        # Text starts after the heading
        text_start = end

        # Text ends at the next heading or stop marker
        if i + 1 < len(headings):
            text_end = headings[i + 1][0]
        else:
            text_end = len(content)

        # Also check stop markers
        for stop_pat in stop_patterns:
            m = stop_pat.search(content, text_start)
            if m and m.start() < text_end:
                text_end = m.start()

        description = content[text_start:text_end]
        description = _clean_paragraph(description)

        if description and len(description) > 10:
            results.append({"title": title, "description": description})

    return results


def parse_broad_areas_of_learning(doc: fitz.Document) -> list[dict[str, str]] | None:
    """Parse Broad Areas of Learning from a PDF document."""
    content = _find_content_pages(
        doc,
        "Broad Areas of Learning",
        ["There are three Broad Areas", "Lifelong Learners", "Sense of Self"],
    )
    if not content:
        return None

    # Remove page headers (page number + subject name at top of pages)
    content = re.sub(r"^\s*\n\s*[^\n]{0,60}\s*\n\s*\n?\s*\d+\s*\n", "\n", content, flags=re.MULTILINE)
    content = re.sub(r"^\d+\s*\n[^\n]+\n", "\n", content, flags=re.MULTILINE)

    results = _find_sections(content, BAL_PATTERNS)

    # Filter to only BAL titles
    bal_titles_set = set(BAL_TITLES)
    results = [r for r in results if r["title"] in bal_titles_set]

    return results if results else None


def parse_cross_curricular_competencies(doc: fitz.Document) -> list[dict[str, str]] | None:
    """Parse Cross-curricular Competencies from a PDF document."""
    content = _find_content_pages(
        doc,
        "Cross-curricular Competencies",
        ["The Cross-curricular Competencies are four", "Developing Thinking", "cross-curricular competencies"],
    )
    if not content:
        return None

    content = re.sub(r"^\s*\n\s*[^\n]{0,60}\s*\n\s*\n?\s*\d+\s*\n", "\n", content, flags=re.MULTILINE)
    content = re.sub(r"^\d+\s*\n[^\n]+\n", "\n", content, flags=re.MULTILINE)

    results = _find_sections(content, CCC_PATTERNS)

    ccc_titles_set = set(CCC_TITLES)
    results = [r for r in results if r["title"] in ccc_titles_set]

    return results if results else None


def _base_subject_name(curriculum_name: str) -> str:
    """Extract short base subject name for file naming."""
    name = re.sub(r"\s*\(.*?\)\s*$", "", curriculum_name)
    name = re.sub(r"[\s,]+[AB]?\d{1,2}(?:[\s,]+(?:[A-Za-z]+\s+)?[AB]?\d{1,2})*\s*$", "", name)
    name = re.sub(r"\s+\d{1,2}(?=\s*:)", "", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(",")
    return name


async def scrape_bal_and_ccc(
    progress_callback=None,
) -> dict[str, Any]:
    """Scrape Broad Areas of Learning and Cross-curricular Competencies from all curriculum PDFs."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    results = {"bal": [], "ccc": [], "skipped": []}

    # Collect unique curriculum IDs with their base names
    seen_ids: set[int] = set()
    curricula_to_process: list[tuple[int, str]] = []

    all_curricula = [
        c for c in CURRICULA
        if c.get("id") is not None and not c.get("skip") and not c.get("treaty")
    ]
    all_curricula.extend(CORE_FRENCH_K9)

    for c in all_curricula:
        cid = c["id"]
        if cid not in seen_ids:
            seen_ids.add(cid)
            base_name = _base_subject_name(c["name"])
            curricula_to_process.append((cid, base_name))

    async with httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": "SK-Curriculum-Scraper/1.0"},
    ) as client:
        total = len(curricula_to_process)
        for i, (cid, base_name) in enumerate(curricula_to_process):
            if progress_callback:
                progress_callback("overall", i, total, f"Processing PDF: {base_name}")

            try:
                pdf_url = f"{BASE_URL}/CurriculumFile?id={cid}"
                resp = await client.get(pdf_url, timeout=120)
                resp.raise_for_status()
                pdf_bytes = resp.content

                doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                try:
                    # Parse BAL
                    bal = parse_broad_areas_of_learning(doc)
                    if bal:
                        bal_data = {"Broad Areas Of Learning": bal}
                        bal_filename = f"broad_areas_of_learning_{base_name}.json"
                        bal_path = OUTPUT_DIR / bal_filename
                        with open(bal_path, "w", encoding="utf-8") as f:
                            json.dump(bal_data, f, indent=4, ensure_ascii=False)
                        results["bal"].append(bal_filename)
                        logger.info(f"Saved BAL: {bal_path}")

                    # Parse CCC
                    ccc = parse_cross_curricular_competencies(doc)
                    if ccc:
                        ccc_data = {"cross_curricular_competencies": ccc}
                        ccc_filename = f"cross_curricular_competencies_{base_name}.json"
                        ccc_path = OUTPUT_DIR / ccc_filename
                        with open(ccc_path, "w", encoding="utf-8") as f:
                            json.dump(ccc_data, f, indent=4, ensure_ascii=False)
                        results["ccc"].append(ccc_filename)
                        logger.info(f"Saved CCC: {ccc_path}")

                    if not bal and not ccc:
                        results["skipped"].append(f"{base_name} (id={cid}): no BAL/CCC content found in PDF")
                        logger.warning(f"No BAL/CCC found in PDF for {base_name}")
                finally:
                    doc.close()

            except Exception as e:
                logger.error(f"Failed to process PDF for {base_name} (id={cid}): {e}")
                results["skipped"].append(f"{base_name} (id={cid}): {e}")

            await asyncio.sleep(0.2)

    if progress_callback:
        progress_callback("overall", total, total, "Complete!")

    return results
