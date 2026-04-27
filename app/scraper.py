"""Scraper for Saskatchewan Curriculum Levels 10, 20, 30."""

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup, Tag

from app.curriculum_config import BASE_URL, CURRICULA

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("scraped_data")


def clean_text(text: str) -> str:
    """Clean whitespace from scraped text while preserving meaningful content."""
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_level_from_code(code: str) -> str | None:
    """Extract level (10, 20, 30) from outcome code like CP10.1 or BI30-SDS1."""
    match = re.search(r"(\d{2})", code)
    if match:
        level = match.group(1)
        if level in ("10", "20", "30"):
            return level
    return None


async def fetch_page(client: httpx.AsyncClient, url: str, retries: int = 3) -> str:
    """Fetch a page with retries."""
    for attempt in range(retries):
        try:
            resp = await client.get(url, timeout=60.0)
            resp.raise_for_status()
            return resp.text
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            if attempt == retries - 1:
                raise
            logger.warning(f"Retry {attempt + 1} for {url}: {e}")
            await asyncio.sleep(2 * (attempt + 1))
    return ""


async def get_pdf_url(client: httpx.AsyncClient, curriculum_id: int) -> str:
    """Get the PDF URL for a curriculum."""
    return f"{BASE_URL}/CurriculumFile?id={curriculum_id}"


def _make_full_url(href: str) -> str:
    """Ensure a href is a full URL."""
    if href and not href.startswith("http"):
        return f"{BASE_URL}/{href}"
    return href


def _parse_sub_list_elements(container, group_name: str) -> list[dict[str, Any]]:
    """Parse outcome-content-homepage__sub-list__element divs."""
    result = []
    elements = container.find_all("div", class_="outcome-content-homepage__sub-list__element")
    for el in elements:
        code_div = el.find("div", class_="outcome-content-homepage__sub-list__element__identifier")
        text_div = el.find("div", class_="outcome-content-homepage__sub-list__element__text")
        if not code_div:
            continue

        code_link = code_div.find("a")
        code = clean_text(code_div.get_text())
        title = clean_text(text_div.get_text()) if text_div else ""
        href = _make_full_url(code_link["href"]) if code_link and code_link.get("href") else ""

        result.append({
            "group": group_name,
            "code": code,
            "title": title,
            "detail_url": href,
        })
    return result


def parse_outcomes_page(html: str, curriculum_id: int) -> list[dict[str, Any]]:
    """Parse the outcomes overview page to get groups and outcome links.

    Handles three page structures:
    - Type A: Groups with sub-list elements containing code + title
    - Type B: Groups where the header link IS the outcome (empty sub-lists)
    - Type C: Flat list of cd_web_menu_item or sub-list elements with no groups
    """
    soup = BeautifulSoup(html, "html.parser")
    result = []

    # First, try Type A: groups with sub-list elements inside
    groups = soup.find_all("div", class_="outcome-content-homepage__group")
    if groups:
        for group in groups:
            header_div = group.find("div", class_="outcome_content_homepage__group__header")
            if not header_div:
                continue
            header_link = header_div.find("a")
            group_name = clean_text(header_link.get_text()) if header_link else clean_text(header_div.get_text())

            sub_elements = _parse_sub_list_elements(group, group_name)

            if sub_elements:
                # Type A: normal sub-list elements inside groups
                result.extend(sub_elements)
            elif header_link and header_link.get("href") and "oc=" in header_link.get("href", ""):
                # Type B: group header itself is the outcome link
                href = _make_full_url(header_link["href"])
                result.append({
                    "group": "",
                    "code": group_name,
                    "title": "",
                    "detail_url": href,
                })

    # If we found outcomes from groups, return them
    if result:
        return result

    # Type C fallback: check for sub-list elements directly in the page (not inside groups)
    homepage_list = soup.find("div", class_="outcome-content-homepage__list")
    if homepage_list:
        sub_elements = _parse_sub_list_elements(homepage_list, "")
        if sub_elements:
            return sub_elements

    # Type C fallback: flat list of cd_web_menu_item links
    menu_items = soup.find_all("div", class_="cd_web_menu_item")
    for item in menu_items:
        link = item.find("a", href=lambda h: h and "oc=" in h)
        if link:
            code = clean_text(link.get_text())
            href = _make_full_url(link["href"])
            result.append({
                "group": "",
                "code": code,
                "title": "",
                "detail_url": href,
            })

    return result


