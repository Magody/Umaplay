from __future__ import annotations

import os
from pathlib import Path
import math
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from core.utils.logger import logger_uma


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    """Return environment variable (Umaplay_* has priority), else default."""
    return os.getenv(f"Umaplay_{name}", os.getenv(name, default))


def _env_bool(name: str, default: bool) -> bool:
    v = _env(name)
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    v = _env(name)
    try:
        return int(v) if v is not None else default
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    v = _env(name)
    try:
        return float(v) if v is not None else default
    except Exception:
        return default


DEFAULT_SUPPORT_PRIORITY: Dict[str, Union[float, bool]] = {
    "enabled": True,
    "scoreBlueGreen": 0.75,
    "scoreOrangeMax": 0.5,
}


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


class Settings:
    """
    Class-style config holder (easy to import as `Settings.*` without instantiation).
    Adjust values below or override via environment variables prefixed with Umaplay_.
    """

    HOTKEY = "F2"
    DEBUG = _env_bool("DEBUG", default=True)
    HOST = "127.0.0.1"
    PORT = 8000

    _ROOT_DIR = Path(__file__).resolve().parents[1]  # repo root (parent of /core)
    RACE_DATA_PATH = _ROOT_DIR / "datasets" / "in_game" / "races.json"

    # --------- Training Configuration ---------
    # Undertrain threshold as a percentage (e.g., 6.0 for 6%)
    UNDERTRAIN_THRESHOLD: float = _env_float("UNDERTRAIN_THRESHOLD", default=6.0)
    # Number of top stats to focus on for undertraining
    TOP_STATS_FOCUS: int = _env_int("TOP_STATS_FOCUS", default=3)
    # Race if no good training options are available (default: False = skip race if no good training)
    RACE_IF_NO_GOOD_VALUE: bool = _env_bool("RACE_IF_NO_GOOD_VALUE", default=False)

    # --------- Career Loop Configuration ---------
    # Career automation loop settings for unattended career farming
    CAREER_LOOP_ENABLED: bool = _env_bool("CAREER_LOOP_ENABLED", default=True)
    CAREER_LOOP_MAX_CAREERS: Optional[int] = _env_int("CAREER_LOOP_MAX_CAREERS", default=5) or None  # None = infinite
    CAREER_LOOP_PREFERRED_SUPPORT: str = _env("CAREER_LOOP_PREFERRED_SUPPORT", default="Riko Kashimoto") or "Riko Kashimoto"
    CAREER_LOOP_PREFERRED_LEVEL: int = _env_int("CAREER_LOOP_PREFERRED_LEVEL", default=50)
    CAREER_LOOP_MAX_REFRESH: int = _env_int("CAREER_LOOP_MAX_REFRESH", default=5)
    CAREER_LOOP_REFRESH_WAIT: float = _env_float("CAREER_LOOP_REFRESH_WAIT", default=5.0)
    CAREER_LOOP_ERROR_THRESHOLD: int = _env_int("CAREER_LOOP_ERROR_THRESHOLD", default=5)

    # --------- Project roots & paths ---------
    CORE_DIR: Path = Path(__file__).resolve().parent
    ROOT_DIR: Path = CORE_DIR.parent

    # Common directories
    ASSETS_DIR: Path = Path(_env("ASSETS_DIR") or (ROOT_DIR / "web/public"))
    MODELS_DIR: Path = Path(_env("MODELS_DIR") or (ROOT_DIR / "models"))
    DEBUG_DIR: Path = Path(_env("DEBUG_DIR") or (ROOT_DIR / "debug"))
    PREFS_DIR: Path = Path(_env("PREFS_DIR") or (ROOT_DIR / "prefs"))
    RUNTIME_SKILL_MEMORY_PATH: Path = Path(
        _env("RUNTIME_SKILL_MEMORY_PATH")
        or (PREFS_DIR / "runtime_skill_memory.json")
    )

    # Models & weights
    _YOLO_WEIGHTS_URA_ENV = _env("YOLO_WEIGHTS_URA") or _env("YOLO_WEIGHTS")
    YOLO_WEIGHTS_URA: Path = Path(_YOLO_WEIGHTS_URA_ENV or (MODELS_DIR / "uma_ura.pt"))
    YOLO_WEIGHTS_UNITY_CUP: Path = Path(
        _env("YOLO_WEIGHTS_UNITY_CUP") or (MODELS_DIR / "uma_unity_cup.pt")
    )

    YOLO_WEIGHTS_NAV: Path = Path(
        _env("YOLO_WEIGHTS_NAV") or (MODELS_DIR / "uma_nav.pt")
    )

    YOLO_WEIGHTS_CAREER_LOOP: Path = Path(
        _env("YOLO_WEIGHTS_CAREER_LOOP") or (MODELS_DIR / "uma_career_loop.pt")
    )

    IS_BUTTON_ACTIVE_CLF_PATH: Path = Path(
        _env("IS_BUTTON_ACTIVE_CLF_PATH") or (MODELS_DIR / "active_button_clf.joblib")
    )

    UNITY_CUP_SPIRIT_COLOR_CLASS_PATH: Path = Path(
        _env("UNITY_CUP_SPIRIT_COLOR_CLASS_PATH") or (MODELS_DIR / "unity_spirit_cnn.pt")
    )

    UNITY_CUP_ADVANCED_DEFAULT: Dict[str, Any] = {
        "burst_allowed_stats": ["SPD", "STA", "PWR", "WIT"],
        "scores": {
            "rainbowCombo": 0.5,
            "whiteSpiritFill": 0.4,
            "whiteSpiritExploded": 0.13,
            "whiteComboBase": 0.2,
            "whiteComboPerFill": 0.25,
            "whiteComboExplodedTiny": 0.01,
            "blueSpiritEach": 0.5,
            "blueComboPerExtraFill": 0.25,
        },
        "multipliers": {
            "juniorClassic": {"white": 1.0, "whiteCombo": 1.0, "blueCombo": 1.0},
            "senior": {"white": 1.0, "whiteCombo": 1.0, "blueCombo": 1.0},
        },
        "burstDeadline": {
            "preSeniorNovEarlyTurns": 4,
            "finalSeasonExplodeLastTurns": 2,
        },
        "opponentSelection": {
            "race1": 2,
            "race2": 1,
            "race3": 1,
            "race4": 1,
            "defaultUnknown": 1,
        },
    }

    MODE: str = _env("MODE", "steam") or "steam"
    USE_ADB: bool = _env_bool("USE_ADB", False)
    ADB_DEVICE: Optional[str] = _env("ADB_DEVICE", "localhost:5555")

    # --------- Detection (YOLO) ---------
    YOLO_IMGSZ: int = _env_int("YOLO_IMGSZ", default=832)
    YOLO_CONF: float = _env_float("YOLO_CONF", default=0.50)  # should be 0.7 in general, but we are a little conservative here...
    YOLO_IOU: float = _env_float("YOLO_IOU", default=0.45)
    UNITY_CUP_GOLDEN_CONF: float = _env_float("UNITY_CUP_GOLDEN_CONF", default=0.61)
    UNITY_CUP_GOLDEN_RELAXED_CONF: float = _env_float(
        "UNITY_CUP_GOLDEN_RELAXED_CONF", default=0.35
    )
    UNITY_CUP_RACE_DAY_CONF: float = _env_float("UNITY_CUP_RACE_DAY_CONF", default=0.61)
    UNITY_CUP_RACE_DAY_RELAXED_CONF: float = _env_float(
        "UNITY_CUP_RACE_DAY_RELAXED_CONF", default=0.35
    )
    UNITY_CUP_FALLBACK_CAPTURE_DEBUG: bool = _env_bool(
        "UNITY_CUP_FALLBACK_CAPTURE_DEBUG", default=False
    )

    # --------- Logging ---------
    LOG_LEVEL: str = _env("LOG_LEVEL", "DEBUG" if DEBUG else "INFO") or (
        "DEBUG" if DEBUG else "INFO"
    )
    FAST_MODE = False
    USE_FAST_OCR = True
    USE_GPU = True
    HINT_IS_IMPORTANT = False
    MAX_FAILURE = 20  # integer, no pct

    STORE_FOR_TRAINING = True
    STORE_FOR_TRAINING_THRESHOLD = 0.71  # YOLO baseline to say is accurate will be 0.7

    ANDROID_WINDOW_TITLE = "23117RA68G"
    WINDOW_TITLE = "Umamusume"

    AGENT_NAME_URA: str = "ura"
    AGENT_NAME_UNITY_CUP: str = "unity_cup"

    AGENT_NAME_NAV: str = "agent_nav"
    USE_EXTERNAL_PROCESSOR = False
    EXTERNAL_PROCESSOR_URL = "http://127.0.0.1:8001"
    TEMPLATE_MATCH_TIMEOUT: float = _env_float("TEMPLATE_MATCH_TIMEOUT", default=300.0)

    REFERENCE_STATS = {
        "SPD": 1150,
        "STA": 900,
        "PWR": 700,
        "GUTS": 300,
        "WIT": 400,
    }

    MINIMUM_SKILL_PTS = 700
    WEAK_TURN_SV_BY_SCENARIO: Dict[str, float] = {
        "ura": 1.0,
        "unity_cup": 1.75,
    }
    RACE_PRECHECK_SV_BY_SCENARIO: Dict[str, float] = {
        "ura": 2.5,
        "unity_cup": 4.0,
    }
    WEAK_TURN_SV: float = WEAK_TURN_SV_BY_SCENARIO["ura"]
    RACE_PRECHECK_SV: float = RACE_PRECHECK_SV_BY_SCENARIO["ura"]
    LOBBY_PRECHECK_ENABLE: bool = False
    JUNIOR_MINIMAL_MOOD: Optional[str] = None
    GOAL_RACE_FORCE_TURNS: int = 5
    PLAN_RACES_TENTATIVE: Dict[str, bool] = {}
    # Skills optimization (interval/delta gates)
    SKILL_CHECK_INTERVAL: int = 3  # only check skills every N turns (1 = every turn)
    SKILL_PTS_DELTA: int = (
        60  # open Skills if points increased by at least this much since last check
    )
    ACCEPT_CONSECUTIVE_RACE = True
    TRY_AGAIN_ON_FAILED_GOAL = True
    AUTO_REST_MINIMUM = 20

    PRIORITY_STATS = ["SPD", "STA", "WIT", "PWR", "GUTS"]

    MINIMAL_MOOD = "normal"

    ACTIVE_SCENARIO: str = "ura"
    ACTIVE_AGENT_NAME: str = AGENT_NAME_URA
    ACTIVE_YOLO_WEIGHTS: Path = YOLO_WEIGHTS_URA
    ACTIVE_SKILL_MEMORY_PATH: Path = RUNTIME_SKILL_MEMORY_PATH

    SUPPORT_PRIORITIES_HAVE_CUSTOMIZATION: bool = False
    SUPPORT_CUSTOM_PRIORITY_KEYS: Set[Tuple[str, str, str]] = set()
    SUPPORT_AVOID_ENERGY: Dict[Tuple[str, str, str], bool] = {}
    # Union of skills to re-check immediately after taking a configured hint
    RECHECK_AFTER_HINT_SKILLS: List[str] = []
    SHOW_PRESET_OVERLAY: bool = _env_bool("SHOW_PRESET_OVERLAY", True)
    PRESET_OVERLAY_DURATION: float = _env_float("PRESET_OVERLAY_DURATION", 4)
    NAV_PREFS: Dict[str, Dict[str, Any]] = {
        "shop": dict(_DEFAULT_NAV_PREFS["shop"]),
        "team_trials": dict(_DEFAULT_NAV_PREFS["team_trials"]),
    }

    UNITY_CUP_ADVANCED: Dict[str, Any] = {
        "burst_allowed_stats": list(UNITY_CUP_ADVANCED_DEFAULT["burst_allowed_stats"]),
        "scores": dict(UNITY_CUP_ADVANCED_DEFAULT["scores"]),
        "multipliers": {
            "juniorClassic": dict(UNITY_CUP_ADVANCED_DEFAULT["multipliers"]["juniorClassic"]),
            "senior": dict(UNITY_CUP_ADVANCED_DEFAULT["multipliers"]["senior"]),
        },
        "burstDeadline": dict(UNITY_CUP_ADVANCED_DEFAULT["burstDeadline"]),
        "opponentSelection": dict(UNITY_CUP_ADVANCED_DEFAULT["opponentSelection"]),
    }

    # Keep the last applied config so other modules can extract runtime preset safely
    _last_config: dict | None = None

    @classmethod
    def resolve_window_title(cls, mode: str) -> str:
        if mode == "steam":
            return "Umamusume"
        elif mode == "bluestack":
            return "BlueStacks App Player"
        elif mode == "adb":
            return ""
        else:  # scrcpy
            return cls.ANDROID_WINDOW_TITLE

    @classmethod
    def apply_config(cls, cfg: dict) -> None:
        """
        Apply values coming from the web UI config.json into process Settings.
        Only the keys we care about on the Python side are mapped here.
        """
        # Persist a copy for later retrieval (e.g., training policy runtime extraction)
        try:
            cls._last_config = dict(cfg or {})
        except Exception:
            cls._last_config = None

        g = (cfg or {}).get("general", {}) or {}
        adv = g.get("advanced", {}) or {}

        # General
        raw_mode = str(g.get("mode", cls.MODE)).strip().lower()
        if raw_mode not in {"steam", "scrcpy", "bluestack", "adb"}:
            raw_mode = cls.MODE.lower()
        cls.MODE = raw_mode

        cls.USE_ADB = bool(g.get("useAdb", cls.USE_ADB))
        adb_device = g.get("adbDevice")
        if isinstance(adb_device, str) and adb_device.strip():
            cls.ADB_DEVICE = adb_device.strip()

        if cls.MODE == "adb":
            cls.USE_ADB = True
            cls.WINDOW_TITLE = cls.WINDOW_TITLE or "Umamusume"
        elif cls.MODE != "bluestack":
            cls.USE_ADB = False
        # One windowTitle for both Steam and scrcpy (Steam still uses it)
        wt = g.get("windowTitle")
        if wt:
            cls.WINDOW_TITLE = wt
            cls.ANDROID_WINDOW_TITLE = wt
        cls.FAST_MODE = bool(g.get("fastMode", cls.FAST_MODE))
        cls.TRY_AGAIN_ON_FAILED_GOAL = bool(
            g.get("tryAgainOnFailedGoal", cls.TRY_AGAIN_ON_FAILED_GOAL)
        )
        if cls.DEBUG:
            logger_uma.info(
                "[settings] TRY_AGAIN_ON_FAILED_GOAL=%s",
                cls.TRY_AGAIN_ON_FAILED_GOAL,
            )
        cls.HINT_IS_IMPORTANT = bool(g.get("prioritizeHint", cls.HINT_IS_IMPORTANT))
        cls.MAX_FAILURE = int(g.get("maxFailure", cls.MAX_FAILURE))
        cls.ACCEPT_CONSECUTIVE_RACE = bool(
            g.get("acceptConsecutiveRace", cls.ACCEPT_CONSECUTIVE_RACE)
        )
        scenario_raw = str(g.get("activeScenario", cls.ACTIVE_SCENARIO)).strip().lower()
        cls.ACTIVE_SCENARIO = cls.normalize_scenario(scenario_raw)
        cls.SCENARIO_CONFIRMED = bool(g.get("scenarioConfirmed", getattr(cls, "SCENARIO_CONFIRMED", False)))
        cls.ACTIVE_AGENT_NAME = cls.resolve_agent_name(cls.ACTIVE_SCENARIO)
        cls.ACTIVE_YOLO_WEIGHTS = cls.resolve_yolo_weights_path(cls.ACTIVE_SCENARIO)
        cls.ACTIVE_SKILL_MEMORY_PATH = cls.resolve_skill_memory_path(cls.ACTIVE_SCENARIO)

        scenario_key = cls.ACTIVE_SCENARIO
        cls.WEAK_TURN_SV = cls.WEAK_TURN_SV_BY_SCENARIO.get(
            scenario_key, cls.WEAK_TURN_SV_BY_SCENARIO.get("ura", 1.0)
        )
        cls.RACE_PRECHECK_SV = cls.RACE_PRECHECK_SV_BY_SCENARIO.get(
            scenario_key, cls.RACE_PRECHECK_SV_BY_SCENARIO.get("ura", 2.5)
        )
        cls.LOBBY_PRECHECK_ENABLE = False
        cls.JUNIOR_MINIMAL_MOOD = None
        cls.PLAN_RACES_TENTATIVE = {}

        def _normalize_unity_cup_advanced(raw_adv: Any) -> Dict[str, Any]:
            adv_defaults = cls.UNITY_CUP_ADVANCED_DEFAULT
            if not isinstance(raw_adv, dict):
                raw_adv = {}

            allowed_stats = {"SPD", "STA", "PWR", "GUTS", "WIT"}

            burst_stats = raw_adv.get("burstAllowedStats") or adv_defaults["burst_allowed_stats"]
            if isinstance(burst_stats, list):
                burst_stats = [
                    str(s).upper()
                    for s in burst_stats
                    if str(s).upper() in allowed_stats
                ]
            else:
                burst_stats = list(adv_defaults["burst_allowed_stats"])

            def _merge_nested(default_block: Dict[str, Any], incoming: Any) -> Dict[str, Any]:
                if not isinstance(incoming, dict):
                    incoming = {}
                merged: Dict[str, Any] = {}
                for key, default_value in default_block.items():
                    value = incoming.get(key, default_value)
                    if isinstance(default_value, dict):
                        merged[key] = _merge_nested(default_value, value)
                    else:
                        try:
                            merged[key] = float(value)
                        except (TypeError, ValueError):
                            merged[key] = default_value
                return merged

            scores = _merge_nested(adv_defaults["scores"], raw_adv.get("scores"))
            multipliers = _merge_nested(adv_defaults["multipliers"], raw_adv.get("multipliers"))
            burst_deadline = _merge_nested(adv_defaults["burstDeadline"], raw_adv.get("burstDeadline"))

            opponents_raw = raw_adv.get("opponentSelection")
            opponent_defaults = adv_defaults["opponentSelection"]
            opponent_selection: Dict[str, int] = {}
            if not isinstance(opponents_raw, dict):
                opponents_raw = {}
            for slot, default_value in opponent_defaults.items():
                try:
                    value = int(opponents_raw.get(slot, default_value))
                except (TypeError, ValueError):
                    value = default_value
                value = max(1, min(3, value))
                opponent_selection[slot] = value

            return {
                "burst_allowed_stats": burst_stats or list(adv_defaults["burst_allowed_stats"]),
                "scores": scores,
                "multipliers": multipliers,
                "burstDeadline": burst_deadline,
                "opponentSelection": opponent_selection,
            }

        presets: List[dict] = []
        active_id: Optional[str] = None
        scenarios_raw = cfg.get("scenarios") if isinstance(cfg, dict) else None

        def _read_branch(raw_branch: Any) -> tuple[List[dict], Optional[str]]:
            if not isinstance(raw_branch, dict):
                return [], None
            branch_presets = raw_branch.get("presets")
            if not isinstance(branch_presets, list):
                branch_presets = []
            active = raw_branch.get("activePresetId")
            if not isinstance(active, str):
                active = None
            return branch_presets, active

        if isinstance(scenarios_raw, dict):
            presets, active_id = _read_branch(scenarios_raw.get(cls.ACTIVE_SCENARIO))
            if not presets:
                # Fallback to URA branch when the selected scenario is missing
                presets, active_id = _read_branch(scenarios_raw.get("ura"))

        if not presets:
            presets = (cfg or {}).get("presets") or []
            active_id = (cfg or {}).get("activePresetId")

        preset = next(
            (
                p
                for p in presets
                if isinstance(p, dict) and p.get("id") == active_id
            ),
            None,
        )
        if not preset and presets:
            first = presets[0]
            preset = first if isinstance(first, dict) else None

        preset_data = preset or {}

        if cls.normalize_scenario(scenario_key) == "unity_cup":
            cls.UNITY_CUP_ADVANCED = _normalize_unity_cup_advanced(preset_data.get("unityCupAdvanced"))
        else:
            cls.UNITY_CUP_ADVANCED = {
                "burst_allowed_stats": list(cls.UNITY_CUP_ADVANCED_DEFAULT["burst_allowed_stats"]),
                "scores": dict(cls.UNITY_CUP_ADVANCED_DEFAULT["scores"]),
                "multipliers": {
                    "juniorClassic": dict(cls.UNITY_CUP_ADVANCED_DEFAULT["multipliers"]["juniorClassic"]),
                    "senior": dict(cls.UNITY_CUP_ADVANCED_DEFAULT["multipliers"]["senior"]),
                },
                "burstDeadline": dict(cls.UNITY_CUP_ADVANCED_DEFAULT["burstDeadline"]),
                "opponentSelection": dict(cls.UNITY_CUP_ADVANCED_DEFAULT["opponentSelection"]),
            }

        # Minimum skill points is now per-preset. Fall back to legacy general setting if missing.
        skill_pts_value = preset_data.get("skillPtsCheck")
        if skill_pts_value is None:
            skill_pts_value = g.get("skillPtsCheck", cls.MINIMUM_SKILL_PTS)
        try:
            cls.MINIMUM_SKILL_PTS = max(0, int(skill_pts_value))
        except Exception:
            try:
                cls.MINIMUM_SKILL_PTS = max(
                    0, int(g.get("skillPtsCheck", cls.MINIMUM_SKILL_PTS))
                )
            except Exception:
                pass

        deck, priorities, avoid_energy = cls._extract_support_priorities_from_preset(
            preset_data
        )
        cls.SUPPORT_DECK = deck
        cls.SUPPORT_CARD_PRIORITIES = priorities
        custom_keys = {
            key for key, p in priorities.items() if cls._priority_is_custom(p)
        }
        cls.SUPPORT_CUSTOM_PRIORITY_KEYS = custom_keys
        cls.SUPPORT_PRIORITIES_HAVE_CUSTOMIZATION = bool(custom_keys)
        cls.SUPPORT_AVOID_ENERGY = avoid_energy
        # Pre-compute recheck skills union for agent hook
        try:
            recheck_skills: Set[str] = set()
            for data in priorities.values():
                if not isinstance(data, dict):
                    continue
                if not bool(data.get("recheckAfterHint", False)):
                    continue
                skills = data.get("skillsRequiredForPriority")
                if isinstance(skills, list):
                    for s in skills:
                        s2 = str(s).strip()
                        if s2:
                            recheck_skills.add(s2)
            cls.RECHECK_AFTER_HINT_SKILLS = sorted(recheck_skills)
        except Exception:
            cls.RECHECK_AFTER_HINT_SKILLS = []

        cls.MINIMAL_MOOD = str(preset_data.get("minimalMood", cls.MINIMAL_MOOD))
        cls.REFERENCE_STATS = preset_data.get("targetStats", cls.REFERENCE_STATS)
        cls.PRIORITY_STATS = preset_data.get("priorityStats", cls.PRIORITY_STATS)
        # Prefer per-preset hint toggle; fallback to general for backward compatibility
        try:
            cls.HINT_IS_IMPORTANT = bool(
                preset_data.get(
                    "prioritizeHint", g.get("prioritizeHint", cls.HINT_IS_IMPORTANT)
                )
            )
        except Exception:
            cls.HINT_IS_IMPORTANT = bool(g.get("prioritizeHint", cls.HINT_IS_IMPORTANT))
        # Advanced
        hk = adv.get("hotkey")
        if hk:
            cls.HOTKEY = hk
        cls.DEBUG = bool(adv.get("debugMode", cls.DEBUG))
        cls.USE_EXTERNAL_PROCESSOR = bool(
            adv.get("useExternalProcessor", cls.USE_EXTERNAL_PROCESSOR)
        )
        url = adv.get("externalProcessorUrl")
        if url:
            cls.EXTERNAL_PROCESSOR_URL = url
        # Match UI/schema key: 'autoRestMinimum'
        cls.AUTO_REST_MINIMUM = int(adv.get("autoRestMinimum", cls.AUTO_REST_MINIMUM))
        if "showPresetOverlay" in adv:
            try:
                cls.SHOW_PRESET_OVERLAY = bool(adv.get("showPresetOverlay"))
            except Exception:
                pass
        if "presetOverlaySeconds" in adv:
            try:
                cls.PRESET_OVERLAY_DURATION = max(1.0, float(adv.get("presetOverlaySeconds")))
            except Exception:
                pass
         # Career Loop Configuration
        career_loop = adv.get("careerLoop", {}) or {}
        cls.CAREER_LOOP_ENABLED = bool(career_loop.get("enabled", cls.CAREER_LOOP_ENABLED))
        
        max_careers = career_loop.get("maxCareers")
        if max_careers is not None:
            try:
                cls.CAREER_LOOP_MAX_CAREERS = int(max_careers) if int(max_careers) > 0 else None
            except (TypeError, ValueError):
                pass
        
        preferred_support = career_loop.get("preferredSupport")
        if preferred_support and isinstance(preferred_support, str):
            cls.CAREER_LOOP_PREFERRED_SUPPORT = preferred_support.strip()
        
        preferred_level = career_loop.get("preferredLevel")
        if preferred_level is not None:
            try:
                cls.CAREER_LOOP_PREFERRED_LEVEL = max(1, min(100, int(preferred_level)))
            except (TypeError, ValueError):
                pass
        
        max_refresh = career_loop.get("maxRefresh")
        if max_refresh is not None:
            try:
                cls.CAREER_LOOP_MAX_REFRESH = max(0, min(20, int(max_refresh)))
            except (TypeError, ValueError):
                pass
        
        refresh_wait = career_loop.get("refreshWait")
        if refresh_wait is not None:
            try:
                cls.CAREER_LOOP_REFRESH_WAIT = max(1.0, min(30.0, float(refresh_wait)))
            except (TypeError, ValueError):
                pass
        
        error_threshold = career_loop.get("errorThreshold")
        if error_threshold is not None:
            try:
                cls.CAREER_LOOP_ERROR_THRESHOLD = max(1, min(20, int(error_threshold)))
            except (TypeError, ValueError):
                pass

        # Update training configuration
        undertrain_threshold = float(
            adv.get("undertrainThreshold", cls.UNDERTRAIN_THRESHOLD)
        )
        cls.UNDERTRAIN_THRESHOLD = max(
            1.0, min(20.0, undertrain_threshold)
        )  # Clamp between 1% and 20%

        # Update top stats focus setting
        top_stats_focus = int(adv.get("topStatsFocus", cls.TOP_STATS_FOCUS))
        cls.TOP_STATS_FOCUS = max(1, min(5, top_stats_focus))  # Clamp between 1 and 5

        # Skills optimization gates
        try:
            interval = int(adv.get("skillCheckInterval", cls.SKILL_CHECK_INTERVAL))
        except Exception:
            interval = cls.SKILL_CHECK_INTERVAL
        cls.SKILL_CHECK_INTERVAL = max(1, min(12, interval))  # 1..12 half-months

        try:
            delta = int(adv.get("skillPtsDelta", cls.SKILL_PTS_DELTA))
        except Exception:
            delta = cls.SKILL_PTS_DELTA
        cls.SKILL_PTS_DELTA = max(0, min(2000, delta))

    @classmethod
    def apply_nav_preferences(cls, nav: Optional[dict]) -> None:
        nav = nav if isinstance(nav, dict) else {}
        shop = nav.get("shop") if isinstance(nav, dict) else None
        team = nav.get("team_trials") if isinstance(nav, dict) else None

        if not isinstance(shop, dict):
            shop = dict(_DEFAULT_NAV_PREFS["shop"])
        if not isinstance(team, dict):
            team = dict(_DEFAULT_NAV_PREFS["team_trials"])

        normalized_shop = {
            "alarm_clock": bool(shop.get("alarm_clock", True)),
            "star_pieces": bool(shop.get("star_pieces", False)),
            "parfait": bool(shop.get("parfait", False)),
        }

        try:
            preferred_banner = int(team.get("preferred_banner", 2))
        except Exception:
            preferred_banner = 2
        preferred_banner = max(1, min(3, preferred_banner))

        cls.NAV_PREFS = {
            "shop": normalized_shop,
            "team_trials": {"preferred_banner": preferred_banner},
        }

    @classmethod
    def get_active_preset_snapshot(cls) -> tuple[Optional[str], Optional[dict], dict]:
        cfg = cls._last_config or {}
        _, active_id, preset = cls._get_active_preset_from_config(cfg)
        return active_id, preset, cfg

    @classmethod
    def get_shop_nav_prefs(cls) -> Dict[str, bool]:
        prefs = cls.NAV_PREFS.get("shop") or {}
        return {
            "alarm_clock": bool(prefs.get("alarm_clock", True)),
            "star_pieces": bool(prefs.get("star_pieces", False)),
            "parfait": bool(prefs.get("parfait", False)),
        }

    @classmethod
    def get_team_trials_banner_pref(cls) -> int:
        team = cls.NAV_PREFS.get("team_trials") or {}
        try:
            preferred = int(
                team.get(
                    "preferred_banner",
                    _DEFAULT_NAV_PREFS["team_trials"]["preferred_banner"],
                )
            )
        except Exception:
            preferred = _DEFAULT_NAV_PREFS["team_trials"]["preferred_banner"]
        return max(1, min(3, preferred))

    @classmethod
    def normalize_scenario(cls, scenario: str | None) -> str:
        key = str(scenario or "ura").strip().lower()
        if key == "aoharu":
            key = "unity_cup"
        if key not in {"ura", "unity_cup"}:
            return "ura"
        return key

    @classmethod
    def resolve_agent_name(cls, scenario: str | None = None) -> str:
        key = cls.normalize_scenario(scenario or cls.ACTIVE_SCENARIO)
        if key == "unity_cup":
            return cls.AGENT_NAME_UNITY_CUP
        return cls.AGENT_NAME_URA

    @classmethod
    def resolve_yolo_weights_path(cls, scenario: str | None = None) -> Path:
        key = cls.normalize_scenario(scenario or cls.ACTIVE_SCENARIO)
        if key == "unity_cup":
            return cls.YOLO_WEIGHTS_UNITY_CUP
        return cls.YOLO_WEIGHTS_URA

    @classmethod
    def resolve_skill_memory_path(cls, scenario: str | None = None) -> Path:
        scenario_key = cls.normalize_scenario(scenario or cls.ACTIVE_SCENARIO)
        base = cls.RUNTIME_SKILL_MEMORY_PATH
        suffix = base.suffix
        if suffix:
            stem = base.stem
            return base.with_name(f"{stem}.{scenario_key}{suffix}")
        return base / f"runtime_skill_memory.{scenario_key}.json"

    @classmethod
    def _get_active_preset_from_config(cls, cfg: dict) -> tuple[list, str | None, dict | None]:
        """
        Extract (presets, active_id, active_preset) from config, handling both:
        - New scenario-based structure: cfg.scenarios[activeScenario].presets
        - Legacy structure: cfg.presets (migration fallback)
        
        Returns: (presets_list, active_id, active_preset_dict)
        """
        cfg = cfg or {}
        
        # Try new scenario-based structure first
        general = cfg.get("general") or {}
        active_scenario = general.get("activeScenario") or "ura"
        scenarios = cfg.get("scenarios") or {}
        scenario_branch = scenarios.get(active_scenario) or {}
        
        presets = scenario_branch.get("presets")
        active_id = scenario_branch.get("activePresetId")
        
        # Fallback to legacy top-level structure if scenario branch is empty
        if not presets:
            presets = cfg.get("presets") or []
            active_id = cfg.get("activePresetId")
        
        # Find the active preset
        preset = next((p for p in presets if p.get("id") == active_id), None) or (
            presets[0] if presets else None
        )
        
        return presets, active_id, preset

    @classmethod
    def extract_runtime_preset(cls, cfg: dict) -> dict:
        """
        Pick the active preset (or first), and return a slim dict with things
        the Python runtime cares about: plan_races, select_style, skill_list, and other settings.
        """
        _, _, preset = cls._get_active_preset_from_config(cfg)
        if not preset:
            return {
                "plan_races": {},
                "skill_list": [],
                "select_style": None,
                "raceIfNoGoodValue": cls.RACE_IF_NO_GOOD_VALUE,
            }

        plan_races = preset.get("plannedRaces", {}) or {}
        # skillsToBuy may be array of names or objects with {name}
        raw_skills = preset.get("skillsToBuy", []) or []
        skill_list = [s["name"] if isinstance(s, dict) else s for s in raw_skills]
        select_style = (
            preset.get("selectStyle") or preset.get("juniorStyle") or None
        )  # 'end'|'late'|'pace'|'front'|null

        try:
            minimum_skill_pts = max(0, int(preset.get("skillPtsCheck", cls.MINIMUM_SKILL_PTS)))
        except Exception:
            minimum_skill_pts = cls.MINIMUM_SKILL_PTS

        # Get the race if no good value setting from preset or use the global default
        race_if_no_good_value = preset.get(
            "raceIfNoGoodValue", cls.RACE_IF_NO_GOOD_VALUE
        )

        deck, priorities, avoid_energy = cls._extract_support_priorities_from_preset(
            preset
        )

        plan_races_tentative_raw = preset.get("plannedRacesTentative", {}) or {}
        plan_races_tentative: Dict[str, bool] = {}
        if isinstance(plan_races_tentative_raw, dict):
            for key, val in plan_races_tentative_raw.items():
                try:
                    normalized_key = str(key)
                except Exception:
                    continue
                plan_races_tentative[normalized_key] = bool(val)
        cls.PLAN_RACES_TENTATIVE = plan_races_tentative

        weak_turn_sv_raw = preset.get("weakTurnSv")
        if isinstance(weak_turn_sv_raw, (int, float)):
            weak_turn_sv = float(weak_turn_sv_raw)
        else:
            weak_turn_sv = cls.WEAK_TURN_SV_BY_SCENARIO.get(
                cls.ACTIVE_SCENARIO,
                cls.WEAK_TURN_SV_BY_SCENARIO.get("ura", cls.WEAK_TURN_SV),
            )

        race_precheck_sv_raw = preset.get("racePrecheckSv")
        if isinstance(race_precheck_sv_raw, (int, float)):
            race_precheck_sv = float(race_precheck_sv_raw)
        else:
            race_precheck_sv = cls.RACE_PRECHECK_SV_BY_SCENARIO.get(
                cls.ACTIVE_SCENARIO,
                cls.RACE_PRECHECK_SV_BY_SCENARIO.get("ura", cls.RACE_PRECHECK_SV),
            )

        lobby_precheck_enable = bool(
            preset.get("lobbyPrecheckEnable", cls.LOBBY_PRECHECK_ENABLE)
        )

        junior_minimal_mood_raw = preset.get("juniorMinimalMood")
        if isinstance(junior_minimal_mood_raw, str) and junior_minimal_mood_raw.strip():
            junior_minimal_mood = junior_minimal_mood_raw.strip().upper()
        else:
            junior_minimal_mood = None

        goal_race_force_turns_raw = preset.get(
            "goalRaceForceTurns", cls.GOAL_RACE_FORCE_TURNS
        )
        try:
            goal_race_force_turns = max(0, int(goal_race_force_turns_raw))
        except Exception:
            goal_race_force_turns = cls.GOAL_RACE_FORCE_TURNS

        cls.WEAK_TURN_SV = weak_turn_sv
        cls.RACE_PRECHECK_SV = race_precheck_sv
        cls.LOBBY_PRECHECK_ENABLE = lobby_precheck_enable
        cls.JUNIOR_MINIMAL_MOOD = junior_minimal_mood
        cls.GOAL_RACE_FORCE_TURNS = goal_race_force_turns

        return {
            "plan_races": plan_races,
            "plan_races_tentative": plan_races_tentative,
            "skill_list": skill_list,
            "select_style": select_style,
            "raceIfNoGoodValue": race_if_no_good_value,
            "weakTurnSv": weak_turn_sv,
            "racePrecheckSv": race_precheck_sv,
            "lobbyPrecheckEnable": lobby_precheck_enable,
            "juniorMinimalMood": junior_minimal_mood,
            "goalRaceForceTurns": goal_race_force_turns,
            "unityCupAdvanced": cls.UNITY_CUP_ADVANCED if cls.normalize_scenario(cls.ACTIVE_SCENARIO) == "unity_cup" else None,
            "minimum_skill_pts": minimum_skill_pts,
            "support_deck": deck,
            "support_card_priorities": [
                {
                    "name": name,
                    "rarity": rarity,
                    "attribute": attribute,
                    "enabled": data["enabled"],
                    "scoreBlueGreen": data["scoreBlueGreen"],
                    "scoreOrangeMax": data["scoreOrangeMax"],
                    # mirrors optional gating controls when present
                    "skillsRequiredForPriority": data.get("skillsRequiredForPriority", []),
                    "recheckAfterHint": data.get("recheckAfterHint", False),
                }
                for (name, rarity, attribute), data in priorities.items()
            ],
            "support_avoid_energy": [
                {
                    "name": name,
                    "rarity": rarity,
                    "attribute": attribute,
                    "avoidEnergyOverflow": avoid_energy.get(
                        (name, rarity, attribute), True
                    ),
                }
                for (name, rarity, attribute) in avoid_energy.keys()
            ],
        }


    @staticmethod
    def _clamp(value: float, min_value: float, max_value: float) -> float:
        try:
            val = float(value)
        except (TypeError, ValueError):
            return min_value
        return max(min_value, min(max_value, val))

    @classmethod
    def default_support_priority(cls) -> Dict[str, Union[float, bool]]:
        base = DEFAULT_SUPPORT_PRIORITY
        return {
            "enabled": bool(base.get("enabled", True)),
            "scoreBlueGreen": float(base.get("scoreBlueGreen", 0.75)),
            "scoreOrangeMax": float(base.get("scoreOrangeMax", 0.5)),
        }

    @classmethod
    def _normalize_priority(cls, raw: Optional[dict]) -> Dict[str, Union[float, bool]]:
        if not raw or not isinstance(raw, dict):
            return cls.default_support_priority()
        enabled = bool(raw.get("enabled", True))
        score_bg = cls._clamp(raw.get("scoreBlueGreen", 0.75), 0.0, 10.0)
        score_om = cls._clamp(raw.get("scoreOrangeMax", 0.5), 0.0, 10.0)
        # Optional gating controls coming from UI schema (ignored if absent)
        skills = raw.get("skillsRequiredForPriority")
        if isinstance(skills, list):
            skills_list: List[str] = [str(s).strip() for s in skills if isinstance(s, (str, int, float))]
            skills_list = [s for s in skills_list if s]
        elif isinstance(skills, str):
            skills_list = [s.strip() for s in str(skills).split(",") if s.strip()]
        else:
            skills_list = []
        recheck = bool(raw.get("recheckAfterHint", False))
        return {
            "enabled": enabled,
            "scoreBlueGreen": score_bg,
            "scoreOrangeMax": score_om,
            # Extra keys are tolerated by downstream consumers
            "skillsRequiredForPriority": skills_list,  # type: ignore[dict-item]
            "recheckAfterHint": recheck,  # type: ignore[dict-item]
        }

    @classmethod
    def _priority_is_custom(cls, priority: Dict[str, Union[float, bool]]) -> bool:
        if not isinstance(priority, dict):
            return False
        default = cls.default_support_priority()

        if bool(priority.get("enabled", True)) != bool(default.get("enabled", True)):
            return True

        default_bg = float(default.get("scoreBlueGreen", 0.75))
        default_om = float(default.get("scoreOrangeMax", 0.5))
        score_bg = float(priority.get("scoreBlueGreen", default_bg))
        score_om = float(priority.get("scoreOrangeMax", default_om))

        if not math.isclose(score_bg, default_bg, rel_tol=1e-6, abs_tol=1e-6):
            return True
        if not math.isclose(score_om, default_om, rel_tol=1e-6, abs_tol=1e-6):
            return True
        # Treat gating controls as customization when present
        skills = priority.get("skillsRequiredForPriority")
        if isinstance(skills, list) and len(skills) > 0:
            return True
        if bool(priority.get("recheckAfterHint", False)):
            return True
        return False

    @classmethod
    def _extract_support_priorities_from_preset(
        cls, preset: Optional[dict]
    ) -> Tuple[
        List[dict],
        Dict[Tuple[str, str, str], Dict[str, Union[float, bool]]],
        Dict[Tuple[str, str, str], bool],
    ]:
        event_setup = (preset or {}).get("event_setup", {}) or {}
        supports = event_setup.get("supports", []) or []

        deck: List[dict] = []
        priorities: Dict[Tuple[str, str, str], Dict[str, Union[float, bool]]] = {}
        avoid_energy: Dict[Tuple[str, str, str], bool] = {}

        for entry in supports:
            if not entry:
                continue
            name = entry.get("name")
            rarity = entry.get("rarity")
            attribute = entry.get("attribute")
            slot = entry.get("slot")
            if not (name and rarity and attribute):
                continue

            try:
                slot_idx = int(slot) if slot is not None else len(deck)
            except (TypeError, ValueError):
                slot_idx = len(deck)

            raw_flag = entry.get("avoidEnergyOverflow")
            if raw_flag is None:
                raw_flag = entry.get("avoid_energy_overflow")
            avoid_flag = bool(raw_flag) if isinstance(raw_flag, bool) else True

            card_info = {
                "slot": slot_idx,
                "name": str(name),
                "rarity": str(rarity),
                "attribute": str(attribute),
                "avoidEnergyOverflow": avoid_flag,
            }
            deck.append(card_info)

            key = (card_info["name"], card_info["rarity"], card_info["attribute"])
            priorities[key] = cls._normalize_priority(entry.get("priority"))
            avoid_energy[key] = avoid_flag

        deck.sort(key=lambda c: c["slot"])
        return deck, priorities, avoid_energy

class Constants:
    map_tile_idx_to_type = {0: "SPD", 1: "STA", 2: "PWR", 3: "GUTS", 4: "WIT"}


    @classmethod
    def get_support_priority(
        cls, name: str, rarity: str, attribute: str
    ) -> Dict[str, Union[float, bool]]:
        key = (name, rarity, attribute)
        if key in Settings.SUPPORT_CARD_PRIORITIES:
            data = Settings.SUPPORT_CARD_PRIORITIES[key]
            return {
                "enabled": bool(data.get("enabled", True)),
                "scoreBlueGreen": float(data.get("scoreBlueGreen", 0.75)),
                "scoreOrangeMax": float(data.get("scoreOrangeMax", 0.5)),
            }
        return Settings.default_support_priority()
