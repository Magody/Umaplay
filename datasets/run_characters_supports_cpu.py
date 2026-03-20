#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PWTimeoutError
from playwright.sync_api import sync_playwright


if os.environ.get("UMA_SCRAPE_CHILD") == "1":
    print("Wrapper invoked as child; refusing to run wrapper logic.")
    raise SystemExit(2)


SCRIPT_DIR = Path(__file__).resolve().parent
SCRAPE_SCRIPT_PATH = (SCRIPT_DIR / "scrape_events.py").resolve()

BASE = "https://gametora.com"
ROBOTS_URL = urljoin(BASE, "/robots.txt")

COMMON_ARGS = [
    "--skills", "in_game/skills.json",
    "--status", "in_game/status.json",
    "--period", "pre_first_anni",
    "--images",
    "--img-dir", "../web/public/events",
    "--debug",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PythonRequests/2.x"
}

MAX_WORKERS = min(6, (os.cpu_count() or 4))

HARDCODED_SCENARIOS = [
    {
        "type": "scenario",
        "name": "Ura Finale",
        "rarity": "None",
        "attribute": "None",
        "choice_events": [
            {
                "type": "special",
                "chain_step": 1,
                "name": "Exhilarating! What a Scoop!",
                "options": {
                    "1": [{"stamina": 10, "bond": 5, "character": "etsuko"}],
                    "2": [{"guts": 10, "bond": 5, "character": "etsuko"}],
                },
                "default_preference": 1,
            },
            {
                "type": "special",
                "chain_step": 1,
                "name": "A Trainer's Knowledge",
                "options": {
                    "1": [{"power": 10, "bond": 5, "character": "etsuko"}],
                    "2": [{"speed": 10, "bond": 5, "character": "etsuko"}],
                },
                "default_preference": 2,
            },
            {
                "type": "special",
                "chain_step": 1,
                "name": "Best Foot Forward!",
                "options": {
                    "1": [{"energy": -10, "power": 20, "guts": 20, "hints": ["Beeline Burst"]}],
                    "2": [{"energy": 30, "stamina": 20, "hints": ["Breath of Fresh Air"]}],
                },
                "default_preference": 2,
            },
        ],
    },
    {
        "type": "scenario",
        "name": "Unity Cup",
        "rarity": "None",
        "attribute": "None",
        "choice_events": [
            {
                "type": "special",
                "chain_step": 1,
                "name": "Tutorial",
                "options": {
                    "1": [{"bond": 0}],
                    "2": [{"bond": 0}],
                },
                "default_preference": 2,
            },
            {
                "type": "special",
                "chain_step": 1,
                "name": "A Team at Last",
                "options": {
                    "1": [{"team": "Happy Hoppers, like Taiki suggested", "hints": ["Mile Maven"]}],
                    "2": [{"team": "Sunny Runners, like Fukukitaru suggested", "hints": ["Clairvoyance"]}],
                    "3": [{"team": "Carrot Pudding, like Haru suggested", "hints": ["Indomitable"]}],
                    "4": [{"team": "Blue Bloom, like Rice suggested", "hints": ["Cooldown"]}],
                    "5": [{"team": "Team Carrot", "hints": ["No Stopping Me!"]}],
                },
                "default_preference": 5,
            },
        ],
    },
]


def _find_npm_executable() -> str | None:
    return shutil.which("npm") or shutil.which("npm.cmd") or shutil.which("npm.exe")


def ensure_npm_available() -> str:
    npm_exe = _find_npm_executable()
    if not npm_exe:
        print("\nERROR: npm is not installed or not on PATH.")
        print("Install Node.js (includes npm) from:")
        print("https://nodejs.org/en/download")
        sys.exit(1)

    result = subprocess.run([npm_exe, "--version"], capture_output=True, text=True)
    if result.returncode != 0:
        print("\nERROR: npm exists but failed to run `npm --version`.")
        if result.stderr:
            print(result.stderr.strip())
        sys.exit(result.returncode)

    version = result.stdout.strip()
    print(f"\n[OK] npm detected (version {version})")
    print("Starting in 5 seconds...\n")
    for i in range(5, 0, -1):
        print(f"{i}...")
        time.sleep(1)
    print()
    return npm_exe