def parse_outcome_detail(html: str) -> list[str]:
    """Parse an outcome detail page to extract indicators."""
    soup = BeautifulSoup(html, "html.parser")
    indicators = []

    indicator_table = soup.find("table", class_="outcome_content_child_list")
    if indicator_table:
        rows = indicator_table.find_all("tr", class_="outcome_content_child")
        for row in rows:
            id_cell = row.find("td", class_="outcome_content_child_identifier")
            text_cell = row.find("td", class_="indicator_child_text")
            if id_cell and text_cell:
                letter = clean_text(id_cell.get_text())
                text = clean_text(text_cell.get_text())
                indicators.append(f"{letter} {text}")
            elif text_cell:
                text = clean_text(text_cell.get_text())
                if text:
                    indicators.append(text)

    if not indicators:
        child_list = soup.find("div", class_="outcome_content_child_list")
        if child_list:
            items = child_list.find_all("li")
            for item in items:
                text = clean_text(item.get_text())
                if text:
                    indicators.append(text)

    return indicators


def parse_outcome_detail_full(html: str) -> dict[str, Any]:
    """Parse an outcome detail page to extract title and indicators with sub-headings."""
    soup = BeautifulSoup(html, "html.parser")

    # PAA module pages: combine module name + actual outcome text as title.
    # e.g. "Module 1: Introduction to Accounting\nInvestigate the need for accounting in business."
    title = ""
    module_name_div = soup.find("div", class_="outcome_content_module_name")
    if module_name_div:
        module_name = clean_text(module_name_div.get_text())
        outcome_text = ""
        child_list_div = soup.find("div", class_="outcome_content_child_list")
        if child_list_div:
            for child in child_list_div.children:
                if not hasattr(child, "get"):
                    continue
                cls = " ".join(child.get("class") or [])
                if "outcome_content_child_list_header" in cls:
                    if clean_text(child.get_text()).lower() == "outcome":
                        continue
                    break
                text = clean_text(child.get_text())
                if text:
                    outcome_text = text
                    break
        if module_name and outcome_text:
            title = f"{module_name}\n{outcome_text}"
        else:
            title = module_name or outcome_text
    if not title:
        title_div = soup.find("div", class_="outcome_content_text")
        title = clean_text(title_div.get_text()) if title_div else ""

    code_div = soup.find("div", class_="outcome_content_identifier")
    if not code_div:
        header = soup.find("div", class_="content_section_header")
        if header:
            header_text = clean_text(header.get_text())
            if re.match(r"^[A-Z]+\d", header_text):
                code_div = header
    code = clean_text(code_div.get_text()) if code_div else ""

    indicators = []
    indicator_table = soup.find("table", class_="outcome_content_child_list")
    if indicator_table:
        rows = indicator_table.find_all("tr", class_="outcome_content_child")
        for row in rows:
            id_cell = row.find("td", class_="outcome_content_child_identifier")
            text_cell = row.find("td", class_="indicator_child_text")
            if text_cell:
                letter = clean_text(id_cell.get_text()) if id_cell else ""
                text_parts = []
                for child in text_cell.children:
                    if isinstance(child, Tag):
                        if child.name == "ul":
                            for li in child.find_all("li"):
                                text_parts.append("• " + clean_text(li.get_text()))
                        elif child.name == "ol":
                            for i, li in enumerate(child.find_all("li"), 1):
                                text_parts.append(f"{i}. " + clean_text(li.get_text()))
                        elif child.name == "br":
                            continue
                        else:
                            t = clean_text(child.get_text())
                            if t:
                                text_parts.append(t)
                    else:
                        t = clean_text(str(child))
                        if t:
                            text_parts.append(t)

                full_text = " ".join(text_parts) if text_parts else clean_text(text_cell.get_text())
                if letter:
                    indicators.append(f"{letter} {full_text}")
                elif full_text:
                    indicators.append(full_text)

    if not indicators:
        child_list = soup.find("div", class_="outcome_content_child_list")
        if child_list:
            items = child_list.find_all("li")
            for item in items:
                text = clean_text(item.get_text())
                if text:
                    indicators.append(text)

    return {
        "code": code,
        "title": title,
        "indicators": indicators,
    }


def determine_level_label(code: str, curriculum_name: str) -> str:
    """Determine the level label (Level 10, Level 20, Level 30) from outcome code or curriculum name."""
    level = extract_level_from_code(code)
    if level:
        return f"Level {level}"
    # Fall back to extracting level from curriculum name for single-level curricula
    # e.g., "Economics 20" -> "Level 20", but NOT "Arts Education 10, 20, 30"
    level_matches = re.findall(r"\b(10|20|30)\b", curriculum_name)
    if len(level_matches) == 1:
        return f"Level {level_matches[0]}"
    return "Outcomes"


def organize_by_level(
    outcomes_data: list[dict[str, Any]], curriculum_name: str
) -> dict[str, list[dict[str, Any]]]:
    """Organize outcomes by level."""
    levels: dict[str, list[dict[str, Any]]] = {}

    for outcome in outcomes_data:
        code = outcome.get("code", "")
        level_label = determine_level_label(code, curriculum_name)
        if level_label not in levels:
            levels[level_label] = []
        levels[level_label].append(outcome)

    return levels


