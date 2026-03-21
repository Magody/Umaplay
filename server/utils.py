import json
from pathlib import Path
import subprocess
from typing import Any, Dict, Tuple, List, Optional

_DEFAULT_NAV_PREFS: Dict[str, Dict[str, Any]] = {
    "shop": {
        "alarm_clock": True,
        "star_pieces": False,
        "parfait": False,
    },
    "team_trials": {
        "preferred_banner": 2,
    },
}

PREFS_DIR = Path(__file__).resolve().parent.parent / "prefs"
CONFIG_PATH = PREFS_DIR / "config.json"
SAMPLE_CONFIG_PATH = PREFS_DIR / "config.sample.json"
NAV_PATH = PREFS_DIR / "nav.json"
SAMPLE_NAV_PATH = PREFS_DIR / "nav.sample.json"

_DATASET_CACHE: Dict[str, Tuple[float, object]] = {}
_VALID_SUPPORT_RARITIES = {"SSR", "SR", "R"}
_VALID_SUPPORT_ATTRIBUTES = {"SPD", "STA", "PWR", "GUTS", "WIT", "PAL"}
_LEGACY_SUPPORT_NAME_ALIASES = {
    "Aoi Kiryuuin": "Aoi Kiryuin",
}


def _repo_root() -> Path:
    # server/utils.py -> server/ -> repo root is parent
    return Path(__file__).resolve().parent.parent


def _dataset_path(*parts: str) -> Path:
    return _repo_root() / "datasets" / "in_game" / Path(*parts)


def load_dataset_json(*rel_parts: str):
    """
    Load a dataset JSON with simple mtime-based caching.
    Example: load_dataset_json("skills.json")
             load_dataset_json("races.json")
    """
    path = _dataset_path(*rel_parts)
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return None

    cached = _DATASET_CACHE.get(key)
    if cached and cached[0] == mtime:
        return cached[1]

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    _DATASET_CACHE[key] = (mtime, data)
    return data

def _normalize_reward_priority(raw: Any, fallback: Optional[List[str]] = None) -> List[str]:
    allowed = ["skill_pts", "stats", "hints"]
    aliases = {
        "skill_points": "skill_pts",
        "skillpts": "skill_pts",
        "hint": "hints",
        "stat": "stats",
    }
    seen: List[str] = []
    if isinstance(raw, (list, tuple)):
        for item in raw:
            if not isinstance(item, str):
                continue
            key = item.strip().lower()
            if not key:
                continue
            mapped = aliases.get(key, key)
            if mapped in allowed and mapped not in seen:
                seen.append(mapped)
    baseline = fallback if fallback else allowed
    for fb in baseline:
        if fb not in seen and fb in allowed:
            seen.append(fb)
    return seen[: len(allowed)]

def _ensure_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return default

def _collapse_spaces(value: str) -> str:
    return " ".join(value.strip().split())

def _apply_legacy_support_alias(name: str) -> str:
    normalized = _collapse_spaces(name)
    for legacy, canonical in _LEGACY_SUPPORT_NAME_ALIASES.items():
        if normalized == legacy or normalized.startswith(f"{legacy} "):
            return f"{canonical}{normalized[len(legacy):]}"
    return normalized

def _canonicalize_support_name(name: Any, rarity: Any, attribute: Any) -> Optional[str]:
    if not isinstance(name, str):
        return None

    normalized = _apply_legacy_support_alias(name)
    if not normalized:
        return None

    if not isinstance(rarity, str) or not isinstance(attribute, str):
        return normalized

    attr = attribute.strip().upper()
    rar = rarity.strip().upper()
    if attr not in _VALID_SUPPORT_ATTRIBUTES or rar not in _VALID_SUPPORT_RARITIES:
        return normalized

    suffix = f"{attr} {rar}"
    if normalized.endswith(suffix) or normalized.endswith(f"{suffix}(Duplicate)"):
        return normalized

    return f"{normalized} {suffix}"

def _canonicalize_support_event_key(key: Any) -> Any:
    if not isinstance(key, str):
        return key

    parts = key.split("/", 4)
    if len(parts) != 5 or parts[0] != "support":
        return key

    canonical_name = _canonicalize_support_name(parts[1], parts[3], parts[2])
    if not canonical_name:
        return key

    return f"support/{canonical_name}/{parts[2]}/{parts[3]}/{parts[4]}"

def _canonicalize_event_setup(raw: Any) -> Any:
    if not isinstance(raw, dict):
        return raw

    result = dict(raw)
    supports = raw.get("supports")
    if isinstance(supports, list):
        normalized_supports = []
        for entry in supports:
            if not isinstance(entry, dict):
                normalized_supports.append(entry)
                continue

            support = dict(entry)
            canonical_name = _canonicalize_support_name(
                support.get("name"),
                support.get("rarity"),
                support.get("attribute"),
            )
            if canonical_name:
                support["name"] = canonical_name
            normalized_supports.append(support)

        result["supports"] = normalized_supports

    prefs = raw.get("prefs")
    if not isinstance(prefs, dict):
        return result

    prefs_out = dict(prefs)
    overrides = prefs.get("overrides")
    if isinstance(overrides, dict):
        prefs_out["overrides"] = {
            _canonicalize_support_event_key(key): value
            for key, value in overrides.items()
        }

    patterns = prefs.get("patterns")
    if isinstance(patterns, list):
        normalized_patterns = []
        for entry in patterns:
            if not isinstance(entry, dict):
                normalized_patterns.append(entry)
                continue
            pattern = entry.get("pattern")
            normalized_patterns.append(
                {
                    **entry,
                    "pattern": _canonicalize_support_event_key(pattern),
                }
                if isinstance(pattern, str)
                else entry
            )
        prefs_out["patterns"] = normalized_patterns

    result["prefs"] = prefs_out
    return result

