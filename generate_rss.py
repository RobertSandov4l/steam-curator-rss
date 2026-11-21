import email.utils
import json
import os
import re
import urllib.request
from xml.sax.saxutils import escape
from bs4 import BeautifulSoup


def fetch_curator_html(curator_id: str, count: int = 15) -> str:
    url = (
        f"https://store.steampowered.com/curator/{curator_id}/"
        f"ajaxgetcuratorrecommendations/render/?query=&start=0&count={count}"
    )
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "es-419,es;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload.get("results_html", "")


def parse_recommendations(curator_id: str, html_fragment: str) -> list[dict]:
    soup = BeautifulSoup(html_fragment, "html.parser")
    items = []

    candidates = soup.select(".recommendation_row")
    if not candidates:
        candidates = [
            el.find_parent("div")
            for el in soup.select("a[href*='/app/'], a[href*='/bundle/'], a[href*='/sub/']")
            if el.find_parent("div")
        ]

    seen_links = set()
    now_rfc2822 = email.utils.formatdate(usegmt=True)

    for block in candidates:
        if block is None:
            continue
        link_tag = block.select_one("a[href*='/app/'], a[href*='/bundle/'], a[href*='/sub/']")
        if not link_tag:
            continue

        raw_href = link_tag.get("href", "").split("?")[0].strip()
        if not raw_href or raw_href in seen_links:
            continue
        seen_links.add(raw_href)

        game_title = " ".join(link_tag.get_text(" ", strip=True).split())
        if not game_title:
            img_tag = block.select_one("img[alt]")
            game_title = img_tag["alt"].strip() if img_tag else "Título de Steam"

        full_text = " ".join(block.get_text(" ", strip=True).split())

        verdict = ""
        for label in ("Recommended", "Not Recommended", "Informational", "Recomendado", "No recomendado", "Informativo"):
            if re.search(rf"\b{re.escape(label)}\b", full_text, flags=re.IGNORECASE):
                verdict = label
                break

        title_with_verdict = f"[{verdict}] {game_title}" if verdict else game_title
        guid = f"steam-curator-{curator_id}-{re.sub(r'[^a-zA-Z0-9_-]+', '-', raw_href)}"

        items.append(
            {
                "title": title_with_verdict,
                "link": raw_href,
                "guid": guid,
                "description": full_text,
                "pub_date": now_rfc2822,
            }
        )

    return items


def build_rss_xml(channel_title: str, channel_link: str, channel_desc: str, items: list[dict]) -> str:
    xml_items = []
    for item in items:
        xml_items.append(
            f"""    <item>
      <title>{escape(item['title'])}</title>
      <link>{escape(item['link'])}</link>
      <guid isPermaLink="false">{escape(item['guid'])}</guid>
      <pubDate>{escape(item['pub_date'])}</pubDate>
      <description>{escape(item['description'])}</description>
    </item>"""
        )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>{escape(channel_title)}</title>
    <link>{escape(channel_link)}</link>
    <description>{escape(channel_desc)}</description>
{chr(10).join(xml_items)}
  </channel>
</rss>
"""


def main() -> None:
    raw_ids = os.environ.get("CURATOR_IDS", "").strip()
    curator_ids = [cid.strip() for cid in raw_ids.split(",") if cid.strip()]
    if not curator_ids:
        raise RuntimeError("La variable de entorno CURATOR_IDS está vacía.")

    for cid in curator_ids:
        html_fragment = fetch_curator_html(cid, count=15)
        items = parse_recommendations(cid, html_fragment)

        single_xml = build_rss_xml(
            channel_title=f"Steam Curator {cid}",
            channel_link=f"https://store.steampowered.com/curator/{cid}/",
            channel_desc=f"Feed RSS individual para el mentor de Steam {cid}.",
            items=items,
        )
        with open(f"feed_{cid}.xml", "w", encoding="utf-8") as f:
            f.write(single_xml)


if __name__ == "__main__":
    main()
