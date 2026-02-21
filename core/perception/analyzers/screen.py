# core/perception/analyzers/screen.py
from collections import Counter
from typing import Dict, List, Optional, Tuple

from core.settings import Settings
from core.types import DetectionDict, ScreenInfo, ScreenName


def _count_conf(
    dets: List[DetectionDict], target_name: str, threshold: float
) -> int:
    return sum(
        1
        for d in dets
        if d["name"] == target_name and float(d.get("conf", 0.0)) >= threshold
    )


def _any_conf(dets: List[DetectionDict], target_name: str, threshold: float) -> bool:
    return any(
        d["name"] == target_name and float(d.get("conf", 0.0)) >= threshold
        for d in dets
    )


def _collect(dets: List[DetectionDict], target_name: str) -> List[DetectionDict]:
    return [d for d in dets if d["name"] == target_name]


def classify_screen_ura(
    dets: List[DetectionDict],
    *,
    lobby_conf: float = 0.70,
    require_infirmary: bool = True,
    training_conf: float = 0.50,
    event_conf: float = 0.60,
    race_conf: float = 0.80,
    names_map: Optional[Dict[str, str]] = None,
) -> Tuple[ScreenName, ScreenInfo]:
    """
    Decide which screen we're on.

    Rules (priority order):
      - 'Event'       → ≥1 'event_choice' @ ≥ event_conf
      - 'Inspiration' -> has_inspiration button
      - 'Raceday' → detect 'lobby_tazuna' @ ≥ lobby_conf AND 'race_race_day' @ ≥ race_conf
      - 'Training'    → exactly 5 'training_button' @ ≥ training_conf
      - 'LobbySummer' → has 'lobby_tazuna' AND 'lobby_rest_summer'
                         AND NOT 'lobby_rest' AND NOT 'lobby_recreation'
      - 'Lobby'       → has 'lobby_tazuna' AND (has 'lobby_infirmary' or not require_infirmary)
      - else 'Unknown'
    """
    names_map = names_map or {
        "tazuna": "lobby_tazuna",
        "infirmary": "lobby_infirmary",
        "training_button": "training_button",
        "event": "event_choice",
        "rest": "lobby_rest",
        "rest_summer": "lobby_rest_summer",
        "recreation": "lobby_recreation",
        "race_day": "race_race_day",
        "event_inspiration": "event_inspiration",
        "race_after_next": "race_after_next",
        "lobby_skills": "lobby_skills",
        "button_claw_action": "button_claw_action",
        "claw": "claw",
        "pal": "lobby_pal",
        "button_change": "button_change",
        "race_badge": "race_badge",
        "career_complete": "career_complete",
    }

    counts = Counter(d["name"] for d in dets)

    n_event_choices = _count_conf(dets, names_map["event"], event_conf)
    n_train = _count_conf(dets, names_map["training_button"], training_conf)

    has_tazuna = _any_conf(dets, names_map["tazuna"], lobby_conf)
    has_infirmary = _any_conf(dets, names_map["infirmary"], lobby_conf)
    has_rest = _any_conf(dets, names_map["rest"], lobby_conf)
    has_rest_summer = _any_conf(dets, names_map["rest_summer"], lobby_conf)
    has_recreation = _any_conf(dets, names_map["recreation"], lobby_conf)
    has_race_day = _any_conf(dets, names_map["race_day"], race_conf)
    has_inspiration = _any_conf(dets, names_map["event_inspiration"], race_conf)
    has_lobby_skills = _any_conf(dets, names_map["lobby_skills"], lobby_conf)
    race_after_next = _any_conf(dets, names_map["race_after_next"], 0.5)
    has_button_claw_action = _any_conf(dets, names_map["button_claw_action"], lobby_conf)
    has_claw = _any_conf(dets, names_map["claw"], lobby_conf)
    has_pal = _any_conf(dets, names_map["pal"], lobby_conf)
    has_button_change = _any_conf(dets, names_map["button_change"], lobby_conf)
    has_race_badge = _any_conf(dets, names_map["race_badge"], lobby_conf)
    has_career_complete = _any_conf(dets, names_map["career_complete"], 0.5)

    if has_career_complete:
        return "CareerComplete", {"has_career_complete": has_career_complete}

    # 1) Event
    if n_event_choices >= 2:
        return "Event", {"event_choices": n_event_choices}

    if has_inspiration:
        return "Inspiration", {"has_inspiration": has_inspiration}
    if has_tazuna and has_race_day:
        return "Raceday", {"tazuna": has_tazuna, "race_day": has_race_day}

    # 2) Training
    if n_train == 5:
        return "Training", {"training_buttons": n_train}

    # 3) LobbySummer
    if has_tazuna and has_rest_summer and (not has_rest) and (not has_recreation):
        return "LobbySummer", {
            "tazuna": has_tazuna,
            "rest_summer": has_rest_summer,
            "infirmary": has_infirmary,
            "recreation_present": has_recreation,
            "pal_available": has_pal,
        }

    # 4) Regular Lobby
    if has_tazuna and (has_infirmary or not require_infirmary) and has_lobby_skills:
        return "Lobby", {
            "tazuna": has_tazuna,
            "infirmary": has_infirmary,
            "has_lobby_skills": has_lobby_skills,
            "pal_available": has_pal,
        }

    if (len(dets) == 2 and has_lobby_skills and race_after_next) or (
        len(dets) <= 2 and has_lobby_skills
    ):
        return "FinalScreen", {
            "has_lobby_skills": has_lobby_skills,
            "race_after_next": race_after_next,
        }

    if has_button_claw_action and has_claw:
        return "ClawMachine", {
            "has_button_claw_action": has_button_claw_action,
            "has_claw": has_claw,
        }
    if n_event_choices == 1:
        return "EventStale", {"event_choices": n_event_choices}

    
    if has_button_change and has_race_badge:
        return "RaceLobby", {
            "button_change": has_button_change,
            "race_badge": has_race_badge,
        }
    # 5) Fallback
    return "Unknown", {
        "training_buttons": n_train,
        "tazuna": has_tazuna,
        "infirmary": has_infirmary,
        "rest": has_rest,
        "rest_summer": has_rest_summer,
        "recreation": has_recreation,
        "race_day": has_race_day,
        "counts": dict(counts),
        "pal_available": has_pal,
        "button_change": has_button_change,
        "race_badge": has_race_badge,
    }