def extract_subject_base_name(curriculum_name: str) -> str:
    """Extract the base subject name without level numbers.

    e.g. 'Arts Education 10, 20, 30' -> 'Arts Education'
         'Biology 30' -> 'Biology'
         'Instrumental Jazz 10' -> 'Instrumental Jazz'
         'History 30: Canadian Studies' -> 'History: Canadian Studies'
         'Accounting 10, 20, 30' -> 'Accounting'
         'Agriculture Production A10, B10, A20, B20, A30, B30' -> 'Agriculture Production'
         'Autobody 10, A20, B20, A30, B30' -> 'Autobody'
         'Electrical and Electronics 10, Electrical 20, 30, Electronics 20, A30, B30'
            -> 'Electrical and Electronics'
    """
    name = re.sub(r"\s*\(.*?\)\s*$", "", curriculum_name)
    # Handle complex PAA names with sub-disciplines (e.g., "Electrical and Electronics 10, Electrical 20, 30, Electronics 20, A30, B30")
    # Remove everything from the first level number onwards
    name = re.sub(r"[\s,]+[AB]?\d{1,2}(?:[\s,]+(?:[A-Za-z]+\s+)?[AB]?\d{1,2})*\s*$", "", name)
    # Handle "History 30: Canadian Studies" — level before a colon
    name = re.sub(r"\s+\d{1,2}(?=\s*:)", "", name)
    # Clean up any resulting double spaces or trailing commas/spaces
    name = re.sub(r"\s+", " ", name).strip().rstrip(",")
    return name


async def scrape_curriculum(
    client: httpx.AsyncClient,
    curriculum_id: int,
    curriculum_name: str,
    progress_callback=None,
    modular: bool = False,
) -> list[dict[str, Any]]:
    """Scrape a single curriculum's outcomes and indicators.

    Returns a list of per-level result dicts, each structured as:
    {subject_name: {"Pdf_url": ..., "Level XX": {"Outcomes": [...]}}}
    """
    pdf_url = await get_pdf_url(client, curriculum_id)

    outcomes_url = f"{BASE_URL}/CurriculumOutcomeContent?id={curriculum_id}"
    logger.info(f"Fetching outcomes page: {outcomes_url}")

    try:
        outcomes_html = await fetch_page(client, outcomes_url)
    except Exception as e:
        logger.error(f"Failed to fetch outcomes for {curriculum_name}: {e}")
        return [{curriculum_name: {"Pdf_url": pdf_url, "error": str(e)}}]

    outcome_list = parse_outcomes_page(outcomes_html, curriculum_id)

    if not outcome_list:
        logger.warning(f"No outcomes found for {curriculum_name}")
        return [{curriculum_name: {"Pdf_url": pdf_url, "note": "No outcomes found on the web page"}}]

    async def fetch_detail(oc: dict) -> dict:
        detail_url = oc.get("detail_url", "")
        if not detail_url:
            return {
                "group": oc["group"],
                "code": oc["code"],
                "title": oc["title"],
                "indicators": [],
            }
        try:
            detail_html = await fetch_page(client, detail_url)
            detail = parse_outcome_detail_full(detail_html)
            return {
                "group": oc["group"],
                "code": detail.get("code") or oc["code"],
                "title": detail.get("title") or oc["title"],
                "indicators": detail.get("indicators", []),
            }
        except Exception as e:
            logger.error(f"Failed to fetch detail for {oc['code']}: {e}")
            return {
                "group": oc["group"],
                "code": oc["code"],
                "title": oc["title"],
                "indicators": [],
                "error": str(e),
            }

    # Fetch detail pages in concurrent batches of 5
    detailed_outcomes = []
    total = len(outcome_list)
    batch_size = 5
    for batch_start in range(0, total, batch_size):
        batch = outcome_list[batch_start:batch_start + batch_size]
        batch_results = await asyncio.gather(*[fetch_detail(oc) for oc in batch])
        detailed_outcomes.extend(batch_results)

        if progress_callback:
            progress_callback(curriculum_name, min(batch_start + batch_size, total), total)

        await asyncio.sleep(0.1)

    if modular:
        label = _modular_level_label(curriculum_name)
        levels = {label: detailed_outcomes}
    else:
        levels = organize_by_level(detailed_outcomes, curriculum_name)
    base_name = extract_subject_base_name(curriculum_name)

    per_level_results = []
    for level_label, outcomes in levels.items():
        level_outcomes = []
        for oc in outcomes:
            outcome_entry: dict[str, Any] = {
                "code": oc["code"],
                "title": oc["title"],
                "indicators": oc["indicators"],
            }
            if oc.get("error"):
                outcome_entry["error"] = oc["error"]
            level_outcomes.append(outcome_entry)

        level_num = level_label.replace("Level ", "") if level_label.startswith("Level ") else ""
        if modular:
            file_subject_name = base_name
        elif level_num:
            file_subject_name = f"{base_name} {level_num}"
        else:
            file_subject_name = curriculum_name

        result: dict[str, Any] = {
            file_subject_name: {
                "Pdf_url": pdf_url,
                level_label: {"Outcomes": level_outcomes},
            }
        }
        per_level_results.append(result)

    return per_level_results


