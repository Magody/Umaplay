# core/utils/nav.py
from __future__ import annotations

import random
import time
from time import sleep
from typing import Callable, List, Sequence, Tuple, Dict, Optional, Iterable

from PIL import Image

from core.controllers.base import IController
from core.perception.yolo.interface import IDetector
from core.settings import Settings
from core.types import DetectionDict
from core.utils.abort import abort_requested
from core.utils.logger import logger_uma
from core.utils.pointer import smart_scroll_small
from core.utils.waiter import Waiter


def collect_snapshot(
    waiter: Waiter,
    yolo_engine: IDetector,
    *,
    agent: Optional[str] = None,
    tag: str,
) -> Tuple[Image.Image, List[DetectionDict]]:
    active_agent = agent if agent is not None else getattr(waiter, "agent", None)
    if active_agent is None:
        logger_uma.warning(
            "collect_snapshot called without agent; defaulting to generic debug folder",
            extra={"collect_snapshot_tag": tag},
        )
    img, _, dets = yolo_engine.recognize(
        imgsz=waiter.cfg.imgsz,
        conf=waiter.cfg.conf,
        iou=waiter.cfg.iou,
        agent=active_agent,
        tag=tag,
    )
    return img, dets


def has(dets: List[DetectionDict], name: str, *, conf_min: float = 0.0) -> bool:
    return any(
        d.get("name") == name and float(d.get("conf", 0.0)) >= conf_min for d in dets
    )


def by_name(
    dets: List[DetectionDict], name: str, *, conf_min: float = 0.0
) -> List[DetectionDict]:
    return [
        d
        for d in dets
        if d.get("name") == name and float(d.get("conf", 0.0)) >= conf_min
    ]


def rows_top_to_bottom(
    dets: List[DetectionDict], name: str, *, conf_min: float = 0.0
) -> List[DetectionDict]:
    rows = by_name(dets, name, conf_min=conf_min)
    rows.sort(key=lambda d: d["xyxy"][1])
    return rows


def _detections_in_row(
    dets: List[DetectionDict], row: DetectionDict, name: str, *, conf_min: float = 0.0
) -> List[DetectionDict]:
    """Return detections with given name whose center lies inside the row bounds."""
    rx1, ry1, rx2, ry2 = row["xyxy"]
    matches: List[DetectionDict] = []
    for d in by_name(dets, name, conf_min=conf_min):
        x1, y1, x2, y2 = d["xyxy"]
        cx = 0.5 * (x1 + x2)
        cy = 0.5 * (y1 + y2)
        if rx1 <= cx <= rx2 and ry1 <= cy <= ry2:
            matches.append(d)
    return matches


def random_center_tap(
    ctrl: IController, img: Image.Image, *, clicks: int, dev_frac: float = 0.20
) -> None:
    """Tap near the center with random deviation."""
    W, H = img.size
    cx = W * 0.5 + random.uniform(-W * dev_frac, W * dev_frac)
    cy = H * 0.5 + random.uniform(-H * dev_frac, H * dev_frac)
    ctrl.click_xyxy_center((cx, cy, cx, cy), clicks=clicks)


def cooperative_sleep(
    seconds: float,
    *,
    step_s: float = 0.10,
    should_stop: Optional[Callable[[], bool]] = None,
) -> bool:
    """Sleep in short slices so long waits can be interrupted quickly."""
    deadline = time.monotonic() + max(0.0, float(seconds))
    while time.monotonic() < deadline:
        if abort_requested() or (should_stop is not None and should_stop()):
            return False
        remaining = deadline - time.monotonic()
        sleep(min(step_s, max(0.0, remaining)))
    return True


def wait_until_seen(
    waiter: Waiter,
    *,
    classes: Optional[Sequence[str]] = None,
    texts: Optional[Sequence[str]] = None,
    tag: str,
    timeout_s: float = 4.0,
    poll_interval_s: float = 0.20,
    conf_min: float = 0.0,
    threshold: float = 0.58,
    should_stop: Optional[Callable[[], bool]] = None,
) -> bool:
    """Poll current UI until the requested state is visible or a stop is requested."""
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    while time.monotonic() < deadline:
        if abort_requested() or (should_stop is not None and should_stop()):
            return False
        if waiter.seen(
            classes=classes,
            texts=texts,
            tag=tag,
            conf_min=conf_min,
            threshold=threshold,
        ):
            return True
        sleep(max(0.01, float(poll_interval_s)))
    return False


