#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
scrape_umamusume_skills.py

Final skills scraper that supports two sources:

1) JSON mode (recommended):
   --url-json https://gametora.com/data/umamusume/skills.XXXXXXXX.json
   (copy from DevTools → Network → XHR → skills.*.json)

2) HTML mode (fallback):
   --url-html https://gametora.com/umamusume/skills  (or --html-file saved page)
   If the page is client-rendered and contains no rows, the script will
   auto-discover /data/umamusume/skills.*.json in the HTML and use it.

Output schema per skill:
{
  "id": int or null,
  "icon_filename": "utx_ico_skill_10021.png",
  "icon_src": "https://gametora.com/images/umamusume/skill_icons/utx_ico_skill_10021.png",
  "name": "Skill Name ○",
  "description": "What the skill does...",
  "color_class": "dnlGQR|geDDHx|bhlwbP",
  "rarity": "normal|gold|unique|inherited",
  "grade_symbol": "◎|○|null"
}
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup, Tag

BASE_URL = "https://gametora.com"
ICON_BASE_URL = f"{BASE_URL}/images/umamusume/skill_icons/"
DEFAULT_IMAGES_DIR = Path("..") / "web" / "public" / "icons" / "skills"

# -------------------- Debug helper --------------------
def dbg(on: bool, *args, **kwargs):
    if on:
        print(*args, file=sys.stderr, **kwargs)

# -------------------- JSON-mode rarity ↔ color mapping --------------------
RARITY_MAP_JSON = {
    1: ("normal", "dnlGQR"),  # gray
    2: ("gold",   "geDDHx"),  # gold
    3: ("unique", "bhlwbP"),  # unique
    "inherited": ("inherited", "dnlGQR"),
}

# -------------------- HTML-mode color → rarity (best effort) ----------------
RARITY_BY_COLOR_CLASS_HTML = {
    "geDDHx": "gold",
    "bhlwbP": "unique",
    # default: "normal"
}

# -------------------- Name symbol helpers --------------------
CIRCLE_SYMBOLS = ("◎", "○")

def grade_symbol_from_name(name: str) -> Optional[str]:
    name = (name or "").strip()
    for sym in CIRCLE_SYMBOLS:
        if name.endswith(sym):
            return sym
    return None

def _coerce_int(v: Any) -> Optional[int]:
    try:
        if v is None or isinstance(v, bool):
            return None
        return int(v)
    except Exception:
        return None

def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)

def download_icon(icon_src: str, dest_path: Path, debug: bool) -> bool:
    if not icon_src:
        return False
    if dest_path.exists():
        dbg(debug, f"[IMG] exists, skipping: {dest_path}")
        return False

    try:
        dbg(debug, f"[IMG] downloading: {icon_src}")
        r = requests.get(icon_src, timeout=30)
        r.raise_for_status()
        dest_path.write_bytes(r.content)
        dbg(debug, f"[IMG] saved: {dest_path}")
        return True
    except requests.RequestException as e:
        print(f"[IMG ERROR] Failed to download {icon_src}: {e}", file=sys.stderr)
        return False

def maybe_download_all_icons(skills: List[Dict[str, Any]], images_dir: Path, debug: bool) -> None:
    ensure_dir(images_dir)

    downloaded = 0
    skipped = 0
    failed = 0

    for sk in skills:
        icon_src = sk.get("icon_src")
        icon_filename = sk.get("icon_filename")
        if not icon_src or not icon_filename:
            skipped += 1
            continue

        dest = images_dir / icon_filename
        ok = download_icon(icon_src, dest, debug)
        if ok:
            downloaded += 1
        elif dest.exists():
            skipped += 1
        else:
            failed += 1

    dbg(
        debug,
        f"[IMG] done. downloaded={downloaded} skipped={skipped} failed={failed} dir={images_dir}"
    )

