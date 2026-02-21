# main.py
from __future__ import annotations

import threading
import time
import argparse
import webbrowser
import uvicorn
import subprocess
import sys
from pathlib import Path
import shutil
import queue

try:
    import keyboard as kb  # type: ignore
except Exception:  # pragma: no cover - optional dependency, handled by keyboard_handler
    kb = None  # type: ignore

from core.utils import keyboard_handler

from core.actions.ura.agent import AgentURA
from core.actions.unity_cup.agent import AgentUnityCup
from core.agent_scenario import AgentScenario
from core.utils.logger import logger_uma, setup_uma_logging
from core.settings import Settings
from core.agent_nav import AgentNav
from core.agent_career_loop import AgentCareerLoop
from core.utils.hotkey_manager import get_hotkey_manager

from server.main import app
from server.utils import (
    load_config,
    ensure_config_exists,
    ensure_nav_exists,
    load_nav_prefs,
    save_config,
)
from core.utils.abort import request_abort, clear_abort
from core.utils.event_processor import UserPrefs
from core.utils.preset_overlay import show_preset_overlay
from core.ui.scenario_prompt import choose_active_scenario, ScenarioSelectionCancelled

# Controllers & perception interfaces
from core.controllers.base import IController
from core.perception.ocr.interface import OCRInterface
from core.perception.yolo.interface import IDetector
from core.controllers.steam import SteamController
from core.controllers.android import ScrcpyController
from core.controllers.adb import ADBController
from core.utils.tkthread import ensure_tk_loop

try:
    # Optional; if your Bluestacks controller is a separate class
    from core.controllers.bluestacks import BlueStacksController

    HAS_BLUESTACKS_CTRL = True
except Exception:
    BlueStacksController = None  # type: ignore
    HAS_BLUESTACKS_CTRL = False


# ---------------------------
# Helpers to instantiate runtimes from Settings
# ---------------------------
def make_controller_from_settings() -> IController:
    """Build a fresh controller based on current Settings.MODE + resolved window title."""
    mode = Settings.MODE.lower().strip()
    window_title = Settings.resolve_window_title(mode)

    logger_uma.debug(
        f"[CTRL] Requested mode='{Settings.MODE}' normalized='{mode}' window_title='{window_title}'"
    )

    if mode == "steam":
        logger_uma.info(f"[CTRL] Mode=steam, window_title='{window_title}'")
        return SteamController(window_title)
    elif mode == "adb":
        device = getattr(Settings, "ADB_DEVICE", None)
        logger_uma.info(f"[CTRL] Mode=adb, device='{device}'")
        return ADBController(device=device)
    elif mode == "bluestack":
        use_adb = getattr(Settings, "USE_ADB", False)
        if use_adb:
            device = getattr(Settings, "ADB_DEVICE", "localhost:5555")
            logger_uma.info(f"[CTRL] Mode=bluestack (ADB), device='{device}'")
            return ADBController(device=device, auto_connect=True)

        logger_uma.info(f"[CTRL] Mode=bluestack, window_title='{window_title}'")
        if HAS_BLUESTACKS_CTRL and BlueStacksController is not None:
            return BlueStacksController(window_title)  # type: ignore
        return ScrcpyController(window_title)
    else:
        # scrcpy (default branch)
        logger_uma.info(f"[CTRL] Mode=scrcpy, window_title='{window_title}'")
        return ScrcpyController(window_title)


