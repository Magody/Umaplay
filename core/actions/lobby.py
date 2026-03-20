# core/actions/lobby.py
from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
import random
import statistics
import time
from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional, Tuple

from core.controllers.base import IController
from core.perception.extractors.state import extract_career_date, extract_goal_text, extract_stats
from core.perception.is_button_active import ActiveButtonClassifier
from core.perception.yolo.interface import IDetector
from core.settings import Settings
from core.actions.training_check import (
    get_compute_support_values,
    scan_training_screen,
)
from core.utils.geometry import calculate_jitter
from core.utils.logger import logger_uma
from core.utils.race_index import RaceIndex, date_key_from_dateinfo
from core.utils.text import fuzzy_contains, fuzzy_best_match, normalize_ocr_text
from core.utils.waiter import Waiter
from core.utils.yolo_objects import collect
from core.constants import CLASS_UI_TURNS, CLASS_UI_GOAL
from core.utils.pal_memory import PalMemoryManager
from core.actions.events import _count_chain_steps
from core.utils.event_processor import predict_next_chain_has_energy_from_raw
from core.utils.abort import abort_requested

from core.utils.date_uma import (
    DateInfo,
    date_cmp,
    date_index,
    date_is_pre_debut,
    date_is_regular_year,
    date_is_terminal,
    date_merge,
    is_summer_in_two_or_less_turns,
    is_summer,
    parse_career_date,
    date_is_confident,
)

@dataclass
class LobbyState:
    goal: Optional[str] = None
    energy: Optional[int] = None
    skill_pts: int = 0
    infirmary_on: Optional[bool] = None
    turn: int = -1
    turns_special: int = -1
    career_date_raw: Optional[str] = None
    date_info: Optional[DateInfo] = None
    is_summer: Optional[bool] = None
    mood: Tuple[str, float] = ("UNKNOWN", -1.0)
    stats = {"SPD": -1, "STA": -1, "PWR": -1, "GUTS": -1, "WIT": -1}
    planned_race_name: Optional[str] = None
    planned_race_canonical: Optional[str] = None
    planned_race_tentative: bool = False
    # PAL icon available near Recreation on the lobby screen
    pal_available: bool = False


@dataclass
class LobbyConfig:
    imgsz: int = 832
    conf: float = 0.51
    iou: float = 0.45
    poll_interval_s: float = 0.25
    default_timeout_s: float = 4.0