# -------------------- JSON MODE --------------------
def fetch_json_skills(url: str, debug: bool) -> List[Dict[str, Any]]:
    dbg(debug, f"[DEBUG] Fetching JSON: {url}")
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"[ERROR] Failed to fetch skills JSON: {e}", file=sys.stderr)
        return []

    try:
        raw = r.json()
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON decode error: {e}", file=sys.stderr)
        return []

    skills: List[Dict[str, Any]] = []
    for sk in raw:
        skill_id = sk.get("id")
        name_en = sk.get("name_en") or sk.get("enname") or ""
        desc_en = sk.get("desc_en") or sk.get("endesc") or ""
        iconid = sk.get("iconid")
        rarity_code = sk.get("rarity")
        sk_type = sk.get("type")
        activation = sk.get("activation")
        condition_groups = sk.get("condition_groups")
        cost = sk.get("cost")

        if skill_id is None or not name_en or not desc_en:
            continue

        iconid_int = _coerce_int(iconid)

        if rarity_code in RARITY_MAP_JSON:
            rarity, color_class = RARITY_MAP_JSON[rarity_code]
        elif isinstance(skill_id, int) and skill_id >= 900000:
            rarity, color_class = RARITY_MAP_JSON["inherited"]
        else:
            rarity, color_class = RARITY_MAP_JSON[1]

        icon_filename: Optional[str] = None
        icon_src: Optional[str] = None
        if iconid_int is not None:
            icon_filename = f"utx_ico_skill_{iconid_int}.png"
            icon_src = ICON_BASE_URL + icon_filename

        out: Dict[str, Any] = {
            "id": skill_id,
            "iconid": iconid_int,
            "icon_filename": icon_filename,
            "icon_src": icon_src,
            "name": name_en,
            "description": desc_en,
            "color_class": color_class,
            "rarity": rarity,
            "grade_symbol": grade_symbol_from_name(name_en),
            "type": sk_type if isinstance(sk_type, list) else (sk_type if sk_type is not None else None),
            "activation": activation if isinstance(activation, (dict, list)) else (activation if activation is not None else None),
            "condition_groups": condition_groups if isinstance(condition_groups, list) else (condition_groups if condition_groups is not None else None),
            "cost": _coerce_int(cost),
            "jpname": sk.get("jpname"),
            "enname": sk.get("enname"),
            "name_en": sk.get("name_en"),
            "desc_en": sk.get("desc_en"),
        }

        for k in (
            "char", "char_e", "sce_e",
            "sup_e", "sup_hint",
            "gene_version", "versions",
            "pre_evo", "evo", "evo_cond",
        ):
            if k in sk:
                out[k] = sk.get(k)

        skills.append(out)

    dbg(debug, f"[DEBUG] JSON-mode: collected {len(skills)} skills")
    return skills