def make_ocr_yolo_from_settings(
    ctrl: IController, weights: str | Path | None = None, career_loop_weights: str | Path | None = None
) -> tuple[OCRInterface, IDetector, IDetector]:
    """Build fresh OCR and YOLO engines based on current Settings."""
    resolved_weights = weights if weights is not None else Settings.ACTIVE_YOLO_WEIGHTS
    weights_str = str(resolved_weights) if resolved_weights is not None else None

    if Settings.USE_FAST_OCR:
        det_name = "PP-OCRv5_mobile_det"
        rec_name = "en_PP-OCRv5_mobile_rec"
    else:
        det_name = "PP-OCRv5_server_det"
        rec_name = "en_PP-OCRv5_server_rec"

    if Settings.USE_EXTERNAL_PROCESSOR:
        logger_uma.info(
            f"[PERCEPTION] Using external processor at: {Settings.EXTERNAL_PROCESSOR_URL}"
        )
        from core.perception.ocr.ocr_remote import RemoteOCREngine
        from core.perception.yolo.yolo_remote import RemoteYOLOEngine

        ocr = RemoteOCREngine(base_url=Settings.EXTERNAL_PROCESSOR_URL)
        if weights_str:
            yolo_engine = RemoteYOLOEngine(
                ctrl=ctrl, base_url=Settings.EXTERNAL_PROCESSOR_URL, weights=weights_str
            )
        else:
            yolo_engine = RemoteYOLOEngine(
                ctrl=ctrl, base_url=Settings.EXTERNAL_PROCESSOR_URL
            )
        if career_loop_weights:
            career_loop_engine = RemoteYOLOEngine(
                ctrl=ctrl, base_url=Settings.EXTERNAL_PROCESSOR_URL, weights=career_loop_weights
            )
        else:
            career_loop_engine = RemoteYOLOEngine(
                ctrl=ctrl, base_url=Settings.EXTERNAL_PROCESSOR_URL, weights=Settings.YOLO_WEIGHTS_CAREER_LOOP
            )
        return ocr, yolo_engine, career_loop_engine

    logger_uma.info("[PERCEPTION] Using internal processors")
    from core.perception.ocr.ocr_local import LocalOCREngine
    from core.perception.yolo.yolo_local import LocalYOLOEngine

    ocr = LocalOCREngine(
        text_detection_model_name=det_name,
        text_recognition_model_name=rec_name,
    )
    if weights_str:
        yolo_engine = LocalYOLOEngine(ctrl=ctrl, weights=weights_str)
    else:
        yolo_engine = LocalYOLOEngine(ctrl=ctrl)

    if career_loop_weights:
        career_loop_engine = LocalYOLOEngine(ctrl=ctrl, weights=career_loop_weights)
    else:
        career_loop_engine = LocalYOLOEngine(ctrl=ctrl, weights=Settings.YOLO_WEIGHTS_CAREER_LOOP)

    return ocr, yolo_engine, career_loop_engine


# ---------------------------
# Server
# ---------------------------
def boot_server():
    url = f"http://{Settings.HOST}:{Settings.PORT}"
    logger_uma.info(f"[SERVER] {url}")
    try:
        webbrowser.open(url, new=2)
    except Exception as exc:
        logger_uma.debug(f"[SERVER] Failed to open browser automatically: {exc}")
    uvicorn.run(app, host=Settings.HOST, port=Settings.PORT, log_level="warning")


# ---------------------------
# Cleanup helpers
# ---------------------------
def cleanup_debug_training_if_needed():
    debug_root = Settings.DEBUG_DIR
    if not debug_root.exists():
        return

    threshold_bytes = 250 * 1024 * 1024
    agent_dirs = [p for p in debug_root.iterdir() if p.is_dir()]

    for agent_dir in sorted(agent_dirs, key=lambda p: p.name.lower()):
        total_bytes = 0
        for path in agent_dir.rglob("*"):
            if path.is_file():
                try:
                    total_bytes += path.stat().st_size
                except OSError:
                    continue

        if total_bytes <= threshold_bytes:
            continue

        timestamp = time.strftime("%y%m%d_%H%M%S")
        set_name = f"{agent_dir.name}_low_{timestamp}"
        cmd = [
            sys.executable,
            "collect_data_training.py",
            "--src",
            str(debug_root),
            "--agent",
            agent_dir.name,
            "--set-name",
            set_name,
            "--percentile",
            "5",
            "--min-per-folder",
            "3",
            "--max-per-folder",
            "10",
            "--action",
            "move",
            "--exclude",
            "general",
            "agent_unknown_advance",
            "screen",
        ]

        size_mb = total_bytes / (1024 * 1024)
        logger_uma.info(
            f"[CLEANUP] debug/{agent_dir.name} size={size_mb:.1f} MB exceeds threshold. Running cleanup to {set_name}."
        )
        try:
            subprocess.run(cmd, check=True)
            for child in agent_dir.iterdir():
                if child.is_dir():
                    try:
                        shutil.rmtree(child)
                        logger_uma.info(f"[CLEANUP] Removed folder {child}")
                    except Exception as exc:
                        logger_uma.warning(
                            f"[CLEANUP] Failed to remove folder {child}: {exc}"
                        )
        except subprocess.CalledProcessError as exc:
            logger_uma.warning(
                f"[CLEANUP] Cleanup command exited with status {exc.returncode}: {exc}"
            )
        except Exception as exc:
            logger_uma.warning(f"[CLEANUP] Cleanup command failed: {exc}")


