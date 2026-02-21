# core/ura/gent.py
from __future__ import annotations

from time import sleep
from typing import Any, Dict, List

from core.actions.training_policy import check_training
from core.actions.skills import SkillsBuyStatus
from core.types import TrainAction
from core.actions.ura.lobby import LobbyFlowURA
from core.controllers.base import IController
from core.perception.analyzers.screen import classify_screen_ura
from core.perception.extractors.state import (
    extract_energy_pct,
    extract_goal_text,
    extract_skill_points,
    find_best,
)
from core.perception.ocr.interface import OCRInterface
from core.perception.yolo.interface import IDetector
from core.agent_scenario import AgentScenario
from core.settings import Settings
from core.utils.logger import logger_uma
from core.utils.text import fuzzy_contains
from core.utils.training_policy_utils import click_training_tile
from core.utils.waiter import PollConfig, Waiter
from core.actions.race import ConsecutiveRaceRefused
from core.utils.abort import abort_requested
from core.utils.event_processor import UserPrefs

class AgentURA(AgentScenario):
    def __init__(
        self,
        ctrl: IController,
        ocr: OCRInterface,
        yolo_engine: IDetector,
        *,
        minimum_skill_pts: int = 700,
        prioritize_g1: bool = False,
        auto_rest_minimum=26,
        plan_races: dict | None = None,
        waiter_config: PollConfig | None = None,
        skill_list=[
            "Concentration",
            "Focus",
            "Professor of Curvature",
            "Swinging Maestro",
            "Corner Recovery",
            "Corner Acceleration",
            "Straightaway Recovery",
            "Homestretch Haste",
            "Straightaway Acceleration",
        ],
        interval_stats_refresh=3,
        select_style=None,
        event_prefs: UserPrefs | None = None,
    ) -> None:
        # Shared Waiter for the whole agent
        if waiter_config is None:
            waiter_config = PollConfig(
                imgsz=Settings.YOLO_IMGSZ,
                conf=Settings.YOLO_CONF,
                iou=Settings.YOLO_IOU,
                poll_interval_s=0.5,
                timeout_s=4.0,
                tag=Settings.ACTIVE_AGENT_NAME,
                agent=Settings.ACTIVE_AGENT_NAME,
            )

        self.waiter = Waiter(ctrl, ocr, yolo_engine, waiter_config)
        super().__init__(
            ctrl=ctrl,
            ocr=ocr,
            yolo_engine=yolo_engine,
            minimum_skill_pts=minimum_skill_pts,
            prioritize_g1=prioritize_g1,
            auto_rest_minimum=auto_rest_minimum,
            plan_races=plan_races,
            waiter_config=waiter_config,
            skill_list=skill_list,
            interval_stats_refresh=interval_stats_refresh,
            select_style=select_style,
            event_prefs=event_prefs,
            lobby_flow=LobbyFlowURA(
                ctrl,
                ocr,
                yolo_engine,
                self.waiter,
                minimum_skill_pts=minimum_skill_pts,
                auto_rest_minimum=auto_rest_minimum,
                prioritize_g1=prioritize_g1,
                interval_stats_refresh=interval_stats_refresh,
                plan_races=plan_races,
            ),
        )

    def run(self, *, delay: float = 0.4, max_iterations: int | None = None) -> None:
        self.ctrl.focus()
        self.is_running = True

        # Ensure memory metadata is aligned at the start of a run
        self._refresh_skill_memory()

        while self.is_running:
            # Hard-stop hook (F2)
            if abort_requested():
                logger_uma.info(
                    "[agent] Abort requested; exiting main loop immediately."
                )
                break
            sleep(delay)
            img, _, dets = self.yolo_engine.recognize(
                imgsz=self.imgsz,
                conf=self.conf,
                iou=self.iou,
                tag="screen",
                agent=self.agent_name,
            )

            screen, _ = classify_screen_ura(
                dets,
                lobby_conf=0.5,
                require_infirmary=True,
                training_conf=0.50,
                names_map=None,
            )

            is_lobby_summer = screen == "LobbySummer"
            unknown_screen = screen.lower() == "unknown"

            self._tick_planned_skip_release()

            # Check if we need to force unknown behavior (EventStale loop breaker)
            if self._force_unknown_once:
                logger_uma.info("[event] Forcing Unknown screen behavior to break EventStale loop.")
                unknown_screen = True
                self._force_unknown_once = False
                self._consecutive_event_stale_clicks = 0

            if unknown_screen:
                # Check if race flow is waiting for manual retry decision
                if hasattr(self, 'race') and self.race._waiting_for_manual_retry_decision:
                    logger_uma.warning(
                        "[agent] Waiting for manual retry decision. Skipping all button clicks."
                    )
                    sleep(1.0)
                    continue
                # Reset event stale counters when on unknown screen
                self._single_event_option_counter = 0
                threshold = 0.65
                if self.patience > 20:
                    # try to auto recover
                    threshold = 0.55
                # Prefer green NEXT/OK/CLOSE/RACE; no busy loops scattered elsewhere
                if self.waiter.click_when(
                    classes=(
                        "button_green",
                        "race_after_next",
                        "button_white",
                    ),  # improve the model. TODO: add text exception for this function
                    texts=("NEXT", "OK", "CLOSE", "PROCEED", "CANCEL"),
                    prefer_bottom=False,
                    allow_greedy_click=False,
                    forbid_texts=("complete", "career", "RACE", "try again"),
                    timeout_s=delay,
                    tag="agent_unknown_advance",
                    threshold=threshold,
                ):
                    self.patience = 0
                else:
                    self.patience += 1

                    if self.patience > 10 == 0:
                        # try single clean click
                        screen_width = img.width
                        screen_height = img.height
                        cx = screen_width * 0.5
                        y = screen_height * 0.1

                        self.ctrl.click_xyxy_center((cx, y, cx, y), clicks=1)
                        pass
                    pat = int(delay * 100)
                    if self.patience >= pat:
                        logger_uma.warning(
                            "Stopping the algorithm just for safeness, nothing happened in 20 iterations"
                        )
                        self.is_running = False
                        break
                continue

            if screen == "EventStale":
                # Single event option detected (slow-rendering UI)
                self.claw_turn = 0
                
                # Loop detection: after 2 consecutive clicks, skip to unknown; after 4, use button_green
                if self._consecutive_event_stale_clicks == 2:
                    logger_uma.warning(
                        "[event] EventStale loop detected (2 consecutive clicks). Will force Unknown screen handler next iteration."
                    )
                    self._force_unknown_once = True
                    self._consecutive_event_stale_clicks += 1
                    continue
                elif self._consecutive_event_stale_clicks >= 4:
                    logger_uma.warning(
                        "[event] EventStale loop persists (4+ clicks). Attempting button_green fallback."
                    )
                    if self.waiter.click_when(
                        classes=("button_green",),
                        texts=("NEXT", "OK", "CLOSE", "PROCEED"),
                        prefer_bottom=True,
                        allow_greedy_click=True,
                        timeout_s=0.5,
                        tag="event_stale_fallback",
                    ):
                        logger_uma.info("[event] EventStale: button_green clicked successfully.")
                        self._consecutive_event_stale_clicks = 0
                        self._single_event_option_counter = 0
                    else:
                        logger_uma.warning("[event] EventStale: No button_green found. Resetting counters.")
                        self._consecutive_event_stale_clicks = 0
                        self._single_event_option_counter = 0
                    continue
                
                if screen == "EventStale":  # Only process if not overridden to Unknown
                    event_choices = [d for d in dets if d.get("name") == "event_choice" and float(d.get("conf", 0.0)) >= 0.60]
                    
                    if len(event_choices) == 1:
                        self._single_event_option_counter += 1
                        logger_uma.debug(
                            "[event] EventStale: Single option detected (%d/%d). Waiting for more options to render...",
                            self._single_event_option_counter,
                            self._single_event_option_threshold,
                        )
                        
                        if self._single_event_option_counter >= self._single_event_option_threshold:
                            logger_uma.info(
                                "[event] EventStale: Threshold reached (%d). Clicking the only available option. (consecutive: %d)",
                                self._single_event_option_threshold,
                                self._consecutive_event_stale_clicks,
                            )
                            choice = event_choices[0]
                            self.ctrl.click_xyxy_center(choice["xyxy"], clicks=1)
                            self._single_event_option_counter = 0
                            self._consecutive_event_stale_clicks += 1
                    else:
                        # Shouldn't happen in EventStale, but reset if it does
                        self._single_event_option_counter = 0
                    continue

            if screen == "Event":
                sleep(0.5)
                self.claw_turn = 0
                # Reset counters when we have proper event screen with multiple options
                self._single_event_option_counter = 0
                self._consecutive_event_stale_clicks = 0
                # pass what we know about current energy (may be None if not read yet)
                self.lobby.state.energy = extract_energy_pct(img, dets)
                curr_energy = (
                    self.lobby.state.energy
                    if isinstance(self.lobby.state.energy, (int, float))
                    else None
                )
                decision = self.event_flow.process_event_screen(
                    img,
                    dets,
                    current_energy=curr_energy,
                    max_energy_cap=100,
                )
                logger_uma.debug(f"[Event] {decision}")
                continue

            if screen == "Training":
                self.claw_turn = 0
                self.patience = 0
                self.waiter.click_when(
                    classes=("button_white", "race_after_next"),
                    texts=("BACK",),
                    prefer_bottom=True,
                    timeout_s=1.0,
                    tag="screen_training_directly",
                )
                continue

            if screen == "Inspiration":
                self.patience = 0
                self.claw_turn = 0
                inspiration = find_best(dets, "event_inspiration", conf_min=0.4)
                if inspiration:
                    self.ctrl.click_xyxy_center(inspiration["xyxy"], clicks=1)
                continue

            if screen == "RaceLobby":
                self.patience = 0
                self.claw_turn = 0
                logger_uma.info("[agent] RaceLobby detected; resuming race flow.")
                if not self.race.lobby():
                    logger_uma.warning("[agent] RaceLobby resume failed; retrying loop.")
                continue

            if screen == "Raceday":
                
                self.patience = 0
                self.claw_turn = 0
                self._iterations_turn += 1
                # Keep skill memory aligned with latest state
                self._refresh_skill_memory()

                # Optimization, only buy in Raceday (where you can actually lose the career)
                self.lobby.state.skill_pts = extract_skill_points(self.ocr, img, dets)
                logger_uma.info(
                    f"[agent] Skill Pts: {self.lobby.state.skill_pts}. Stats: {self.lobby.state.stats}"
                )

                # Skills optimization gate
                if (
                    len(self.skill_list) > 0
                    and self.lobby.state.skill_pts >= self._minimum_skill_pts
                ):
                    try:
                        current_turn = int(self.lobby.state.turn)
                    except Exception:
                        current_turn = -1
                    interval = int(Settings.SKILL_CHECK_INTERVAL)
                    delta_thr = int(Settings.SKILL_PTS_DELTA)
                    last_pts = (
                        self._last_skill_pts_seen
                        if self._last_skill_pts_seen is not None
                        else int(self.lobby.state.skill_pts)
                    )
                    pts_delta = max(0, int(self.lobby.state.skill_pts) - int(last_pts))
                    turn_gate = (interval <= 1) or (
                        current_turn >= 0 and (current_turn % max(1, interval) == 0)
                    )
                    delta_gate = pts_delta >= delta_thr
                    should_open_skills = (
                        turn_gate or delta_gate or self._last_skill_buy_succeeded
                    )
                    logger_uma.debug(
                        f"[skills] check interval={interval} turn={current_turn} turn_gate={turn_gate} delta={pts_delta} delta_gate={delta_gate} last_ok={self._last_skill_buy_succeeded}"
                    )
                    if should_open_skills or self._first_race_day:
                        self._first_race_day = False
                        self.lobby._go_skills()
                        skills_result = self.skills_flow.buy(self.skill_list)
                        self._last_skill_buy_succeeded = (
                            skills_result.status is SkillsBuyStatus.SUCCESS
                        )
                        self._last_skill_pts_seen = int(self.lobby.state.skill_pts)
                        self._last_skill_check_turn = (
                            current_turn
                            if current_turn >= 0
                            else self._last_skill_check_turn
                        )
                        logger_uma.info(
                            "[agent] Skills buy result: %s (exit_recovered=%s)",
                            skills_result.status.value,
                            skills_result.exit_recovered,
                        )
                        if not skills_result.exited_cleanly:
                            logger_uma.warning(
                                "[agent] Skills exit not confirmed; retrying loop before racing."
                            )
                            continue
                    else:
                        # Track last seen points even when skipping
                        self._last_skill_pts_seen = int(self.lobby.state.skill_pts)
                else:
                    # if not standar buying check if recheck buying
                    self._consume_pending_hint_recheck()
                career_date_raw = self.lobby.state.career_date_raw or ""

                race_predebut = "predebut" in career_date_raw.lower().replace("-", "")
                logger_uma.debug(f"Race day, is predebut= {race_predebut}")

                if not race_predebut and self.select_style and not career_date_raw:
                    if not self.lobby.state.goal:
                        self.lobby.state.goal = (
                            extract_goal_text(self.ocr, img, dets) or ""
                        )

                    race_predebut = fuzzy_contains(
                        self.lobby.state.goal.lower(),
                        "junior make debut",
                        threshold=0.8,
                    )
                    logger_uma.debug(
                        f"Unknown date but  select_style= {self.select_style}. checking goal: {self.lobby.state.goal.lower()}. race debut?:{race_predebut}"
                    )

                if race_predebut:
                    # Enter or confirm race, then run RaceFlow
                    # Run RaceFlow; it will ensure navigation into Raceday if needed
                    ok = self.race.run(
                        prioritize_g1=False,
                        select_style=self.select_style,
                        from_raceday=True,
                        reason="Pre-debut (race day)",
                    )
                    if not ok:
                        reason_tag = getattr(self.race, "_last_failure_reason", None)
                        logger_uma.error(
                            "[race] Couldn't race on pre-debut day (failure=%s)",
                            getattr(reason_tag, "value", str(reason_tag) or "unknown"),
                        )
                        continue
                    # Mark raced on current date-key to avoid double-race if date OCR doesn't tick
                    self.lobby.mark_raced_today(self._today_date_key())
                    continue
                else:
                    ok = self.race.run(
                        prioritize_g1=False,
                        select_style=None,
                        from_raceday=True,
                        reason="Normal (race day)",
                    )
                    if not ok:
                        reason_tag = getattr(self.race, "_last_failure_reason", None)
                        logger_uma.error(
                            "[race] Couldn't race on normal race day (failure=%s)",
                            getattr(reason_tag, "value", str(reason_tag) or "unknown"),
                        )
                        continue
                    self.lobby.mark_raced_today(self._today_date_key())
                    continue

            if screen == "Lobby" or is_lobby_summer:
                self._consume_pending_hint_recheck()
                self.patience = 0
                self.claw_turn = 0
                self._iterations_turn += 1
                outcome, reason = self.lobby.process_turn()
                # outcome = "TO_TRAINING"
                # self.lobby._go_training_screen_from_lobby(img, dets)
                # sleep(1)

                if outcome == "TO_RACE":
                    if "G1" in reason.upper():
                        logger_uma.info(reason)
                        try:
                            ok = self.race.run(
                                prioritize_g1=True,
                                is_g1_goal=True,
                                reason=self.lobby.state.goal,
                            )
                        except ConsecutiveRaceRefused:
                            logger_uma.info(
                                "[lobby] Consecutive race refused → backing out; set skip guard."
                            )
                            self.lobby._go_back()
                            self.lobby._skip_race_once = True
                            continue
                        if not ok:
                            logger_uma.error(
                                "[lobby] Couldn't race (G1 target). Backing out; set skip guard."
                            )
                            self.lobby._go_back()
                            self.lobby._skip_race_once = True
                            continue
                        self.lobby.mark_raced_today(self._today_date_key())
                    elif "PLAN" in reason.upper():
                        desired_race_name = self._desired_race_today()
                        if desired_race_name:
                            # Planned race
                            logger_uma.info(
                                "[planned_race] attempting desired='%s' key=%s skip=%s",
                                desired_race_name,
                                self._today_date_key(),
                                self.lobby._skip_race_once,
                            )
                            try:
                                ok = self.race.run(
                                    prioritize_g1=self.prioritize_g1,
                                    is_g1_goal=False,
                                    desired_race_name=desired_race_name,
                                    date_key=self._today_date_key(),
                                    reason=f"Planned race: {desired_race_name}",
                                )
                            except ConsecutiveRaceRefused:
                                logger_uma.info(
                                    "[lobby] Consecutive race refused on planned race → back & skip once."
                                )
                                self.lobby._go_back()
                                self.lobby._skip_race_once = True
                                logger_uma.info(
                                    "[planned_race] skip_guard=1 after refusal desired='%s' key=%s",
                                    desired_race_name,
                                    self._today_date_key(),
                                )
                                self._schedule_planned_skip_release()
                                continue
                            if not ok:
                                logger_uma.error(
                                    f"[race] Couldn't race {desired_race_name}"
                                )
                                self.lobby._go_back()
                                self.lobby._skip_race_once = True
                                logger_uma.info(
                                    "[planned_race] skip_guard=1 after failure desired='%s' key=%s",
                                    desired_race_name,
                                    self._today_date_key(),
                                )
                                self._schedule_planned_skip_release()
                                # TODO: smart Continue with training instead of continue
                                continue

                            # Clean planned
                            self.lobby.mark_raced_today(self._today_date_key())
                            logger_uma.info(
                                "[planned_race] completed desired='%s' key=%s",
                                desired_race_name,
                                self._today_date_key(),
                            )
                            self._clear_planned_skip_release()

                    elif "FANS" in reason.upper():
                        logger_uma.info(reason)
                        try:
                            ok = self.race.run(
                                prioritize_g1=self.prioritize_g1,
                                is_g1_goal=False,
                                reason=self.lobby.state.goal,
                            )
                        except ConsecutiveRaceRefused:
                            logger_uma.info(
                                "[lobby] Consecutive race refused → back & skip once."
                            )
                            self.lobby._go_back()
                            self.lobby._skip_race_once = True
                            continue
                        if not ok:
                            logger_uma.error(
                                "[lobby] Couldn't race (fans target). Backing out; set skip guard."
                            )
                            self.lobby._go_back()
                            self.lobby._skip_race_once = True
                            continue
                        self.lobby.mark_raced_today(self._today_date_key())

                if outcome == "TO_TRAINING":
                    logger_uma.info(
                        f"[lobby] goal='{self.lobby.state.goal}' | energy={self.lobby.state.energy} | "
                        f"skill_pts={self.lobby.state.skill_pts} | turn={self.lobby.state.turn} | "
                        f"summer={self.lobby.state.is_summer} | mood={self.lobby.state.mood} | stats={self.lobby.state.stats} |"
                        f"infirmary={self.lobby.state.infirmary_on}"
                    )
                    # sleep(1.0)
                    self.handle_training()
                    continue

                if outcome == "TRAINING_READY":
                    logger_uma.info(
                        f"[lobby] Pre-check tile already clicked, waiting for confirm | reason={reason}"
                    )
                    # Tile already clicked by pre-check, just wait for the normal flow
                    # The agent will detect TrainingConfirm screen and handle it
                    sleep(1.5)  # Give time for UI to settle
                    continue

                # For other outcomes ("INFIRMARY", "RESTED", "CONTINUE") we just loop
                continue

            if screen == "FinalScreen" or screen == "CareerComplete":
                self.claw_turn = 0
                # Only if skill list defined
                if len(self.skill_list) > 0 and self.lobby._go_skills():
                    sleep(1.0)
                    final_result = self.skills_flow.buy(self.skill_list)
                    self._last_skill_buy_succeeded = (
                        final_result.status is SkillsBuyStatus.SUCCESS
                    )
                    logger_uma.info(
                        "[agent] Final screen skills result: %s (exit_recovered=%s)",
                        final_result.status.value,
                        final_result.exit_recovered,
                    )

                self.is_running = False  # end of career
                logger_uma.info("Detected end of career")
                
                try:
                    self.skill_memory.reset(persist=True)
                    logger_uma.info("[skill_memory] Reset after career completion")
                except Exception as exc:
                    logger_uma.error("[skill_memory] reset failed: %s", exc)
                continue

            if screen == "ClawMachine":
                self.claw_turn += 1
                logger_uma.debug(
                    f"Claw Machine detected... starting to play. Claw turn: {self.claw_turn}"
                )
                if self.claw_game.play_once(tag_prefix="claw", try_idx=self.claw_turn):
                    logger_uma.debug("Claw Machine triggered sucessfully")
                else:
                    logger_uma.error("Couldn't trigger Claw Machine")
                sleep(3)
                continue

    # --------------------------
    # Training handling (acts on decisions from policy)
    # --------------------------
    def handle_training(self) -> None:
        """
        Act on the training decision:
         - If a tile action: click the tile.
         - If REST/RECREATION/RACE: go back to lobby and execute via LobbyFlow/RaceFlow.
         - If race fails as a training action, re-run the decision once with skip_race=True.
        """
        if not self.is_running:
            return
        # Initial decision (no skip)
        decision = check_training(self, skip_race=self._skip_training_race_once)
        if decision is None:
            return

        if not self.is_running:
            return
        self._skip_training_race_once = False
        action = decision.action
        tidx = decision.tile_idx
        training_state = decision.training_state

        tile_actions_train = {
            TrainAction.TRAIN_MAX.value,
            TrainAction.TRAIN_WIT.value,
            TrainAction.TRAIN_DIRECTOR.value,
            TrainAction.TAKE_HINT.value,
        }
        # Tile actions within the training screen
        if action.value in tile_actions_train and tidx is not None:
            ok = click_training_tile(self.ctrl, training_state, tidx, pause_after=5)
            if not ok:
                logger_uma.error(
                    "[training] Failed to click training tile idx=%s", tidx
                )
                return
            # Optional slow-path: after landing on a hint tile, defer re-check until back in lobby
            supports_for_recheck: List[Dict[str, Any]] = []
            try:
                tile = None
                tile_supports = []
                for t in training_state:
                    if int(t.get("tile_idx", -1)) == int(tidx):
                        tile = t
                        break
                if tile:
                    tile_supports = list(tile.get("supports") or [])
                for s in tile_supports:
                    if not s or not bool(s.get("has_hint", False)):
                        continue
                    pcfg = s.get("priority_config") or {}
                    if isinstance(pcfg, dict) and bool(pcfg.get("recheckAfterHint", False)):
                        matched = s.get("matched_card") or {}
                        name = (
                            pcfg.get("displayName")
                            or matched.get("name")
                            or s.get("name")
                            or "support"
                        )
                        support_key = None
                        if isinstance(matched, dict):
                            nm = matched.get("name")
                            rarity = matched.get("rarity")
                            attr = matched.get("attribute")
                            if isinstance(nm, str) and nm and isinstance(rarity, str) and isinstance(attr, str):
                                support_key = (nm, rarity, attr)
                        supports_for_recheck.append(
                            {
                                "label": str(name),
                                "key": support_key,
                            }
                        )
                if supports_for_recheck:
                    self._pending_hint_recheck = True
                    self._pending_hint_supports = supports_for_recheck
                    labels = [str(entry.get("label", "support")) for entry in supports_for_recheck]
                    logger_uma.info(
                        "[post-hint] Deferred re-check scheduled for: %s",
                        ", ".join(labels),
                    )
            except Exception as e:
                logger_uma.error("[post-hint] Failed scheduling deferred re-check: %s", e)
            return
        action_is_in_last_screen = action.value in (
            TrainAction.REST.value,
            TrainAction.RECREATION.value,
            TrainAction.RACE.value,
        )

        # Actions that require going back to the lobby
        if action_is_in_last_screen:
            # Return to lobby from training
            if not self.lobby._go_back():
                raise RuntimeError("Couldn't return to previous screen from training")

            if action.value == TrainAction.REST.value:
                if not self.lobby._go_rest(reason="Resting..."):
                    logger_uma.error("[training] ERROR when trying to rest")
                return

            if action.value == TrainAction.RECREATION.value:
                if not self.lobby._go_recreate(reason="Recreating..."):
                    logger_uma.error("[training] ERROR when trying to recreate")
                return

            if action.value == TrainAction.RACE.value:
                # Try to race from lobby (RaceFlow will navigate into Raceday)
                try:
                    if self.race.run(
                        prioritize_g1=self.prioritize_g1,
                        reason="Training policy → race",
                    ):
                        return
                except ConsecutiveRaceRefused:
                    logger_uma.info(
                        "[training] Consecutive race refused → back to training and skip once."
                    )
                    self.lobby._go_back()
                    self.lobby._skip_race_once = True
                    self._skip_training_race_once = True
                    if self.lobby._go_training_screen_from_lobby(None, None):
                        decision2 = check_training(self, skip_race=True)
                        if (
                            decision2
                            and decision2.action.value in tile_actions_train
                            and decision2.tile_idx is not None
                        ):
                            click_training_tile(
                                self.ctrl, decision2.training_state, decision2.tile_idx
                            )
                    return

                # Race failed → go back, revisit training once with skip_race=True
                logger_uma.warning(
                    "[training] Couldn't race from training policy; retrying decision without racing (Also, suitable G1 probably wasn't found)."
                )
                self.lobby._go_back()
                self.lobby._skip_race_once = True
                self._skip_training_race_once = True

                # Navigate back to training screen explicitly, then decide again (skip_race)
                if self.lobby._go_training_screen_from_lobby(None, None):
                    # sleep(1.2)
                    decision2 = check_training(self, skip_race=True)
                    if decision2 is None:
                        return
                    # If the second decision is a tile action, click it
                    if (
                        decision2.action.value in tile_actions_train
                        and decision2.tile_idx is not None
                    ):
                        click_training_tile(
                            self.ctrl, decision2.training_state, decision2.tile_idx
                        )
                    else:
                        logger_uma.info(
                            "[training] Second decision after failed race: %s",
                            decision2.action.value,
                        )
                return

        # Fallback: nothing to do
        logger_uma.debug("[training] No actionable decision.")