def _run_npm(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    npm_exe = _find_npm_executable()
    if not npm_exe:
        print("\nERROR: npm is not installed or not on PATH.")
        print("Install Node.js (includes npm) from:")
        print("https://nodejs.org/en/download")
        sys.exit(1)
    return subprocess.run([npm_exe, *args], cwd=cwd)


SKILLS_PAGE = "https://gametora.com/umamusume/skills"
SKILLS_JSON_RE = re.compile(r"/data/umamusume/skills\.[a-f0-9]+\.json", re.I)


def ensure_playwright_chromium_installed() -> None:
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        return
    except Exception as e:
        msg = str(e).lower()
        needs_install = (
            "playwright install" in msg
            or "executable doesn't exist" in msg
            or "executable does not exist" in msg
            or ("chromium" in msg and "install" in msg)
        )
        if not needs_install:
            raise

        print("[INFO] Playwright Chromium not installed. Installing now...")
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        print("[INFO] Playwright Chromium install OK.")


def find_skills_json_url_via_browser(timeout_ms: int = 60000) -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        def route_handler(route):
            if route.request.resource_type in ("image", "font", "media"):
                return route.abort()
            return route.continue_()

        page.route("**/*", route_handler)
        found_url = None

        try:
            with page.expect_request(lambda req: SKILLS_JSON_RE.search(req.url), timeout=timeout_ms) as req_info:
                page.goto(SKILLS_PAGE, wait_until="domcontentloaded", timeout=timeout_ms)
            found_url = req_info.value.url
        except PWTimeoutError:
            try:
                with page.expect_response(lambda resp: SKILLS_JSON_RE.search(resp.url), timeout=timeout_ms) as resp_info:
                    page.goto(SKILLS_PAGE, wait_until="domcontentloaded", timeout=timeout_ms)
                    page.wait_for_timeout(1500)
                found_url = resp_info.value.url
            except PWTimeoutError:
                pass
        finally:
            browser.close()

        if not found_url:
            raise RuntimeError(
                "Loaded page, but did not observe /data/umamusume/skills.<hash>.json. "
                "The site may have changed endpoints or blocked headless mode."
            )
        return found_url


def run_scrape_skills(json_url: str) -> None:
    out_path = SCRIPT_DIR / "in_game" / "skills.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "scrape_skills.py",
        "--url-json", json_url,
        "--out", str(out_path),
        "--images",
        "--debug",
    ]

    print("\n=== Skills JSON detected via Playwright ===")
    print("Found JSON:", json_url)
    print("Running:", " ".join(cmd))

    env = os.environ.copy()
    env["UMA_SCRAPE_CHILD"] = "1"
    subprocess.run(cmd, check=True, env=env, cwd=SCRIPT_DIR)

    print(f"[OK] Wrote: {out_path}\n")


def run_skills_step_after_npm_check() -> None:
    ensure_playwright_chromium_installed()
    json_url = find_skills_json_url_via_browser()
    run_scrape_skills(json_url)


def get_sitemap_url() -> str:
    r = requests.get(ROBOTS_URL, headers=HEADERS, timeout=60)
    r.raise_for_status()
    for line in r.text.splitlines():
        line = line.strip()
        if line.lower().startswith("sitemap:"):
            return line.split(":", 1)[1].strip()
    return urljoin(BASE, "/sitemap.xml")


def fetch_xml(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=60)
    r.raise_for_status()
    return r.text


def extract_sitemap_locs(xml_text: str) -> list[str]:
    soup = BeautifulSoup(xml_text, "xml")
    return [loc.get_text(strip=True) for loc in soup.find_all("loc")]


def iter_all_urls_from_sitemap(sitemap_url: str) -> list[str]:
    xml_text = fetch_xml(sitemap_url)
    soup = BeautifulSoup(xml_text, "xml")

    if soup.find("sitemapindex"):
        child_sitemaps = extract_sitemap_locs(xml_text)
        all_urls: list[str] = []
        for child in child_sitemaps:
            child_xml = fetch_xml(child)
            all_urls.extend(extract_sitemap_locs(child_xml))
        return all_urls

    return extract_sitemap_locs(xml_text)