# ---------------------------
# Bot state & helpers
# ---------------------------
class BotState:
    def __init__(self):
        self.thread: threading.Thread | None = None
        self.agent_scenario: AgentScenario | None = None
        self.running: bool = False
        self._lock = threading.Lock()

    def start(self, career_loop_mode: False):
        """
        Reload config.json -> Settings.apply_config -> build fresh controller + OCR/YOLO -> run Player.
        This guarantees we always reflect the latest UI changes at start time.
        """
        with self._lock:
            if self.running:
                logger_uma.info("[BOT] Already running.")
                return

            # 1) Re-hydrate Settings from the (possibly updated) config.json
            try:
                cfg = load_config()
            except Exception:
                cfg = {}
            Settings.apply_config(cfg or {})
            
            # Load nav preferences
            try:
                nav_prefs = load_nav_prefs()
                Settings.apply_nav_preferences(nav_prefs)
            except Exception:
                pass

            # 2) Configure logging using (possibly updated) Settings.DEBUG
            setup_uma_logging(debug=Settings.DEBUG)

            # 3) Build fresh controller & perception engines using the *current* settings
            ctrl = make_controller_from_settings()
            if not ctrl.focus():
                # Helpful mode-aware error
                mode = Settings.MODE.lower()
                if mode == "adb" or (mode == "bluestack" and Settings.USE_ADB):
                    logger_uma.error(
                        f"[BOT] Could not connect to ADB device '{Settings.ADB_DEVICE}'. "
                        "Ensure ADB is installed, the device is reachable (e.g. adb connect localhost:5555), and BlueStacks ADB is enabled."
                    )
                else:
                    miss = (
                        "Steam"
                        if mode == "steam"
                        else ("BlueStacks" if mode == "bluestack" else "SCRCPY")
                    )
                    logger_uma.error(
                        f"[BOT] Could not find/focus the {miss} window (title='{Settings.resolve_window_title(mode)}')."
                    )
                return

            ocr, yolo_engine, career_loop_engine = make_ocr_yolo_from_settings(ctrl)

            # 4) Extract preset-specific runtime opts (skill_list / plan_races / select_style)
            preset_opts = Settings.extract_runtime_preset(cfg or {})

            # 5) Build event prefs from config (active preset). If malformed/missing,
            #    UserPrefs.from_config() returns safe defaults and EventFlow will still
            #    pick the top option if a pick is invalid at runtime.
            event_prefs = UserPrefs.from_config(cfg or {})
            

            if Settings.ACTIVE_SCENARIO == "unity_cup":
                # Instantiate Player with runtime knobs from Settings + presets + event prefs
                self.agent_scenario = AgentUnityCup(
                    ctrl=ctrl,
                    ocr=ocr,
                    yolo_engine=yolo_engine,
                    interval_stats_refresh=1,
                    minimum_skill_pts=preset_opts.get("minimum_skill_pts", Settings.MINIMUM_SKILL_PTS),
                    prioritize_g1=False,
                    auto_rest_minimum=Settings.AUTO_REST_MINIMUM,
                    plan_races=preset_opts["plan_races"],
                    skill_list=preset_opts["skill_list"],
                    select_style=preset_opts[
                        "select_style"
                    ],  # "end"|"late"|"pace"|"front"|None
                    event_prefs=event_prefs,
                )
            else:
                # Instantiate Player with runtime knobs from Settings + presets + event prefs
                self.agent_scenario = AgentURA(
                    ctrl=ctrl,
                    ocr=ocr,
                    yolo_engine=yolo_engine,
                    interval_stats_refresh=1,
                    minimum_skill_pts=preset_opts.get("minimum_skill_pts", Settings.MINIMUM_SKILL_PTS),
                    prioritize_g1=False,
                    auto_rest_minimum=Settings.AUTO_REST_MINIMUM,
                    plan_races=preset_opts["plan_races"],
                    skill_list=preset_opts["skill_list"],
                    select_style=preset_opts[
                        "select_style"
                    ],  # "end"|"late"|"pace"|"front"|None
                    event_prefs=event_prefs,
                )

            def _runner():
                re_init = False
                try:
                    if career_loop_mode:
                        logger_uma.info("[BOT] Started in Career Loop Mode.")
                    else:
                        logger_uma.info("[BOT] Started.")
                    

                    if career_loop_mode:
                        logger_uma.info("[BOT] Career Loop Mode: Creating AgentCareerLoop wrapper...")
                        # Create Waiter instance for career loop flows
                        from core.utils.waiter import Waiter, PollConfig
                        waiter_config = PollConfig(
                            imgsz=Settings.YOLO_IMGSZ,
                            conf=Settings.YOLO_CONF,
                            iou=Settings.YOLO_IOU,
                            poll_interval_s=0.5,
                            timeout_s=4.0,
                            tag="career_loop",
                            agent="career_loop",
                        )
                        waiter = Waiter(ctrl=ctrl, ocr=ocr, yolo_engine=career_loop_engine, config=waiter_config)
                        
                        # Create AgentCareerLoop with all required dependencies
                        career_loop = AgentCareerLoop(
                            ctrl=ctrl,
                            ocr=ocr,
                            yolo_engine=career_loop_engine,
                            waiter=waiter,
                            agent_scenario=self.agent_scenario,
                            preferred_support=Settings.CAREER_LOOP_PREFERRED_SUPPORT,
                            preferred_level=Settings.CAREER_LOOP_PREFERRED_LEVEL,
                            max_refresh_attempts=Settings.CAREER_LOOP_MAX_REFRESH,
                            refresh_wait_seconds=Settings.CAREER_LOOP_REFRESH_WAIT,
                            max_careers=Settings.CAREER_LOOP_MAX_CAREERS,
                            error_threshold=Settings.CAREER_LOOP_ERROR_THRESHOLD,
                        )
                        
                        logger_uma.info(
                            "[BOT] Career Loop Mode: Starting loop (max_careers=%s, support=%s Lv%d)...",
                            Settings.CAREER_LOOP_MAX_CAREERS or "infinite",
                            Settings.CAREER_LOOP_PREFERRED_SUPPORT,
                            Settings.CAREER_LOOP_PREFERRED_LEVEL,
                        )

                        # Run the career loop
                        career_loop.run()
                    # if not none
                    elif self.agent_scenario:
                        self.agent_scenario.run(
                            delay=getattr(Settings, "MAIN_LOOP_DELAY", 0.4),
                            max_iterations=getattr(Settings, "MAX_ITERATIONS", None),
                        )
                except Exception as e:
                    msg = str(e).lower()
                    if "connection aborted" in msg or "adb command timed out" in msg:
                        logger_uma.info(
                            "Trying to recover from bot crash, connection to host was lost"
                        )
                        time.sleep(2)
                        if self.agent_scenario:
                            self.agent_scenario.run(
                                delay=getattr(Settings, "MAIN_LOOP_DELAY", 0.4),
                                max_iterations=getattr(Settings, "MAX_ITERATIONS", None),
                            )
                    else:
                        logger_uma.exception("[BOT] Crash: %s", e)
                finally:
                    if not re_init:
                        with self._lock:
                            self.running = False
                            logger_uma.info("[BOT] Stopped.")

            self.thread = threading.Thread(target=_runner, daemon=True)
            self.running = True
            logger_uma.debug("[BOT] Launching agent thread…")
            clear_abort()  # ensure previous abort state is cleared
            self.thread.start()

    def stop(self):
        with self._lock:
            if not self.running or not self.agent_scenario:
                logger_uma.info("[BOT] Not running.")
                return
            logger_uma.info("[BOT] Stopping… (signal loop to exit)")
            request_abort()
            self.agent_scenario.is_running = False
            try:
                self.agent_scenario.emergency_stop()
            except Exception:
                pass

    def toggle(self, source: str = "hotkey"):
        logger_uma.debug(f"[BOT] toggle() called from {source}. running={self.running}")
        if self.running:
            self.stop()
        else:
            self.start()


