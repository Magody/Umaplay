"""
Unit tests for support card detection and automation logic
"""
import json
import tempfile
import os
from pathlib import Path
import sys

# Add the datasets directory to path so we can import our modules
sys.path.insert(0, str(Path(__file__).parent.parent / "datasets"))

def test_load_support_registry():
    """Test loading the support registry"""
    from auto_scrape_missing_supports import load_support_registry

    # Test with non-existent file (should return default)
    registry = load_support_registry()
    assert isinstance(registry, dict)
    assert 'supports' in registry
    assert 'version' in registry
    assert registry['version'] == 'global'
    assert 'metadata' in registry

def test_save_support_registry():
    """Test saving the support registry"""
    from auto_scrape_missing_supports import load_support_registry, save_support_registry

    # Create a temporary registry
    test_registry = {
        "last_updated": "2026-04-08T10:00:00",
        "version": "global",
        "supports": [
            {
                "name": "Test Support",
                "rarity": "SSR",
                "attribute": "SPD",
                "first_seen": "2026-01-01",
                "last_scraped": "2026-04-08"
            }
        ],
        "metadata": {
            "description": "Test registry",
            "format_version": "1.0"
        }
    }

    # Save to a temporary file
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
        temp_path = f.name

    try:
        # Temporarily override the registry path
        original_path = Path("datasets/in_game/support_registry.json")
        backup_exists = original_path.exists()

        # Create the directory if it doesn't exist
        original_path.parent.mkdir(parents=True, exist_ok=True)

        # Backup original if it exists
        if backup_exists:
            original_path.rename(original_path.with_suffix('.json.backup'))

        # Save our test registry to the actual path
        save_support_registry(test_registry)

        # Load it back and verify
        loaded_registry = load_support_registry()
        assert loaded_registry['last_updated'] == "2026-04-08T10:00:00"
        assert len(loaded_registry['supports']) == 1
        assert loaded_registry['supports'][0]['name'] == "Test Support"

    finally:
        # Restore original if it existed
        backup_path = original_path.with_suffix('.json.backup')
        if backup_path.exists():
            if original_path.exists():
                original_path.unlink()
            backup_path.rename(original_path)
        elif original_path.exists():
            original_path.unlink()

        # Clean up temp file
        if os.path.exists(temp_path):
            os.unlink(temp_path)

def test_load_existing_supports_from_events():
    """Test loading existing supports from events.json"""
    from auto_scrape_missing_supports import load_existing_supports_from_events

    # This should work with the actual events.json file
    existing = load_existing_supports_from_events()
    assert isinstance(existing, list)
    # We know there are supports in the file from earlier testing
    if len(existing) > 0:
        # Check structure of first support
        first_support = existing[0]
        assert 'name' in first_support
        assert 'rarity' in first_support
        assert 'attribute' in first_support
        assert 'key' in first_support
        assert isinstance(first_support['key'], str)

def test_generate_support_defaults():
    """Test generating support defaults string"""
    from auto_scrape_missing_supports import generate_support_defaults

    # Test with sample data
    sample_supports = [
        {'name': 'Test Support', 'rarity': 'SSR', 'attribute': 'SPD'},
        {'name': 'Another Support', 'rarity': 'SR', 'attribute': 'PWR'}
    ]

    defaults = generate_support_defaults(sample_supports)
    expected = "Test Support-SSR-SPD|Another Support-SR-PWR"
    assert defaults == expected

    # Test with empty list
    assert generate_support_defaults([]) == ""

    # Test with missing fields (should skip invalid entries)
    invalid_supports = [
        {'name': 'Valid Support', 'rarity': 'SSR', 'attribute': 'SPD'},
        {'name': 'Invalid Support', 'rarity': 'SSR'},  # Missing attribute
        {'rarity': 'SSR', 'attribute': 'SPD'},  # Missing name
        {'name': 'Also Invalid', 'attribute': 'SPD'},  # Missing rarity
    ]

    defaults = generate_support_defaults(invalid_supports)
    assert defaults == "Valid Support-SSR-SPD"

    # Test with None values
    none_supports = [
        {'name': None, 'rarity': 'SSR', 'attribute': 'SPD'},
        {'name': 'Valid Support', 'rarity': None, 'attribute': 'SPD'},
        {'name': 'Valid Support', 'rarity': 'SSR', 'attribute': None},
    ]

    defaults = generate_support_defaults(none_supports)
    assert defaults == ""  # All should be skipped

def test_find_missing_supports():
    """Test finding missing supports"""
    from auto_scrape_missing_supports import find_missing_supports

    existing = [
        {'name': 'Existing Support', 'rarity': 'SSR', 'attribute': 'SPD', 'key': 'existing_support_ssr_spd'}
    ]

    gametora_data = [
        {
            'name': 'Existing Support',
            'rarity': 'SSR',
            'attribute': 'SPD',
            'choice_events': []
        },
        {
            'name': 'Missing Support',
            'rarity': 'SR',
            'attribute': 'PWR',
            'choice_events': []
        },
        {
            'name': 'Another Missing Support',
            'rarity': 'SSR',
            'attribute': 'WIT',
            'choice_events': []
        }
    ]

    missing = find_missing_supports(existing, gametora_data)
    assert len(missing) == 2
    # Check that we got the missing ones
    missing_names = [s['name'] for s in missing]
    assert 'Missing Support' in missing_names
    assert 'Another Missing Support' in missing_names
    # Check that we didn't get the existing one
    assert 'Existing Support' not in missing_names

def test_find_missing_supports_empty():
    """Test finding missing supports when none are missing"""
    from auto_scrape_missing_supports import find_missing_supports

    existing = [
        {'name': 'Support A', 'rarity': 'SSR', 'attribute': 'SPD', 'key': 'support_a_ssr_spd'},
        {'name': 'Support B', 'rarity': 'SR', 'attribute': 'PWR', 'key': 'support_b_sr_pwr'}
    ]

    gametora_data = [
        {
            'name': 'Support A',
            'rarity': 'SSR',
            'attribute': 'SPD',
            'choice_events': []
        },
        {
            'name': 'Support B',
            'rarity': 'SR',
            'attribute': 'PWR',
            'choice_events': []
        }
    ]

    missing = find_missing_supports(existing, gametora_data)
    assert len(missing) == 0

if __name__ == "__main__":
    test_load_support_registry()
    test_save_support_registry()
    test_load_existing_supports_from_events()
    test_generate_support_defaults()
    test_find_missing_supports()
    test_find_missing_supports_empty()
    print("All tests passed!")