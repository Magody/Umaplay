#!/usr/bin/env python3
"""
Automatically detect and scrape missing Global version support cards.
Usage: python auto_scrape_missing_supports.py --output missing_supports.json
"""

import json
from pathlib import Path
from typing import List, Dict, Any
import sys

def load_support_registry() -> Dict[str, Any]:
    """Load the support card registry"""
    registry_path = Path("datasets/in_game/support_registry.json")
    if registry_path.exists():
        with open(registry_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    # Return default structure if file doesn't exist
    return {
        "last_updated": None,
        "version": "global",
        "supports": [],
        "metadata": {
            "description": "Registry of scraped support cards to prevent duplicate scraping",
            "format_version": "1.0"
        }
    }

def save_support_registry(registry: Dict[str, Any]) -> None:
    """Save the support card registry"""
    registry_path = Path("datasets/in_game/support_registry.json")
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    with open(registry_path, 'w', encoding='utf-8') as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)

def load_existing_supports_from_events() -> List[Dict[str, str]]:
    """Extract support card information from the current events.json file"""
    events_path = Path("datasets/in_game/events.json")
    if not events_path.exists():
        return []

    try:
        with open(events_path, 'r', encoding='utf-8') as f:
            events_data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []

    existing_supports = []
    for entry in events_data:
        if entry.get('type') == 'support':
            support_info = {
                'name': entry.get('name', ''),
                'rarity': entry.get('rarity', 'None'),
                'attribute': entry.get('attribute', 'None'),
                'key': f"{entry.get('name', '').lower()}_{entry.get('rarity', 'None')}_{entry.get('attribute', 'None')}"
            }
            existing_supports.append(support_info)

    return existing_supports

def find_missing_supports(
    existing_supports: List[Dict[str, str]],
    gametora_supports: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Compare existing supports with Gametora data to find missing supports.
    """
    # Create a set of existing support keys for fast lookup
    existing_keys = set()
    for support in existing_supports:
        key = (support['name'].lower(), support['rarity'], support['attribute'])
        existing_keys.add(key)

    missing_supports = []

    for support in gametora_supports:
        # Extract key fields
        name = support.get('name', '')
        rarity = support.get('rarity', 'None')
        attribute = support.get('attribute', 'None')

        # Create lookup key
        key = (name.lower(), rarity, attribute)

        # Check if we already have this support
        if key in existing_keys:
            continue

        missing_supports.append(support)

    return missing_supports

def generate_support_defaults(missing_supports: List[Dict[str, Any]]) -> str:
    """
    Generate a support defaults string suitable for passing to scrape_events.py
    Format: "Name1-RARITY1-ATTRIBUTE1|Name2-RARITY2-ATTRIBUTE2|..."
    """
    defaults = []
    for support in missing_supports:
        name = support.get('name')
        rarity = support.get('rarity')
        attribute = support.get('attribute')

        # Skip if any required field is missing or None/empty
        if not name or not rarity or not attribute:
            continue

        # Also skip if any field is explicitly "None" string
        if name == "None" or rarity == "None" or attribute == "None":
            continue

        defaults.append(f"{name}-{rarity}-{attribute}")

    return "|".join(defaults)

def parse_arguments():
    """Parse command line arguments"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Automatically detect and scrape missing Global version support cards"
    )

    parser.add_argument(
        "--support-list",
        type=str,
        help="Comma-separated list of Gametora support slugs to check (e.g., 30036-riko-kashimoto,30034-rice-shower)"
    )

    parser.add_argument(
        "--discover-all",
        action="store_true",
        help="Automatically discover all supports from Gametora's supports index page (will be filtered for Global version)"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be scraped without actually doing it"
    )

    parser.add_argument(
        "--force-rescan",
        action="store_true",
        help="Ignore registry and check all supports (useful for initial setup)"
    )

    parser.add_argument(
        "--output",
        type=str,
        default="supports_events.json",
        help="Output file for scraped events (default: supports_events.json)"
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging"
    )

    return parser.parse_args()


