"""
Shared lightweight types and constants used across the project.

Keep this file small and focused on:
- Structural types (TypedDict / dataclasses) for detections, scans, and enrichments.
- Small enums/aliases and cross-module constants (e.g., mood map).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import enum
from typing import Any, Dict, List, Literal, Tuple, TypedDict

# ---------- Basic geometric aliases ----------

XYXY = Tuple[float, float, float, float]  # (x1, y1, x2, y2)
XYWH = Tuple[int, int, int, int]  # (x, y, w, h) integers (screen/client coords)

# ---------- Detection & screen classification ----------


class DetectionDict(TypedDict):
    """Normalized YOLO detection for a single box in last-screenshot coordinates."""

    idx: int
    name: str
    conf: float
    xyxy: XYXY


ScreenName = Literal[
    "CareerComplete",
    "Raceday",
    "RaceLobby",
    "Inspiration",
    "Lobby",
    "LobbySummer",
    "Event",
    "Training",
    "FinalScreen",
    "ClawMachine",
    "EventStale",
    "Unknown",
    "UnityCupRaceday",
    "EventGolden",
    "KashimotoTeam",
]


class ScreenInfo(TypedDict, total=False):
    """
    Optional diagnostic payload returned with ScreenName.
    Example keys:
      - training_buttons: int
      - tazuna: bool
      - infirmary: bool
      - counts: {class_name: count}
    """

    training_buttons: int
    tazuna: bool
    infirmary: bool
    event_choices: int
    has_inspiration: bool
    rest: bool
    rest_summer: bool
    recreation: bool
    recreation_present: bool
    pal_available: bool
    race_day: bool
    has_lobby_skills: bool
    race_after_next: bool
    has_golden: bool
    has_button_claw_action: bool
    has_claw: bool
    counts: Dict[str, int]
    has_button_white: bool
    button_change: bool
    race_badge: bool


# ---------- Training scan structures ----------


@dataclass(frozen=True)
class TileScanEntry:
    """
    Result of scanning a single training tile after click/raise.
    - tile_xyxy is in the same coordinate system as the last screenshot (left half capture).
    - supports is a list of raw detections (support_* classes) for that tile's overlay.
    """

    tile_idx: int
    tile_xyxy: XYXY
    tile_center_x: float
    supports: List[DetectionDict]


# ---------- Analyzers / support attributes ----------

FriendshipColor = Literal["gray", "blue", "green", "yellow", "pink", "unknown"]


@dataclass(frozen=True)
class FriendshipBarInfo:
    color: FriendshipColor
    progress_pct: int  # 0..100
    fill_ratio: float  # 0.0..1.0
    is_max: bool


@dataclass(frozen=True)
class SupportAttributes:
    """
    Attributes extracted for a single support crop via analyzers.
    """

    support_type: str
    support_type_score: float
    friendship_bar: FriendshipBarInfo
    has_hint: bool
    has_rainbow: bool


@dataclass(frozen=True)
class ScanTileEnriched:
    """
    Enriched tile entry after analyzers and ROI-based OCR:
      - supports_enriched mirrors 'supports' but with semantic attributes.
      - failure_pct is the parsed integer (0..100) for the “Failure XX%” bubble, or -1 if not found.
      - has_any_rainbow is a convenience flag aggregated from supports_enriched.
      - skipped_click tells whether we reused a pre-raised tile and avoided clicking.
    """

    tile_idx: int
    tile_xyxy: XYXY
    tile_center_x: float
    supports_enriched: List[SupportAttributes]
    has_any_rainbow: bool
    failure_pct: int
    skipped_click: bool


# Mood categories and a simple numeric mapping used for downstream logic.
MoodName = Literal["AWFUL", "BAD", "NORMAL", "GOOD", "GREAT", "UNKNOWN"]

_Box = List[List[float]]  # 4 points [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
OCRItem = Tuple[_Box, str, float]  # (box, text, score)

RegionXYWH = Tuple[int, int, int, int]


class TrainAction(enum.Enum):
    """Atomic decisions for the training turn."""

    # Tile-targeting actions (return a tile_idx)
    TRAIN_MAX = "train_max"  # train the highest-SV tile (risk-allowed)
    TRAIN_WIT = "train_wit"  # specifically train WIT tile
    TRAIN_DIRECTOR = "train_director"  # train where Director is (special rule)
    TAKE_HINT = "take_hint"  # train any tile that has a hint

    # Non-tile actions (tile_idx=None)
    REST = "rest"
    RECREATION = "recreation"
    RACE = "race"
    SECURE_SKILL = "secure_skill"  # late-game safety: ensure 1200/600
    NOOP = "noop"  # fallback (should not normally happen)


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
        