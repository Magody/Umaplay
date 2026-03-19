# core/agent_nav.py
from __future__ import annotations

import threading
from collections import Counter
from time import sleep
from typing import Dict, List, Tuple

from PIL import Image

from core.actions.daily_race import DailyRaceFlow
from core.actions.roulette import RouletteFlow
from core.actions.team_trials import TeamTrialsFlow
from core.controllers.base import IController
from core.perception.ocr.interface import OCRInterface
from core.perception.yolo.interface import IDetector
from core.settings import Settings
from core.types import DetectionDict
from core.utils import nav
from core.utils.logger import logger_uma
from core.utils.text import fuzzy_contains
from core.utils.waiter import PollConfig, Waiter


ScreenInfo = Dict[str, object]
ScreenName = str


class AgentNav:
    """
    YOLO-driven navigator for the new menus.
    Delegates domain-specific logic to actions.* flows and reuses utils.nav helpers.
    """

    def __init__(
        self,
        ctrl: IController,
        ocr: OCRInterface,
        yolo_engine: IDetector,
        action: str,
        waiter_config: PollConfig = PollConfig(
            imgsz=Settings.YOLO_IMGSZ,
            conf=Settings.YOLO_CONF,
            iou=Settings.YOLO_IOU,
            poll_interval_s=0.5,
            timeout_s=4.0,
            tag=Settings.AGENT_NAME_NAV,
            agent=Settings.AGENT_NAME_NAV,
        ),
    ) -> None:
        self.ctrl = ctrl
        self.ocr = ocr
        self.yolo_engine = yolo_engine
        self.waiter = Waiter(ctrl, ocr, yolo_engine, waiter_config)
        self.action = action
        self.agent_name = waiter_config.agent
        self._stop_event = threading.Event()
        self._thr = {
            "race_team_trials": 0.50,
            "race_daily_races": 0.50,
            "banner_opponent": 0.50,
            "race_daily_races_monies_row": 0.70,
            "race_team_trials_go": 0.45,
            "button_pink": 0.35,
            "button_advance": 0.35,
            "shop_clock": 0.35,
            "shop_exchange": 0.35,
            "button_back": 0.35,
            "button_green": 0.35,
            "button_white": 0.35,
            "roulette_button": 0.60,
        }

        # flows
        self.team_trials = TeamTrialsFlow(ctrl, ocr, yolo_engine, self.waiter)
        self.daily_race = DailyRaceFlow(ctrl, ocr, yolo_engine, self.waiter)
        self.roulette = RouletteFlow(
            ctrl,
            ocr,
            yolo_engine,
            self.waiter,
            stop_event=self._stop_event,
        )

    # --------------------------
    # Screen classification
    # --------------------------

    def _button_text_seen(
        self,
        img: Image.Image,
        dets: List[DetectionDict],
        *,
        cls_name: str,
        target_text: str,
        conf_min: float = 0.0,
        threshold: float = 0.58,
    ) -> bool:
        if self.ocr is None:
            return False
        for det in dets:
            if det.get("name") != cls_name or float(det.get("conf", 0.0)) < conf_min:
                continue
            try:
                x1, y1, x2, y2 = det["xyxy"]
                crop = img.crop((x1, y1, x2, y2))
                txt = (self.ocr.text(crop) or "").strip()
                if txt and fuzzy_contains(txt, target_text, threshold=threshold):
                    return True
            except Exception:
                continue
        return False

    def classify_nav_screen(
        self, img: Image.Image, dets: List[DetectionDict]
    ) -> Tuple[ScreenName, ScreenInfo]:
        counts = Counter(d["name"] for d in dets)

        action = (self.action or "").lower()

        if action == "team_trials":
            if nav.has(
                dets, "race_team_trials", conf_min=self._thr["race_team_trials"]
            ):
                return "RaceScreen", {"counts": dict(counts)}

            if nav.has(dets, "banner_opponent", conf_min=self._thr["banner_opponent"]):
                return "TeamTrialsBanners", {"counts": dict(counts)}

            if self._button_text_seen(
                img,
                dets,
                cls_name="button_green",
                target_text="RESTORE",
                conf_min=self._thr["button_green"],
            ):
                return "TeamTrialsFinished", {"counts": dict(counts)}

            if nav.has(dets, "button_pink", conf_min=self._thr["button_pink"]) and (
                nav.has(dets, "button_green", conf_min=self._thr["button_green"])
                or nav.has(dets, "button_advance", conf_min=self._thr["button_advance"])
            ):
                return "TeamTrialsRaceAgain", {"counts": dict(counts)}

            if self._button_text_seen(
                img,
                dets,
                cls_name="button_green",
                target_text="SHOP",
                conf_min=self._thr["button_green"],
            ) and nav.has(dets, "button_white", conf_min=self._thr["button_white"]):
                return "TeamTrialsSalePrompt", {"counts": dict(counts)}

            if self._button_text_seen(
                img,
                dets,
                cls_name="button_green",
                target_text="NEXT",
                conf_min=self._thr["button_green"],
            ):
                return "TeamTrialsContinue", {"counts": dict(counts)}

            if nav.has(
                dets, "race_team_trials_go", conf_min=self._thr["race_team_trials_go"]
            ):
                return "TeamTrialsGo", {"counts": dict(counts)}

            if nav.has(dets, "shop_clock", conf_min=self._thr["shop_clock"]) or nav.has(
                dets, "shop_exchange", conf_min=self._thr["shop_exchange"]
            ):
                return "TeamTrialsShop", {"counts": dict(counts)}

            if nav.has(
                dets, "button_advance", conf_min=self._thr["button_advance"]
            ) and nav.has(
                dets, "button_white", conf_min=self._thr["button_back"]
            ):
                return "TeamTrialsResults", {"counts": dict(counts)}

            if nav.has(dets, "button_white", conf_min=self._thr["button_back"]) and not nav.has(dets, "button_pink", conf_min=self._thr["button_pink"]) and not nav.has(dets, "button_advance", conf_min=self._thr["button_advance"]) and not nav.has(dets, "button_green", conf_min=self._thr["button_green"]):
                return "TeamTrialsStale", {"counts": dict(counts)}

        elif action == "daily_races":
            if nav.has(
                dets, "race_daily_races", conf_min=self._thr["race_daily_races"]
            ):
                return "RaceScreen", {"counts": dict(counts)}

            if nav.has(
                dets,
                "race_daily_races_monies_row",
                conf_min=self._thr["race_daily_races_monies_row"],
            ):
                return "RaceDailyRows", {"counts": dict(counts)}

            if nav.has(dets, "button_white", conf_min=self._thr["button_back"]) and nav.has(
                dets, "button_green", conf_min=self._thr["button_green"]
            ):
                return "DailyRaceResume", {"counts": dict(counts)}

        elif action == "roulette":
            if nav.has(dets, "roulette_button", conf_min=self._thr["roulette_button"]):
                return "Roulette", {"counts": dict(counts)}

            return "RouletteUnknown", {"counts": dict(counts)}
        else:
            if nav.has(
                dets, "race_team_trials", conf_min=self._thr["race_team_trials"]
            ) or nav.has(
                dets, "race_daily_races", conf_min=self._thr["race_daily_races"]
            ):
                return "RaceScreen", {"counts": dict(counts)}

            if nav.has(dets, "banner_opponent", conf_min=self._thr["banner_opponent"]):
                return "TeamTrialsBanners", {"counts": dict(counts)}

            if self._button_text_seen(
                img,
                dets,
                cls_name="button_green",
                target_text="RESTORE",
                conf_min=self._thr["button_green"],
            ):
                return "TeamTrialsFinished", {"counts": dict(counts)}

            if nav.has(dets, "button_pink", conf_min=self._thr["button_pink"]) and (
                nav.has(dets, "button_green", conf_min=self._thr["button_green"])
                or nav.has(dets, "button_advance", conf_min=self._thr["button_advance"])
            ):
                return "TeamTrialsRaceAgain", {"counts": dict(counts)}

            if self._button_text_seen(
                img,
                dets,
                cls_name="button_green",
                target_text="SHOP",
                conf_min=self._thr["button_green"],
            ) and nav.has(dets, "button_white", conf_min=self._thr["button_white"]):
                return "TeamTrialsSalePrompt", {"counts": dict(counts)}

            if self._button_text_seen(
                img,
                dets,
                cls_name="button_green",
                target_text="NEXT",
                conf_min=self._thr["button_green"],
            ):
                return "TeamTrialsContinue", {"counts": dict(counts)}

            if nav.has(
                dets,
                "race_daily_races_monies_row",
                conf_min=self._thr["race_daily_races_monies_row"],
            ):
                return "RaceDailyRows", {"counts": dict(counts)}

            if nav.has(
                dets, "race_team_trials_go", conf_min=self._thr["race_team_trials_go"]
            ):
                return "TeamTrialsGo", {"counts": dict(counts)}

            if nav.has(dets, "shop_clock", conf_min=self._thr["shop_clock"]) or nav.has(
                dets, "shop_exchange", conf_min=self._thr["shop_exchange"]
            ):
                return "TeamTrialsShop", {"counts": dict(counts)}

            if nav.has(dets, "button_pink", conf_min=self._thr["button_pink"]) and nav.has(
                dets, "button_advance", conf_min=self._thr["button_advance"]
            ) and nav.has(
                dets, "button_white", conf_min=self._thr["button_back"]
            ):
                return "TeamTrialsResults", {"counts": dict(counts)}

            if (
                sum(1 for d in dets if d["name"] == "button_white") <= 1 and
                nav.has(dets, "button_white", conf_min=self._thr["button_back"]) and not nav.has(dets, "button_pink", conf_min=self._thr["button_pink"]) and not nav.has(dets, "button_advance", conf_min=self._thr["button_advance"]) and not nav.has(dets, "button_green", conf_min=self._thr["button_green"])
            ):
                return "TeamTrialsStale", {"counts": dict(counts)}

            if nav.has(dets, "button_white", conf_min=self._thr["button_back"]) and nav.has(
                dets, "button_green", conf_min=self._thr["button_green"]
            ):
                return "DailyRaceResume", {"counts": dict(counts)}

        if nav.has(dets, "roulette_button", conf_min=self._thr["roulette_button"]):
            return "Roulette", {"counts": dict(counts)}

        return "UnknownNav", {"counts": dict(counts)}

    # --------------------------
    # Main
    # --------------------------

    def run(self) -> Tuple[ScreenName, ScreenInfo]:
        self.ctrl.focus()
        try:
            self._stop_event.clear()
        except Exception:
            pass
        self.is_running = True
        last_screen: ScreenName = "UnknownNav"
        last_info: ScreenInfo = {}

        counter = 60
        patience = 3  # for any use case
        while not self._stop_event.is_set() and counter > 0 and patience > 0:
            img, dets = nav.collect_snapshot(
                self.waiter, self.yolo_engine, agent=self.agent_name, tag="screen_detector"
            )
            screen, info = self.classify_nav_screen(img, dets)
            logger_uma.debug(f"[AgentNav] screen={screen} | info={info}")

            if screen == "RaceScreen":
                if self.action == "daily_races":
                    if self.daily_race.enter_from_menu():
                        sleep(1.0)
                elif self.action == "team_trials":
                    if self.team_trials.enter_from_menu():
                        sleep(1.0)

            elif screen == "RaceDailyRows":
                if self.daily_race.pick_first_row():
                    sleep(1.0)
                    if self.daily_race.confirm_and_next_to_race():
                        sleep(1.0)
                        finalized = self.daily_race.run_race_and_collect()

                        if finalized:
                            self.is_running = False
                            counter = 0
                    else:
                        logger_uma.info("[AgentNav] DailyRace confirm_and_next_to_race HARD stopped for safety.")
                        self.is_running = False
                        counter = 0
                        break

            elif self.action == "team_trials" and screen == "TeamTrialsBanners":
                logger_uma.info("[AgentNav] TeamTrials banners detected")
                self.team_trials.process_banners_screen()
            elif self.action == "team_trials" and screen in {
                "TeamTrialsGo",
                "TeamTrialsShop",
                "TeamTrialsResults",
                "TeamTrialsStale",
            }:
                logger_uma.info(
                    f"[AgentNav] TeamTrials recovery state detected: {screen}"
                )
                self.team_trials.resume()

            elif self.action == "team_trials" and screen == "TeamTrialsContinue":
                logger_uma.info("[AgentNav] TeamTrials continue screen detected")
                self.team_trials.handle_continue_screen()

            elif self.action == "team_trials" and screen == "TeamTrialsRaceAgain":
                logger_uma.info("[AgentNav] TeamTrials race-again screen detected")
                self.team_trials.handle_race_again_screen()

            elif self.action == "team_trials" and screen == "TeamTrialsSalePrompt":
                logger_uma.info("[AgentNav] TeamTrials sale prompt detected")
                self.team_trials.handle_sale_prompt()

            elif self.action == "team_trials" and screen == "TeamTrialsFinished":
                logger_uma.info("[AgentNav] TeamTrials finished detected")
                self.team_trials.handle_finished_prompt()
                self.is_running = False
                counter = 0

            elif screen == "DailyRaceResume":
                logger_uma.info("[AgentNav] DailyRace resume detected")
                finalized = self.daily_race.run_race_and_collect()

                if finalized:
                    self.is_running = False
                    counter = 0

            elif self.action == "roulette":
                if self._stop_event.is_set():
                    break

                if screen == "Roulette":
                    result = self.roulette.run_cycle(tag_prefix="agent_nav_roulette")
                    if self._stop_event.is_set():
                        break
                    if result.get("spun"):
                        counter = min(counter + 3, 90)
                        patience = 3
                    else:
                        patience -= 1  # safe stop
                        sleep(0.5)
                elif screen == "RouletteUnknown":
                    clicked = self.waiter.click_when(
                        classes=("button_white",),
                        texts=("CLOSE",),
                        allow_greedy_click=False,
                        prefer_bottom=False,
                        forbid_texts=("BACK",),
                        tag="agent_nav_roulette_close_seen",
                    )
                    if self._stop_event.is_set():
                        break
                    if clicked:
                        logger_uma.info("[AgentNav] Roulette close detected")
                        sleep(0.6)
                    else:
                        patience -= 1

            else:
                counter -= 1

            last_screen, last_info = screen, info
            for _ in range(20):
                if self._stop_event.is_set():
                    break
                sleep(0.1)

        self.is_running = False
        return last_screen, last_info

    def stop(self) -> None:
        """Signal the run loop to stop on the next iteration."""
        try:
            logger_uma.info("[AgentNav] Stop signal received.")
        except Exception:
            pass
        self.is_running = False
        self._stop_event.set()
