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


def _clean_paragraph(text: str) -> str:
    """Clean extracted PDF paragraph text."""
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_section_text(full_text: str, start_marker: str, end_markers: list[str]) -> str:
    """Extract text between a start marker and the first matching end marker."""
    start_idx = full_text.find(start_marker)
    if start_idx == -1:
        return ""
    start_idx += len(start_marker)

    end_idx = len(full_text)
    for marker in end_markers:
        idx = full_text.find(marker, start_idx)
        if idx != -1 and idx < end_idx:
            end_idx = idx

    return full_text[start_idx:end_idx].strip()


def _find_content_pages(doc: fitz.Document, section_name: str, content_marker: str) -> str:
    """Find and concatenate pages containing a section's actual content (not TOC)."""
    pages_text = []
    collecting = False

    for i in range(min(30, doc.page_count)):
        text = doc[i].get_text()
        if not collecting:
            if section_name in text and content_marker in text and "Table of Contents" not in text:
                collecting = True
                pages_text.append(text)
        else:
            # Don't stop if page still has subsection content we need
            has_subsection = any(title in text for title in BAL_TITLES + CCC_TITLES)
            has_stop_heading = any(heading in text for heading in [
                "Outcomes and Indicators",
                "Curriculum Outcomes",
                "Teaching Resources",
                "Assessment and Evaluation",
                "References",
            ]) and section_name not in text
            if has_stop_heading and not has_subsection:
                break
            pages_text.append(text)
            # Stop after 3 continuation pages max
            if len(pages_text) >= 4:
                break

    return "\n".join(pages_text)


def parse_broad_areas_of_learning(doc: fitz.Document) -> list[dict[str, str]] | None:
    """Parse Broad Areas of Learning from a PDF document."""
    content = _find_content_pages(doc, "Broad Areas of Learning", "There are three Broad Areas")
    if not content:
        # Try alternate intro phrasing
        content = _find_content_pages(doc, "Broad Areas of Learning", "Lifelong Learners")
    if not content:
        return None

    # Remove page headers (page numbers + subject name lines at top of pages)
    content = re.sub(r"^\d+\n[^\n]+\n", "", content, flags=re.MULTILINE)

    results = []
    end_markers_by_title = {
        "Lifelong Learners": ["Sense of Self, Community, and Place", "Related to the following"],
        "Sense of Self, Community, and Place": ["Engaged Citizens", "Related to the following"],
        "Engaged Citizens": ["Related to the following", "Cross-curricular Competencies", "K-12 Goals"],
    }

    for title in BAL_TITLES:
        end_markers = end_markers_by_title.get(title, ["Related to the following"])
        description = _extract_section_text(content, title, end_markers)
        description = _clean_paragraph(description)
        if description:
            results.append({"title": title, "description": description})

    return results if results else None


def parse_cross_curricular_competencies(doc: fitz.Document) -> list[dict[str, str]] | None:
    """Parse Cross-curricular Competencies from a PDF document."""
    content = _find_content_pages(
        doc,
        "Cross-curricular Competencies",
        "The Cross-curricular Competencies are four",
    )
    if not content:
        content = _find_content_pages(doc, "Cross-curricular Competencies", "Developing Thinking")
    if not content:
        return None

    content = re.sub(r"^\d+\n[^\n]+\n", "", content, flags=re.MULTILINE)

    results = []
    end_markers_by_title = {
        "Developing Thinking": ["Developing Identity and Interdependence", "K-12 Goals for"],
        "Developing Identity and Interdependence": ["Developing Literacies", "K-12 Goals for"],
        "Developing Literacies": ["Developing Social Responsibility", "K-12 Goals for"],
        "Developing Social Responsibility": ["K-12 Aim and Goals", "K-12 Goals for", "Aim and Goals of", "An Effective"],
    }

    for title in CCC_TITLES:
        end_markers = end_markers_by_title.get(title, ["K-12 Goals for"])
        description = _extract_section_text(content, title, end_markers)
        description = _clean_paragraph(description)
        if description:
            results.append({"title": title, "description": description})

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

                doc.close()

            except Exception as e:
                logger.error(f"Failed to process PDF for {base_name} (id={cid}): {e}")
                results["skipped"].append(f"{base_name} (id={cid}): {e}")

            await asyncio.sleep(0.2)

    if progress_callback:
        progress_callback("overall", total, total, "Complete!")

    return results
