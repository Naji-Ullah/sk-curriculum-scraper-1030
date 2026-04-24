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


def parse_outcomes_page(html: str, curriculum_id: int) -> list[dict[str, Any]]:
    """Parse the outcomes overview page to get groups and outcome links."""
    soup = BeautifulSoup(html, "html.parser")
    groups = soup.find_all("div", class_="outcome-content-homepage__group")
    result = []

    for group in groups:
        header_div = group.find("div", class_="outcome_content_homepage__group__header")
        if not header_div:
            continue
        header_link = header_div.find("a")
        group_name = clean_text(header_link.get_text()) if header_link else clean_text(header_div.get_text())

        elements = group.find_all("div", class_="outcome-content-homepage__sub-list__element")
        for el in elements:
            code_div = el.find("div", class_="outcome-content-homepage__sub-list__element__identifier")
            text_div = el.find("div", class_="outcome-content-homepage__sub-list__element__text")
            if not code_div:
                continue

            code_link = code_div.find("a")
            code = clean_text(code_div.get_text())
            title = clean_text(text_div.get_text()) if text_div else ""

            href = ""
            if code_link and code_link.get("href"):
                href = code_link["href"]
                if not href.startswith("http"):
                    href = f"{BASE_URL}/{href}"

            result.append({
                "group": group_name,
                "code": code,
                "title": title,
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

    title_div = soup.find("div", class_="outcome_content_text")
    title = clean_text(title_div.get_text()) if title_div else ""

    code_div = soup.find("div", class_="outcome_content_identifier")
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
    """Determine the level label (Level 10, Level 20, Level 30) from outcome code."""
    level = extract_level_from_code(code)
    if level:
        return f"Level {level}"
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


async def scrape_curriculum(
    client: httpx.AsyncClient,
    curriculum_id: int,
    curriculum_name: str,
    progress_callback=None,
) -> dict[str, Any]:
    """Scrape a single curriculum's outcomes and indicators."""
    pdf_url = await get_pdf_url(client, curriculum_id)

    outcomes_url = f"{BASE_URL}/CurriculumOutcomeContent?id={curriculum_id}"
    logger.info(f"Fetching outcomes page: {outcomes_url}")

    try:
        outcomes_html = await fetch_page(client, outcomes_url)
    except Exception as e:
        logger.error(f"Failed to fetch outcomes for {curriculum_name}: {e}")
        return {curriculum_name: {"Pdf_url": pdf_url, "error": str(e)}}

    outcome_list = parse_outcomes_page(outcomes_html, curriculum_id)

    if not outcome_list:
        logger.warning(f"No outcomes found for {curriculum_name}")
        return {curriculum_name: {"Pdf_url": pdf_url, "note": "No outcomes found on the web page"}}

    detailed_outcomes = []
    total = len(outcome_list)
    for i, oc in enumerate(outcome_list):
        detail_url = oc.get("detail_url", "")
        if not detail_url:
            detailed_outcomes.append({
                "group": oc["group"],
                "code": oc["code"],
                "title": oc["title"],
                "indicators": [],
            })
            continue

        try:
            detail_html = await fetch_page(client, detail_url)
            detail = parse_outcome_detail_full(detail_html)

            detailed_outcomes.append({
                "group": oc["group"],
                "code": detail.get("code") or oc["code"],
                "title": detail.get("title") or oc["title"],
                "indicators": detail.get("indicators", []),
            })
        except Exception as e:
            logger.error(f"Failed to fetch detail for {oc['code']}: {e}")
            detailed_outcomes.append({
                "group": oc["group"],
                "code": oc["code"],
                "title": oc["title"],
                "indicators": [],
                "error": str(e),
            })

        if progress_callback and (i + 1) % 5 == 0:
            progress_callback(curriculum_name, i + 1, total)

        await asyncio.sleep(0.3)

    levels = organize_by_level(detailed_outcomes, curriculum_name)

    result: dict[str, Any] = {curriculum_name: {"Pdf_url": pdf_url}}

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

        result[curriculum_name][level_label] = {"Outcomes": level_outcomes}

    return result


async def scrape_all(progress_callback=None) -> list[dict[str, Any]]:
    """Scrape all Level 10/20/30 curricula."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    results = []
    skipped = []

    active_curricula = [
        c for c in CURRICULA
        if c.get("id") is not None and not c.get("skip") and not c.get("treaty")
    ]

    async with httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": "SK-Curriculum-Scraper/1.0"},
    ) as client:
        total = len(active_curricula)
        for i, curr in enumerate(active_curricula):
            name = curr["name"]
            cid = curr["id"]
            logger.info(f"[{i+1}/{total}] Scraping: {name} (id={cid})")

            if progress_callback:
                progress_callback(
                    "overall", i, total, f"Scraping: {name}"
                )

            try:
                result = await scrape_curriculum(client, cid, name, progress_callback)
                results.append(result)

                filename = re.sub(r"[^\w\s-]", "", name).strip()
                filename = re.sub(r"\s+", " ", filename)
                output_path = OUTPUT_DIR / f"{filename}.json"
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(result, f, indent=4, ensure_ascii=False)

                logger.info(f"Saved: {output_path}")

            except Exception as e:
                logger.error(f"Failed to scrape {name}: {e}")
                results.append({name: {"error": str(e)}})

            await asyncio.sleep(0.5)

    if progress_callback:
        progress_callback("overall", total, total, "Complete!")

    return results