# ---------------------------
# AgentNav one-shot runner state
# ---------------------------
class NavState:
    def __init__(self):
        self.thread: threading.Thread | None = None
        self.agent: AgentNav | None = None
        self.running: bool = False
        self._lock = threading.Lock()
        self.current_action: str | None = None

    def start(self, action: str):
        with self._lock:
            if self.running:
                logger_uma.info(
                    f"[AgentNav] Already running (action={getattr(self.agent, 'action', '?')})."
                )
                return

            # Re-hydrate settings and logging similar to Player start
            try:
                cfg = load_config()
            except Exception:
                cfg = {}
            Settings.apply_config(cfg or {})
            
            # Load nav preferences
            try:
                nav_prefs = load_nav_prefs()
                Settings.apply_nav_preferences(nav_prefs)
            except Exception:
                pass
            
            setup_uma_logging(debug=Settings.DEBUG)

            ctrl = make_controller_from_settings()
            if not ctrl.focus():
                mode = Settings.MODE.lower()
                if mode == "adb" or (mode == "bluestack" and Settings.USE_ADB):
                    logger_uma.error(
                        f"[AgentNav] Could not connect to ADB device '{Settings.ADB_DEVICE}'. "
                        "Ensure ADB is installed, the device is reachable, and BlueStacks ADB is enabled."
                    )
                else:
                    miss = (
                        "Steam"
                        if mode == "steam"
                        else ("BlueStacks" if mode == "bluestack" else "SCRCPY")
                    )
                    logger_uma.error(
                        f"[AgentNav] Could not find/focus the {miss} window (title='{Settings.resolve_window_title(mode)}')."
                    )
                return

            # OCR from settings, YOLO engine for NAV specifically
            ocr, yolo_engine_nav = make_ocr_yolo_from_settings(
                ctrl, weights=Settings.YOLO_WEIGHTS_NAV
            )

            self.agent = AgentNav(ctrl, ocr, yolo_engine_nav, action=action)

            def _runner():
                try:
                    logger_uma.info(f"[AgentNav] Started (action={action}).")
                    if self.agent:
                        self.agent.run()
                except Exception as e:
                    logger_uma.exception("[AgentNav] Crash: %s", e)
                finally:
                    with self._lock:
                        self.running = False
                        self.current_action = None
                        logger_uma.info("[AgentNav] Stopped.")

            self.thread = threading.Thread(target=_runner, daemon=True)
            self.running = True
            self.current_action = action
            self.thread.start()

    def stop(self):
        with self._lock:
            if not self.running or not self.agent:
                logger_uma.info("[AgentNav] Not running.")
                return
            logger_uma.info(
                f"[AgentNav] Stopping current run (action={self.current_action})."
            )
            try:
                self.agent.stop()
            except Exception:
                pass
            # Fallback: set internal stop event if exposed
            try:
                evt = getattr(self.agent, "_stop_event", None)
                if evt is not None:
                    evt.set()
            except Exception:
                pass
            t = self.thread
            if t is not None:
                # Wait in small intervals so we can break early if stopped
                for _ in range(30):  # ~3s total
                    try:
                        t.join(timeout=0.1)
                    except Exception:
                        break
                    if not t.is_alive():
                        break
                if t.is_alive():
                    logger_uma.warning(
                        "[AgentNav] Worker thread is still alive after stop request."
                    )
            self.running = False
            self.thread = None
            self.agent = None
            self.current_action = None
            logger_uma.info("[AgentNav] Stop requested.")