def classify_screen_unity_cup(
    dets: List[DetectionDict],
    *,
    lobby_conf: float = 0.70,
    require_infirmary: bool = True,
    training_conf: float = 0.50,
    event_conf: float = 0.60,
    race_conf: float = 0.80,
    names_map: Optional[Dict[str, str]] = None,
) -> Tuple[ScreenName, ScreenInfo]:
    """
    Decide which screen we're on.

    Rules (priority order):
      - 'Event'       → ≥1 'event_choice' @ ≥ event_conf
      - 'Inspiration' -> has_inspiration button
      - 'Raceday' → detect 'lobby_tazuna' @ ≥ lobby_conf AND 'race_race_day' @ ≥ race_conf
      - 'UnityCupRaceday' → detect 'race_race_day' @ ≥ race_conf
      - 'Training'    → exactly 5 'training_button' @ ≥ training_conf
      - 'LobbySummer' → has 'lobby_tazuna' AND 'lobby_rest_summer'
                         AND NOT 'lobby_rest' AND NOT 'lobby_recreation'
      - 'Lobby'       → has 'lobby_tazuna' AND (has 'lobby_infirmary' or not require_infirmary)
      - 'KashimotoTeam' → has 'button_golden' AND 'button_white'
      - else 'Unknown'
    """
    names_map = names_map or {
        "tazuna": "lobby_tazuna",
        "infirmary": "lobby_infirmary",
        "training_button": "training_button",
        "event": "event_choice",
        "rest": "lobby_rest",
        "rest_summer": "lobby_rest_summer",
        "recreation": "lobby_recreation",
        "race_day": "race_race_day",
        "event_golden": "button_golden",
        "race_after_next": "race_after_next",
        "lobby_skills": "lobby_skills",
        "button_claw_action": "button_claw_action",
        "claw": "claw",
        "button_white": "button_white",
        "button_green": "button_green",
        "button_pink": "button_pink",
        "pal": "lobby_pal",
        "button_change": "button_change",
        "race_badge": "race_badge",
        "career_complete": "career_complete",
    }

    counts = Counter(d["name"] for d in dets)

    n_event_choices = _count_conf(dets, names_map["event"], event_conf)
    n_train = _count_conf(dets, names_map["training_button"], training_conf)

    has_tazuna = _any_conf(dets, names_map["tazuna"], lobby_conf)
    has_infirmary = _any_conf(dets, names_map["infirmary"], lobby_conf)
    has_rest = _any_conf(dets, names_map["rest"], lobby_conf)
    has_rest_summer = _any_conf(dets, names_map["rest_summer"], lobby_conf)
    has_recreation = _any_conf(dets, names_map["recreation"], lobby_conf)

    button_white_present = _any_conf(dets, names_map["button_white"], lobby_conf)
    button_green_present = _any_conf(dets, names_map["button_green"], lobby_conf)
    has_button_pink = _any_conf(dets, names_map["button_pink"], lobby_conf)
    has_pal = _any_conf(dets, names_map["pal"], lobby_conf)

    has_button_white = button_white_present
    has_button_green = button_green_present
    has_button_change = _any_conf(dets, names_map["button_change"], lobby_conf)
    has_race_badge = _any_conf(dets, names_map["race_badge"], lobby_conf)

    race_day_primary_conf = Settings.UNITY_CUP_RACE_DAY_CONF or race_conf
    race_day_relaxed_conf = Settings.UNITY_CUP_RACE_DAY_RELAXED_CONF
    golden_primary_conf = Settings.UNITY_CUP_GOLDEN_CONF or race_conf
    golden_relaxed_conf = Settings.UNITY_CUP_GOLDEN_RELAXED_CONF

    race_day_primary_conf = race_day_primary_conf or race_conf
    golden_primary_conf = golden_primary_conf or race_conf

    def _apply_relaxed(
        detections: List[DetectionDict],
        primary: float,
        relaxed: float,
        *,
        require_support: bool,
    ) -> bool:
        if not detections:
            return False
        if any(float(d.get("conf", 0.0)) >= primary for d in detections):
            return True
        use_relaxed = relaxed and relaxed < primary
        if not use_relaxed:
            return False
        relaxed_hits = [d for d in detections if float(d.get("conf", 0.0)) >= relaxed]
        if not relaxed_hits:
            return False
        return not require_support or (button_white_present or button_green_present)

    race_day_candidates = _collect(dets, names_map["race_day"])
    has_race_day = _apply_relaxed(
        race_day_candidates,
        race_day_primary_conf,
        race_day_relaxed_conf,
        require_support=True,
    )

    golden_candidates = _collect(dets, names_map["event_golden"])
    has_golden = _apply_relaxed(
        golden_candidates,
        golden_primary_conf,
        golden_relaxed_conf,
        require_support=False,
    )

    has_lobby_skills = _any_conf(dets, names_map["lobby_skills"], lobby_conf)
    race_after_next = _any_conf(dets, names_map["race_after_next"], 0.5)
    has_button_claw_action = _any_conf(dets, names_map["button_claw_action"], lobby_conf)
    has_claw = _any_conf(dets, names_map["claw"], lobby_conf)
    has_career_complete = _any_conf(dets, names_map["career_complete"], 0.5)
    
    if has_career_complete:
        return "CareerComplete", {"has_career_complete": has_career_complete}
    
    # 1) Event
    if n_event_choices >= 2:
        return "Event", {"event_choices": n_event_choices}

    if has_golden:
        if has_button_white:
            return "KashimotoTeam", {"has_golden": has_golden, "has_button_white": has_button_white}
        return "Inspiration", {"has_golden": has_golden}

    if has_race_day:
        if has_tazuna:
            return "Raceday", {"tazuna": has_tazuna, "race_day": has_race_day}
        return "UnityCupRaceday", {"race_day": has_race_day}

    # 2) Training
    if n_train == 5:
        return "Training", {"training_buttons": n_train}

    # 3) LobbySummer
    if has_tazuna and has_rest_summer and (not has_rest) and (not has_recreation):
        return "LobbySummer", {
            "tazuna": has_tazuna,
            "rest_summer": has_rest_summer,
            "infirmary": has_infirmary,
            "recreation_present": has_recreation,
            "pal_available": has_pal,
        }

    # 4) Regular Lobby
    if has_tazuna and (has_infirmary or not require_infirmary) and has_lobby_skills:
        return "Lobby", {
            "tazuna": has_tazuna,
            "infirmary": has_infirmary,
            "has_lobby_skills": has_lobby_skills,
            "pal_available": has_pal,
        }

    if (
        len(dets) <= 3 and has_lobby_skills and has_button_pink
    ):
        return "FinalScreen", {
            "has_lobby_skills": has_lobby_skills,
            "race_after_next": race_after_next,
        }

    if has_button_claw_action and has_claw:
        return "ClawMachine", {
            "has_button_claw_action": has_button_claw_action,
            "has_claw": has_claw,
        }
    if n_event_choices == 1:
        return "EventStale", {"event_choices": n_event_choices}
    
    if has_button_change and has_race_badge:
        return "RaceLobby", {
            "button_change": has_button_change,
            "race_badge": has_race_badge,
        }
    # 5) Fallback
    return "Unknown", {
        "training_buttons": n_train,
        "tazuna": has_tazuna,
        "infirmary": has_infirmary,
        "rest": has_rest,
        "rest_summer": has_rest_summer,
        "recreation": has_recreation,
        "race_day": has_race_day,
        "counts": dict(counts),
        "pal_available": has_pal,
        "button_change": has_button_change,
        "race_badge": has_race_badge,
    }
