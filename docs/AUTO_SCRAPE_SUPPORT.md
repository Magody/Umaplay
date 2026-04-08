# Auto Scrape Missing Supports Script Documentation

## Overview

The `auto_scrape_missing_supports.py` script is an intelligent automation tool designed to detect and scrape missing Global version support cards from Gametora. It eliminates guesswork by automatically comparing your local data with Gametora's database to identify exactly which supports need to be scraped.

## Purpose

This script addresses the manual and error-prone process of determining which support cards need to be scraped from Gametora. Instead of maintaining manual lists or guessing which supports are missing, the script:

1. Analyzes your current `events.json` and support registry
2. Fetches current data from Gametora for specified supports
3. Identifies precisely which supports are missing or outdated
4. Generates appropriate parameters for the scraper
5. Orchestrates the scraping process with safety confirmations

## Key Features

### Smart Detection
- Compares existing support data with Gametora to find only missing supports
- Uses case-insensitive name matching with exact rarity/attribute matching
- Skips supports with missing or invalid data

### Flexible Input Methods
- **Specific List**: Check particular supports using `--support-list`
- **Auto-Discovery**: Automatically find all Global version supports using `--discover-all`
- **Combined Approach**: Use both methods together for comprehensive coverage

### Safety Mechanisms
- **Dry Run Mode**: Preview actions without making changes (`--dry-run`)
- **Manual Confirmation**: Required approval before scraping (bypassable with `--force-rescan`)
- **Manual Data Merge**: You review and merge scraped data yourself (critical safety feature)
- **Registry Tracking**: Prevents unnecessary re-scraping of already processed supports

### Integration
- Works seamlessly with existing `scrape_events.py` and `build_catalog.py`
- Updates support registry timestamps automatically
- Compatible with existing workflow described in `README.dev.md`

## Detailed Workflow

When executed, the script follows this precise sequence:

### 1. Initialization and Argument Parsing
- Parses command-line arguments for mode selection and options
- Sets up debug logging if requested
- Loads existing support data from registry and events.json

### 2. Support Identification
Depending on arguments:
- **`--discover-all`**: Uses Playwright (with HTTP fallback) to scrape Gametora's supports index page
- **`--support-list`**: Uses comma-separated list of support slugs
- **Both**: Combines discovered and specified supports (union)

### 3. Data Retrieval
- Fetches current support data from Gametora using JSON mode via `scrape_events.py` functions
- Loads required lookup data (skills.json, status.json) for proper parsing
- Optionally enables debug logging for troubleshooting

### 4. Missing Support Detection
- Creates lookup keys from existing supports (name_lowercase, rarity, attribute)
- Compares against Gametora data to find truly missing supports
- Filters out supports with missing/invalid required fields

### 5. Preparation for Scraping
- Generates support defaults string in format: `"Name-RARITY-ATTRIBUTE|Name2-RARITY2-ATTRIBUTE2|..."`
- Validates that all required fields are present and not "None"
- Shows preview of what will be processed

### 6. Confirmation and Execution
- Unless `--force-rescan` or `--dry-run` is used, prompts for user confirmation
- Executes `scrape_events.py` with appropriate parameters from the datasets directory
- Handles errors gracefully with informative output

### 7. Post-Processing
- Prompts for manual merge of scraped data into `events.json` (safety step)
- Optionally runs `build_catalog.py` to update the event catalog
- Updates support registry with current timestamp
- Provides clear next steps for completion

## Command-Line Interface

### Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `--support-list` | Comma-separated list of Gametora support slugs (e.g., "30036-riko-kashimoto,30034-rice-shower") | None |
| `--discover-all` | Automatically discover all supports from Gametora's supports index page | False |
| `--dry-run` | Show what would be scraped without actually doing it | False |
| `--force-rescan` | Ignore registry and check all supports (useful for initial setup) | False |
| `--output` | Output file for scraped events (relative to datasets directory) | "supports_events.json" |
| `--debug` | Enable debug logging | False |

### Argument Combinations

#### Specific Support Checking
```bash
python datasets/auto_scrape_missing_supports.py --support-list "30036-riko-kashimoto,30034-rice-shower"
```

#### Auto-Discovery Mode
```bash
python datasets/auto_scrape_missing_supports.py --discover-all --dry-run
```

#### Combined Approach
```bash
python datasets/auto_scrape_missing_supports.py --discover-all --support-list "30036-riko-kashimoto" --dry-run
```

#### Force Rescan (Ignore Registry)
```bash
python datasets/auto_scrape_missing_supports.py --support-list "30036-riko-kashimoto,30034-rice-shower" --force-rescan
```

## Output Files

The script generates or interacts with these key files:

### Input Files
- `datasets/in_game/events.json` - Master event data (source of existing supports)
- `datasets/in_game/support_registry.json` - Tracking of previously scraped supports
- `datasets/in_game/skills.json` - Skill lookup data for parsing
- `datasets/in_game/status.json` - Status lookup data for parsing

### Output/Temporary Files
- `datasets/supports_events.json` (or custom `--output`) - Raw scraped data from Gametora
- Updated `datasets/in_game/support_registry.json` - Refreshed timestamp

### Manual Steps (After Script Execution)
1. Merge `datasets/supports_events.json` into `datasets/in_game/events.json`
2. Run `python build_catalog.py` to update runtime catalog
3. Commit changes to version control

## Technical Implementation Details

### Support Discovery Mechanism
When using `--discover-all`, the script employs a two-tier approach:

1. **Primary Method (Playwright)**:
   - Launches headless Chromium browser
   - Navigates to https://gametora.com/umamusume/supports
   - Implements aggressive scrolling (30 iterations) to load all content
   - Attempts to click "load more" buttons to reveal paginated content
   - Extracts support slugs from elements with class `.sc-77f9d665-1.gpkuqF`
   - Filters and deduplicates results