# ---------------------------
# Hotkey loop (keyboard lib + polling fallback)
# ---------------------------
def hotkey_loop(bot_state: BotState, nav_state: NavState):
    # Support configured hotkey and F2 as backup for Player; F7/F8 for AgentNav
    configured = str(getattr(Settings, "HOTKEY", "F2")).upper()
    keys_bot = sorted(set([configured, "F2"]))
    keys_career_loop = ["F1"]
    keys_nav = ["F7", "F8", "F9"]
    logger_uma.info(f"[HOTKEY] Run bot in Scenario (e.g. URA, Unity Cup): press {', '.join(keys_bot)} to start/stop.")
    logger_uma.info("[HOTKEY] AgentNav: press F7=TeamTrials, F8=DailyRaces")
    logger_uma.info("[HOTKEY] AgentNav: press F9=Roulette/PrizeDerby.")

    # Track which keys successfully registered hooks (to skip in polling)
    hooked_keys = set()
    event_q: "queue.SimpleQueue[tuple[str, str]]" = queue.SimpleQueue()
    
    # Debounce across both hook & poll paths - INCREASED to prevent race condition
    # between hook (trigger_on_release) and polling (while key pressed)
    last_ts_toggle = 0.0
    last_ts_career_loop = 0.0
    last_ts_team = 0.0
    last_ts_daily = 0.0
    last_ts_roulette = 0.0

    def _show_preset_overlay_if_needed():
        if bot_state.running:
            return
        if not getattr(Settings, "SHOW_PRESET_OVERLAY", False):
            return
        try:
            cfg = load_config() or {}
            try:
                Settings._last_config = dict(cfg)
            except Exception:
                pass
        except Exception:
            cfg = Settings._last_config or {}
        try:
            # Read from scenario-aware structure
            general = cfg.get("general") or {}
            active_scenario = general.get("activeScenario", "ura")
            scenarios = cfg.get("scenarios") or {}
            scenario_branch = scenarios.get(active_scenario) or {}
            presets = scenario_branch.get("presets") or []
            active_id = scenario_branch.get("activePresetId")
            
            preset = next((p for p in presets if p.get("id") == active_id), None)
            if not preset and presets:
                preset = presets[0]
            name = (preset or {}).get("name") or "Unnamed preset"
            scenario_label = active_scenario.replace("_", " ").title()
            duration = getattr(Settings, "PRESET_OVERLAY_DURATION", 5.0)
            show_preset_overlay(
                f"Scenario: {scenario_label}\nActive preset: {name}",
                duration=max(1.0, float(duration or 0.0)),
                x=32,
                y="center",
            )
        except Exception as exc:
            logger_uma.debug("[HOTKEY] Failed to display preset overlay: %s", exc)

    def _show_scenario_stopped_overlay_if_needed():
        if not getattr(Settings, "SHOW_PRESET_OVERLAY", False):
            return
        try:
            cfg = load_config() or {}
            try:
                Settings._last_config = dict(cfg)
            except Exception:
                pass
        except Exception:
            cfg = Settings._last_config or {}
        try:
            general = cfg.get("general") or {}
            active_scenario = general.get("activeScenario", "ura")
            scenario_label = active_scenario.replace("_", " ").title()
            duration = getattr(Settings, "PRESET_OVERLAY_DURATION", 5.0)
            show_preset_overlay(
                f"Scenario: {scenario_label}\nRequesting stop...",
                duration=max(1.0, float(duration or 0.0)),
                x=32,
                y="center",
                background="#F97316",
            )
        except Exception as exc:
            logger_uma.debug(
                "[HOTKEY] Failed to display stop overlay: %s", exc
            )

    def _select_scenario_before_start() -> bool:
        try:
            cfg = load_config() or {}
        except Exception as exc:
            logger_uma.warning(
                f"[HOTKEY] Failed to load config for scenario selection: {exc}"
            )
            cfg = Settings._last_config or {}
            if not isinstance(cfg, dict):
                cfg = {}

        general = cfg.get("general")
        if not isinstance(general, dict):
            general = {}
            cfg["general"] = general

        last = (general.get("activeScenario") or "ura").strip().lower()
        if last not in {"ura", "unity_cup"}:
            last = "ura"

        scenario_confirmed = bool(general.get("scenarioConfirmed"))
        if scenario_confirmed:
            general["activeScenario"] = last
            general["scenarioConfirmed"] = True
            try:
                Settings._last_config = dict(cfg)
            except Exception:
                Settings._last_config = None
            try:
                save_config(cfg)
            except Exception as exc:
                logger_uma.debug(
                    "[HOTKEY] Skipped scenario prompt; failed to persist confirmation: %s",
                    exc,
                )
            logger_uma.debug("[HOTKEY] Scenario already confirmed: %s", last)
            return True

        try:
            choice = choose_active_scenario(last)
        except ScenarioSelectionCancelled:
            logger_uma.info("[HOTKEY] Scenario selection cancelled; start aborted.")
            return False
        choice = (choice or "ura").strip().lower()
        if choice not in {"ura", "unity_cup"}:
            choice = "ura"

        general["activeScenario"] = choice
        # Do not mark as confirmed when chosen via prompt; only Web UI sets this flag.
        general["scenarioConfirmed"] = scenario_confirmed

        try:
            Settings._last_config = dict(cfg)
        except Exception:
            Settings._last_config = None

        try:
            save_config(cfg)
        except Exception as exc:
            logger_uma.warning(
                f"[HOTKEY] Failed to persist scenario '{choice}': {exc}"
            )

        logger_uma.info(f"[HOTKEY] Scenario selected: {choice}")

        if getattr(Settings, "SHOW_PRESET_OVERLAY", False):
            try:
                label = choice.replace("_", " ").title()
                show_preset_overlay(f"Scenario: {label}")
            except Exception as exc:
                logger_uma.debug(
                    "[HOTKEY] Failed to display scenario overlay: %s", exc
                )

        return True

    def _debounced_toggle(source: str):
        nonlocal last_ts_toggle
        now = time.time()
        # Increased debounce from 0.35s to 0.8s to handle hook+poll race condition
        if now - last_ts_toggle < 0.8:
            logger_uma.debug(f"[HOTKEY] Debounced toggle from {source}.")
            return
        last_ts_toggle = now

        was_running = bot_state.running
        if not was_running:
            if not _select_scenario_before_start():
                return
            _show_preset_overlay_if_needed()

        bot_state.toggle(source=source)

        if was_running:
            _show_scenario_stopped_overlay_if_needed()

    def _debounced_career_loop(source: str):
        nonlocal last_ts_career_loop
        now = time.time()
        if now - last_ts_career_loop < 0.8:
            logger_uma.debug(f"[HOTKEY] Debounced career-loop from {source}.")
            return
        last_ts_career_loop = now

        # Check if career loop is enabled in settings
        if not bot_state.running and not Settings.CAREER_LOOP_ENABLED:
            logger_uma.warning(
                "[HOTKEY] Career Loop Mode is disabled in settings. "
                "Enable it in the Web UI (config.json: careerLoop.enabled = true) to use F1."
            )
            return

        if not bot_state.running:
            # Starting career loop mode
            if not _select_scenario_before_start():
                return
            
            logger_uma.info("[HOTKEY] Career Loop Mode: Starting...")
            _show_preset_overlay_if_needed()
            
            # Start in career loop mode (start() will set running flag internally)
            bot_state.start(career_loop_mode=True)
        else:
            # Stopping career loop mode
            logger_uma.info("[HOTKEY] Career Loop Mode: Stopping...")
            bot_state.stop()
        

    def _debounced_team(source: str):
        nonlocal last_ts_team
        now = time.time()
        if now - last_ts_team < 0.8:
            logger_uma.debug(f"[HOTKEY] Debounced team-trials from {source}.")
            return
        last_ts_team = now
        if bot_state.running:
            logger_uma.warning(
                "[AgentNav] Cannot start while Player is running. Stop the Player first (F2)."
            )
            return
        # Toggle or switch
        if nav_state.running:
            if nav_state.current_action == "team_trials":
                nav_state.stop()
            else:
                nav_state.stop()
                nav_state.start(action="team_trials")
        else:
            nav_state.start(action="team_trials")

    def _debounced_daily(source: str):
        nonlocal last_ts_daily
        now = time.time()
        if now - last_ts_daily < 0.8:
            logger_uma.debug(f"[HOTKEY] Debounced daily-races from {source}.")
            return
        last_ts_daily = now
        if bot_state.running:
            logger_uma.warning(
                "[AgentNav] Cannot start while Player is running. Stop the Player first (F2)."
            )
            return
        # Toggle or switch
        if nav_state.running:
            if nav_state.current_action == "daily_races":
                nav_state.stop()
            else:
                nav_state.stop()
                nav_state.start(action="daily_races")
        else:
            nav_state.start(action="daily_races")

    def _debounced_roulette(source: str):
        nonlocal last_ts_roulette
        now = time.time()
        if now - last_ts_roulette < 0.8:
            logger_uma.debug(f"[HOTKEY] Debounced roulette from {source}.")
            return
        last_ts_roulette = now
        if bot_state.running:
            logger_uma.warning(
                "[AgentNav] Cannot start while Player is running. Stop the Player first (F2)."
            )
            return
        if nav_state.running:
            if nav_state.current_action == "roulette":
                nav_state.stop()
            else:
                nav_state.stop()
                nav_state.start(action="roulette")
        else:
            nav_state.start(action="roulette")


    # Try to register hooks
    hotkey_mgr = get_hotkey_manager()
    
    # F1 for Career Loop Mode
    for k in keys_career_loop:
        try:
            logger_uma.debug(f"[HOTKEY] Registering hook for {k}…")
            success = hotkey_mgr.add_hotkey(
                k,
                lambda key=k: event_q.put(("career_loop", f"hook:{key}")),
                suppress=False,
                trigger_on_release=True,
            )
            if success:
                hooked_keys.add(k)
                logger_uma.info(f"[HOTKEY] Hook active for '{k}' (Career Loop Mode).")
        except PermissionError as e:
            logger_uma.warning(
                f"[HOTKEY] PermissionError registering '{k}'. On Windows you may need to run as Administrator. {e}"
            )
        except Exception as e:
            logger_uma.warning(f"[HOTKEY] Could not register '{k}': {e}")

    # Try to register hooks
    
    for k in keys_bot:
        try:
            logger_uma.debug(f"[HOTKEY] Registering hook for {k}…")
            success = hotkey_mgr.add_hotkey(
                k,
                lambda key=k: event_q.put(("toggle", f"hook:{key}")),
                suppress=False,
                trigger_on_release=True,
            )
            if success:
                hooked_keys.add(k)
                logger_uma.info(f"[HOTKEY] Hook active for '{k}'.")
        except PermissionError as e:
            logger_uma.warning(
                f"[HOTKEY] PermissionError registering '{k}'. On Windows you may need to run as Administrator. {e}"
            )
        except Exception as e:
            logger_uma.warning(f"[HOTKEY] Could not register '{k}': {e}")

    for k, fn_name in [("F7", "team"), ("F8", "daily"), ("F9", "roulette")]:
        try:
            logger_uma.debug(f"[HOTKEY] Registering hook for {k}…")
            success = hotkey_mgr.add_hotkey(
                k,
                lambda key=k, name=fn_name: event_q.put((name, f"hook:{key}")),
                suppress=False,
                trigger_on_release=True,
            )
            if success:
                hooked_keys.add(k)
                logger_uma.info(f"[HOTKEY] Hook active for '{k}'.")
        except PermissionError as e:
            logger_uma.warning(
                f"[HOTKEY] PermissionError registering '{k}'. On Windows you may need to run as Administrator. {e}"
            )
        except Exception as e:
            logger_uma.warning(f"[HOTKEY] Could not register '{k}': {e}")

    # Polling fallback (works even when hooks fail, and is the primary method on Linux)
    # With increased debounce (0.8s), both hook and poll can coexist safely
    if hooked_keys:
        logger_uma.debug(f"[HOTKEY] Hooks registered for: {hooked_keys}. Polling also active with 0.8s debounce to prevent race condition.")
    else:
        logger_uma.debug("[HOTKEY] No hooks registered. Using polling only.")
    logger_uma.debug("[HOTKEY] Polling fallback thread running…")
    try:
        while True:
            # Drain queued events from hook callbacks first
            try:
                while True:
                    ev, source = event_q.get_nowait()
                    logger_uma.debug(f"[HOTKEY] Poll detected '{ev}' from {source}.")
                    if ev == "career_loop":
                        _debounced_career_loop(source)
                    elif ev == "toggle":
                        _debounced_toggle(source)
                    elif ev == "team":
                        _debounced_team(source)
                    elif ev == "daily":
                        _debounced_daily(source)
                    elif ev == "roulette":
                        _debounced_roulette(source)
            except queue.Empty:
                pass

            fired = False
            # F1 for Career Loop Mode
            for k in keys_career_loop:
                try:
                    if hotkey_mgr.is_pressed(k):
                        logger_uma.debug(f"[HOTKEY] Poll detected '{k}'.")
                        _debounced_career_loop(f"poll:{k}")
                        fired = True
                        time.sleep(0.20)
                except Exception as e:
                    logger_uma.debug(f"[HOTKEY] Poll error on '{k}': {e}")
            for k in keys_bot:
                try:
                    if hotkey_mgr.is_pressed(k):
                        logger_uma.debug(f"[HOTKEY] Poll detected '{k}'.")
                        _debounced_toggle(f"poll:{k}")
                        fired = True
                        time.sleep(0.20)
                except Exception as e:
                    logger_uma.debug(f"[HOTKEY] Poll error on '{k}': {e}")
            # Nav keys
            for k, fn in [("F7", _debounced_team), ("F8", _debounced_daily), ("F9", _debounced_roulette)]:
                try:
                    if hotkey_mgr.is_pressed(k):
                        logger_uma.debug(f"[HOTKEY] Poll detected '{k}'.")
                        fn(f"poll:{k}")
                        fired = True
                        time.sleep(0.20)
                except Exception as e:
                    logger_uma.debug(f"[HOTKEY] Poll error on '{k}': {e}")
            if not fired:
                time.sleep(0.08)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            hotkey_mgr.stop()
            logger_uma.debug("[HOTKEY] Stopped hotkey manager.")
        except Exception:
            pass