def _modular_level_label(curriculum_name: str) -> str:
    """Build a combined level label for modular (PAA) curricula from the curriculum name.

    e.g. 'Accounting 10, 20, 30' -> 'Level 10, 20, 30'
         'Agribusiness 30' -> 'Level 30'
         'Agriculture Production A10, B10, A20, B20, A30, B30' -> 'Level 10, 20, 30'
    """
    level_matches = re.findall(r"[AB]?(\d{2})", curriculum_name)
    unique_levels = sorted(set(lv for lv in level_matches if lv in ("10", "20", "30")))
    if unique_levels:
        return "Level " + ", ".join(unique_levels)
    return "Outcomes"


def _filename_for_subject(subject_name: str) -> str:
    """Generate a sanitized filename for a subject."""
    filename = re.sub(r"[^\w\s-]", "", subject_name).strip()
    return re.sub(r"\s+", " ", filename)


def _get_existing_files() -> set[str]:
    """Return set of existing JSON filenames (without extension) in the output dir."""
    if not OUTPUT_DIR.exists():
        return set()
    return {f.stem for f in OUTPUT_DIR.glob("*.json")}


async def scrape_all(progress_callback=None, skip_existing: bool = False) -> list[dict[str, Any]]:
    """Scrape all Level 10/20/30 curricula, producing one JSON file per level.

    If skip_existing=True, curricula whose JSON files already exist are skipped.
    """
    OUTPUT_DIR.mkdir(exist_ok=True)
    results = []

    active_curricula = [
        c for c in CURRICULA
        if c.get("id") is not None and not c.get("skip") and not c.get("treaty")
    ]

    existing_files = _get_existing_files() if skip_existing else set()

    async with httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": "SK-Curriculum-Scraper/1.0"},
    ) as client:
        total = len(active_curricula)
        for i, curr in enumerate(active_curricula):
            name = curr["name"]
            cid = curr["id"]

            if progress_callback:
                progress_callback(
                    "overall", i, total, f"Scraping: {name}"
                )

            # Check if this curriculum's files already exist
            if skip_existing and _should_skip(name, curr, existing_files):
                logger.info(f"[{i+1}/{total}] Skipping (already scraped): {name}")
                if progress_callback:
                    progress_callback(
                        "overall", i, total, f"Skipped (exists): {name}"
                    )
                continue

            logger.info(f"[{i+1}/{total}] Scraping: {name} (id={cid})")

            try:
                per_level_results = await scrape_curriculum(
                    client, cid, name, progress_callback,
                    modular=curr.get("modular", False),
                )

                for result in per_level_results:
                    results.append(result)
                    subject_name = list(result.keys())[0]
                    filename = _filename_for_subject(subject_name)
                    output_path = OUTPUT_DIR / f"{filename}.json"
                    with open(output_path, "w", encoding="utf-8") as f:
                        json.dump(result, f, indent=4, ensure_ascii=False)
                    logger.info(f"Saved: {output_path}")

            except Exception as e:
                logger.error(f"Failed to scrape {name}: {e}")
                results.append({name: {"error": str(e)}})

            await asyncio.sleep(0.2)

    if progress_callback:
        progress_callback("overall", total, total, "Complete!")

    return results


def _should_skip(
    curriculum_name: str,
    curr: dict[str, Any],
    existing_files: set[str],
) -> bool:
    """Check if a curriculum's output files already exist."""
    is_modular = curr.get("modular", False)
    if is_modular:
        base_name = extract_subject_base_name(curriculum_name)
        filename = _filename_for_subject(base_name)
        return filename in existing_files

    base_name = extract_subject_base_name(curriculum_name)
    level_matches = re.findall(r"\b(10|20|30)\b", curriculum_name)
    if len(level_matches) <= 1:
        filename = _filename_for_subject(f"{base_name} {level_matches[0]}" if level_matches else curriculum_name)
        return filename in existing_files

    return all(
        _filename_for_subject(f"{base_name} {lvl}") in existing_files
        for lvl in level_matches
    )
