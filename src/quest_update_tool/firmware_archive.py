import re
from dataclasses import dataclass
from typing import List, Optional

import requests
from bs4 import BeautifulSoup, Tag


BASE_URL = "https://cocaine.trade"


@dataclass
class FirmwareLink:
    incremental: str
    version: str
    runtime_version: str
    build_date: str
    fingerprint: str
    sha256: str
    href: str
    version_num: int


META_DEVICE_PAGES = {
    "Quest": "Quest_firmware",
    "Quest 2": "Quest_2_firmware",
    "Quest Pro": "Quest_Pro_firmware",
    "Quest 3": "Quest_3_firmware",
    "Quest 3S": "Quest_3S_firmware",
    "Ray-Ban Display": "Ray-Ban_Display_firmware",
}


KINDLE_DEVICE_PAGES = {
    "KS2": "KS2_firmware",
    "KS": "KS_firmware",
    "CS": "CS_firmware",
    "PW6": "PW6_firmware",
    "KT6": "KT6_firmware",
    "KT5": "KT5_firmware",
    "PW5": "PW5_firmware",
    "KOA3": "KOA3_firmware",
    "KT4": "KT4_firmware",
    "PW4": "PW4_firmware",
    "KOA2": "KOA2_firmware",
    "KT3": "KT3_firmware",
    "KOA": "KOA_firmware",
    "PW3": "PW3_firmware",
    "KV": "KV_firmware",
    "KT2": "KT2_firmware",
    "PW2": "PW2_firmware",
    "Legacy": "Legacy_firmware",
}


def get_firmware_page_for_device(device_name: str) -> Optional[str]:
    """
    Вернуть slug страницы прошивок для указанного устройства.
    Сначала ищем среди Meta, затем среди Kindle.
    """
    if device_name in META_DEVICE_PAGES:
        return META_DEVICE_PAGES[device_name]
    if device_name in KINDLE_DEVICE_PAGES:
        return KINDLE_DEVICE_PAGES[device_name]
    return None


def _extract_version_num_from_tag(link: Tag) -> int:
    href = link.get("href")
    text_content = link.get_text() or ""
    text = f"{href or ''} {text_content}"
    nums = re.findall(r"\d+", text)
    return int(nums[-1]) if nums else -1


def fetch_firmware_links(page_slug: str, timeout: float = 15.0) -> List[FirmwareLink]:
    """
    Загрузить страницу прошивок и вернуть список структурированных ссылок.
    Бросает requests.RequestException при сетевых ошибках.
    """
    url = f"{BASE_URL}/{page_slug}"
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()

    bs: BeautifulSoup = BeautifulSoup(resp.text, "lxml")
    table = bs.find("table")
    if table is None:
        return []

    rows = table.find_all("tr")
    links: List[FirmwareLink] = []

    # пропускаем заголовок
    for row in rows[1:]:
        cells = row.find_all("td")
        if len(cells) < 6:
            continue

        # Структура таблицы:
        # 0 - Incremental (ссылка)
        # 1 - Version
        # 2 - Runtime Version
        # 3 - Build Date
        # 4 - Fingerprint
        # 5 - SHA-256
        inc_link = cells[0].find("a", class_="fw-link")
        if inc_link is None:
            continue

        incremental = inc_link.get_text(strip=True)
        href_val = inc_link.get("href")
        href = str(href_val).strip() if href_val is not None else ""

        version = cells[1].get_text(strip=True)
        runtime_version = cells[2].get_text(strip=True)
        build_date = cells[3].get_text(strip=True)
        fingerprint = cells[4].get_text(strip=True)
        sha256 = cells[5].get_text(strip=True)

        # Используем числовую часть incremental как основу для сортировки
        nums = re.findall(r"\d+", incremental)
        version_num = int(nums[0]) if nums else 0

        if href:
            links.append(
                FirmwareLink(
                    incremental=incremental,
                    version=version,
                    runtime_version=runtime_version,
                    build_date=build_date,
                    fingerprint=fingerprint,
                    sha256=sha256,
                    href=href,
                    version_num=version_num,
                )
            )
    return links


def sort_firmware_links_by_version(links: List[FirmwareLink]) -> List[FirmwareLink]:
    """
    Отсортировать ссылки по номеру версии (убывание).
    """
    return sorted(links, key=lambda link: link.version_num, reverse=True)


def choose_latest_firmware(links: List[FirmwareLink]) -> Optional[FirmwareLink]:
    """
    Выбрать самую новую прошивку из списка.
    """
    if not links:
        return None
    return max(links, key=lambda link: link.version_num)

