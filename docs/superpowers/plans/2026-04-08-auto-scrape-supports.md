# Implementation Plan: Auto-Scrape Missing Supports

## Overview
This plan outlines the implementation of an automated system for detecting and scraping missing Global version support cards from Gametora, with automatic triggering of `build_catalog.py`.

## Tasks

### Task 1: Create Support Registry System
- Create `datasets/in_game/support_registry.json` to track scraped supports
- Implement `load_support_registry()` and `save_support_registry()` functions
- Structure: last_updated, version, supports array, metadata

### Task 2: Implement Support Detection Logic
- Create `load_existing_supports_from_events()` to extract supports from current data
- Implement `find_missing_supports()` to compare existing vs Gametora data
- Add version detection to filter only Global version supports
- Create `generate_support_defaults()` to format arguments for scraper

### Task 3: Build Automation Orchestration Script
- Create `datasets/auto_scrape_missing_supports.py` as main entry point
- Implement argument parsing with `--support-list`, `--dry-run`, `--force-rescan`, `--debug`
- Orchestrate workflow: load → detect → fetch → generate defaults → scrape → merge → build → update registry
- Include safety features: dry-run mode, manual confirmation, manual data merge

### Task 4: Enhance Existing Scraper with Version Detection
- Modify `datasets/scrape_events.py` to add Global version detection
- Add `GLOBAL_VERSION_INDICATORS` and `JP_VERSION_INDICATORS` constants
- Implement `is_global_version()` and `extract_version_indicators_from_gametora_response()` functions
- Integrate version checking into the scraping workflow

### Task 5: Create Comprehensive Documentation
- Write `docs/AUTOMATION_GUIDE.md` with usage instructions, workflow explanation, safety features
- Update `README.dev.md` to include automation options section
- Add clear examples for basic usage, dry run, force rescan, and debug modes

### Task 6: Implement Unit Tests
- Create `tests/test_support_detection.py` with tests for all core functions
- Test registry loading/saving, support extraction, missing support detection
- Test support defaults generation with various input scenarios
- Ensure tests cover edge cases and error conditions

### Task 7: Verify Integration with Existing Workflow
- Test that automation enhances but doesn't replace manual workflow from README.dev.md
- Confirm that users still need to manually verify and merge scraped data
- Verify that `build_catalog.py` can be run automatically or manually
- Ensure proper error handling and informative messages

## Acceptance Criteria
- [ ] Support registry properly tracks scraped supports and prevents duplicates
- [ ] Automation correctly identifies missing Global-only supports from Gametora
- [ ] Script generates appropriate `--support-defaults` arguments for the scraper
- [ ] Dry-run mode shows what would happen without making changes
- [ ] Manual confirmation is required before scraping (unless `--force-rescan`)
- [ ] After scraping, user is prompted to manually merge data and can auto-run `build_catalog.py`
- [ ] Support registry is updated after successful scrape and build
- [ ] All unit tests pass
- [ ] Documentation is clear and complete
- [ ] Integration with existing workflow is seamless

## Files to Modify/Create
- `datasets/in_game/support_registry.json` (new)
- `datasets/auto_scrape_missing_supports.py` (new)
- `docs/AUTOMATION_GUIDE.md` (new)
- `datasets/scrape_events.py` (modified)
- `README.dev.md` (modified)
- `tests/test_support_detection.py` (new)

## Dependencies
- Existing `scrape_events.py` script
- Existing `build_catalog.py` script
- Required data files: `skills.json`, `status.json`, `events.json`
- Python standard library: json, pathlib, typing, subprocess, sys, datetime, argparse

## Risks and Mitigations
- Risk: Incorrect version detection leading to scraping Japanese version data
  - Mitigation: Robust version detection heuristics, manual verification step
- Risk: Duplicate scraping despite registry
  - Mitigation: Multiple layers of checking (registry + events.json comparison)
- Risk: Broken workflow due to script errors
  - Mitigation: Comprehensive error handling, informative messages, dry-run mode
- Risk: Manual merge step overlooked
  - Mitigation: Clear documentation and prompts emphasizing manual verification requirement

## Estimated Effort
- Task 1: 1 hour
- Task 2: 2 hours
- Task 3: 3 hours
- Task 4: 1 hour
- Task 5: 2 hours
- Task 6: 1 hour
- Task 7: 2 hours
- Total: 12 hours