class LobbyFlow(ABC):
    """
    Encapsulates all Lobby decisions & navigation.
    Composes RaceFlow and centralizes waits via a single Waiter.
    """

    def __init__(
        self,
        ctrl: IController,
        ocr,
        yolo_engine: IDetector,
        waiter: Waiter,
        *,
        minimum_skill_pts: int = 500,
        auto_rest_minimum: int = 24,
        prioritize_g1: bool = False,
        process_on_demand=True,
        interval_stats_refresh=1,
        max_critical_turn=8,
        plan_races={},
        date_layout: Literal["above", "right"] = "above",
        date_turns_class: str = CLASS_UI_TURNS,
        date_goal_class: str = CLASS_UI_GOAL,
    ) -> None:
        self.ctrl = ctrl
        self.ocr = ocr
        self.yolo_engine = yolo_engine
        self.minimum_skill_pts = int(minimum_skill_pts)
        self.auto_rest_minimum = int(auto_rest_minimum)
        self.prioritize_g1 = bool(prioritize_g1)
        self.process_on_demand = bool(process_on_demand)
        self.interval_stats_refresh = interval_stats_refresh
        self._stats_refresh_counter = 0
        self.max_critical_turn = max_critical_turn

        self.state = LobbyState()
        self._skip_race_once = False

        self.waiter = waiter
        self.last_turns_left_prediction = None
        self._last_date_key: Optional[str] = None
        self.plan_races = plan_races
        self.plan_races_tentative = getattr(Settings, "PLAN_RACES_TENTATIVE", {}) or {}
        self._date_stable_count: int = 0
        self._date_artificial: bool = False
        self._pending_date_jump = None
        self._pending_date_back = None
        self._pending_date_back_count: int = 0
        self._last_turn_at_date_update: Optional[int] = None
        self._raced_keys_recent: set[str] = set()
        self._skip_guard_key: Optional[str] = None
        self._peek_cache_key: Optional[Tuple[Optional[str], int, Optional[int]]] = None
        self._peek_cache_value: Optional[Tuple[float, Dict[str, Any]]] = None

        self.date_layout = date_layout
        self.date_turns_class = date_turns_class
        self.date_goal_class = date_goal_class

        # Lightweight PAL memory for recreation/dating context
        try:
            from core.settings import Settings as _S
            pal_path = _S.PREFS_DIR / "runtime_pal_memory.json"
            self.pal_memory = PalMemoryManager(pal_path, scenario=getattr(_S, "ACTIVE_SCENARIO", None))
        except Exception:
            # Fallback to a local temp path if settings are unavailable
            from pathlib import Path
            self.pal_memory = PalMemoryManager(Path("runtime_pal_memory.json"))

    # Keep PAL memory metadata aligned with current run
    def _refresh_pal_memory(self) -> None:
        try:
            from core.settings import Settings as _S
            cfg = getattr(_S, "_last_config", None) or {}
            # Reuse AgentScenario helper behavior: Settings might provide a private util
            preset_id = None
            try:
                _, active_id, _ = _S._get_active_preset_from_config(cfg)
                if isinstance(active_id, str) and active_id.strip():
                    preset_id = active_id.strip()
            except Exception:
                preset_id = None
            di = getattr(self.state, "date_info", None)
            date_key = di.as_key() if di else None
            idx = date_index(di) if di else None
            if not self.pal_memory.is_compatible_run(
                preset_id=preset_id,
                date_key=date_key,
                date_index=idx,
                scenario=getattr(_S, "ACTIVE_SCENARIO", None),
            ):
                self.pal_memory.reset()
            self.pal_memory.set_run_metadata(
                preset_id=preset_id,
                date_key=date_key,
                date_index=idx,
                scenario=getattr(_S, "ACTIVE_SCENARIO", None),
                commit=True,
            )
        except Exception:
            pass

    # Centralized helper to update PAL availability from detections
    def _update_pal_from_dets(self, dets) -> None:
        try:
            has_pal = any(
                d.get("name") == "lobby_pal" and float(d.get("conf", 0.0)) >= 0.6
                for d in dets
            )
            self.state.pal_available = bool(has_pal)
            date_key = (
                date_key_from_dateinfo(self.state.date_info)
                if getattr(self.state, "date_info", None)
                else None
            )
            turn_val = self.state.turn if isinstance(self.state.turn, int) else None
            self.pal_memory.record_availability(has_pal, date_key=date_key, turn=turn_val, commit=True)
        except Exception:
            # Non-fatal; keep running without PAL telemetry if something goes wrong
            pass

    @abstractmethod
    def process_turn(self):
        """
        Evaluate the Lobby and take the next action.
        Returns a short outcome string:
          - "RACED"          → we entered/finished a race flow
          - "INFIRMARY"      → we went to infirmary
          - "RESTED"         → we chose rest/recreation
          - "TO_TRAINING"    → we navigated to the training screen
          - "CONTINUE"       → we did a minor click or nothing
          - "ETC"

        optional extra message
        """
        raise NotImplementedError

    def _update_stats(self, img, dets) -> None:
        """
        Smart, monotonic-ish stat updater with refresh gating, noise guards,
        and recovery from early misreads.

        Rules (per stat key in {SPD, STA, PWR, GUTS, WIT}):
          - Ignore invalid reads (-1) and out-of-range values.
          - If previous is -1, accept the first valid value.
          - Accept normal increases up to MAX_UP_STEP per refresh.
          - For larger upward jumps, require the same value to repeat
            PERSIST_FRAMES times before accepting (prevents OCR spikes),
            EXCEPT during a short warm-up window or if the previous value was
            artificial (imputed) — in those cases accept immediately to fix
            early misreads like 103→703.
          - Allow small decreases up to MAX_DOWN_STEP (rare debuffs / wobble).
          - If at least one of the 5 stats is still -1, force a refresh even
            if we are between interval gates.
          - If a stat remains -1 while others are valid, fill it with the
            average of the known ones and mark it *artificial* so any later
            real read will overwrite it unconditionally.
        """
        KEYS = ("SPD", "STA", "PWR", "GUTS", "WIT")
        STAT_MIN, STAT_MAX = 0, 1200
        MAX_UP_STEP = 150  # typical per-turn cap; tune if you see legit bigger jumps
        MAX_DOWN_STEP = 60  # allow tiny decreases; block bigger drops
        PERSIST_FRAMES_UP = 2  # confirm large upward jumps across this many refreshes
        PERSIST_FRAMES_DOWN = (
            2  # confirm large downward corrections across this many refreshes
        )
        WARMUP_FRAMES = 2  # after accepting a value, allow big fixes for a few frames
        SUSPECT_FRAMES = 5  # after a big upward jump is accepted, allow big downward correction for a while
        HIST_LEN = 5  # how many recent raw reads to keep per stat
        CORR_TOL = 80  # tolerance near baseline/median to treat as a correction (not a real drop)

        # lazy init of helper state
        if not hasattr(self, "_stats_last_pred"):
            self._stats_last_pred = {k: -1 for k in KEYS}
        if not hasattr(self, "_stats_pending"):
            self._stats_pending = {k: None for k in KEYS}
        if not hasattr(self, "_stats_pending_down"):
            self._stats_pending_down = {k: None for k in KEYS}
        if not hasattr(self, "_stats_pending_down_count"):
            self._stats_pending_down_count = {k: 0 for k in KEYS}
        if not hasattr(self, "_stats_pending_count"):
            self._stats_pending_count = {k: 0 for k in KEYS}
        if not hasattr(self, "_stats_stable_count"):
            self._stats_stable_count = {k: 0 for k in KEYS}
        if not hasattr(self, "_stats_artificial"):
            self._stats_artificial = set()
        if not hasattr(self, "_stats_suspect_until"):
            self._stats_suspect_until = {k: 0 for k in KEYS}
        if not hasattr(self, "_stats_prejump_value"):
            self._stats_prejump_value = {k: None for k in KEYS}
        # small history of raw valid reads to compute medians
        if not hasattr(self, "_stats_history"):
            self._stats_history = {k: deque(maxlen=HIST_LEN) for k in KEYS}

        # Refresh gating (preserve optimization) + force if any unknowns
        any_missing = any((self.state.stats or {}).get(k, -1) == -1 for k in KEYS)
        if (
            self._stats_refresh_counter == 0
            or self._stats_refresh_counter % self.interval_stats_refresh == 0
            or any_missing
        ):
            observed = extract_stats(self.ocr, img, dets)  # dict[str,int]
            current = dict(self.state.stats or {})  # copy to modify safely
            prev_snapshot = dict(current)
            changed = []

            for key in KEYS:
                new_val = int(observed.get(key, -1))
                prev = int(current.get(key, -1))
                prev_was_artificial = key in self._stats_artificial

                # remember last prediction for debugging/telemetry
                self._stats_last_pred[key] = new_val

                # reject invalids early
                if new_val < STAT_MIN or new_val > STAT_MAX:
                    continue
                if new_val == -1:
                    logger_uma.debug(
                        f"[stats] {key}: invalid read (-1), keeping {prev}"
                    )
                    continue
                # keep history of valid raw reads
                self._stats_history[key].append(new_val)
                if prev == -1:
                    # first valid observation
                    current[key] = new_val
                    self._stats_artificial.discard(key)
                    self._stats_pending[key] = None
                    self._stats_pending_count[key] = 0
                    self._stats_pending_down[key] = None
                    self._stats_pending_down_count[key] = 0
                    self._stats_stable_count[key] = 0
                    self._stats_suspect_until[key] = 0
                    self._stats_prejump_value[key] = None
                    changed.append((key, -1, new_val))
                    continue

                delta = new_val - prev

                # small negative change allowed; big drop rejected
                if delta < 0:
                    drop = abs(delta)
                    if drop <= MAX_DOWN_STEP:
                        current[key] = new_val
                        self._stats_artificial.discard(key)
                        self._stats_pending[key] = None
                        self._stats_pending_count[key] = 0
                        self._stats_pending_down[key] = None
                        self._stats_pending_down_count[key] = 0
                        self._stats_stable_count[key] = 0
                        self._stats_suspect_until[key] = max(
                            0, self._stats_suspect_until[key] - 1
                        )
                        changed.append((key, prev, new_val))
                    else:
                        # Large downward move. Treat as a possible correction if:
                        #  - previous was artificial, OR
                        #  - we are inside a suspect window after a big upward jump
                        #    and the new value is close to the pre-jump baseline, OR
                        #  - it persists across a few frames and is closer to the median of recent raw reads.
                        accept = False
                        reason = "blocked"

                        if prev_was_artificial:
                            accept, reason = True, "prev artificial"
                        elif self._stats_suspect_until.get(key, 0) > 0:
                            base = self._stats_prejump_value.get(key, prev)
                            if (
                                base is not None
                                and abs(new_val - int(base)) <= CORR_TOL
                            ):
                                accept, reason = (
                                    True,
                                    f"suspect-window correction to ~{base}",
                                )
                        else:
                            # persistence gate + median support
                            pend = self._stats_pending_down[key]
                            if pend == new_val:
                                self._stats_pending_down_count[key] += 1
                            else:
                                self._stats_pending_down[key] = new_val
                                self._stats_pending_down_count[key] = 1
                            if (
                                self._stats_pending_down_count[key]
                                >= PERSIST_FRAMES_DOWN
                            ):
                                med = (
                                    statistics.median(self._stats_history[key])
                                    if self._stats_history[key]
                                    else new_val
                                )
                                if abs(new_val - med) <= CORR_TOL or abs(
                                    prev - med
                                ) > abs(new_val - med):
                                    accept, reason = (
                                        True,
                                        f"median≈{int(med)} persistence",
                                    )

                        if accept:
                            current[key] = new_val
                            self._stats_artificial.discard(key)
                            self._stats_pending[key] = None
                            self._stats_pending_count[key] = 0
                            self._stats_pending_down[key] = None
                            self._stats_pending_down_count[key] = 0
                            self._stats_stable_count[key] = 0
                            self._stats_suspect_until[key] = 0
                            self._stats_prejump_value[key] = None
                            changed.append((key, prev, new_val))
                            logger_uma.debug(
                                f"[stats] {key}: accepted big downward correction {prev}->{new_val} ({reason})"
                            )
                        else:
                            logger_uma.debug(
                                f"[stats] {key}: holding large drop {prev}->{new_val} (Δ={delta})"
                            )
                    continue

                # non-negative delta
                if delta <= MAX_UP_STEP:
                    # normal progression
                    current[key] = new_val
                    self._stats_artificial.discard(key)
                    self._stats_pending[key] = None
                    self._stats_pending_count[key] = 0
                    self._stats_pending_down[key] = None
                    self._stats_pending_down_count[key] = 0
                    self._stats_stable_count[key] = 0
                    # normal moves are not "suspect"
                    self._stats_suspect_until[key] = max(
                        0, self._stats_suspect_until[key] - 1
                    )

                    changed.append((key, prev, new_val))
                else:
                    # large upward jump
                    # Accept immediately if:
                    #  - we are in warm-up for this key (value just accepted recently), or
                    #  - the previous value was artificial (imputed placeholder).
                    if (
                        self._stats_stable_count.get(key, 0) < WARMUP_FRAMES
                        or prev_was_artificial
                    ):
                        current[key] = new_val
                        self._stats_artificial.discard(key)
                        self._stats_pending[key] = None
                        self._stats_pending_count[key] = 0
                        self._stats_pending_down[key] = None
                        self._stats_pending_down_count[key] = 0
                        self._stats_stable_count[key] = 0
                        # Mark as suspect so we can accept a later big correction down.
                        self._stats_suspect_until[key] = SUSPECT_FRAMES
                        self._stats_prejump_value[key] = prev
                        changed.append((key, prev, new_val))
                        logger_uma.debug(
                            f"[stats] {key}: accepted big correction {prev}->{new_val} (Δ={delta})"
                        )
                    else:
                        # require persistence
                        pend = self._stats_pending[key]
                        if pend == new_val:
                            self._stats_pending_count[key] += 1
                        else:
                            self._stats_pending[key] = new_val
                            self._stats_pending_count[key] = 1

                        if self._stats_pending_count[key] >= PERSIST_FRAMES_UP:
                            current[key] = new_val
                            changed.append((key, prev, new_val))
                            logger_uma.debug(
                                f"[stats] {key}: accepted confirmed big jump {prev}->{new_val} (Δ={delta})"
                            )
                            self._stats_pending[key] = None
                            self._stats_pending_count[key] = 0
                            self._stats_stable_count[key] = 0
                            self._stats_artificial.discard(key)
                            # big confirmed upward jump → open suspect window
                            self._stats_suspect_until[key] = SUSPECT_FRAMES
                            self._stats_prejump_value[key] = prev
                        else:
                            logger_uma.debug(
                                f"[stats] {key}: holding big jump {prev}->{new_val} (Δ={delta}); "
                                f"need {PERSIST_FRAMES_UP - self._stats_pending_count[key]} more confirm(s)"
                            )

            # If some stats are still unknown, impute with the average of known ones
            missing_keys = [k for k in KEYS if current.get(k, -1) == -1]
            known_vals = [current[k] for k in KEYS if current.get(k, -1) != -1]
            if missing_keys and known_vals:
                avg_val = int(round(sum(known_vals) / max(1, len(known_vals))))
                avg_val = max(STAT_MIN, min(STAT_MAX, avg_val))
                for k in missing_keys:
                    current[k] = avg_val
                    self._stats_artificial.add(k)
                logger_uma.debug(f"[stats] imputed {missing_keys} with avg={avg_val}")

            # update stability counters (keys that didn’t change grow older)
            for k in KEYS:
                if current.get(k, -1) != -1 and current.get(k) == prev_snapshot.get(k):
                    self._stats_stable_count[k] = self._stats_stable_count.get(k, 0) + 1
                else:
                    # when a value changes, shrink suspect window a bit
                    if self._stats_suspect_until.get(k, 0) > 0:
                        self._stats_suspect_until[k] = max(
                            0, self._stats_suspect_until[k] - 1
                        )

            # commit
            self.state.stats = current
            if changed:
                chs = ", ".join(f"{k}:{a}->{b}" for k, a, b in changed)
                logger_uma.info(f"[stats] update: {chs}")

        else:
            logger_uma.debug(
                "[Optimization] Reusing previously calculated stats until new refresh interval"
            )
            time.sleep(1.2)

        # advance counter
        self._stats_refresh_counter += 1

    @abstractmethod
    def _update_state(self, img, dets) -> None:
        raise NotImplementedError

    def _process_date_info(self, img, dets) -> None:
        """
        Robust date updater with:
          • Warm-up acceptance for backward corrections (like stats big-jump fix).
          • 'Artificial' flag to allow overwriting auto-advanced dates.
          • Turn-aware auto-advance when OCR returns nothing but a day was consumed.
        """
        WARMUP_FRAMES = 2
        PERSIST_FRAMES = 2
        MAX_SUSP_JUMP_HALVES = 6
        raw = extract_career_date(
            self.ocr,
            img,
            dets,
            layout=self.date_layout,
            turns_class=self.date_turns_class,
            goal_class=self.date_goal_class,
        )
        cand = parse_career_date(raw) if raw else None

        prev: Optional[DateInfo] = getattr(self.state, "date_info", None)

        # Store for debugging even if we reject
        self.state.career_date_raw = raw

        # If OCR produced nothing, consider turn-based auto-advance (day consumed)
        if cand is None:
            logger_uma.debug("Date OCR parse failed/empty.")
            if prev and (prev.year_code in (1, 2, 3)):
                try:
                    curr_turn = int(self.state.turn)
                except Exception:
                    curr_turn = -1
                lt = self._last_turn_at_date_update
                # day likely progressed if turns decreased since last accepted date
                if (lt is not None) and (curr_turn >= 0) and (curr_turn < lt):
                    di = prev
                    # advance by +1 half safely (reuse same logic as below)
                    y, m, h = di.year_code, di.month, di.half
                    advanced: Optional[DateInfo] = None
                    if y in (1, 2, 3) and (m is not None):
                        if h == 1:
                            advanced = DateInfo(
                                raw=di.raw, year_code=y, month=m, half=2
                            )
                        else:
                            if m == 12:
                                if y in (1, 2):
                                    advanced = DateInfo(
                                        raw=di.raw, year_code=y + 1, month=1, half=1
                                    )
                                else:
                                    # Senior Late Dec -> Final Season
                                    advanced = DateInfo(
                                        raw=di.raw, year_code=4, month=None, half=None
                                    )
                            else:
                                advanced = DateInfo(
                                    raw=di.raw, year_code=y, month=m + 1, half=1
                                )
                    if advanced:
                        self.state.date_info = advanced
                        self.state.is_summer = is_summer(advanced)
                        self._date_stable_count = 0
                        self._date_artificial = True
                        self._last_turn_at_date_update = curr_turn
                        # new key → clear raced-today memory
                        new_key = self.state.date_info.as_key()
                        if new_key != self._last_date_key:
                            self._raced_keys_recent.clear()
                            self._last_date_key = new_key
                        logger_uma.info(
                            "[date] Auto-advanced by turns: %s -> %s",
                            prev.as_key(),
                            advanced.as_key(),
                        )
                        return
            # Nothing to do, keep previous
            return

        # If we already reached Final Season, only accept Final→Final
        if date_is_terminal(prev):
            if cand.year_code == 4:
                self.state.date_info = cand
                self.state.is_summer = is_summer(cand)
                self._date_stable_count = 0
                self._date_artificial = False
                self._last_turn_at_date_update = (
                    self.state.turn if isinstance(self.state.turn, int) else None
                )
                # new key guard
                new_key = self.state.date_info.as_key()
                if new_key != self._last_date_key:
                    self._raced_keys_recent.clear()
                    self._last_date_key = new_key
            else:
                logger_uma.debug("Ignoring non-final date after Final Season lock.")
            return

        # Pre-debut handling: allow 0→(1..3/4), but never accept (1..3)→0
        if prev and date_is_regular_year(prev) and date_is_pre_debut(cand):
            logger_uma.debug(
                f"Ignoring backward date {cand.as_key()} after {prev.as_key()}."
            )
            return

        # Monotonic acceptance (with warm-up/backfix)
        if not prev:
            # First observation: accept even if partial
            accepted = cand
            reason = "initial"
        else:
            cmp = date_cmp(cand, prev)
            if cmp < 0:
                # Backward correction. Allow if we just accepted prev (warm-up) or prev was artificial.
                idx_prev = date_index(prev)
                idx_new = date_index(cand)
                big_back = (
                    (idx_prev is not None)
                    and (idx_new is not None)
                    and ((idx_prev - idx_new) > MAX_SUSP_JUMP_HALVES)
                )
                if self._date_artificial or (self._date_stable_count < WARMUP_FRAMES):
                    accepted = cand
                    reason = "backfix (warmup/artificial)"
                    self._pending_date_back = None
                    self._pending_date_back_count = 0
                else:
                    # require persistence for suspicious backward jumps
                    if big_back:
                        if (
                            self._pending_date_back
                            and date_cmp(cand, self._pending_date_back) == 0
                        ):
                            self._pending_date_back_count += 1
                        else:
                            self._pending_date_back = cand
                            self._pending_date_back_count = 1
                        need = PERSIST_FRAMES - self._pending_date_back_count
                        if self._pending_date_back_count >= PERSIST_FRAMES:
                            accepted = cand
                            reason = "backfix (confirmed)"
                            self._pending_date_back = None
                            self._pending_date_back_count = 0
                        else:
                            logger_uma.debug(
                                f"Holding backward jump {prev.as_key()} -> {cand.as_key()} ; need {need} confirm(s)"
                            )
                            return
                    else:
                        # small/backward but reasonable → accept
                        accepted = cand
                        reason = "backfix (small)"
            else:
                # cmp >= 0 → monotonic or equal; proceed
                pass
            # (Optional) sanity guard against gigantic jumps in one frame
            idx_prev = date_index(prev)
            idx_new = date_index(cand)
            if (idx_prev is not None and idx_new is not None) and (
                idx_new - idx_prev > 6
            ):
                # Legitimate boundary: Senior Dec (Early/Late) → Final Season
                if (
                    prev.year_code == 3
                    and prev.month == 12
                    and (prev.half in (1, 2) or prev.half is None)
                    and cand.year_code == 4
                ):
                    # Accept immediately (no persistence required).
                    pass
                else:
                    # more than ~3 months (6 halves) in one hop → likely OCR glitch; require persistence
                    logger_uma.debug(
                        f"Suspicious jump {prev.as_key()} -> {cand.as_key()} (Δ={idx_new - idx_prev}). "
                        "Holding previous; will accept if persists on next frame."
                    )
                    # You can remember this candidate and require 2 consecutive hits to accept.
                    self._pending_date_jump = cand
                    return
            # If we stored a pending jump and it repeats, accept now
            if hasattr(self, "_pending_date_jump") and self._pending_date_jump:
                if date_cmp(cand, self._pending_date_jump) == 0:
                    reason = "confirmed jump"
                    accepted = cand
                    self._pending_date_jump = None
                else:
                    reason = "monotonic (no pending)"
                    accepted = cand
                    self._pending_date_jump = None
            else:
                reason = "monotonic"
                accepted = cand

        # Merge missing fields with previous when compatible (keeps half when month unchanged)
        merged = date_merge(prev, accepted)

        # Commit to state
        self.state.date_info = merged
        self.state.is_summer = is_summer(merged) if merged else None
        self._date_stable_count = 0
        # accepted from OCR → not artificial
        self._date_artificial = False
        self._last_turn_at_date_update = (
            self.state.turn if isinstance(self.state.turn, int) else None
        )
        # new key guard
        new_key_for_accept = merged.as_key() if merged else None
        if new_key_for_accept and new_key_for_accept != self._last_date_key:
            self._raced_keys_recent.clear()
            self._last_date_key = new_key_for_accept
        prev_key = prev.as_key() if prev else "None"
        merged_key = merged.as_key() if merged else "None"
        logger_uma.debug(f"[date] prev: {prev}. Cand: {cand}. accepted: {accepted}")
        logger_uma.info(f"[date] {reason}: {prev_key} -> {merged_key}")

        # If OCR produced the same compact key (no visible change) advance the date by +1 half.
        # Examples:
        #   Y1-Jun-1 -> Y1-Jun-2
        #   Y2-Dec-2 -> Y3-Jan-1
        #   Y3-Dec-2 -> Y4 (Final Season)
        try:
            # IF merged_key and prev_key are the same, probably the prediction now is correct
            if (
                merged_key == "None"
                and self.state.date_info
                and self.state.date_info.year_code not in (0, 4)
            ):
                di = self.state.date_info
                if (
                    di.year_code in (1, 2, 3)
                    and (di.month is not None)
                    and (di.half in (1, 2))
                ):
                    y, m, h = di.year_code, int(di.month), int(di.half)
                    if h == 1:
                        # Early -> Late (same month)
                        new_y, new_m, new_h = y, m, 2
                    else:
                        # Late -> Early next month/year (or Final Season after Y3-Dec-2)
                        if m == 12:
                            if y in (1, 2):
                                new_y, new_m, new_h = y + 1, 1, 1
                                logger_uma.info(
                                    "Naive date update, adding +1 half +1 year and reset"
                                )
                            else:
                                # Senior Late Dec -> Final Season (no month/half)
                                self.state.date_info = DateInfo(
                                    raw=di.raw, year_code=4, month=None, half=None
                                )
                                self.state.is_summer = is_summer(self.state.date_info)
                                logger_uma.info(
                                    "[date] No change detected; auto-advanced half: %s -> Y4",
                                    merged_key,
                                )
                                self._date_stable_count = 0
                                self._date_artificial = True
                                self._last_turn_at_date_update = (
                                    self.state.turn
                                    if isinstance(self.state.turn, int)
                                    else None
                                )
                                # new key → clear raced-today memory
                                if self.state.date_info.as_key() != self._last_date_key:
                                    self._raced_keys_recent.clear()
                                    self._last_date_key = self.state.date_info.as_key()

                                return
                        else:
                            new_y, new_m, new_h = y, m + 1, 1

                    advanced = DateInfo(
                        raw=di.raw, year_code=new_y, month=new_m, half=new_h
                    )

                    self.state.date_info = advanced
                    self.state.is_summer = is_summer(advanced)
                    self._date_stable_count = 0
                    self._date_artificial = True
                    self._last_turn_at_date_update = (
                        self.state.turn if isinstance(self.state.turn, int) else None
                    )
                    # new key → clear raced-today memory
                    if self.state.date_info.as_key() != self._last_date_key:
                        self._raced_keys_recent.clear()
                        self._last_date_key = self.state.date_info.as_key()

                    logger_uma.info(
                        "[date] No change detected; auto-advanced half: %s -> %s",
                        merged_key,
                        advanced.as_key(),
                    )
        except Exception as _adv_e:
            logger_uma.debug(f"[date] auto-advance skipped due to error: {_adv_e}")
        else:
            # If we got here without committing a new date (i.e., different accepted path),
            # increase stability counter.
            if self.state.date_info:
                self._date_stable_count += 1

    def _log_planned_race_decision(
        self,
        *,
        action: str,
        reason: Optional[str] = None,
        plan_name: Optional[str] = None,
        extra: Optional[dict] = None,
    ) -> None:
        di = getattr(self.state, "date_info", None)
        date_key = date_key_from_dateinfo(di) if di else None
        date_label = di.as_key() if di else None
        payload = [
            f"action={action}",
            f"plan={plan_name or self.state.planned_race_name or '-'}",
        ]
        if reason:
            payload.append(f"reason={reason}")
        payload.extend(
            [
                f"date_key={date_key or '-'}",
                f"date_label={date_label or '-'}",
                f"raw={self.state.career_date_raw or '-'}",
                f"skip={self._skip_race_once}",
                f"artificial={self._date_artificial}",
                f"stable={self._date_stable_count}",
                f"turn={self.state.turn}",
            ]
        )
        if extra:
            for key, value in extra.items():
                payload.append(f"{key}={value}")
        logger_uma.info("[planned_race] %s", " ".join(str(p) for p in payload))

    def _plan_race_today(self) -> None:
        """
        Decide (and cache) whether today has a planned race; explicit date->name
        in Settings.RACES wins over PRIORITIZE_G1 detection.
        """
        di = self.state.date_info
        key = date_key_from_dateinfo(di) if di else None
        self._last_date_key = key
        self.state.planned_race_name = None
        self.state.planned_race_canonical = None
        self.state.planned_race_tentative = False
        if not key:
            return

        # 1) explicit plan wins
        #    but skip if we already raced on this key (OCR didn’t tick yet)
        if (key in self.plan_races) and (key not in self._raced_keys_recent):
            raw_name = str(self.plan_races[key]).strip()
            canon = RaceIndex.canonicalize(raw_name)
            if not RaceIndex.valid_date_for_race(canon or raw_name, key):
                logger_uma.warning(
                    "[lobby] RACES plan '%s' is not present on %s in dataset; "
                    "will attempt OCR match anyway.",
                    raw_name,
                    key,
                )
            self.state.planned_race_name = raw_name
            self.state.planned_race_canonical = canon or raw_name.lower()
            self.state.planned_race_tentative = bool(
                self.plan_races_tentative.get(key)
            )
            self._log_planned_race_decision(
                action="plan_selected",
                plan_name=raw_name,
                extra={"already_raced": False},
            )
            return
        if key in self.plan_races and key in self._raced_keys_recent:
            name = str(self.plan_races[key]).strip()
            self._log_planned_race_decision(
                action="plan_already_completed",
                plan_name=name,
                extra={"already_raced": True},
            )
            return

        self._log_planned_race_decision(action="plan_missing_for_date", extra={"date_key": key})

    # Allow Agent to mark that we already raced for this date key
    def mark_raced_today(self, date_key: Optional[str]) -> None:
        if not date_key:
            return
        self._raced_keys_recent.add(date_key)
        # one-shot guard this loop as well
        self._skip_race_once = True
        self._skip_guard_key = date_key

    def _current_date_key(self) -> Optional[str]:
        di = getattr(self.state, "date_info", None)
        return date_key_from_dateinfo(di) if di else None

    def _invalidate_peek_cache(self) -> None:
        self._peek_cache_key = None
        self._peek_cache_value = None

    def _precheck_allowed(self) -> bool:
        if not Settings.LOBBY_PRECHECK_ENABLE:
            return False
        energy = self.state.energy
        if energy is None:
            return False
        if energy <= self.auto_rest_minimum:
            return False
        if (
            self.state.date_info
            and energy <= 30
            and is_summer_in_two_or_less_turns(self.state.date_info)
        ):
            return False
        return True

    def _peek_training_best_sv(
        self,
        img,
        dets,
        *,
        force_refresh: bool = False,
        stay_if_above_threshold: bool = False,
    ) -> Tuple[float, Dict[str, Any]]:
        if not Settings.LOBBY_PRECHECK_ENABLE:
            return 0.0, {"status": "disabled"}

        turn = self.state.turn if isinstance(self.state.turn, int) else -1
        energy = self.state.energy if isinstance(self.state.energy, (int, float)) else None
        cache_key = (self._current_date_key(), int(turn), energy)

        if (
            not force_refresh
            and self._peek_cache_key == cache_key
            and self._peek_cache_value is not None
        ):
            cached_sv, cached_meta = self._peek_cache_value
            meta = dict(cached_meta)
            meta["cache_hit"] = True
            return cached_sv, meta

        enter_ok = self._go_training_screen_from_lobby(img, dets, reason="PRECHECK")
        if not enter_ok:
            meta = {"status": "enter_failed"}
            self._peek_cache_key = cache_key
            self._peek_cache_value = (0.0, dict(meta))
            return 0.0, meta

        best_sv = 0.0
        meta: Dict[str, Any] = {"status": "unknown"}
        try:
            training_state, _, _ = scan_training_screen(
                self.ctrl,
                self.ocr,
                self.yolo_engine,
                energy=self.state.energy,
            )

            if not training_state:
                meta = {"status": "scan_empty"}
                self._peek_cache_key = cache_key
                self._peek_cache_value = (best_sv, dict(meta))
                return best_sv, meta

            try:
                sv_rows = get_compute_support_values()(training_state)
            except Exception as exc:  # pragma: no cover - defensive
                meta = {"status": "compute_failed", "error": str(exc)}
                self._peek_cache_key = cache_key
                self._peek_cache_value = (best_sv, dict(meta))
                return best_sv, meta

            allowed_rows = [r for r in sv_rows if r.get("allowed_by_risk")]
            best_row = None
            best_tile_xyxy = None
            if allowed_rows:
                best_row = max(
                    allowed_rows,
                    key=lambda r: float(r.get("sv_total", 0.0)),
                )
                best_sv = float(best_row.get("sv_total", 0.0))
                # Find the tile_xyxy from training_state using tile_idx
                tile_idx = best_row.get("tile_idx")
                if tile_idx is not None:
                    for tile in training_state:
                        if tile.get("tile_idx") == tile_idx:
                            best_tile_xyxy = tile.get("tile_xyxy")
                            break
                meta = {
                    "status": "ok",
                    "tile_idx": best_row.get("tile_idx"),
                    "tile_type": best_row.get("tile_type"),
                    "sv_total": best_sv,
                    "failure_pct": best_row.get("failure_pct"),
                    "tile_xyxy": best_tile_xyxy,  # Store geometry for clicking
                }
            else:
                meta = {"status": "no_allowed_tiles"}

        finally:
            # Only go back if we're not staying in training or if SV is below threshold
            should_stay = (
                stay_if_above_threshold
                and best_sv >= Settings.RACE_PRECHECK_SV
                and meta.get("status") == "ok"
            )
            if not should_stay:
                logger_uma.info(f"[lobby] Pre-check SV too low={best_sv} is not more than {Settings.RACE_PRECHECK_SV}, going back")
                self._go_back()
            else:
                # Click the best tile directly to save time
                tile_xyxy = meta.get("tile_xyxy")
                if tile_xyxy:
                    self.ctrl.click_xyxy_center(
                        tile_xyxy,
                        clicks=random.randint(3, 4),
                        jitter=calculate_jitter(tile_xyxy, percentage_offset=0.20),
                    )
                    logger_uma.info(
                        "[lobby] Pre-check clicked tile_idx=%s type=%s sv=%.2f",
                        meta.get("tile_idx"),
                        meta.get("tile_type"),
                        best_sv,
                    )
                    meta["stayed_in_training"] = True
                    meta["tile_clicked"] = True
                else:
                    # Fallback: go back if we can't click
                    logger_uma.warning(
                        "[lobby] Pre-check optimization failed: tile_xyxy not found for tile_idx=%s",
                        meta.get("tile_idx"),
                    )
                    self._go_back()
                    meta["stayed_in_training"] = False

        # Don't cache if we clicked a tile (state changed)
        if not meta.get("tile_clicked"):
            self._peek_cache_key = cache_key
            self._peek_cache_value = (best_sv, dict(meta))
        
        meta_with_flag = dict(meta)
        meta_with_flag["cache_hit"] = False
        return best_sv, meta_with_flag

    @abstractmethod
    def _process_turns_left(self, img, dets):
        raise NotImplementedError

    def _maybe_do_goal_race(self, img, dets) -> Tuple[bool, str]:
        """Implements the critical-goal race logic from your old Lobby branch."""

        if self.process_on_demand:
            self.state.goal = extract_goal_text(self.ocr, img, dets)

        goal = (self.state.goal or "").lower()

        there_is_progress_text = fuzzy_contains(goal, "progress", threshold=0.58)
        
        # Detect "Win Maiden race" or similar race-winning goals
        critical_goal_win_race = (
            fuzzy_contains(goal, "win", 0.7)
            and fuzzy_contains(goal, "maiden", 0.7)
            and fuzzy_contains(goal, "race", 0.7)
        )
        
        critical_goal_fans = there_is_progress_text or critical_goal_win_race or (
            fuzzy_contains(goal, "go", 0.7)
            and fuzzy_contains(goal, "fan", 0.7)
            and not fuzzy_contains(goal, "achieve", 0.7)
        )
        critical_goal_g1 = there_is_progress_text and (
            (fuzzy_contains(goal, "g1", 0.7) or fuzzy_contains(goal, "gl", 0.7))
            or fuzzy_contains(goal, "place within", 0.7)
            or (
                fuzzy_contains(goal, "place", 0.7)
                and fuzzy_contains(goal, "top", 0.7)
                and fuzzy_contains(goal, "time", 0.7)
            )
        )

        # Skip racing right at the first junior date (matching your original constraint)
        is_first_junior_date = (
            bool(self.state.date_info)
            and self.state.date_info.year_code == 1
            and self.state.date_info.month == 7
            and self.state.date_info.half == 1
        )

        if is_first_junior_date or self._skip_race_once:
            return False, "It is first day, no races available"

        if self.state.turn <= self.max_critical_turn:
            force_deadline = (
                isinstance(self.state.turn, int)
                and self.state.turn >= 0
                and self.state.turn <= Settings.GOAL_RACE_FORCE_TURNS
            )
            # Critical G1
            if critical_goal_g1:
                if self._precheck_allowed() and not force_deadline:
                    best_sv, meta = self._peek_training_best_sv(img, dets, stay_if_above_threshold=True)
                    if best_sv >= Settings.RACE_PRECHECK_SV:
                        logger_uma.info(
                            "[lobby] Goal G1 pre-check skip: sv=%.2f threshold=%.2f meta=%s",
                            best_sv,
                            Settings.RACE_PRECHECK_SV,
                            meta,
                        )
                        # Mark that tile is already clicked if optimization applied
                        reason = f"Pre-check training (G1) sv={best_sv:.2f}"
                        if meta.get("tile_clicked"):
                            reason += " [tile_clicked]"
                        return False, reason
                return True, f"[lobby] Critical goal G1 | turn={self.state.turn}"
            # Critical Fans
            elif critical_goal_fans:
                if self._precheck_allowed() and not force_deadline:
                    best_sv, meta = self._peek_training_best_sv(img, dets, stay_if_above_threshold=True)
                    if best_sv >= Settings.RACE_PRECHECK_SV:
                        logger_uma.info(
                            "[lobby] Goal fans pre-check skip: sv=%.2f threshold=%.2f meta=%s",
                            best_sv,
                            Settings.RACE_PRECHECK_SV,
                            meta,
                        )
                        # Mark that tile is already clicked if optimization applied
                        reason = f"Pre-check training (fans) sv={best_sv:.2f}"
                        if meta.get("tile_clicked"):
                            reason += " [tile_clicked]"
                        return False, reason
                return True, f"[lobby] Critical goal FANS/MAIDEN | turn={self.state.turn}"

        return False, "Unknown"

    def _should_skip_planned_race_for_training(self, img, dets) -> Tuple[bool, str]:
        if not self._precheck_allowed():
            return False, "precheck_disabled"

        best_sv, meta = self._peek_training_best_sv(img, dets, stay_if_above_threshold=True)
        threshold = Settings.RACE_PRECHECK_SV
        cache_info = f"cache={'hit' if meta.get('cache_hit') else 'miss'}"

        if best_sv >= threshold:
            logger_uma.info(
                "[lobby] Planned race pre-check skip: sv=%.2f threshold=%.2f meta=%s",
                best_sv,
                threshold,
                meta,
            )
            reason = f"Pre-check training sv={best_sv:.2f} {cache_info}"
            if meta.get("tile_clicked"):
                reason += " [tile_clicked]"
            return True, reason

        logger_uma.info(
            "[lobby] Planned race pre-check fail: sv=%.2f threshold=%.2f meta=%s",
            best_sv,
            threshold,
            meta,
        )
        return False, cache_info

    # --------------------------
    # Click helpers (Lobby targets)
    # --------------------------
    def _go_rest(self, *, reason: str) -> bool:
        logger_uma.info(f"[lobby] {reason}")
        # Prefer explicit REST; if summer, also accept the summer rest tile
        click = self.waiter.click_when(
            classes=("lobby_rest", "lobby_rest_summer"),
            prefer_bottom=True,
            timeout_s=2.5,
            tag="lobby_rest",
        )
        if click:
            time.sleep(3)
        return click

    def _go_recreate(self, *, reason: str = "Mood is low, recreating") -> bool:
        logger_uma.info(f"[lobby] {reason}")
        click = self.waiter.click_when(
            classes=("lobby_recreation", "lobby_rest_summer"),
            prefer_bottom=True,
            timeout_s=2.5,
            tag="lobby_recreate",
        )
        if click:
            time.sleep(2)
            # Tazuna recreation screen possible elements: recreation_row, support_tazuna, button_white
            # Check if there are 2 recreation_row if that is the case click in the top first

            # Collect the current screen state
            img, dets = collect(
                self.yolo_engine,
                imgsz=self.waiter.cfg.imgsz,
                conf=self.waiter.cfg.conf,
                iou=self.waiter.cfg.iou,
                tag="recreation_screen",
                agent=self.waiter.cfg.agent,
            )
            # Persist a lightweight snapshot of PAL rows (support + chain steps)
            try:
                date_key = date_key_from_dateinfo(getattr(self.state, "date_info", None))
                turn_val = self.state.turn if isinstance(self.state.turn, int) else None
                # Gather PAL deck names for fuzzy matching (Settings preset deck)
                try:
                    pal_deck_names = [
                        str(e.get("name")).strip()
                        for e in (getattr(Settings, "SUPPORT_DECK", []) or [])
                        if str(e.get("attribute", "")).strip().upper() == "PAL"
                        and str(e.get("name", "")).strip()
                    ]
                except Exception:
                    pal_deck_names = []
                static_pal_names = ["Riko Kashimoto", "Tazuna Hayakawa", "Aoi Kiryuin"]
                pal_name_targets = list({*(n for n in static_pal_names if n), *pal_deck_names})
                # Group items inside each recreation_row by bounding box containment
                def _center(xyxy):
                    x1, y1, x2, y2 = xyxy
                    return (0.5 * (x1 + x2), 0.5 * (y1 + y2))

                def _inside(pt, xyxy):
                    x, y = pt
                    x1, y1, x2, y2 = xyxy
                    return x1 <= x <= x2 and y1 <= y <= y2

                supports = [d for d in dets if isinstance(d.get("name"), str) and d["name"].startswith("support_")]
                chains = [d for d in dets if d.get("name") == "event_chain"]
                candidates = []
                # Iterate rows and persist observations
                for row in [d for d in dets if d.get('name') == 'recreation_row']:
                    rxy = row['xyxy']
                    # Match first support face inside the row
                    support_det = None
                    for s in supports:
                        if _inside(_center(s['xyxy']), rxy):
                            support_det = s
                            break

                    # Map detection class to canonical support name for catalog lookups
                    support_name = None
                    if support_det:
                        if support_det['name'] == 'support_kashimoto':
                            support_name = 'Riko Kashimoto'
                        elif support_det['name'] == 'support_tazuna':
                            support_name = 'Tazuna Hayakawa'
                        elif support_det['name'] == 'support_director':
                            # Aoi Kiryuin (Director)
                            support_name = 'Aoi Kiryuin'
                    # If face class not detected, try OCR name in row (top-half, left area)
                    if not support_name and self.ocr is not None and pal_name_targets:
                        try:
                            x1, y1, x2, y2 = rxy
                            W = float(x2 - x1)
                            H = float(y2 - y1)
                            crop = img.crop((x1, y1, x1 + max(1.0, 0.72 * W), y1 + max(1.0, 0.52 * H)))
                            raw_txt = self.ocr.text(crop, joiner=" ", min_conf=0.2)
                            # Remove common UI tokens
                            blacklist = [
                                "friendship",
                                "gauge",
                                "event progress",
                                "trainee umamusume",
                                "cancel",
                            ]
                            nt = normalize_ocr_text(raw_txt)
                            for token in blacklist:
                                nt = nt.replace(normalize_ocr_text(token), " ")
                            nt = nt.strip()
                            if nt:
                                best, score = fuzzy_best_match(nt, [normalize_ocr_text(n) for n in pal_name_targets])
                                if best and score >= 0.65:
                                    # Map back from normalized to original target by index
                                    idx = [normalize_ocr_text(n) for n in pal_name_targets].index(best)
                                    support_name = pal_name_targets[idx]
                        except Exception:
                            pass
                    # Final fallback: single PAL in deck and only two rows
                    if not support_name and len(pal_deck_names) == 1:
                        # Heuristic: if only two rows exist, top one is likely PAL
                        # We'll assign the single deck PAL as the candidate name
                        support_name = pal_deck_names[0]

                    # Count completed chain steps (blue arrows only)
                    steps_completed = 0
                    if chains:
                        chain_in_row = [c for c in chains if _inside(_center(c['xyxy']), rxy)]
                        cnt = _count_chain_steps(chain_in_row, frame=img)
                        steps_completed = int(cnt) if cnt is not None else 0

                    next_step = max(1, steps_completed + 1)
                    energy_expected = None
                    energy_max = None
                    if support_name:
                        energy_expected = predict_next_chain_has_energy_from_raw(
                            support_name=support_name,
                            next_step=next_step,
                            attribute='PAL',
                        )
                        try:
                            from core.utils.event_processor import (
                                predict_next_chain_max_energy_from_raw,
                            )
                            energy_max = predict_next_chain_max_energy_from_raw(
                                support_name=support_name,
                                next_step=next_step,
                                attribute='PAL',
                            )
                        except Exception:
                            energy_max = None

                    # Persist using detection key as the dictionary key (stable class name)
                    key = (support_det['name'] if support_det else None)
                    if key:
                        self.pal_memory.record_chain_snapshot(
                            key,
                            steps=max(0, int(steps_completed)),
                            date_key=date_key,
                            turn=turn_val,
                            next_energy=(bool(energy_expected) if energy_expected is not None else None),
                            commit=False,
                        )
                        # Score this row
                        # Determine if row appears active
                        try:
                            crop = img.crop(rxy)
                            clf = ActiveButtonClassifier.load(Settings.IS_BUTTON_ACTIVE_CLF_PATH)
                            is_active = bool(clf.predict(crop))
                        except Exception:
                            is_active = True
                        score = 0.0
                        energy_val = self.state.energy if isinstance(self.state.energy, (int, float)) else None
                        energy_need = energy_val is not None and energy_val <= float(self.auto_rest_minimum)
                        if energy_need and energy_expected:
                            score += 10.0
                        if not energy_need:
                            sclass = str(key)
                            if sclass == 'support_kashimoto':
                                score += 3.0
                            elif sclass == 'support_tazuna':
                                score += 2.0
                            elif sclass == 'support_director':
                                score += 1.0
                        if energy_val is not None and energy_max is not None:
                            try:
                                if energy_val + int(energy_max) > 100:
                                    score -= 1.0
                            except Exception:
                                pass
                        if is_active:
                            score += 0.5
                        candidates.append((score, row))
                # Persist once after processing all rows
                self.pal_memory.save()
            except Exception as e:
                logger_uma.debug(f"[pal] snapshot failed: {e}")

            # Check for recreation rows in detections
            recreation_rows = [d for d in dets if d.get('name') == 'recreation_row']

            if recreation_rows:
                # Always filter out inactive rows first to avoid clicking completed PAL chains
                active_rows = []
                clf = ActiveButtonClassifier.load(Settings.IS_BUTTON_ACTIVE_CLF_PATH)
                for row in recreation_rows:
                    try:
                        crop = img.crop(row['xyxy'])
                        is_active = bool(clf.predict(crop))
                    except Exception:
                        is_active = True
                    if is_active:
                        active_rows.append(row)
                
                chosen = None
                if candidates:
                    # Filter candidates to only include active rows
                    active_candidates = [(score, row) for score, row in candidates if row in active_rows]
                    if active_candidates:
                        active_candidates.sort(key=lambda x: float(x[0]), reverse=True)
                        chosen = active_candidates[0][1]
                        logger_uma.info("[lobby] Selected PAL recreation row (scored)")
                    elif active_rows:
                        # Fallback: pick first active row if no scored candidates remain
                        active_rows.sort(key=lambda r: r['xyxy'][1])
                        chosen = active_rows[0]
                        logger_uma.info("[lobby] Selected first active recreation row (fallback)")
                elif active_rows:
                    # No PAL candidates, pick first active row
                    active_rows.sort(key=lambda r: r['xyxy'][1])
                    chosen = active_rows[0]
                    logger_uma.info("[lobby] Selected first active recreation row")
                
                if chosen is not None:
                    self.ctrl.click_xyxy_center(chosen['xyxy'])
                    time.sleep(0.5)
                else:
                    logger_uma.warning("[lobby] No active recreation rows found, skipping click")
                
            time.sleep(2)
        return click

    def _go_skills(self) -> bool:
        logger_uma.info("[lobby] Opening Skills")
        clicked = self.waiter.click_when(
            classes=("lobby_skills",),
            prefer_bottom=True,
            timeout_s=2.5,
            tag="lobby_skills",
        )
        if clicked:
            time.sleep(1)
        return clicked

    def _go_infirmary(self) -> bool:
        logger_uma.info("[lobby] Infirmary ON → going to infirmary")
        click = self.waiter.click_when(
            classes=("lobby_infirmary",),
            prefer_bottom=True,
            timeout_s=2.5,
            tag="lobby_infirmary",
        )
        if click:
            time.sleep(2)
        return click

    def _go_training_screen_from_lobby(self, img, dets, reason: Optional[str] = None) -> bool:
        if reason:
            logger_uma.info("[lobby] %s → go Train", reason)
        else:
            logger_uma.info("[lobby] No critical actions → go Train")
        clicked = self.waiter.click_when(
            classes=("lobby_training",),
            prefer_bottom=True,
            timeout_s=2.5,
            tag="lobby_training",
        )
        if not clicked:
            logger_uma.warning("[lobby] Training button click failed")
            return False

        confirmed = False
        deadline = time.time() + 2.5
        while time.time() < deadline:
            if abort_requested():
                logger_uma.info("[lobby] Training confirmation aborted")
                return False
            if self.waiter.seen(
                classes=("training_button",),
                conf_min=0.35,
                tag="lobby_training_confirm",
            ):
                confirmed = True
                break
            time.sleep(0.15)

        if not confirmed:
            logger_uma.warning("[lobby] Training screen not confirmed after click")
            return False

        logger_uma.info("[lobby] Training screen confirmed")
        if clicked:
            # This replaces a time.sleep... time.sleep(1.2)
            # If the machine is ultra powerful, then you should neet a time.sleep(1.2)
            # Meanwhile we wait for animation we calculate stats
            # TODO: PROCESS IN PARALLEL
            if self.process_on_demand and dets is not None:
                self._update_stats(img, dets)
                self._stats_refresh_counter += 1

        return clicked

    def _go_back(self) -> bool:
        # Minimal, OCR-gated BACK
        ok = self.waiter.click_when(
            classes=("button_white",),
            texts=("BACK",),
            prefer_bottom=True,
            timeout_s=2.0,
            tag="lobby_back",
        )
        if ok:
            logger_uma.info("[lobby] GO BACK")
            # After back, wait for animation to end
            time.sleep(1)
        return ok