def click_button_loop(
    waiter: Waiter,
    *,
    classes: Sequence[str],
    tag_prefix: str,
    max_clicks: int = 6,
    sleep_between_s: float = 0.30,
    prefer_bottom: bool = True,
    texts: Optional[Sequence[str]] = None,
    clicks_each: int = 1,
    allow_greedy_click: bool = True,
    forbid_texts: Optional[Sequence[str]] = None,
    timeout_s: float = 2.0,
) -> int:
    """
    Repeatedly click a button limited by max_clicks. Returns number of successful clicks.
    """
    done = 0
    while done < max_clicks:
        ok = waiter.click_when(
            classes=classes,
            texts=texts,
            prefer_bottom=prefer_bottom,
            allow_greedy_click=allow_greedy_click,
            forbid_texts=forbid_texts,
            clicks=clicks_each,
            timeout_s=timeout_s,
            tag=f"{tag_prefix}_loop",
        )
        if not ok:
            break
        done += 1
        sleep(sleep_between_s)
    return done


def advance_sequence_with_mid_taps(
    waiter: Waiter,
    yolo_engine: IDetector,
    ctrl: IController,
    *,
    tag_prefix: str,
    iterations_max: int = 6,
    advance_class: str = "button_advance",
    advance_texts: Optional[Sequence[str]] = None,
    taps_each_click: Tuple[int, int] = (3, 4),
    tap_dev_frac: float = 0.20,
    sleep_after_advance: float = 0.40,
):
    """
    Click NEXT/advance a few times; after each advance, tap around center to nudge UI.
    Returns number of advances performed.
    """
    advances = 0
    last_clicked_pos = None  # Store the last clicked position
    for i in range(iterations_max):
        did, clicked_obj = waiter.click_when(
            classes=(advance_class,),
            texts=advance_texts,
            prefer_bottom=True,
            allow_greedy_click=True,
            timeout_s=2.3,
            clicks=random.randint(*taps_each_click),
            tag=f"{tag_prefix}_advance",
            return_object=True,
        )
        if not did and i > 5:
            break
        sleep(sleep_after_advance)
        
        # Click on the same position as the button we just clicked
        if clicked_obj:
            # Update last clicked position
            x1, y1, x2, y2 = clicked_obj["xyxy"]
            last_clicked_pos = (x1, y1 + 10, x2, y2 + 10)  # Store with offset
            ctrl.click_xyxy_center(
                last_clicked_pos,
                clicks=random.randint(2, 3),
            )
        elif last_clicked_pos and i < 5:
            # Click the last known position if available
            ctrl.click_xyxy_center(
                last_clicked_pos,
                clicks=random.randint(2, 3),
            )
        else:
            # Fallback to bottom right corner if no previous position
            img, _ = collect_snapshot(waiter, yolo_engine, tag=f"{tag_prefix}_tap")
            width, height = img.size
            # Click bottom right quarter of the screen
            bottom_right = (width * 0.9, height * 0.9, width * 0.98, height * 0.98)
            ctrl.click_xyxy_center(
                bottom_right,
                clicks=random.randint(2, 3),
            )
        
        advances += 1
        sleep(sleep_after_advance)
    return advances


def _shop_item_order() -> Iterable[Tuple[str, str]]:
    prefs = Settings.get_shop_nav_prefs()
    order = [
        ("shop_clock", "alarm_clock"),
        ("shop_star_piece", "star_pieces"),
        ("shop_parfait", "parfait"),
    ]
    for det_name, pref_key in order:
        if prefs.get(pref_key, False):
            yield det_name, pref_key


def _confirm_exchange_dialog(waiter: Waiter, tag_prefix: str) -> bool:
    ok = waiter.click_when(
        classes=("button_green",),
        texts=("EXCHANGE",),
        prefer_bottom=False,
        timeout_s=3.0,
        allow_greedy_click=False,
        tag=f"{tag_prefix}_confirm_exchange",
    )
    if not ok:
        return False

    sleep(1.5)
    if not waiter.click_when(
        classes=("button_white",),
        texts=("CLOSE",),
        prefer_bottom=False,
        timeout_s=3.0,
        allow_greedy_click=False,
        tag=f"{tag_prefix}_close",
    ):
        return False

    sleep(0.8)
    return True

def end_sale_dialog(waiter: Waiter, tag_prefix: str) -> bool:
    if not waiter.click_when(
        classes=("button_white",),
        texts=("END SALE",),
        prefer_bottom=False,
        timeout_s=2.0,
        allow_greedy_click=False,
        tag=f"{tag_prefix}_end_sale",
    ):
        return False

    sleep(0.7)
    waiter.click_when(
        classes=("button_green",),
        texts=("OK",),
        prefer_bottom=False,
        timeout_s=2.0,
        allow_greedy_click=False,
        tag=f"{tag_prefix}_ok",
    )
    sleep(0.6)
    waiter.click_when(
        classes=("ui_race",),
        prefer_bottom=True,
        timeout_s=2.0,
        allow_greedy_click=True,
        tag=f"{tag_prefix}_race",
    )
    return True