2. **Fallback Method (HTTP/BeautifulSoup)**:
   - Used when Playwright is unavailable or fails
   - Fetches raw HTML and parses with BeautifulSoup
   - Uses both CSS selector and regex pattern matching
   - Combines results from multiple extraction methods

### Data Comparison Logic
The core detection algorithm:
1. Creates composite keys from existing supports: `(name_lowercase, rarity, attribute)`
2. Creates identical keys from Gametora data
3. Identifies supports present in Gametora but missing from local data
4. Excludes entries with missing/null/"None" values in required fields

### Support Defaults Generation
For each missing support, creates a formatted string:
```
{name}-{rarity}-{attribute}
```
Joined with `|` delimiter for consumption by `scrape_events.py --support-defaults`

### Integration with Scraper
The script calls `scrape_events.py` with:
- `--supports-card`: List of supports to check (from --support-list or --discover-all)
- `--support-defaults`: Generated string for missing supports only
- Standard parameters for skills, status, and output file locations
- Runs from the datasets directory for correct relative paths

## Error Handling and Logging

### Error Conditions Handled
- Missing or unreadable input files (events.json, registry, etc.)
- Network failures when fetching from Gametora
- Playwright unavailable or browser launch failures
- HTTP request failures during discovery
- JSON parsing errors in scraped data
- Scraper execution failures
- User cancellation during prompts

### Logging Levels
- **INFO**: Standard operational information (always shown)
- **DEBUG**: Verbose diagnostic information (with --debug flag)
- **ERROR**: Failure conditions requiring attention
- **WARNING**: Potentially problematic situations
- **SUCCESS**: Completion of major steps

## Best Practices

### Recommended Usage Pattern
1. **Initial Assessment**: Run with `--discover-all --dry-run` to see what needs updating
2. **Verification**: Review the list of missing supports for accuracy
3. **Execution**: Run with `--discover-all --force-rescan` to perform actual scraping
4. **Manual Review**: Carefully examine scraped data before merging
5. **Catalog Update**: Run build_catalog.py to update runtime data
6. **Version Control**: Commit changes to events.json and event_catalog.json

### Maintenance Tips
- Run periodically to catch newly released supports
- Use `--debug` when troubleshooting discovery or parsing issues
- Keep the support registry updated to prevent unnecessary work
- Always verify scraped data manually before merging (safety critical)
- Consider scheduling regular runs for active development periods

### Troubleshooting Common Issues
- **Discovery Fails**: Try without --discover-all and provide explicit --support-list
- **No Missing Supports Found**: Verify your events.json is current and complete
- **Scraper Fails**: Check that skills.json and status.json are present and valid
- **Memory Issues**: Discovery process can be memory-intensive; consider smaller batches

## Integration with Existing Documentation

This script complements and enhances the workflows described in:

### AUTOMATION_GUIDE.md
- Provides the intelligent detection layer that makes automation practical
- Replaces manual support list maintenance with automatic detection
- Maintains all safety features and manual oversight steps

### README.dev.md
- Works alongside manual scraping commands
- Enhances rather than replaces existing build_catalog.py workflow
- Preserves the manual data verification step as a critical safety feature

## Future Enhancements

Potential improvements tracked in the source code TODOs:

1. **Automatic Data Merge**: Implement safe merging of scraped data into events.json
2. **Enhanced Version Detection**: More sophisticated Global vs Japanese version filtering
3. **Scheduled Execution**: Cron job or similar for automatic periodic checks
4. **Incremental Updates**: More granular tracking of individual support freshness
5. **Notification System**: Alerts when new supports are discovered or scraping completes

## Examples

### Basic Usage Scenarios

#### Check Specific Supports for Updates
```bash
# See if specific supports need updating
python datasets/auto_scrape_missing_supports.py --support-list "30036-riko-kashimoto,30034-rice-shower"

# Preview what would be done
python datasets/auto_scrape_missing_supports.py --support-list "30036-riko-kashimoto,30034-rice-shower" --dry-run

# Force update of specific supports
python datasets/auto_scrape_missing_supports.py --support-list "30036-riko-kashimoto,30034-rice-shower" --force-rescan
```

#### Full Library Maintenance
```bash
# Discover all supports and see what's missing (recommended first step)
python datasets/auto_scrape_missing_supports.py --discover-all --dry-run

# If list looks good, proceed with actual scraping
python datasets/auto_scrape_missing_supports.py --discover-all --force-rescan

# Following the prompts:
# 1. Review and merge scraped data into datasets/in_game/events.json
# 2. Run build_catalog.py to update the runtime catalog
# 3. Support registry updated automatically
```

#### Hybrid Approach
```bash
# Check both auto-discovered supports and specific ones of interest
python datasets/auto_scrape_missing_supports.py \
    --discover-all \
    --support-list "30036-riko-kashimoto,20029-seeking-the-pearl" \
    --dry-run
```

## Safety and Reliability

### Built-in Safeguards
1. **No Automatic Data Modification**: Script never writes directly to events.json
2. **Explicit User Approval Required**: For scraping and build steps (unless forced)
3. **Manual Data Review Mandatory**: You must examine and merge scraped data yourself
4. **Conservative Detection**: Only flags supports with complete, valid data as missing
5. **Clear Audit Trail**: Detailed logging shows exactly what was checked and found

### Data Integrity Protection
- Preserves existing events.json until you manually merge
- Registry tracks what's been checked, not what's been merged
- Output file separation prevents accidental overwrites
- Multiple confirmation points prevent accidental execution

This documentation provides a comprehensive guide to using and understanding the auto_scrape_missing_supports.py script. For the most current implementation details, always refer to the source code directly.