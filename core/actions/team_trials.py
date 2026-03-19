# core/actions/team_trials.py
from __future__ import annotations

import random
import time
from time import sleep
from typing import Tuple, List
from enum import Enum

from core.controllers.base import IController
from core.controllers.android import ScrcpyController

try:
    from core.controllers.bluestacks import BlueStacksController
except Exception:
    BlueStacksController = None  # type: ignore
from core.perception.ocr.interface import OCRInterface
from core.perception.yolo.interface import IDetector
from core.types import DetectionDict
from core.utils.abort import abort_requested
from core.utils.logger import logger_uma
from core.utils.waiter import Waiter
from core.utils import nav
from core.settings import Settings

class TeamTrialsState(Enum):
    UNKNOWN = "unknown"
    HOME = "home"
    GO = "go"
    BANNERS = "banners"
    RESULTS = "results"
    SHOP = "shop"
    STALE = "stale"
    LOOP_CHOICES = "loop_choices"


class TeamTrialsFlow:
    """
    Encapsulates Team Trials navigation, banners, race start, advances, and optional shop.
    """

    def __init__(
        self,
        ctrl: IController,
        ocr: OCRInterface,
        yolo_engine: IDetector,
        waiter: Waiter,
    ) -> None:
        self.ctrl = ctrl
        self.ocr = ocr
        self.yolo_engine = yolo_engine
        self.waiter = waiter
        self._thr = {
            "banner_opponent": 0.50,
            "race_team_trials": 0.45,
            "race_team_trials_go": 0.45,
            "shop_clock": 0.35,
            "shop_exchange": 0.35,
            "button_pink": 0.35,
            "button_advance": 0.35,
            "button_white": 0.35,
            "button_back": 0.35,
            "button_green": 0.35,
        }

    # ---------- High-level entry points ----------

    def enter_from_menu(self) -> bool:
        """Click main menu → Team Trials → 'GO'."""
        ok_menu = self.waiter.click_when(
            classes=("race_team_trials",),
            prefer_bottom=False,
            timeout_s=2.0,
            tag="team_trials_menu",
        )
        if not ok_menu:
            return False

        sleep(1.5)
        ok_go = self.waiter.click_when(
            classes=("race_team_trials_go",),
            prefer_bottom=False,
            timeout_s=4.5,
            tag="team_trials_go",
        )
        if ok_go:
            logger_uma.info("[TeamTrials] Entered 'Team Trials' → GO")
        return ok_go

    def process_banners_screen(self) -> None:
        """
        Handle the 'opponent banners' screen:
          - click a banner (bottom-most preferred)
          - pre-start button-green loops
          - start race (RACE!)
          - advance through post-race, handle shop if it appears, then try 'RACE AGAIN'
        """
        if not nav.cooperative_sleep(1.0):
            return
        img, dets = nav.collect_snapshot(
            self.waiter, self.yolo_engine, tag="team_trials_banners"
        )
        banners = [d for d in dets if d["name"] == "banner_opponent"]
        if not banners:
            logger_uma.warning("[TeamTrials] No opponent banners detected")
            return

        banners.sort(
            key=lambda d: (
                -float(d.get("conf", 0.0)),
                -float(d["xyxy"][3]),
            )
        )
        if len(banners) > 3:
            banners = banners[:3]

        banners.sort(key=lambda d: d["xyxy"][1])
        preferred_index = Settings.get_team_trials_banner_pref() - 1
        preferred_index = max(0, min(len(banners) - 1, preferred_index))
        target_banner = banners[preferred_index]

        if target_banner is None:
            logger_uma.warning("[TeamTrials] Preferred banner index out of range")
            return

        self.ctrl.click_xyxy_center(target_banner["xyxy"], clicks=random.randint(3, 4))
        logger_uma.info(
            f"[TeamTrials] Clicked opponent banner (slot={preferred_index + 1})"
        )
        nav.wait_until_seen(
            self.waiter,
            classes=("button_green", "button_white"),
            tag="team_trials_after_banner_ready",
            timeout_s=9.0,
        )

        # Pre-start: a few green clicks (progression prompts)
        pre = nav.click_button_loop(
            self.waiter,
            classes=("button_green",),
            tag_prefix="team_trials_prestart",
            max_clicks=1,
            sleep_between_s=0.30,
            prefer_bottom=True,
            timeout_s=4.0,
        )
        if pre == 0:
            pre_2 = nav.click_button_loop(
                self.waiter,
                classes=("button_green",),
                tag_prefix="team_trials_prestart",
                max_clicks=1,
                sleep_between_s=0.30,
                prefer_bottom=True,
                timeout_s=4.0,
            )
            if pre_2 == 0:
                logger_uma.warning("[TeamTrials] Could not press Start button of race in second intent")
                return
        logger_uma.debug(f"[TeamTrials] pre-start greens: {pre}")
        if not nav.cooperative_sleep(1.8):
            return
        # Try to hit 'RACE!' (avoid CANCEL)
        started = self.waiter.click_when(
            classes=("button_green",),
            texts=("RACE!",),
            prefer_bottom=True,
            allow_greedy_click=False,
            forbid_texts=("CANCEL",),
            clicks=random.randint(1, 2),
            timeout_s=2.0,
            tag="team_trials_race_start",
        )
        if not started:
            logger_uma.warning("[TeamTrials] Couldn't press 'RACE!'")
        if not nav.cooperative_sleep(1.0):
            return
        self._handle_post_race_sequence(ensure_enter_shop=True)

    def resume(self, *, max_steps: int = 8) -> bool:
        """
        Attempt to recover Team Trials flow regardless of the current screen.
        Returns True if any step was handled during the recovery loop.
        """
        handled_any = False
        for _ in range(max_steps):
            if abort_requested():
                break
            if not nav.cooperative_sleep(0.8):
                break
            img, dets = nav.collect_snapshot(
                self.waiter, self.yolo_engine, tag="team_trials_resume"
            )
            state = self._classify_state(dets)
            logger_uma.debug(f"[TeamTrials] resume detected state={state.value}")
            if state is TeamTrialsState.UNKNOWN:
                break

            handled_any = True

            if state is TeamTrialsState.HOME:
                self.enter_from_menu()
            elif state is TeamTrialsState.GO:
                self._handle_go_screen()
            elif state is TeamTrialsState.BANNERS:
                self.process_banners_screen()
            elif state is TeamTrialsState.RESULTS:
                self._handle_results_screen()
            elif state is TeamTrialsState.SHOP:
                self._handle_shop_in_place()
            elif state is TeamTrialsState.STALE:
                self._handle_stale_screen()
            elif state is TeamTrialsState.LOOP_CHOICES:
                self.handle_race_again_screen()

            if abort_requested():
                break
            if not nav.cooperative_sleep(1.0):
                break

        return handled_any

    def _classify_state(self, dets: List[DetectionDict]) -> TeamTrialsState:
        if nav.has(dets, "shop_clock", conf_min=self._thr["shop_clock"]) or nav.has(
            dets, "shop_exchange", conf_min=self._thr["shop_exchange"]
        ):
            return TeamTrialsState.SHOP

        if nav.has(
            dets, "banner_opponent", conf_min=self._thr["banner_opponent"]
        ):
            return TeamTrialsState.BANNERS

        if nav.has(
            dets, "race_team_trials_go", conf_min=self._thr["race_team_trials_go"]
        ):
            return TeamTrialsState.GO

        if nav.has(dets, "race_team_trials", conf_min=self._thr["race_team_trials"]):
            return TeamTrialsState.HOME

        if nav.has(dets, "button_pink", conf_min=self._thr["button_pink"]) and nav.has(
            dets, "button_green", conf_min=self._thr["button_green"]
        ):
            return TeamTrialsState.LOOP_CHOICES

        if nav.has(dets, "button_pink", conf_min=self._thr["button_pink"]) or nav.has(
            dets, "button_advance", conf_min=self._thr["button_advance"]
        ):
            return TeamTrialsState.RESULTS

        if nav.has(dets, "button_white", conf_min=self._thr["button_white"]) and not nav.has(dets, "button_pink", conf_min=self._thr["button_pink"]) and not nav.has(dets, "button_advance", conf_min=self._thr["button_advance"]):
            return TeamTrialsState.STALE

        return TeamTrialsState.UNKNOWN

    def _handle_stale_screen(self) -> None:
        logger_uma.info("[TeamTrials] Stale screen detected; clicking 'Back'.")
        self.waiter.click_when(
            classes=("button_white",),
            texts=("BACK",),
            prefer_bottom=True,
            timeout_s=2.0,
            tag="team_trials_stale_back",
        )

    def _handle_go_screen(self) -> None:
        logger_uma.info("[TeamTrials] GO button detected; attempting to enter.")
        self.waiter.click_when(
            classes=("race_team_trials_go",),
            prefer_bottom=False,
            timeout_s=2.0,
            tag="team_trials_go_screen",
        )

    def _handle_results_screen(self) -> None:
        logger_uma.info("[TeamTrials] Results screen detected; advancing flow.")
        self._handle_post_race_sequence(ensure_enter_shop=True)

    def _handle_shop_in_place(self) -> None:
        logger_uma.info("[TeamTrials] Shop screen detected; processing exchange.")
        if nav.handle_shop_exchange(
            self.waiter,
            self.yolo_engine,
            self.ctrl,
            tag_prefix="team_trials_shop_resume",
            ensure_enter=False,
            should_stop=abort_requested,
        ):
            logger_uma.info("[TeamTrials] Completed shop exchange flow")
        else:
            logger_uma.warning("[TeamTrials] Unable to process shop exchange")

    def handle_continue_screen(self) -> bool:
        logger_uma.info("[TeamTrials] Continue screen detected; clicking 'Next'.")
        clicked = self.waiter.click_when(
            classes=("button_green",),
            texts=("NEXT",),
            prefer_bottom=True,
            allow_greedy_click=False,
            timeout_s=2.0,
            tag="team_trials_continue_next",
        )
        if not clicked:
            clicked = self.waiter.click_when(
                classes=("button_green",),
                prefer_bottom=True,
                allow_greedy_click=True,
                timeout_s=1.0,
                tag="team_trials_continue_next_fallback",
            )
        if not clicked:
            logger_uma.warning("[TeamTrials] Continue screen 'Next' not found")
            return False
        nav.wait_until_seen(
            self.waiter,
            classes=("button_pink", "button_green", "shop_exchange", "shop_clock", "race_team_trials_go"),
            tag="team_trials_continue_followup",
            timeout_s=4.0,
        )
        return True

    def handle_sale_prompt(self) -> bool:
        logger_uma.info("[TeamTrials] Daily sale prompt detected; entering shop.")
        clicked = self.waiter.click_when(
            classes=("button_green",),
            texts=("SHOP",),
            prefer_bottom=True,
            allow_greedy_click=False,
            timeout_s=2.0,
            tag="team_trials_sale_shop",
        )
        if not clicked:
            clicked = self.waiter.click_when(
                classes=("button_green",),
                prefer_bottom=True,
                allow_greedy_click=True,
                timeout_s=1.0,
                tag="team_trials_sale_shop_fallback",
            )
        if not clicked:
            logger_uma.warning("[TeamTrials] Could not press 'Shop' on Daily Sale prompt")
            return False
        nav.wait_until_seen(
            self.waiter,
            classes=("shop_exchange", "shop_clock", "button_white", "button_green"),
            tag="team_trials_sale_after_shop",
            timeout_s=4.0,
        )
        return True

    def handle_race_again_screen(self) -> bool:
        logger_uma.info("[TeamTrials] Race Again screen detected; clicking 'Race Again'.")
        clicked = self.waiter.click_when(
            classes=("button_pink",),
            texts=("RACE AGAIN",),
            prefer_bottom=True,
            allow_greedy_click=False,
            timeout_s=2.0,
            tag="team_trials_loop_race_again",
        )
        if not clicked:
            clicked = self.waiter.click_when(
                classes=("button_pink",),
                prefer_bottom=True,
                allow_greedy_click=True,
                timeout_s=1.0,
                tag="team_trials_loop_race_again_fallback",
            )
        if not clicked:
            logger_uma.warning("[TeamTrials] Race Again button not found on loop screen")
            return False
        nav.wait_until_seen(
            self.waiter,
            classes=("race_team_trials_go", "banner_opponent", "button_green", "button_white"),
            tag="team_trials_loop_followup",
            timeout_s=5.0,
        )
        return True

    def _handle_post_race_sequence(self, *, ensure_enter_shop: bool) -> None:
        nav.wait_until_seen(
            self.waiter,
            classes=("button_advance", "button_skip", "button_green", "button_white"),
            tag="team_trials_post_race_ready",
            timeout_s=10.0,
        )
        if abort_requested():
            return
        adv = nav.advance_sequence_with_mid_taps(
            self.waiter,
            self.yolo_engine,
            self.ctrl,
            tag_prefix="team_trials_adv",
            iterations_max=1,
            advance_class="button_advance",
            advance_texts=None,
            taps_each_click=(3, 4),
            tap_dev_frac=0.1,
            sleep_after_advance=1,
        )
        logger_uma.debug(f"[TeamTrials] advances performed: {adv}")
        skip_clicks = 0
        t0 = time.time()
        while (time.time() - t0) < 5.0 and skip_clicks < 1 and not abort_requested():
            if self.waiter.click_when(
                classes=("button_skip",),
                prefer_bottom=True,
                timeout_s=2.0,
                clicks=random.randint(3, 5),
                tag="team_trials_skip",
            ):
                skip_clicks += 1
            if not nav.cooperative_sleep(0.12):
                return

        if not nav.cooperative_sleep(2.0):
            return

        if skip_clicks > 0:
            logger_uma.debug(f"[TeamTrials] Completed skip sequence (clicks={skip_clicks})")
            if self.waiter.click_when(
                classes=("button_green",),
                texts=("NEXT",),
                allow_greedy_click=True,
                timeout_s=2.0,
                tag="team_trials_next",
            ):
                nav.wait_until_seen(
                    self.waiter,
                    classes=("race_after_next", "button_green", "button_white"),
                    tag="team_trials_after_next_ready",
                    timeout_s=5.0,
                )
                if self.waiter.click_when(
                    classes=("race_after_next",),
                    allow_greedy_click=True,
                    tag="team_trials_race_after_next",
                ):
                    logger_uma.debug("[TeamTrials] Clicked race_after_next")
                    if not nav.cooperative_sleep(3.0):
                        return
        else:
            nav.wait_until_seen(
                self.waiter,
                classes=("button_advance", "button_green", "button_white", "shop_exchange"),
                tag="team_trials_results_ready",
                timeout_s=5.0,
            )

        img, dets = nav.collect_snapshot(
            self.waiter, self.yolo_engine, tag="team_trials_midtap"
        )
        nav.random_center_tap(
            self.ctrl, img, clicks=random.randint(4, 5), dev_frac=0.01
        )
        nav.wait_until_seen(
            self.waiter,
            classes=("button_advance", "button_green", "button_pink", "shop_exchange", "shop_clock"),
            tag="team_trials_reward_ready",
            timeout_s=4.2,
        )
        if abort_requested():
            return
        img, dets = nav.collect_snapshot(
            self.waiter, self.yolo_engine, tag="team_trials_especial_reward"
        )
        logger_uma.debug(f"[TeamTrials] especial reward detected: {len(dets) == 1}. dets: {dets}")
        
        # if len(dets) == 1 or not button_pink in dets
        if len(dets) == 1 or not nav.has(dets, "button_pink"):
            did_next = self.waiter.click_when(
                classes=("button_advance",),
                prefer_bottom=True,
                clicks=1,
                forbid_texts=("VIEW RACE",),
                allow_greedy_click=True,
                timeout_s=2.6,
                tag="team_trials_reward_next",
            )
            self.waiter.click_when(
                classes=("button_green",),
                prefer_bottom=True,
                timeout_s=2.8,
                clicks=1,
                tag="team_trials_reward_next_green",
            )
            if not nav.cooperative_sleep(0.5):
                return

        did_shop = nav.handle_shop_exchange(
            self.waiter,
            self.yolo_engine,
            self.ctrl,
            tag_prefix="team_trials_shop",
            ensure_enter=ensure_enter_shop,
            should_stop=abort_requested,
        )
        if did_shop:
            logger_uma.info("[TeamTrials] Completed shop exchange flow")

        if not self.waiter.click_when(
            classes=("button_pink",),
            # texts=("RACE AGAIN",),
            prefer_bottom=True,
            timeout_s=2.2,
            clicks=1,
            # allow_greedy_click=False,
            tag="team_trials_race_again",
        ):
            logger_uma.info("[TeamTrials] RACE AGAIN NOT FOUND")

    def handle_finished_prompt(self) -> None:
        """
        After all trials are consumed: click 'NO', then press advance once to exit.
        """
        logger_uma.info("[TeamTrials] Finished flow detected; cleaning up.")
        self.waiter.click_when(
            classes=("button_white",),
            texts=("NO",),
            prefer_bottom=True,
            timeout_s=2.0,
            tag="team_trials_finished_no",
        )
        sleep(0.5)
        self.waiter.click_when(
            classes=("button_advance",),
            prefer_bottom=True,
            timeout_s=2.0,
            tag="team_trials_finished_advance",
        )
