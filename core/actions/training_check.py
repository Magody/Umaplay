from __future__ import annotations

import time
from typing import List, Dict, Optional, Tuple
import random

import cv2
import numpy as np
from PIL import Image

from core.perception.extractors.training_metrics import extract_failure_pct_for_tile
from core.perception.yolo.interface import IDetector
from core.settings import Settings
from core.utils.analyzers import analyze_support_crop
from core.utils.geometry import calculate_jitter
from core.utils.logger import logger_uma
from typing import Any
from dataclasses import dataclass, asdict

# ---- knobs you may want to tweak later (kept here for clarity) ----
BASE_MAX_FAILURE = 20  # default risk cap (%) if not provided elsewhere
GREEDY_THRESHOLD = 3.0  # "pick immediately" threshold (if you use it)
HIGH_SV_THRESHOLD = 3.5  # when SV >= this, allow risk up to ×RISK_RELAX_FACTOR
RISK_RELAX_FACTOR = 1.5  # e.g., 20% -> 30% when SV is high

# Director scoring by bar color (latest rule you wrote)
DIRECTOR_SCORE_BY_COLOR = {
    "blue": 0.25,  # "blue or less"
    "green": 0.15,
    "orange": 0.10,
    "yellow": 0.00,  # max (or treat is_max as yellow)
    "max": 0.00,  # alias
}

# What counts as blue/green vs orange/max for the standard supports
BLUE_GREEN = {"blue", "green"}
ORANGE_MAX = {"orange", "yellow"}


@dataclass
class TileSV:
    tile_idx: int
    failure_pct: int
    risk_limit_pct: int
    allowed_by_risk: bool
    sv_total: float
    sv_by_type: Dict[str, float]
    greedy_hit: bool
    notes: List[str]

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # optional rounding/pretty-printing
        d["sv_total"] = float(f"{d['sv_total']:.2f}")
        d["sv_by_type"] = {k: float(f"{v:.2f}") for k, v in d["sv_by_type"].items()}
        return d


SUPPORT_NAMES = {
    "support_card",
    "support_card_rainbow",
    "support_etsuko",
    "support_director",
}


def _center(xyxy: Tuple[float, float, float, float]) -> Tuple[float, float]:
    x1, y1, x2, y2 = xyxy
    return (0.5 * (x1 + x2), 0.5 * (y1 + y2))


def _raised_training_ltr_index(
    parsed_objects_screen: List[Dict], tol_px: int = 3, tol_frac_h: float = 0.06
) -> Optional[int]:
    """
    Return the left-to-right index of the 'raised' training tile if one clearly stands out,
    else None. 'Raised' = top y noticeably smaller than the others.
    """
    btns = [d for d in parsed_objects_screen if d["name"] == "training_button"]
    if len(btns) < 2:
        return None

    tops = np.array([d["xyxy"][1] for d in btns], dtype=float)
    heights = np.array([d["xyxy"][3] - d["xyxy"][1] for d in btns], dtype=float)
    med_top = float(np.median(tops))
    min_top = float(np.min(tops))
    thr = max(float(tol_px), float(tol_frac_h) * float(np.median(heights)))

    if (med_top - min_top) > thr:
        xs = np.array([(d["xyxy"][0] + d["xyxy"][2]) * 0.5 for d in btns], dtype=float)
        ltr_idx = np.argsort(xs)
        btns_ltr = [btns[i] for i in ltr_idx]
        tops_ltr = [b["xyxy"][1] for b in btns_ltr]
        raised_idx_ltr = int(np.argmin(tops_ltr))
        return raised_idx_ltr

    return None


def _center_x(xyxy):
    x1, y1, x2, y2 = xyxy
    return 0.5 * (x1 + x2)