# ---------------------------
# Main
# ---------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Umabot server")
    parser.add_argument("--port", type=int, help="Override FastAPI server port")
    args = parser.parse_args()

    ensure_nav_exists()
    # Ensure config.json exists (seed from config.sample.json if needed)
    try:
        created = ensure_config_exists()
        if created:
            logger_uma.info("[SERVER] Created config.json from config.sample.json")
    except Exception as e:
        logger_uma.warning(f"[SERVER] Could not ensure config.json exists: {e}")

    # Load once for initial logging setup (will be reloaded again on each Start)
    try:
        cfg0 = load_config()
    except Exception:
        cfg0 = {}
    Settings.apply_config(cfg0 or {})
    
    if args.port is not None:
        Settings.PORT = args.port

    # Load nav preferences
    try:
        nav_prefs = load_nav_prefs()
        Settings.apply_nav_preferences(nav_prefs)
    except Exception:
        pass
    
    setup_uma_logging(debug=Settings.DEBUG)

    try:
        cleanup_debug_training_if_needed()
    except Exception as e:
        logger_uma.warning(f"[SERVER] Could not cleanup debug training: {e}")

    # Ensure shared Tk dispatcher loop is running before any UI usage
    try:
        ensure_tk_loop()
    except Exception as exc:
        logger_uma.warning("[INIT] Failed to start Tk loop: %s", exc)

    # Launch hotkey listener and server
    state = BotState()
    nav_state = NavState()
    logger_uma.debug("[INIT] Spawning server thread…")
    srv_thread = threading.Thread(target=boot_server, daemon=True)
    srv_thread.start()

    try:
        hotkey_loop(state, nav_state)
    except KeyboardInterrupt:
        pass
    finally:
        logger_uma.debug("[SHUTDOWN] Stopping bot and joining threads…")
        state.stop()
        if state.thread:
            state.thread.join(timeout=2.0)
        if nav_state.thread:
            nav_state.thread.join(timeout=2.0)
        logger_uma.info("[SHUTDOWN] Bye.")
