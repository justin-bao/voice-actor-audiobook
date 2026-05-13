import os
import re
import time
import argparse
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


MAIN_CHAPTER_START = re.compile(r"^第[一二三四五六七八九十百千万\d]+章")
SUPPLEMENTARY_STOP_WORDS = ("完结", "第五季", "在线阅读", "全文阅读")
NAVIGATION_MARKERS = (
    "上一章",
    "下一章",
    "返回目录",
    "章节目录",
    "超禁忌游戏上一章",
    "超禁忌游戏下一章",
)


def is_main_chapter_start(title):
    return bool(MAIN_CHAPTER_START.search(title.strip()))


def is_post_book_supplement(title):
    normalized = title.strip()
    if "大结局" in normalized and is_main_chapter_start(normalized):
        return False
    return any(word in normalized for word in SUPPLEMENTARY_STOP_WORDS)


def filter_main_chapter_run(links):
    """Keep the contiguous story run from 第一章 through the final main chapter.

    Kongbugushi indexes include landing pages before chapter one and sometimes
    post-book/next-season pages after the ending. Some continuation pages have
    ugly titles like "第十九章 5" or "第二季）", so filtering only by title would
    accidentally drop real story text. Once chapter one starts, keep the run
    until the first obvious post-book supplement.
    """
    filtered = []
    in_story = False
    for title, url in links:
        title = title.strip()
        if not in_story:
            if title == "第一章" or is_main_chapter_start(title):
                in_story = True
            else:
                continue
        if is_post_book_supplement(title):
            break
        filtered.append((title, url))
    return filtered


def clean_chapter_text(title, raw_text):
    """Remove site navigation/link text and keep only the chapter body.

    Kongbugushi chapter pages put previous/next links before the story body.
    BeautifulSoup's get_text() turns those links into plain text, so stripping
    anchors alone is not enough; we remove the marker line and the linked
    chapter title that follows it.
    """
    lines = [line.strip() for line in raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    cleaned = []
    skip_next_nonempty = 0
    seen_body = False

    for line in lines:
        if not line:
            if seen_body and cleaned and cleaned[-1] != "":
                cleaned.append("")
            continue

        if not seen_body and line == title.strip():
            continue

        if is_navigation_marker(line):
            skip_next_nonempty = max(skip_next_nonempty, 1)
            continue

        if skip_next_nonempty:
            skip_next_nonempty -= 1
            continue

        if is_site_boilerplate(line):
            continue

        seen_body = True
        cleaned.append(line)

    while cleaned and cleaned[-1] == "":
        cleaned.pop()
    return "\n\n".join(cleaned)


def is_navigation_marker(line):
    normalized = line.strip().rstrip("：:")
    return any(normalized == marker for marker in NAVIGATION_MARKERS)


def is_site_boilerplate(line):
    boilerplate_patterns = (
        "最新网址",
        "手机阅读",
        "加入书签",
        "返回书页",
        "恐怖故事",
        "kongbugushi",
        "www.",
    )
    return any(pattern in line for pattern in boilerplate_patterns)


def scrape_book(base_url, output_dir, main_chapters_only=True, clean_text=True):
    
    # Create the target directory
    os.makedirs(output_dir, exist_ok=True)

    # Headers to mimic a browser, preventing simple 403 Forbidden blocks
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    print(f"Fetching index from {base_url}...")
    response = requests.get(base_url, headers=headers)
    response.encoding = response.apparent_encoding
    soup = BeautifulSoup(response.text, 'html.parser')

    links = []
    
    # Find all anchor tags. Novel indexes typically link to relative '.html' files.
    for a in soup.find_all('a', href=True):
        href = a['href']
        # Filter for typical chapter link patterns (e.g., "12345.html" or similar relative paths)
        if re.match(r'^\d+\.html$', href) or ('chaojinjiyouxi' in href and href != base_url.split('/')[-1]):
            url = urljoin(base_url, href)
            title = a.get_text(strip=True)
            if title and url not in [l[1] for l in links]:
                links.append((title, url))

    # Fallback if the heuristic above didn't catch the links
    if not links:
        for a in soup.find_all('a', href=True):
            href = a['href']
            if href.endswith('.html') and not href.startswith('http'):
                url = urljoin(base_url, href)
                title = a.get_text(strip=True)
                if title and url not in [l[1] for l in links]:
                    links.append((title, url))

    print(f"Found {len(links)} links.")
    if main_chapters_only:
        original_count = len(links)
        links = filter_main_chapter_run(links)
        print(f"Filtered to {len(links)} main story pages (removed {original_count - len(links)}).")
    print("Starting download...")

    for i, (title, link) in enumerate(links):
        try:
            print(f"[{i+1}/{len(links)}] Fetching: {title}...")
            ch_resp = requests.get(link, headers=headers, timeout=10)
            ch_resp.encoding = ch_resp.apparent_encoding
            ch_soup = BeautifulSoup(ch_resp.text, 'html.parser')

            # Look for common div ids/classes used to hold chapter text on Chinese novel sites
            content_div = ch_soup.find('div', id='content') or ch_soup.find('div', class_='content') or ch_soup.find('div', id='book_text')
            
            text = content_div.get_text(separator='\n\n', strip=True) if content_div else ch_soup.body.get_text(separator='\n\n', strip=True)
            if clean_text:
                text = clean_chapter_text(title, text)

            # Clean the title to ensure it's a valid filename
            safe_title = re.sub(r'[\\/*?:"<>|]', "", title)
            filename = f"{i+1:03d}_{safe_title}.txt"
            filepath = os.path.join(output_dir, filename)

            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(f"{title}\n\n{text}")

            # Be polite to the server to avoid getting IP banned
            time.sleep(1.5) 

        except Exception as e:
            print(f"Failed to scrape {link}: {e}")

    print(f"\nDone! Extracted chapters are saved in the '{output_dir}' folder.")


def parse_args():
    parser = argparse.ArgumentParser(description="Scrape Kongbugushi chapters.")
    parser.add_argument(
        "--base-url",
        default="http://www.kongbugushi.com/chaojinjiyouxi/chaojinjiyouxi4.html",
    )
    parser.add_argument("--output-dir", default="chaojinjiyouxi4")
    parser.add_argument(
        "--include-supplementary",
        action="store_true",
        help="Download every discovered link instead of only the main chapter run.",
    )
    parser.add_argument(
        "--raw-page-text",
        action="store_true",
        help="Keep page navigation text instead of extracting just chapter body text.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    scrape_book(
        base_url=args.base_url,
        output_dir=args.output_dir,
        main_chapters_only=not args.include_supplementary,
        clean_text=not args.raw_page_text,
    )