# -------------------- HTML MODE --------------------
def _clean_text(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\xa0", " ")
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\s*\n\s*", "\n", s)
    return s.strip()

def _absolute_url(rel: Optional[str]) -> Optional[str]:
    if not rel:
        return None
    if rel.startswith("http://") or rel.startswith("https://"):
        return rel
    return BASE_URL.rstrip("/") + "/" + rel.lstrip("/")

def _find_color_class_from_namediv(name_div: Tag) -> Tuple[str, str]:
    classes = name_div.get("class", [])
    color_cls = None
    for c in classes:
        if c.startswith("skills_table_jpname__"):
            continue
        if c.startswith("sc-"):
            continue
        color_cls = c
        break
    rarity = RARITY_BY_COLOR_CLASS_HTML.get(color_cls or "", "normal")
    return color_cls or "", rarity

def parse_html_skills_from_str(html: str, debug: bool) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")

    rows = []
    for sel in ["div[class^='skills_table_row_']", "div[class*=' skills_table_row_']"]:
        rows.extend(soup.select(sel))
    seen = set()
    rows = [r for r in rows if not (id(r) in seen or seen.add(id(r)))]

    out: List[Dict[str, Any]] = []
    for row in rows:
        icon_img = row.select_one("div[class^='skills_table_icon__'] img, div[class*=' skills_table_icon__'] img")
        name_div = row.select_one("div[class^='skills_table_jpname__'], div[class*=' skills_table_jpname__']")
        desc_div = row.select_one("div[class^='skills_table_desc__'], div[class*=' skills_table_desc__']")

        name = _clean_text(name_div.get_text(" ", strip=True)) if name_div else ""
        desc = _clean_text(desc_div.get_text(" ", strip=True)) if desc_div else ""

        if not name and not desc:
            continue

        icon_rel = icon_img.get("src") if icon_img else None
        icon_src = _absolute_url(icon_rel) if icon_rel else None
        icon_filename = icon_rel.split("/")[-1] if icon_rel else None

        color_class, rarity = ("", "normal")
        if name_div:
            color_class, rarity = _find_color_class_from_namediv(name_div)

        out.append({
            "id": None,
            "iconid": None,
            "icon_filename": icon_filename,
            "icon_src": icon_src,
            "name": name,
            "description": desc,
            "color_class": color_class,
            "rarity": rarity,
            "grade_symbol": grade_symbol_from_name(name),
            "type": None,
            "activation": None,
            "condition_groups": None,
            "cost": None,
            "jpname": None,
            "enname": None,
            "name_en": None,
            "desc_en": None,
        })

    dbg(debug, f"[DEBUG] HTML-mode: collected {len(out)} skills")
    return out

def fetch_or_read_html(url_html: Optional[str], html_file: Optional[str], debug: bool) -> Optional[str]:
    if html_file:
        try:
            with open(html_file, "r", encoding="utf-8") as f:
                data = f.read()
            dbg(debug, f"[DEBUG] Loaded HTML from file: {html_file} ({len(data)} bytes)")
            return data
        except Exception as e:
            print(f"[ERROR] Failed to read HTML file: {e}", file=sys.stderr)
            return None
    if url_html:
        dbg(debug, f"[DEBUG] Fetching HTML page: {url_html}")
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (UmaSkillScraper; +https://gametora.com)",
                "Accept-Language": "en-US,en;q=0.9",
            }
            r = requests.get(url_html, headers=headers, timeout=30)
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            print(f"[ERROR] Failed to fetch HTML: {e}", file=sys.stderr)
            return None
    return None

# --------- NEW: discover skills.*.json from the HTML (CSR fallback) ----------
DISCOVER_RX = re.compile(r'["\'](/?data/umamusume/skills\.[a-zA-Z0-9]+\.json)["\']')

def discover_json_from_html(html: str, debug: bool) -> Optional[str]:
    """
    On client-rendered pages, the list rows aren't in static HTML.
    This scans the HTML source for a reference to /data/umamusume/skills.*.json
    and returns an absolute URL if found.
    """
    m = DISCOVER_RX.search(html or "")
    if not m:
        dbg(debug, "[DEBUG] No /data/umamusume/skills.*.json reference found in HTML.")
        return None
    path = m.group(1)
    url = path if path.startswith("http") else BASE_URL.rstrip("/") + "/" + path.lstrip("/")
    dbg(debug, f"[DEBUG] Discovered skills JSON from HTML: {url}")
    return url

# -------------------- merge & dedupe --------------------
def _norm_name(n: str) -> str:
    return (n or "").strip().lower()

def merge_and_dedupe(a: List[Dict[str, Any]], b: List[Dict[str, Any]], debug: bool) -> List[Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}

    def put(sk: Dict[str, Any], prefer_existing: bool):
        key = f"id:{sk['id']}" if sk.get("id") is not None else f"name:{_norm_name(sk.get('name',''))}"
        if key in result and prefer_existing:
            return
        result[key] = sk

    # Put HTML first (lower priority), then JSON overrides when same key
    for sk in a:
        put(sk, prefer_existing=False)
    for sk in b:
        put(sk, prefer_existing=False)

    merged = list(result.values())
    dbg(debug, f"[DEBUG] merged total: {len(merged)}")
    return merged

# -------------------- CLI --------------------
def main():
    ap = argparse.ArgumentParser(description="Scrape Uma Musume skills (JSON mode recommended; HTML fallback with JSON discovery).")
    ap.add_argument("--url-json", help="XHR endpoint like https://gametora.com/data/umamusume/skills.XXXXXXXX.json")
    ap.add_argument("--url-html", help="Public skills page https://gametora.com/umamusume/skills")
    ap.add_argument("--html-file", help="Saved HTML of the skills page (offline parsing)")
    ap.add_argument("--out", default="in_game/skills.json", help="Output JSON path (default: %(default)s)")
    ap.add_argument(
        "--images",
        nargs="?",
        const=str(DEFAULT_IMAGES_DIR),
        default=None,
        help=r"Download icons to directory (default: ..\web\public\icons\skills). Usage: --images or --images PATH",
    )
    ap.add_argument("--debug", action="store_true", help="Verbose debug logging to stderr")
    args = ap.parse_args()

    json_list: List[Dict[str, Any]] = []
    html_list: List[Dict[str, Any]] = []

    # 1) JSON mode (explicit)
    if args.url_json:
        json_list = fetch_json_skills(args.url_json, args.debug)

    # 2) HTML mode (rows or discovery)
    if args.url_html or args.html_file:
        html = fetch_or_read_html(args.url_html, args.html_file, args.debug)
        if html:
            html_list = parse_html_skills_from_str(html, args.debug)
            # If no rows (CSR), try to auto-discover the skills.*.json and fetch it
            if not html_list:
                dbg(args.debug, "[DEBUG] No rows found in static HTML; attempting JSON discovery...")
                discovered = discover_json_from_html(html, args.debug)
                if discovered:
                    discovered_json = fetch_json_skills(discovered, args.debug)
                    if discovered_json:
                        json_list = discovered_json

    # 3) If we still have nothing, bail
    if not json_list and not html_list:
        print("[ERROR] Could not find skills in HTML and no JSON source available. "
              "Pass --url-json (recommended) or ensure the HTML contains the list or a skills.*.json reference.",
              file=sys.stderr)
        sys.exit(2)

    # 4) Prefer JSON entries (IDs) and merge with HTML (if any)
    final_list = merge_and_dedupe(html_list, json_list, args.debug)

    if args.images is not None:
        maybe_download_all_icons(final_list, Path(args.images), args.debug)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(final_list, f, ensure_ascii=False, indent=2)

    print(f"[OK] Wrote {len(final_list)} skill entries -> {args.out}")

if __name__ == "__main__":
    main()