def scan_training_screen(
    ctrl,
    ocr,  # OCRInterface
    yolo_engine: IDetector,
    energy,
    *,
    pause_after_click_range: list = [0.3, 0.4],
    conf_support: float = 0.60,
) -> Tuple[List[Dict], Image.Image, List[Dict]]:
    """
    Efficient scan:
      • One initial capture.
      • If a tile is already raised, harvest it first (no click).
      • Then click each remaining tile exactly once; after each click:
          - recapture once,
          - refresh button geometry (LTR),
          - collect supports present in that capture (they belong to the raised tile),
          - extract failure%.
    Returns: (training_state, last_img, last_parsed)
    """
    # -------- detector params --------
    param_imgsz = 832
    param_conf = 0.60  # lower than 0.8 so we don't miss support cards
    param_iou = 0.45

    # -------- helpers --------
    def _center_x(xyxy):
        x1, _, x2, _ = xyxy
        return 0.5 * (x1 + x2)

    def _get_buttons_ltr(parsed_objs: List[Dict]) -> List[Dict]:
        btns = [d for d in parsed_objs if d["name"] == "training_button"]
        btns.sort(key=lambda d: _center_x(d["xyxy"]))
        return btns

    def _raised_training_ltr_index(
        parsed: List[Dict], tol_px: int = 3, tol_frac_h: float = 0.06
    ) -> Optional[int]:
        btns = [d for d in parsed if d["name"] == "training_button"]
        if len(btns) < 2:
            return None
        tops = np.array([d["xyxy"][1] for d in btns], dtype=float)
        heights = np.array([d["xyxy"][3] - d["xyxy"][1] for d in btns], dtype=float)
        med_top = float(np.median(tops))
        min_top = float(np.min(tops))
        thr = max(float(tol_px), float(tol_frac_h) * float(np.median(heights)))
        if (med_top - min_top) > thr:
            xs = np.array(
                [(d["xyxy"][0] + d["xyxy"][2]) * 0.5 for d in btns], dtype=float
            )
            order = np.argsort(xs)
            btns_ltr = [btns[i] for i in order]
            tops_ltr = [b["xyxy"][1] for b in btns_ltr]
            return int(np.argmin(tops_ltr))
        return None

    def _jitter_delay():
        if pause_after_click_range and len(pause_after_click_range) >= 2:
            a, b = float(pause_after_click_range[0]), float(pause_after_click_range[1])
            lo, hi = (a, b) if a <= b else (b, a)
            return max(0.0, random.uniform(lo, hi))
        return 0.6

    def _collect_supports_enriched(
        cur_img: Image.Image, cur_parsed: List[Dict]
    ) -> Tuple[List[Dict], bool]:
        """
        Take *all* supports visible in this capture — they correspond to the currently raised tile.
        Enrich each with bar/type pieces, hint, rainbow, etc.
        """
        frame_bgr = cv2.cvtColor(np.array(cur_img), cv2.COLOR_RGB2BGR)
        supports = [
            d
            for d in cur_parsed
            if d["name"] in SUPPORT_NAMES and d.get("conf", 0.0) >= conf_support
        ]
        parts_bar = [d for d in cur_parsed if d["name"] == "support_bar"]
        parts_type = [d for d in cur_parsed if d["name"] == "support_type"]

        def _inside(inner, outer, pad=2):
            ix1, iy1, ix2, iy2 = inner
            ox1, oy1, ox2, oy2 = outer
            return (
                ix1 >= ox1 - pad
                and iy1 >= oy1 - pad
                and ix2 <= ox2 + pad
                and iy2 <= oy2 + pad
            )

        enriched: List[Dict] = []
        any_rainbow = False

        for s in supports:
            x1, y1, x2, y2 = [max(0, int(v)) for v in s["xyxy"]]
            crop = frame_bgr[y1:y2, x1:x2].copy()

            # parts within this support
            bar_xyxy = None
            type_xyxy = None
            for pb in parts_bar:
                bx1, by1, bx2, by2 = [int(v) for v in pb["xyxy"]]
                if _inside((bx1, by1, bx2, by2), (x1, y1, x2, y2), pad=1):
                    bar_xyxy = (bx1, by1, bx2, by2)
                    break
            for pt in parts_type:
                tx1, ty1, tx2, ty2 = [int(v) for v in pt["xyxy"]]
                if _inside((tx1, ty1, tx2, ty2), (x1, y1, x2, y2), pad=1):
                    type_xyxy = (tx1, ty1, tx2, ty2)
                    break

            bar_crop = (
                None
                if bar_xyxy is None
                else frame_bgr[
                    bar_xyxy[1] : bar_xyxy[3], bar_xyxy[0] : bar_xyxy[2]
                ].copy()
            )
            type_crop = (
                None
                if type_xyxy is None
                else frame_bgr[
                    type_xyxy[1] : type_xyxy[3], type_xyxy[0] : type_xyxy[2]
                ].copy()
            )

            attrs = analyze_support_crop(
                s["name"],
                crop,
                piece_bar_bgr=bar_crop,
                piece_type_bgr=type_crop,
            )

            has_rainbow = s["name"].endswith("_rainbow") or (
                s["name"] == "support_card_rainbow"
            )
            any_rainbow |= has_rainbow

            enriched.append(
                {
                    **s,
                    "name": s["name"].replace("_rainbow", ""),  # normalize
                    "support_type": attrs["support_type"],
                    "support_type_score": attrs["support_type_score"],
                    "friendship_bar": attrs["friendship_bar"],
                    "has_hint": attrs["has_hint"],
                    "has_rainbow": bool(has_rainbow),
                }
            )

        return enriched, any_rainbow

    ENERGY_TO_IGNORE_FAILURE = 45

    def _failure_pct(cur_img, cur_parsed, tile_xyxy):
        if energy >= ENERGY_TO_IGNORE_FAILURE:
            return 0

        failure_predict = extract_failure_pct_for_tile(
            cur_img, cur_parsed, tile_xyxy, ocr
        )
        if failure_predict == -1:
            # try again
            time.sleep(0.2)
            failure_predict = extract_failure_pct_for_tile(
                cur_img, cur_parsed, tile_xyxy, ocr
            )

            if failure_predict == -1:
                failure_predict = Settings.MAX_FAILURE + 1

        return failure_predict

    # -------- 1) Initial capture, wait for button training animations --------
    time.sleep(0.3)
    cur_img, _, cur_parsed = yolo_engine.recognize(
        imgsz=param_imgsz, conf=param_conf, iou=param_iou, tag="training"
    )

    btns = _get_buttons_ltr(cur_parsed)

    if btns and len(btns) != 5:
        time.sleep(0.5)
        # try again
        cur_img, _, cur_parsed = yolo_engine.recognize(
            imgsz=param_imgsz, conf=param_conf, iou=param_iou, tag="training"
        )

        btns = _get_buttons_ltr(cur_parsed)
    if not btns:
        logger_uma.warning("No training buttons detected.")
        return [], cur_img, cur_parsed

    # Fixed LTR scaffold
    scan = [
        {
            "tile_idx": j,
            "tile_xyxy": btns[j]["xyxy"],
            "tile_center_x": float(_center_x(btns[j]["xyxy"])),
            "supports": [],
        }
        for j in range(len(btns))
    ]

    processed: set = set()
    results: List[Dict] = []

    # -------- 2) Already-raised tile (no click) --------
    ridx = _raised_training_ltr_index(cur_parsed)
    if ridx is not None and 0 <= ridx < len(scan):
        tile = scan[ridx]
        supps, any_rainbow = _collect_supports_enriched(cur_img, cur_parsed)
        results.append(
            {
                **tile,
                "supports": supps,
                "has_any_rainbow": any_rainbow,
                "failure_pct": _failure_pct(cur_img, cur_parsed, tile["tile_xyxy"]),
                "skipped_click": True,
            }
        )
        processed.add(ridx)

    # -------- 3) Visit remaining tiles exactly once --------
    for idx in range(len(scan)):
        if idx in processed:
            continue

        tile = scan[idx]
        # Click to raise this tile
        ctrl.click_xyxy_center(
            tile["tile_xyxy"],
            clicks=1,
            jitter=calculate_jitter(tile["tile_xyxy"], percentage_offset=0.20),
        )

        time.sleep(_jitter_delay())

        # Recapture once
        cur_img, _, cur_parsed = yolo_engine.recognize(
            imgsz=param_imgsz, conf=param_conf, iou=param_iou, tag="training"
        )

        # Refresh geometry (LTR) to keep tile_xyxy up-to-date
        btns_now = _get_buttons_ltr(cur_parsed)
        if len(btns_now) == len(scan):
            for j, b in enumerate(btns_now):
                scan[j]["tile_xyxy"] = b["xyxy"]
                scan[j]["tile_center_x"] = float(_center_x(b["xyxy"]))
        else:
            logger_uma.warning(
                "Button count changed during scan: expected %d, got %d; keeping previous geometry",
                len(scan),
                len(btns_now),
            )

        # Whichever tile is raised after the click is the effective index
        ridx = _raised_training_ltr_index(cur_parsed)
        eff_idx = ridx if (ridx is not None and 0 <= ridx < len(scan)) else idx
        eff_tile = scan[eff_idx]

        supps, any_rainbow = _collect_supports_enriched(cur_img, cur_parsed)

        results.append(
            {
                **eff_tile,
                "supports": supps,
                "has_any_rainbow": any_rainbow,
                "failure_pct": _failure_pct(cur_img, cur_parsed, eff_tile["tile_xyxy"]),
                "skipped_click": False,
            }
        )
        processed.add(eff_idx)

    results.sort(key=lambda r: r["tile_idx"])
    return results, cur_img, cur_parsed