def dedupe_preserve_order(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def ids_from_urls(urls: list[str], kind: str) -> list[str]:
    pat = re.compile(rf"/umamusume/{kind}/(\d+-[a-z-]+)")
    ids = []
    for url in urls:
        m = pat.search(url)
        if m:
            ids.append(m.group(1))
    return dedupe_preserve_order(ids)


def inventory_ids(per_item_dir: Path) -> set[str]:
    per_item_dir.mkdir(parents=True, exist_ok=True)
    out: set[str] = set()
    for fp in per_item_dir.glob("*.json"):
        out.add(fp.stem)
    return out


def compute_missing(sitemap_ids: list[str], existing_ids: set[str]) -> list[str]:
    return [item_id for item_id in sitemap_ids if item_id not in existing_ids]


def _run_one_scrape(flag: str, item_id: str, out_path: Path) -> tuple[str, int]:
    env = os.environ.copy()
    env["UMA_SCRAPE_CHILD"] = "1"

    cmd = [
        sys.executable,
        str(SCRAPE_SCRIPT_PATH),
        flag, item_id,
        "--out", str(out_path),
        *COMMON_ARGS,
    ]
    result = subprocess.run(cmd, cwd=SCRIPT_DIR, env=env)
    return item_id, result.returncode


def scrape_missing(label: str, flag: str, missing_ids: list[str], per_item_dir: Path) -> bool:
    if not missing_ids:
        print(f"\n=== {label}: nothing missing ===")
        return False

    print(f"\n=== {label}: missing {len(missing_ids)}. Scraping ALL ===")
    per_item_dir.mkdir(parents=True, exist_ok=True)

    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(_run_one_scrape, flag, item_id, per_item_dir / f"{item_id}.json"): item_id
            for item_id in missing_ids
        }

        done = 0
        total = len(missing_ids)
        for future in as_completed(futures):
            done += 1
            item_id = futures[future]
            try:
                _, rc = future.result()
            except Exception as e:
                rc = 1
                print(f"[{label}] EXCEPTION {item_id}: {e}")

            if rc != 0:
                failures.append(item_id)
                print(f"[{label}] FAIL {item_id} ({done}/{total})")
            else:
                print(f"[{label}] OK   {item_id} ({done}/{total})")

    if failures:
        print(f"\n=== {label}: {len(failures)} failures ===")
        print("First 20 failures:", ", ".join(failures[:20]))
        sys.exit(1)

    return True


def _stable_fingerprint(obj) -> str:
    if isinstance(obj, dict) and isinstance(obj.get("id"), str) and obj["id"].strip():
        return f"id::{obj['id'].strip()}"
    try:
        return "obj::" + json.dumps(obj, sort_keys=True, ensure_ascii=False)
    except TypeError:
        return "repr::" + repr(obj)


def merge_outputs_to_list(per_item_dir: Path) -> tuple[list, dict]:
    files = sorted(per_item_dir.glob("*.json"))
    merged: list = []
    seen: set[str] = set()

    stats = {
        "files_total": len(files),
        "read_ok": 0,
        "read_bad": 0,
        "written": 0,
        "removed_dupes": 0,
    }

    for fp in files:
        try:
            with fp.open("r", encoding="utf-8") as f:
                data = json.load(f)
            stats["read_ok"] += 1
        except Exception:
            stats["read_bad"] += 1
            continue

        items = data if isinstance(data, list) else [data]
        for obj in items:
            key = _stable_fingerprint(obj)
            if key in seen:
                stats["removed_dupes"] += 1
                continue
            seen.add(key)
            merged.append(obj)

    stats["written"] = len(merged)
    return merged, stats


def write_json_list(path: Path, data_list: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data_list, ensure_ascii=False, indent=2), encoding="utf-8")


def run_post_build_steps() -> None:
    project_root = SCRIPT_DIR.parent
    web_dir = project_root / "web"

    print("\n=== Running build_catalog.py (../) ===\n")
    result = subprocess.run([sys.executable, "build_catalog.py"], cwd=project_root)
    if result.returncode != 0:
        sys.exit(result.returncode)

    print("\n=== Running npm run build (../web) ===\n")
    build_result = _run_npm(["run", "build"], cwd=web_dir)

    if build_result.returncode != 0:
        print("\nRetrying with npm install...\n")
        install_result = _run_npm(["install"], cwd=web_dir)
        if install_result.returncode != 0:
            sys.exit(install_result.returncode)

        retry_result = _run_npm(["run", "build"], cwd=web_dir)
        if retry_result.returncode != 0:
            sys.exit(retry_result.returncode)

    print("\n=== Post-build steps completed successfully ===\n")