def handle_shop_exchange(
    waiter: Waiter,
    yolo_engine: IDetector,
    ctrl: IController,
    *,
    tag_prefix: str = "shop",
    ensure_enter: bool = True,
    max_cycles: int = 6,
    should_stop: Optional[Callable[[], bool]] = None,
) -> bool:
    prefs_enabled = list(_shop_item_order())
    if not prefs_enabled:
        logger_uma.info("[nav] shop: all items disabled by preference")
        return False

    shop_appeared = True
    if ensure_enter:
        shop_appeared = waiter.click_when(
            classes=("button_green",),
            texts=("SHOP",),
            prefer_bottom=False,
            allow_greedy_click=False,
            timeout_s=8.0,
            clicks=2,
            tag=f"{tag_prefix}_enter",
        )
        if not shop_appeared:
            return False
        if not cooperative_sleep(2.5, should_stop=should_stop):
            logger_uma.info("[nav] shop: aborted while entering")
            return False
    else:
        if not cooperative_sleep(1.0, should_stop=should_stop):
            logger_uma.info("[nav] shop: aborted before scan")
            return False

    attempts = 0
    any_purchased = False
    stagnant_attempts = 0
    while attempts < max_cycles:
        if abort_requested() or (should_stop is not None and should_stop()):
            logger_uma.info("[nav] shop: abort requested during exchange loop")
            break
        attempts += 1
        logger_uma.debug(
            "[nav] shop: scan attempt %d/%d (prefs=%s)",
            attempts,
            max_cycles,
            [pref_key for _, pref_key in prefs_enabled],
        )
        img, dets = collect_snapshot(waiter, yolo_engine, tag=f"{tag_prefix}_scan")

        rows = rows_top_to_bottom(dets, "shop_row")
        if not rows:
            logger_uma.debug("[nav] shop: no shop_row detected, retry scrolling")
            smart_scroll_small(ctrl, steps_android=1, steps_pc=1)
            if not cooperative_sleep(1.0, should_stop=should_stop):
                logger_uma.info("[nav] shop: abort requested while recovering rows")
                break
            continue

        progress_this_cycle = False
        expected_purchases = len(prefs_enabled)
        for det_name, pref_key in prefs_enabled:
            for row in rows:
                if abort_requested() or (should_stop is not None and should_stop()):
                    logger_uma.info("[nav] shop: abort requested while evaluating rows")
                    break
                items = _detections_in_row(dets, row, det_name)
                if not items:
                    continue

                exchanges = _detections_in_row(dets, row, "shop_exchange")
                if not exchanges:
                    logger_uma.debug(
                        f"[nav] shop: exchange button missing in row for {pref_key}"
                    )
                    continue

                target_exchange = max(exchanges, key=lambda d: float(d.get("conf", 0.0)))
                ctrl.click_xyxy_center(target_exchange["xyxy"], clicks=1)
                logger_uma.info(
                    f"[nav] shop: clicked exchange for '{det_name}' (pref={pref_key})"
                )
                if not cooperative_sleep(0.5, should_stop=should_stop):
                    logger_uma.info("[nav] shop: abort requested before confirmation")
                    break

                confirmed = _confirm_exchange_dialog(waiter, tag_prefix)
                if confirmed:
                    logger_uma.info(
                        f"[nav] shop: completed exchange for {pref_key}"
                    )
                    any_purchased = True
                    progress_this_cycle = True
                    expected_purchases -= 1
                else:
                    logger_uma.debug(
                        f"[nav] shop: confirmation failed for {pref_key}, continuing"
                    )
                if pref_key != "star_pieces":
                    # if star pieces we may need to look in other rows
                    break
            if abort_requested() or (should_stop is not None and should_stop()):
                break

        if expected_purchases > 0:
            stagnant_attempts = stagnant_attempts + 1 if not progress_this_cycle else 0
            if stagnant_attempts >= 3:
                logger_uma.info(
                    "[nav] shop: no progress for %d scans, exiting early",
                    stagnant_attempts,
                )
                break
            smart_scroll_small(ctrl, steps_android=1, steps_pc=1)
            if not cooperative_sleep(1.0, should_stop=should_stop):
                logger_uma.info("[nav] shop: abort requested while scrolling")
                break
        elif progress_this_cycle:
            # everything purchased at first glance
            end_sale_dialog(waiter, tag_prefix)
            return True

    if any_purchased:
        end_sale_dialog(waiter, tag_prefix)
        return True

    exited = end_sale_dialog(waiter, tag_prefix)
    if exited:
        logger_uma.info("[nav] shop: exited shop after incomplete scan")
        return True

    logger_uma.info("[nav] shop: preferences not satisfied after scroll attempts")
    return False
