# core/actions/daily_race.py
from __future__ import annotations

import random
from time import sleep
from typing import List

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


class DailyRaceFlow:
    """
    Daily Races navigation: enter menu, pick a 'monies' card/row, confirm, race, results.
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
            "row": 0.70,  # race_daily_races_monies_row
        }

    def enter_from_menu(self) -> bool:
        ok = self.waiter.click_when(
            classes=("race_daily_races",),
            prefer_bottom=False,
            timeout_s=2.0,
            tag="daily_race_menu",
        )
        if not ok:
            return False
        if not nav.cooperative_sleep(1.7):
            return False
        # Often need to click the 'monies' card
        self.waiter.click_when(
            classes=("race_daily_races_monies",),
            prefer_bottom=True,
            timeout_s=3.2,
            tag="daily_race_monies",
        )
        return True

    def pick_first_row(self) -> bool:
        """
        Click the first valid 'race_daily_races_monies_row' (topmost above threshold).
        """
        img, dets = nav.collect_snapshot(
            self.waiter, self.yolo_engine, tag="daily_race_rows"
        )
        rows = nav.rows_top_to_bottom(dets, "race_daily_races_monies_row")
        for row in rows:
            if float(row.get("conf", 0.0)) >= self._thr["row"]:
                self.ctrl.click_xyxy_center(row["xyxy"])
                logger_uma.info("[DailyRace] Clicked 'Monies' row")
                return True
        return False

    def confirm_and_next_to_race(self) -> bool:
        """
        NEXT -> RACE
        """
        if not nav.cooperative_sleep(1.5):
            return False
        ok = self.waiter.click_when(
            classes=("button_green",),
            prefer_bottom=False,
            allow_greedy_click=False,
            texts=("RACE", "RACE!", "RACEL"),
            forbid_texts=("OK", "PURCHASE", "BUY", "RESTORE"),
            timeout_s=2.0,
            tag="daily_race_next_0",
        )
        if not ok:
            # Check with waiter if there are the next button with texts: button_white 'CANCEL' , button_green 'OK'. If that is the case, click in Cancel, wait 1 sec, capture /recognize objects in screen and press ui_home
            if self.waiter.click_when(
                classes=("button_white",),
                prefer_bottom=False,
                allow_greedy_click=False,
                texts=("CANCEL",),
                forbid_texts=("OK", "PURCHASE", "BUY", "RESTORE"),
                timeout_s=2.0,
                tag="daily_race_cancel",
            ):
                if not nav.cooperative_sleep(1.5):
                    return False
                img, dets = nav.collect_snapshot(
                    self.waiter, self.yolo_engine, tag="daily_race_cancel"
                )
                # Click ui_home  that may be inside dets
                self.waiter.click_when(
                    classes=("ui_home",),
                    prefer_bottom=True,
                    timeout_s=3.0,
                    tag="daily_race_ui_home",
                )
                nav.cooperative_sleep(1.5)
                logger_uma.debug("[DailyRace] Canceling races")
                return False
        if not nav.cooperative_sleep(1.5):
            return False
        if self.waiter.click_when(
            classes=("button_green",),
            prefer_bottom=True,
            timeout_s=2.0,
            tag="daily_race_race",
        ):
            logger_uma.info("[DailyRace] RACE 1")
            return True
        return False

    def run_race_and_collect(self) -> bool:
        """
        Start race, view results and proceed.
        """

        race_again = True
        finalized = False
        counter = 5
        while race_again and counter > 0:
            if abort_requested():
                break
            if not nav.cooperative_sleep(3.0):
                break
            if isinstance(self.ctrl, ScrcpyController) or (
                BlueStacksController is not None
                and isinstance(self.ctrl, BlueStacksController)
            ):
                if not nav.cooperative_sleep(1.5):
                    break
            if self.waiter.click_when(
                classes=("button_green",),
                prefer_bottom=True,
                timeout_s=6.0,
                tag="daily_race_next_1",
            ):
                logger_uma.info("[DailyRace] NEXT (1)")
            else:
                race_again = False
                continue
            if not nav.cooperative_sleep(1.5):
                break

            if self.waiter.click_when(
                classes=("button_green",),
                prefer_bottom=True,
                timeout_s=2.0,
                tag="daily_race_race",
            ):
                logger_uma.info("[DailyRace] race (2)")
            else:
                race_again = False
                continue
            counter -= 1
            if not nav.cooperative_sleep(2.0):
                break
            # After race, click 'View Results' / proceed with white button spamming
            img, _ = nav.collect_snapshot(
                self.waiter, self.yolo_engine, tag="daily_race_view_results"
            )
            self.waiter.click_when(
                classes=("button_white",),
                prefer_bottom=False,
                timeout_s=7.3,
                texts=("VIEW RESULTS", "CLOSE"),
                forbid_texts=("BACK",),
                allow_greedy_click=False,
                clicks=1,
                tag="daily_race_view_results_white",
            )
            if not nav.cooperative_sleep(2.0):
                break
            # nav.random_center_tap(
            #     self.ctrl, img, clicks=random.randint(3, 4), dev_frac=0.20
            # )
            # sleep(2.0)

            # Then green to continue
            if self.waiter.click_when(
                classes=("button_green",),
                prefer_bottom=True,
                timeout_s=2.0,
                tag="daily_race_after_results_green",
            ):
                logger_uma.info("[DailyRace] Results: continued")

            # check for shop, reuse the nav method
            did_shop = nav.handle_shop_exchange(
                self.waiter,
                self.yolo_engine,
                self.ctrl,
                tag_prefix="daily_race_shop",
                ensure_enter=True,
                should_stop=abort_requested,
            )
            if did_shop:
                logger_uma.info("[DailyRace] Completed shop exchange flow")
                finalized = False  # Shop, uncertain if finalized
                break
            else:
                if not self.waiter.click_when(
                    classes=("button_pink",),
                    texts=("RACE AGAIN",),
                    prefer_bottom=True,
                    timeout_s=4.2,
                    clicks=1,
                    allow_greedy_click=True,
                    tag="daily_race_again",
                ):
                    logger_uma.info("[TeamTrials] RACE AGAIN NOT FOUND")
                    finalized = True
                    break
                else:
                    if not nav.cooperative_sleep(2.0):
                        break
                    if self.waiter.seen(
                        classes=("button_green",),
                        texts=("OK",),
                        tag="agent_nav_daily_race_ok",
                    ):
                        logger_uma.info("[DailyRace] OK seen no more dailys")
                        # Click in button_white using the waiter
                        self.waiter.click_when(
                            classes=("button_white",),
                            prefer_bottom=True,
                            timeout_s=2.0,
                            tag="daily_race_ok",
                        )
                        if not nav.cooperative_sleep(2.0):
                            break
                        # Click in button_advance using the waiter
                        self.waiter.click_when(
                            classes=("button_advance",),
                            prefer_bottom=True,
                            timeout_s=4,
                            tag="daily_race_advance",
                        )
                        if not nav.cooperative_sleep(2.0):
                            break
                        if isinstance(self.ctrl, ScrcpyController) or (
                            BlueStacksController is not None
                            and isinstance(self.ctrl, BlueStacksController)
                        ):
                            if not nav.cooperative_sleep(4.0):
                                break
                        # Click object with class ui_home
                        self.waiter.click_when(
                            classes=("ui_home",),
                            prefer_bottom=True,
                            timeout_s=2.0,
                            tag="daily_race_home",
                        )
                        finalized = True
                        race_again = False
                    continue
        return finalized