def main() -> None:
    if not SCRAPE_SCRIPT_PATH.exists():
        print(f"\nERROR: scrape_events.py not found at: {SCRAPE_SCRIPT_PATH}")
        print("Put scrape_events.py in the same folder as this wrapper (datasets).")
        sys.exit(1)

    scrape_out_root = SCRIPT_DIR / "_scrape_out"
    if scrape_out_root.exists():
        print(f"[INFO] Removing existing {scrape_out_root} ...")
        try:
            shutil.rmtree(scrape_out_root)
        except Exception as e:
            print(f"[WARN] Failed to remove {scrape_out_root}: {e}")
            sys.exit(1)

    ensure_npm_available()
    run_skills_step_after_npm_check()

    sitemap_url = get_sitemap_url()
    print(f"Using sitemap: {sitemap_url}")

    all_urls = dedupe_preserve_order(iter_all_urls_from_sitemap(sitemap_url))
    supports = ids_from_urls(all_urls, "supports")
    characters = ids_from_urls(all_urls, "characters")

    print(f"\nSitemap counts: supports={len(supports)} characters={len(characters)}")

    supports_dir = SCRIPT_DIR / "_scrape_out" / "supports"
    chars_dir = SCRIPT_DIR / "_scrape_out" / "characters"

    existing_supports = inventory_ids(supports_dir)
    existing_chars = inventory_ids(chars_dir)
    missing_supports = compute_missing(supports, existing_supports)
    missing_chars = compute_missing(characters, existing_chars)

    print(f"\nInventory counts: supports_files={len(existing_supports)} characters_files={len(existing_chars)}")
    print(f"Missing counts: supports_missing={len(missing_supports)} characters_missing={len(missing_chars)}")

    project_root = SCRIPT_DIR.parent
    support_events_dir = project_root / "web" / "public" / "events" / "support"
    trainee_events_dir = project_root / "web" / "public" / "events" / "trainee"

    for path in (support_events_dir, trainee_events_dir):
        if path.exists():
            print(f"[INFO] Removing existing directory tree: {path}")
            try:
                shutil.rmtree(path)
            except Exception as e:
                print(f"[ERROR] Failed to remove {path}: {e}")
                sys.exit(1)

    scraped_supports = scrape_missing("SUPPORTS", "--supports-card", missing_supports, supports_dir)
    scraped_chars = scrape_missing("CHARACTERS", "--characters-card", missing_chars, chars_dir)

    print("\n=== Merging + deduping SUPPORTS ===")
    supports_list, s_stats = merge_outputs_to_list(supports_dir)
    print(
        f"supports: files={s_stats['files_total']} ok={s_stats['read_ok']} bad={s_stats['read_bad']} "
        f"written={s_stats['written']} removed_dupes={s_stats['removed_dupes']}"
    )

    print("\n=== Merging + deduping CHARACTERS ===")
    chars_list, c_stats = merge_outputs_to_list(chars_dir)
    print(
        f"characters: files={c_stats['files_total']} ok={c_stats['read_ok']} bad={c_stats['read_bad']} "
        f"written={c_stats['written']} removed_dupes={c_stats['removed_dupes']}"
    )

    combined = supports_list + chars_list

    print("\n=== Appending hardcoded scenario events ===")
    combined.extend(HARDCODED_SCENARIOS)

    print("\n=== Final dedupe across supports+characters+scenarios ===")
    final_seen: set[str] = set()
    final_list: list = []
    final_removed = 0
    for obj in combined:
        key = _stable_fingerprint(obj)
        if key in final_seen:
            final_removed += 1
            continue
        final_seen.add(key)
        final_list.append(obj)
    print(f"combined: written={len(final_list)} removed_dupes={final_removed}")

    out_path = SCRIPT_DIR / "in_game" / "events.json"
    write_json_list(out_path, final_list)
    print(f"\n[OK] Wrote: {out_path}")

    if scraped_supports or scraped_chars:
        print("\nNew files were scraped. Running build steps...")
        run_post_build_steps()
    else:
        print("\nNo missing files were scraped. Skipping build steps.")


if __name__ == "__main__":
    main()