def _canonicalize_config_event_setups(data: Any) -> Any:
    if not isinstance(data, dict):
        return data

    result = dict(data)
    presets = result.get("presets")
    if isinstance(presets, list):
        result["presets"] = [
            {**preset, "event_setup": _canonicalize_event_setup(preset.get("event_setup"))}
            if isinstance(preset, dict)
            else preset
            for preset in presets
        ]

    scenarios = result.get("scenarios")
    if not isinstance(scenarios, dict):
        return result

    normalized_scenarios = {}
    for key, branch in scenarios.items():
        if not isinstance(branch, dict):
            normalized_scenarios[key] = branch
            continue

        branch_out = dict(branch)
        branch_presets = branch.get("presets")
        if isinstance(branch_presets, list):
            branch_out["presets"] = [
                {**preset, "event_setup": _canonicalize_event_setup(preset.get("event_setup"))}
                if isinstance(preset, dict)
                else preset
                for preset in branch_presets
            ]
        normalized_scenarios[key] = branch_out

    result["scenarios"] = normalized_scenarios
    return result

def _normalize_support(entry: Any, slot: int, fallback_priority: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    if not isinstance(entry, dict):
        return None
    name = entry.get("name")
    rarity = entry.get("rarity")
    attribute = entry.get("attribute")
    canonical_name = _canonicalize_support_name(name, rarity, attribute)
    if not (canonical_name and isinstance(rarity, str) and isinstance(attribute, str)):
        return None
    result = {
        "slot": slot,
        "name": canonical_name,
        "rarity": rarity,
        "attribute": attribute,
    }
    if "priority" in entry and isinstance(entry["priority"], dict):
        result["priority"] = entry["priority"]
    reward_priority = _normalize_reward_priority(
        entry.get("rewardPriority", entry.get("reward_priority")),
        fallback_priority,
    )
    result["rewardPriority"] = reward_priority
    result["avoidEnergyOverflow"] = _ensure_bool(
        entry.get("avoidEnergyOverflow", entry.get("avoid_energy_overflow")), True
    )
    return result

def _normalize_entity(entry: Any, fallback_priority: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    if not isinstance(entry, dict):
        return None
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    return {
        "name": name,
        "avoidEnergyOverflow": _ensure_bool(
            entry.get("avoidEnergyOverflow", entry.get("avoid_energy_overflow")), True
        ),
        "rewardPriority": _normalize_reward_priority(
            entry.get("rewardPriority", entry.get("reward_priority")),
            fallback_priority,
        ),
    }

def load_event_setup_defaults(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    else:
        raw = _canonicalize_event_setup(raw)

    prefs_raw = raw.get("prefs")
    prefs_in = prefs_raw if isinstance(prefs_raw, dict) else {}
    global_reward_priority = _normalize_reward_priority(
        prefs_in.get("rewardPriority", prefs_in.get("reward_priority"))
    )

    supports_out: List[Optional[Dict[str, Any]]] = []
    supports_raw = raw.get("supports")
    supports_in = supports_raw if isinstance(supports_raw, list) else []
    for idx in range(6):
        entry = supports_in[idx] if idx < len(supports_in) else None
        supports_out.append(_normalize_support(entry, idx, global_reward_priority))

    scenario_out = _normalize_entity(raw.get("scenario"), global_reward_priority)
    trainee_out = _normalize_entity(raw.get("trainee"), global_reward_priority)
    overrides_raw = prefs_in.get("overrides")
    overrides = overrides_raw if isinstance(overrides_raw, dict) else {}
    patterns_raw = prefs_in.get("patterns")
    if isinstance(patterns_raw, list):
        safe_patterns = [p for p in patterns_raw if isinstance(p, dict)]
    else:
        safe_patterns = []
    defaults_raw = prefs_in.get("defaults")
    defaults = defaults_raw if isinstance(defaults_raw, dict) else {}
    prefs_out = {
        "overrides": overrides,
        "patterns": safe_patterns,
        "defaults": {
            "support": int(defaults.get("support", 1) or 1),
            "trainee": int(defaults.get("trainee", 1) or 1),
            "scenario": int(defaults.get("scenario", 1) or 1),
        },
        "rewardPriority": global_reward_priority,
    }

    return {
        "supports": supports_out,
        "scenario": scenario_out,
        "trainee": trainee_out,
        "prefs": prefs_out,
    }


def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r") as f:
            data = json.load(f)
    else:
        data = {}

    if not isinstance(data, dict):
        data = {}

    data = _canonicalize_config_event_setups(data)

    general = data.get("general")
    if not isinstance(general, dict):
        general = {}
        data["general"] = general
    general.setdefault("activeScenario", "ura")
    general.setdefault("scenarioConfirmed", False)
    general["scenarioConfirmed"] = bool(general.get("scenarioConfirmed"))

    scenarios_raw = data.get("scenarios")
    scenarios = dict(scenarios_raw) if isinstance(scenarios_raw, dict) else {}
    # Migrate legacy top-level presets structure into URA branch
    legacy_presets = data.pop("presets", None)
    legacy_active = data.pop("activePresetId", None)
    if isinstance(legacy_presets, list):
        existing_branch = scenarios.get("ura")
        if not isinstance(existing_branch, dict):
            existing_branch = {}
        branch_presets_raw = existing_branch.get("presets")
        branch_presets = branch_presets_raw if isinstance(branch_presets_raw, list) else []
        merged_presets: List[dict] = [p for p in branch_presets if isinstance(p, dict)]
        merged_presets.extend(p for p in legacy_presets if isinstance(p, dict))
        branch_active = (
            existing_branch.get("activePresetId")
            if isinstance(existing_branch.get("activePresetId"), str)
            else None
        )
        if isinstance(legacy_active, str):
            branch_active = legacy_active
        elif branch_active is None and merged_presets:
            first_id = merged_presets[0].get("id")
            if isinstance(first_id, str):
                branch_active = first_id
        scenarios["ura"] = {
            "presets": merged_presets,
            "activePresetId": branch_active,
        }

    normalized_scenarios: dict[str, dict] = {}
    for key, branch_raw in scenarios.items():
        branch = branch_raw if isinstance(branch_raw, dict) else {}
        presets_raw = branch.get("presets")
        presets = [p for p in presets_raw if isinstance(p, dict)] if isinstance(presets_raw, list) else []
        active_id_raw = branch.get("activePresetId")
        active_id = active_id_raw if isinstance(active_id_raw, str) else None
        normalized_scenarios[key] = {"presets": presets, "activePresetId": active_id}

    if "ura" not in normalized_scenarios:
        normalized_scenarios["ura"] = {"presets": [], "activePresetId": None}
    if "unity_cup" not in normalized_scenarios:
        normalized_scenarios["unity_cup"] = {"presets": [], "activePresetId": None}

    # Ensure active scenario branch has an active preset when possible
    active_key = general.get("activeScenario", "ura")
    branch = normalized_scenarios.get(active_key) or normalized_scenarios.get("ura")
    if branch and not branch.get("activePresetId") and branch.get("presets"):
        first = branch["presets"][0]
        first_id = first.get("id") if isinstance(first, dict) else None
        if isinstance(first_id, str):
            branch["activePresetId"] = first_id
    data["scenarios"] = normalized_scenarios

    return data


def save_config(data: dict):
    normalized = _canonicalize_config_event_setups(data)
    with open(CONFIG_PATH, "w") as f:
        json.dump(normalized, f, indent=2)


def load_nav_prefs() -> Dict[str, Dict[str, Any]]:
    ensure_nav_exists()
    try:
        with open(NAV_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
            if isinstance(raw, dict):
                return raw
    except Exception:
        pass
    return json.loads(json.dumps(_DEFAULT_NAV_PREFS))


def save_nav_prefs(data: Dict[str, Dict[str, Any]]):
    with open(NAV_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# -----------------------------
# Datasets helpers (skills/races)
# -----------------------------
def ensure_config_exists() -> bool:
    """
    Ensure config.json exists. If it doesn't, try to seed it with
    config.sample.json; otherwise write an empty JSON object.
    Returns True if we created it now, False if it already existed.
    """
    if CONFIG_PATH.exists():
        return False
    try:
        if SAMPLE_CONFIG_PATH.exists():
            with open(SAMPLE_CONFIG_PATH, "r") as sf:
                sample = json.load(sf)
            save_config(sample)
        else:
            save_config({})
        return True
    except Exception:
        # As a last resort, write {}
        try:
            save_config({})
            return True
        except Exception:
            return False


def ensure_nav_exists() -> bool:
    """Ensure nav.json exists; seed from sample or defaults when missing."""
    if NAV_PATH.exists():
        return False
    try:
        if SAMPLE_NAV_PATH.exists():
            with open(SAMPLE_NAV_PATH, "r", encoding="utf-8") as sf:
                sample = json.load(sf)
            save_nav_prefs(sample)
        else:
            save_nav_prefs(_DEFAULT_NAV_PREFS)
        return True
    except Exception:
        try:
            save_nav_prefs(_DEFAULT_NAV_PREFS)
            return True
        except Exception:
            return False


def run_cmd(args: list[str], cwd: Path, timeout: int = 30) -> Tuple[int, str, str]:
    """Run a command and return (code, stdout, stderr)."""
    proc = subprocess.Popen(
        args, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
    return proc.returncode, out, err


def repo_root() -> Path:
    # repo root is parent of /core (same logic as Settings.ROOT_DIR)
    return Path(__file__).resolve().parent.parent