def main():
    """Main execution function for the auto-scraper"""
    import subprocess
    from datetime import datetime

    args = parse_arguments()

    if args.debug:
        print("[DEBUG] Debug mode enabled")

    print("[INFO] Starting auto-detection of missing Global version supports...")

    # 1. Load existing supports from our registry and events.json
    registry = load_support_registry()
    existing_from_registry = {
        (s['name'].lower(), s['rarity'], s['attribute']): s
        for s in registry.get('supports', [])
    }

    existing_from_events = load_existing_supports_from_events()
    existing_keys = {
        (s['name'].lower(), s['rarity'], s['attribute']): s
        for s in existing_from_events
    }

    # Use events.json as source of truth (more current than registry)
    print(f"[INFO] Found {len(existing_from_events)} supports in events.json")

    # 2. Get list of supports to check
    if args.discover_all:
        # Automatically discover all supports from Gametora index page
        support_slugs = discover_all_gametora_supports(args.debug)
        if not support_slugs:
            print("[ERROR] No supports discovered from Gametora index page")
            return 1
        print(f"[INFO] Discovered {len(support_slugs)} total supports from Gametora index page")
    elif args.support_list:
        support_slugs = [s.strip() for s in args.support_list.split(',') if s.strip()]
        print(f"[INFO] Checking {len(support_slugs)} specific supports: {support_slugs}")
    else:
        # For now, we need a predefined list or discovery mechanism
        # In production, this would either:
        # 1. Read from a configured list of known supports
        # 2. Scrape Gametora's supports index to discover all supports
        # 3. Use a whitelist of Global-only supports
        print("[WARNING] No support list provided. Please use --support-list or --discover-all to specify which supports to check.")
        print("    Example: --support-list \"30036-riko-kashimoto,30034-rice-shower,20029-seeking-the-pearl\"")
        print("    Or: --discover-all (to automatically find all Global version supports)")
        return 1

    # 3. Fetch data from Gametora
    print("[INFO] Fetching support data from Gametora...")
    gametora_supports = fetch_gametora_supports(support_slugs, args.debug)

    if not gametora_supports:
        print("[ERROR] No support data retrieved from Gametora")
        return 1

    print(f"[INFO] Retrieved {len(gametora_supports)} supports from Gametora")

    # 4. Find missing supports
    missing_supports = find_missing_supports(list(existing_from_events), gametora_supports)

    if not missing_supports:
        print("[SUCCESS] All specified supports are already scraped!")
        # Update registry timestamp
        registry['last_updated'] = datetime.now().isoformat()
        save_support_registry(registry)
        return 0

    print(f"[INFO] Found {len(missing_supports)} missing supports:")
    for support in missing_supports:
        print(f"  - {support.get('name', 'Unknown')} ({support.get('rarity', 'None')}-{support.get('attribute', 'None')})")

    # 5. Generate support defaults for scraper
    support_defaults = generate_support_defaults(missing_supports)
    print(f"[INFO] Generated support defaults: {support_defaults[:100]}{'...' if len(support_defaults) > 100 else ''}")

    if args.dry_run:
        print("\n[DRY-RUN] Would now:")
        print("   1. Scrape {} missing supports".format(len(missing_supports)))
        print("   2. Merge results into ../datasets/in_game/events.json")
        print("   3. Run build_catalog.py")
        print("   4. Update support registry")
        return 0

    # 6. Ask for confirmation before proceeding (unless forced)
    if not args.force_rescan:
        response = input("\n[QUERY] Proceed with scraping and building catalog? (y/N): ").lower().strip()
        if response not in ['y', 'yes']:
            print("[INFO] Operation cancelled by user")
            return 1

    # 7. Run the actual scraper
    print("\n[INFO] Running event scraper...")

    # For discover-all mode, we need to pass the discovered supports as --support-list
    support_list_arg = ','.join(support_slugs) if args.discover_all else args.support_list

    # When running from datasets directory, paths need to be relative to that directory
    scraper_cmd = [
        sys.executable,
        "scrape_events.py",
        "--supports-card", support_list_arg,
        "--skills", "../datasets/in_game/skills.json",
        "--status", "../datasets/in_game/status.json",
        "--out", args.output,
    ]

    if support_defaults:
        scraper_cmd.extend(["--support-defaults", support_defaults])

    if args.debug:
        scraper_cmd.append("--debug")

    # Run from datasets directory
    result = subprocess.run(
        scraper_cmd,
        cwd="datasets",
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        print(f"[ERROR] Scraper failed:")
        print(f"[ERROR] STDOUT: {result.stdout}")
        print(f"[ERROR] STDERR: {result.stderr}")
        return 1

    print("[SUCCESS] Scraper completed successfully")
    if args.debug:
        print(f"[DEBUG] Scraper output: {result.stdout}")

    # 8. Merge scraped data into events.json (manual step notification)
    print("\n[ACTION] MANUAL STEP REQUIRED:")
    print(f"   Please review and merge new data from {args.output} into ../datasets/in_game/events.json")
    print("   Then run: python build_catalog.py")
    print("   Finally, update the support registry with the newly scraped supports")

    # 9. Offer to run build_catalog.py automatically
    build_response = input("\n[QUERY] Automatically run build_catalog.py now? (Y/n): ").lower().strip()
    if build_response not in ['n', 'no']:
        print("[INFO] Running build_catalog.py...")
        build_result = subprocess.run(
            [sys.executable, "build_catalog.py"],
            cwd=".",
            capture_output=True,
            text=True
        )

        if build_result.returncode != 0:
            print(f"[ERROR] Build catalog failed:")
            print(f"[ERROR] STDOUT: {build_result.stdout}")
            print(f"[ERROR] STDERR: {build_result.stderr}")
            print("[WARNING] Please run build_catalog.py manually after merging the data")
        else:
            print("[SUCCESS] build_catalog.py completed successfully")
            if args.debug:
                print(f"[DEBUG] Build output: {build_result.stdout}")

            # 10. Update registry with newly scraped supports
            print("[INFO] Updating support registry...")
            # In a full implementation, we would extract the newly added supports
            # and add them to the registry. For now, we'll update the timestamp.
            registry['last_updated'] = datetime.now().isoformat()
            # TODO: Add logic to extract and add newly scraped supports to registry
            save_support_registry(registry)
            print("[SUCCESS] Support registry updated")

    print("\n[SUCCESS] Automation process completed!")
    print("[INFO] Next steps:")
    print("   1. Review merged data in ../datasets/in_game/events.json")
    print("   2. Verify build_catalog.py ran successfully")
    print("   3. Test that new supports appear in the bot/web UI")
    print("   4. Commit changes: git add ../datasets/in_game/events.json ../datasets/in_game/event_catalog.json")
    print("   5. (Optional) Run again with --discover-all to check for newly released supports")

    return 0


def fetch_gametora_supports(support_slugs: List[str], debug: bool) -> List[Dict[str, Any]]:
    """
    Fetch support card data from Gametora using JSON mode.
    """
    if not support_slugs:
        return []

    # Import scraper functions
    from scrape_events import (
        fetch_and_parse_cards,
        load_skill_data,
        load_status_data
    )

    # Load required data files
    skill_lookup = load_skill_data("datasets/in_game/skills.json", False)
    status_lookup = load_status_data("datasets/in_game/status.json", False)

    # Fetch and parse supports
    supports_data = fetch_and_parse_cards(
        slugs=support_slugs,
        card_type="support",
        skill_lookup=skill_lookup,
        status_lookup=status_lookup,
        period="",  # Use default period
        img_dir=None,  # Don't download images for detection phase
        download_images=False,
        debug=debug,
        shared_events_path=None
    )

    return supports_data


def discover_all_gametora_supports(debug: bool) -> List[str]:
    """
    Discover all available support cards from Gametora's supports index page.
    Scrapes https://gametora.com/umamusume/supports and gets HTML elements
    with class="sc-77f9d665-1 gpkuqF" then extracts support card data based on href.
    """
    if debug:
        print("[DEBUG] Discovering all supports from Gametora index page...")

    try:
        # Try to use Playwright for JavaScript-rendered content
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            # Launch browser in headless mode for automation
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            # Set user agent to avoid blocking
            page.set_extra_http_headers({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            })

            # Navigate to the supports page
            print(f"[INFO] Loading supports page: https://gametora.com/umamusume/supports")
            page.goto("https://gametora.com/umamusume/supports", wait_until="domcontentloaded", timeout=60000)

            # Wait for the target elements to load
            try:
                page.wait_for_selector('.sc-77f9d665-1.gpkuqF', timeout=10000)
            except:
                # If selector not found, wait a bit and continue anyway
                page.wait_for_timeout(5000)

            # Scroll to load all content (aggressive scrolling to ensure we get everything)
            print(f"[INFO] Scrolling to load all support cards...")

            # Scroll multiple times with intervals to ensure all content loads
            for scroll_iteration in range(30):  # Scroll 30 times
                # Scroll to bottom
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

                # Trigger scroll events
                page.evaluate("""
                    window.dispatchEvent(new Event('scroll'));
                    window.dispatchEvent(new Event('resize'));
                """)

                # Wait for content to load
                page.wait_for_timeout(2000)

                # Try to find and click "load more" buttons
                load_more_selectors = [
                    'button:has-text("Load more")',
                    'button:has-text("Show more")',
                    'button:has-text("もっと見る")',
                    '[class*="load"]:has-text("more")',
                    '[class*="more"]:has-text("Load")',
                    '.load-more',
                    '.show-more'
                ]

                for selector in load_more_selectors:
                    try:
                        buttons = page.query_selector_all(selector)
                        for button in buttons:
                            if button.is_visible() and button.is_enabled():
                                button.click()
                                page.wait_for_timeout(3000)
                                if debug:
                                    print(f"[DEBUG] Clicked load more button: {selector}")
                    except:
                        pass  # Ignore errors in button clicking

                # Progress indicator
                if debug and scroll_iteration % 5 == 0:
                    count = len(page.query_selector_all('.sc-77f9d665-1.gpkuqF'))
                    print(f"[DEBUG] After {scroll_iteration + 1} scrolls: {count} support elements found")

            # Final scroll to bottom and wait
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(5000)

            # Get all elements with the specified class
            print(f"[INFO] Finding elements with class='sc-77f9d665-1 gpkuqF'")
            elements = page.query_selector_all('.sc-77f9d665-1.gpkuqF')

            support_slugs = []
            for element in elements:
                # Get the href attribute from the element or its children
                href = element.get_attribute('href')
                if not href:
                    # Try to find href in child elements
                    link_element = element.query_selector('a[href]')
                    if link_element:
                        href = link_element.get_attribute('href')

                if href and href.startswith('/umamusume/supports/'):
                    # Extract the slug part: '/umamusume/supports/30086-narita-top-road' -> '30086-narita-top-road'
                    slug = href.replace('/umamusume/supports/', '')
                    # Filter out invalid slugs
                    if slug and not slug.endswith('.png') and 'support_card' not in slug and len(slug) > 3:
                        support_slugs.append(slug)

            browser.close()

            # Remove duplicates while preserving order
            seen = set()
            unique_slugs = []
            for slug in support_slugs:
                if slug not in seen:
                    seen.add(slug)
                    unique_slugs.append(slug)

            print(f"[INFO] Discovered {len(unique_slugs)} unique supports using Playwright")

            if debug and unique_slugs:
                print("[DEBUG] First few support slugs discovered:")
                for i, slug in enumerate(unique_slugs[:10]):
                    print(f"  {i+1}. {slug}")
                if len(unique_slugs) > 10:
                    print(f"[DEBUG] ... and {len(unique_slugs) - 10} more")

            return unique_slugs

    except ImportError:
        print("[WARNING] Playwright not available, falling back to HTTP-based discovery")
        # Fall back to the original HTTP-based method
        return _discover_via_http(debug)
    except Exception as e:
        print(f"[ERROR] Error discovering supports with Playwright: {e}")
        if debug:
            import traceback
            print(f"[DEBUG] Traceback: {traceback.format_exc()}")
        # Fall back to HTTP-based method on error
        return _discover_via_http(debug)


def _discover_via_http(debug: bool) -> List[str]:
    """
    Fallback method to discover supports using HTTP requests only.
    Less reliable for JavaScript-heavy sites but works as backup.
    Enhanced to capture more supports by looking for broader patterns.
    """
    if debug:
        print("[DEBUG] Using HTTP-based discovery fallback...")

    import requests
    import re
    from bs4 import BeautifulSoup

    try:
        # Fetch the supports index page
        url = "https://gametora.com/umamusume/supports"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        print(f"[INFO] Fetching supports index from: {url}")
        response = requests.get(url, headers=headers, timeout=15)
        print(f"[INFO] Response status: {response.status_code}")
        response.raise_for_status()

        # Parse HTML with BeautifulSoup for better extraction
        soup = BeautifulSoup(response.content, 'html.parser')

        # Find all links that point to support pages
        support_links = soup.find_all('a', href=True)
        support_slugs = []

        for link in support_links:
            href = link.get('href')
            if href and href.startswith('/umamusume/supports/'):
                # Extract the slug part
                slug = href.replace('/umamusume/supports/', '')
                # Filter out invalid slugs
                if slug and not slug.endswith('.png') and 'support_card' not in slug and len(slug) > 5:
                    support_slugs.append(slug)

        print(f"[INFO] Found {len(support_slugs)} support links using BeautifulSoup parsing")

        # Alternative: Also try the regex approach as backup
        pattern = r'/umamusume/supports/([0-9]+-[a-z0-9\-]+)'
        regex_matches = re.findall(pattern, response.text)
        print(f"[INFO] Regex pattern found {len(regex_matches)} matches")

        # Combine both methods, removing duplicates
        all_slugs = list(set(support_slugs + regex_matches))
        seen = set()
        unique_slugs = []
        for slug in all_slugs:
            if slug not in seen:
                seen.add(slug)
                unique_slugs.append(slug)

        print(f"[INFO] Extracted {len(unique_slugs)} unique support slugs")

        if debug and unique_slugs:
            print("[DEBUG] First few support slugs found:")
            for i, slug in enumerate(unique_slugs[:10]):
                print(f"  {i+1}. {slug}")
            if len(unique_slugs) > 10:
                print(f"[DEBUG] ... and {len(unique_slugs) - 10} more")

        return unique_slugs

    except Exception as e:
        print(f"[ERROR] Error discovering supports via HTTP: {e}")
        if debug:
            import traceback
            print(f"[DEBUG] Traceback: {traceback.format_exc()}")
        return []


if __name__ == "__main__":
    sys.exit(main())