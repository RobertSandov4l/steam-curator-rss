import email.utils
import json
import os
import re
import urllib.parse
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


def title_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 3 and parts[0] in ("app", "bundle", "sub"):
        slug = urllib.parse.unquote(parts[2]).replace("_", " ").replace("-", " ")
        return " ".join(slug.split())
    return "Título de Steam"


def unwrap_steam_external_url(href: str) -> str:
    if not href:
        return ""
    cleaned = href.strip()
    if cleaned.startswith("#") or cleaned.lower().startswith("javascript:"):
        return ""

    parsed = urllib.parse.urlparse(cleaned)
    host = parsed.netloc.lower()

    if "steamcommunity.com" in host and "/linkfilter" in parsed.path.lower():
        qs = urllib.parse.parse_qs(parsed.query)
        target = qs.get("u") or qs.get("url")
        if target and target[0]:
            return urllib.parse.unquote(target[0]).strip()
        return ""

    if "store.steampowered.com" in host or "steamcommunity.com" in host:
        return ""

    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        return cleaned
    return ""


def _find_external_link_in_node(node, game_link_tag) -> str:
    if node is None or not getattr(node, "select", None):
        return ""
    for a in node.select("a[href]"):
        if a is game_link_tag:
            continue
        real_url = unwrap_steam_external_url(a.get("href") or "")
        if real_url:
            return real_url
    return ""


def extract_single_review_data(link_tag) -> tuple[str, str]:
    single_container = None
    cur = link_tag.parent
    for _ in range(5):
        if cur is None or cur.name in ("[document]", "body", "html"):
            break
        game_links = cur.select("a[href*='/app/'], a[href*='/bundle/'], a[href*='/sub/']")
        if len(game_links) == 1:
            single_container = cur
        elif len(game_links) > 1:
            break
        cur = cur.parent

    if single_container is not None:
        text = " ".join(single_container.get_text(" ", strip=True).split())
        ext_link = _find_external_link_in_node(single_container, link_tag)
        if len(text) > 25:
            return text, ext_link

    parts = []
    ext_link = _find_external_link_in_node(link_tag.parent, link_tag)
    start_node = (
        link_tag.parent
        if link_tag.parent and link_tag.parent.name in ("td", "div", "li", "span")
        else link_tag
    )
    sib = start_node.find_next_sibling()
    steps = 0
    while sib is not None and steps < 12:
        steps += 1
        if getattr(sib, "select_one", None) and sib.select_one(
            "a[href*='/app/'], a[href*='/bundle/'], a[href*='/sub/']"
        ):
            break
        sib_text = " ".join(sib.get_text(" ", strip=True).split())
        if sib_text:
            parts.append(sib_text)
        if not ext_link:
            ext_link = _find_external_link_in_node(sib, link_tag)
        if "Leer la reseña completa" in sib_text:
            break
        sib = sib.find_next_sibling()

    return " ".join(parts).strip(), ext_link


def clean_review_quote(raw_text: str) -> str:
    quotes = re.findall(r'["“]([^"”]{10,})["”]', raw_text)
    if quotes:
        best_quote = max(quotes, key=len)
        return " ".join(best_quote.split()).strip()

    cleaned = raw_text
    cleaned = re.sub(
        r"El autor de la reseña recibió este producto de forma gratuita.*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"Leer la reseña completa\s*\[[^\]]*\]?",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"-\d+%\s*\d+\s*%\s*de descuento\.\s*Normalmente cuesta[^,]*,\s*con descuento es\s*[^\s]+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"#steam_curator_(?:notrecommended|informative|recommended)",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b(?:Not Recommended|No recomendado|Informational|Informativo|Recommended|Recomendado)\b",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\bFree to Play\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -|")
    return cleaned


def detect_verdict(text: str) -> str:
    lowered = text.lower()
    if "#steam_curator_notrecommended" in lowered or re.search(
        r"\b(not recommended|no recomendado)\b", lowered
    ):
        return "No recomendado"
    if "#steam_curator_informative" in lowered or re.search(
        r"\b(informational|informativo)\b", lowered
    ):
        return "Informativo"
    if "#steam_curator_recommended" in lowered or re.search(
        r"\b(recommended|recomendado)\b", lowered
    ):
        return "Recomendado"
    return ""


def parse_recommendations(curator_id: str, html_fragment: str) -> list[dict]:
    soup = BeautifulSoup(html_fragment, "html.parser")
    items = []

    link_tags = soup.select("a[href*='/app/'], a[href*='/bundle/'], a[href*='/sub/']")
    seen_links = set()
    now_rfc2822 = email.utils.formatdate(usegmt=True)

    for link_tag in link_tags:
        raw_href = link_tag.get("href", "").split("?")[0].strip()
        if not raw_href or raw_href in seen_links:
            continue
        seen_links.add(raw_href)

        game_title = " ".join(link_tag.get_text(" ", strip=True).split())
        if not game_title:
            img_tag = link_tag.select_one("img[alt]") or (
                link_tag.parent.select_one("img[alt]") if link_tag.parent else None
            )
            if img_tag and img_tag.get("alt", "").strip():
                game_title = img_tag["alt"].strip()
        if not game_title:
            game_title = title_from_url(raw_href)

        raw_review_text, ext_link = extract_single_review_data(link_tag)
        verdict = detect_verdict(raw_review_text)
        quote = clean_review_quote(raw_review_text)

        if ext_link:
            host = urllib.parse.urlparse(ext_link).netloc.lower().removeprefix("www.")
            label = "Juego Hispano" if "juegohispano.com" in host else (host or "Enlace externo")
            description = f"{quote}\n\nReseña completa: [{label}]({ext_link})".strip()
        else:
            description = quote

        title_with_verdict = f"[{verdict}] {game_title}" if verdict else game_title
        guid = f"steam-curator-{curator_id}-{re.sub(r'[^a-zA-Z0-9_-]+', '-', raw_href)}"

        items.append(
            {
                "title": title_with_verdict,
                "link": raw_href,
                "guid": guid,
                "description": description,
                "pub_date": now_rfc2822,
            }
        )

    return items


def build_rss_xml(
    channel_title: str, channel_link: str, channel_desc: str, items: list[dict]
) -> str:
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