def compute_support_values(training_state: List[Dict]) -> List[Dict[str, Any]]:
    """
    Compute Support Value (SV) per tile and apply the failure filtering rule.

    Inputs
    ------
    training_state : list[dict]
        Exactly the structure you pasted (each tile has 'supports', 'failure_pct', ...).

    Scoring Rules (as provided)
    ---------------------------
    • Each blue/green gauge support: +1  (per card)
    • If any blue/green support on the tile has a hint: +0.5 once (tile-capped) x2 if HINT_IS_IMPORTANT is True in Settings
    • Rainbow support (FT): +1 per rainbow card
      - Combo bonus: if >=2 rainbous, only add 0.5
    • Orange/Max gauge WITHOUT hint: +0  (no base)
      Orange/Max gauge WITH hint: +0.5 once (tile-capped, even if multiple)  x2 if HINT_IS_IMPORTANT is True in Settings
    • Reporter (support_etsuko): +0.1
    • Director (support_director): color-based
        blue: +0.25, green: +0.15, orange: +0.10, yellow/max: +0

    Failure rule
    ------------
    Let max_failure = BASE_MAX_FAILURE (20%) by default.
    - If SV < 3.5 → the tile must have failure_pct ≤ max_failure
    - If SV ≥ 3.5 → allow up to min(100, floor(max_failure * 1.5))

    Returns
    -------
    List[dict] with keys:
      - tile_idx, failure_pct, risk_limit_pct, allowed_by_risk
      - sv_total (float)
      - sv_by_type (dict[str,float])    # per support_type aggregation
      - greedy_hit (bool)               # SV ≥ GREEDY_THRESHOLD
      - notes (list[str])               # human-readable breakdown
    """
    out: List[TileSV] = []

    for tile in training_state:
        idx = int(tile.get("tile_idx", -1))
        failure_pct = int(tile.get("failure_pct", 0) or 0)
        supports = tile.get("supports", []) or []

        sv_total = 0.0
        sv_by_type: Dict[str, float] = {}
        notes: List[str] = []

        # Tile-level caps/flags
        any_bluegreen_hint = False
        any_orange_max_hint = False

        # For rainbow combo computation (per type)
        rainbow_count = 0

        # ---- 1) per-support contributions ----
        for s in supports:
            sname = s.get("name", "")
            # stype = s.get("support_type", "unknown") or "unknown"
            bar = s.get("friendship_bar", {}) or {}
            color = str(bar.get("color", "unknown")).lower()
            is_max = bool(bar.get("is_max", False))
            has_hint = bool(s.get("has_hint", False))
            has_rainbow = bool(s.get("has_rainbow", False))

            # Normalize 'max' color if flagged
            if is_max and color not in ("yellow", "max"):
                color = "yellow"

            # --- special cameos ---
            if sname == "support_etsuko":  # reporter
                sv_total += 0.1
                sv_by_type["special_reporter"] = (
                    sv_by_type.get("special_reporter", 0.0) + 0.1
                )
                notes.append("Reporter: +0.1")
                continue

            if sname == "support_director":
                # director score depends on color (blue/green/orange/yellow)
                score = DIRECTOR_SCORE_BY_COLOR.get(
                    color, DIRECTOR_SCORE_BY_COLOR.get("yellow", 0.0)
                )
                if score > 0:
                    sv_total += score
                    sv_by_type["special_director"] = (
                        sv_by_type.get("special_director", 0.0) + score
                    )
                    notes.append(f"Director ({color}): +{score:.2f}")
                else:
                    notes.append(f"Director ({color}): +0.00")
                continue

            # --- standard support cards (including rainbow variants) ---
            # Rainbow counts as +1 baseline
            if has_rainbow:
                sv_total += 1.0
                notes.append("rainbow: +1.00")
                rainbow_count = rainbow_count + 1
                # Rainbow hint does not add extra beyond standard tile-capped hint rules;
                # we still let hint rules below consider color buckets if needed.
                # (If you want rainbow to bypass color gates, keep as-is)
            else:
                # Blue/green baseline
                if color in BLUE_GREEN:
                    sv_total += 1.0
                    sv_by_type["cards"] = sv_by_type.get("cards", 0.0) + 1.0
                    notes.append(f"{color}: +1.00")
                    if has_hint:
                        any_bluegreen_hint = True
                # Orange/Max baseline is 0; only hint may help (tile-capped)
                elif color in ORANGE_MAX or is_max:
                    if has_hint:
                        any_orange_max_hint = True
                    notes.append(f"{color}: +0.00")
                else:
                    notes.append(f"{color}: +0.00 (unknown color category)")

        # ---- 2) tile-capped hint bonuses ----
        if any_bluegreen_hint:
            hint_value = 0.75
            if Settings.HINT_IS_IMPORTANT:
                hint_value *= 3
            sv_total += hint_value
            sv_by_type["hint_bluegreen"] = (
                sv_by_type.get("hint_bluegreen", 0.0) + hint_value
            )
            notes.append(
                f"Hint on blue/green (tile-capped): +{hint_value}. Settings.HINT_IS_IMPORTANT={Settings.HINT_IS_IMPORTANT}"
            )

        if any_orange_max_hint:
            hint_value = 0.75
            if Settings.HINT_IS_IMPORTANT:
                hint_value *= 3
            sv_total += hint_value
            sv_by_type["hint_orange_max"] = (
                sv_by_type.get("hint_orange_max", 0.0) + hint_value
            )
            notes.append(
                f"Hint on orange/max (tile-capped): +{hint_value}. Settings.HINT_IS_IMPORTANT={Settings.HINT_IS_IMPORTANT}"
            )

        # ---- 3) rainbow combo bonus (per type) ----

        if rainbow_count >= 2:
            # +0.5 for each *type* that has ≥2 rainbow cards
            combo_bonus = 0.5
            sv_total += combo_bonus
            sv_by_type["rainbow_combo"] = (
                sv_by_type.get("rainbow_combo", 0.0) + combo_bonus
            )
            notes.append(f"Rainbow combo +{combo_bonus}")

        # ---- 4) risk gating with “good SV” relax ----
        base_limit = BASE_MAX_FAILURE
        risk_limit = int(
            min(
                100,
                base_limit
                * (RISK_RELAX_FACTOR if sv_total >= HIGH_SV_THRESHOLD else 1.0),
            )
        )
        allowed = failure_pct <= risk_limit

        # ---- 5) greedy mark (optional early exit logic can use this) ----
        greedy_hit = (sv_total >= GREEDY_THRESHOLD) and allowed
        if greedy_hit:
            notes.append(
                f"Greedy hit: SV {sv_total:.2f} ≥ {GREEDY_THRESHOLD} and failure {failure_pct}% ≤ {risk_limit}%"
            )

        out.append(
            TileSV(
                tile_idx=idx,
                failure_pct=failure_pct,
                risk_limit_pct=risk_limit,
                allowed_by_risk=bool(allowed),
                sv_total=float(sv_total),
                sv_by_type=sv_by_type,
                greedy_hit=greedy_hit,
                notes=notes,
            )
        )

    # Return simple dicts for convenience in notebooks / JSON
    return [t.as_dict() for t in out]
