from __future__ import annotations

import base64
import io
import json
import fnmatch
import os
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Set

from PIL import Image
import imagehash
from rapidfuzz import fuzz, process
from PIL.Image import Image as PILImage
from core.perception.analyzers.matching.base import TemplateEntry, TemplateMatcherBase
from core.perception.analyzers.matching.remote import RemoteTemplateMatcherBase as _RemoteTMB
from core.settings import Settings

from core.utils.logger import logger_uma

try:
    import cv2
    import numpy as np
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False

# -----------------------------
# Paths (adjust if needed)
# -----------------------------
DATASETS_EVENTS = Path("datasets/in_game/events.json")
ASSETS_EVENTS_DIR = Path(
    "web/public/events"
)  # /{support|trainee|scenario}/<name>_<rarity>.png
BUILD_DIR = Path("build")  # will hold event_catalog.json
CATALOG_JSON = Path("datasets/in_game/event_catalog.json")


DEFAULT_REWARD_PRIORITY: List[str] = ["skill_pts", "stats", "hints"]

_REWARD_KEY_TO_ALIAS: Dict[str, str] = {
    "energy": "energy",
    "skill_pts": "skill_pts",
    "skill_points": "skill_pts",
    "hint": "hints",
    "hints": "hints",
    "speed": "stats",
    "spd": "stats",
    "stamina": "stats",
    "sta": "stats",
    "power": "stats",
    "pwr": "stats",
    "guts": "stats",
    "gut": "stats",
    "wit": "stats",
    "wisdom": "stats",
    "intelligence": "stats",
    "stats": "stats",
}

_VALID_REWARD_CATEGORIES: Set[str] = {"skill_pts", "hints", "stats", "energy"}


def _coerce_float(val: Any) -> Optional[float]:
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        try:
            return float(val.strip())
        except Exception:
            return None
    return None


def normalize_reward_priority_list(raw: Optional[Any]) -> List[str]:
    if not isinstance(raw, (list, tuple)):
        return list(DEFAULT_REWARD_PRIORITY)
    seen: Set[str] = set()
    result: List[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        key = item.strip().lower()
        if not key:
            continue
        mapped = _REWARD_KEY_TO_ALIAS.get(key, key)
        if mapped in _VALID_REWARD_CATEGORIES and mapped not in seen:
            result.append(mapped)
            seen.add(mapped)
    if not result:
        return list(DEFAULT_REWARD_PRIORITY)
    return result


def max_positive_energy(outcomes: List[Dict[str, Any]]) -> int:
    max_gain = 0

    def visit(node: Any) -> None:
        nonlocal max_gain
        if isinstance(node, dict):
            for k, v in node.items():
                key = str(k).strip().lower()
                if key == "energy":
                    val = _coerce_float(v)
                    if val is not None and val > 0:
                        max_gain = max(max_gain, int(val))
                visit(v)
        elif isinstance(node, (list, tuple)):
            for item in node:
                visit(item)

    visit(outcomes)
    return max_gain


def extract_reward_categories(outcomes: List[Dict[str, Any]]) -> Set[str]:
    categories: Set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                key = str(k).strip().lower()
                mapped = _REWARD_KEY_TO_ALIAS.get(key)
                if mapped == "skill_pts":
                    val = _coerce_float(v)
                    if val is None or val > 0:
                        categories.add("skill_pts")
                elif mapped == "hints":
                    if isinstance(v, (list, tuple)):
                        if any(v):
                            categories.add("hints")
                    elif v:
                        categories.add("hints")
                elif mapped == "stats":
                    val = _coerce_float(v)
                    if (isinstance(v, (int, float)) and v > 0) or (
                        isinstance(v, dict) or isinstance(v, (list, tuple))
                    ) or (val is not None and val > 0):
                        categories.add("stats")
                elif mapped == "energy":
                    val = _coerce_float(v)
                    if val is None or val > 0:
                        categories.add("energy")
                visit(v)
        elif isinstance(node, (list, tuple)):
            for item in node:
                visit(item)

    visit(outcomes)
    return categories & _VALID_REWARD_CATEGORIES


def select_candidate_by_priority(
    candidate_order: List[int],
    safe_candidates: List[int],
    option_categories: Dict[int, Set[str]],
    priority: List[str],
) -> Optional[Tuple[int, Optional[str]]]:
    if not safe_candidates:
        return None
    priority = [p for p in priority if p in _VALID_REWARD_CATEGORIES] or list(
        DEFAULT_REWARD_PRIORITY
    )

    safe_seen = set(safe_candidates)
    for category in priority:
        for opt in candidate_order:
            if opt not in safe_seen:
                continue
            if category in option_categories.get(opt, set()):
                return opt, category

    for opt in candidate_order:
        if opt in safe_seen:
            return opt, None
    return None


def safe_phash_from_image(img: PILImage) -> Optional[int]:
    """Compute 64-bit pHash from an in-memory PIL image."""
    try:
        ph = imagehash.phash(img)
        return int(str(ph), 16)
    except Exception:
        return None


# -----------------------------
# Helpers
# -----------------------------


def _coerce_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
                return False
    return default


# -----------------------------
# Utilities
# -----------------------------


def _support_key(name: str, attribute: str, rarity: str) -> Tuple[str, str, str]:
    return (
        str(name or "").strip(),
        str(attribute or "None").strip().upper(),
        str(rarity or "None").strip().upper(),
    )


def _scenario_key(name: str) -> str:
    return normalize_text(name or "")


def _trainee_key(name: str) -> str:
    return normalize_text(name or "")


def normalize_text(s: str) -> str:
    """Basic normalization robust to punctuation and spacing differences."""
    if not s:
        return ""
    # unify punctuation commonly seen in Uma text (music notes, arrows, full/half width)
    rep = {
        "≫": ">>",
        "«": "<<",
        "»": ">>",
        "♪": " note ",
        "☆": "*",
        "★": "*",
        "　": " ",  # full-width space
        "–": "-",
        "—": "-",
        "―": "-",
        "-": "-",
        "…": "...",
    }
    s2 = s.strip().lower()
    for k, v in rep.items():
        s2 = s2.replace(k, v)
    # collapse spaces
    while "  " in s2:
        s2 = s2.replace("  ", " ")
    return s2


def safe_phash(img_path: Path) -> Optional[int]:
    """Compute 64-bit pHash for an image path, return as python int (or None if missing)."""
    try:
        with Image.open(img_path) as im:
            ph = imagehash.phash(im)  # 64-bit by default
            return int(str(ph), 16)  # store hex→int for portability
    except Exception:
        return None


def hamming_similarity64(a_int: Optional[int], b_int: Optional[int]) -> float:
    """Return similarity in [0,1] from two 64-bit pHash integers. If any None, return 0."""
    if a_int is None or b_int is None:
        return 0.0
    # hamming distance of 64-bit integers
    # Python 3.8+: use int.bit_count()
    dist = (a_int ^ b_int).bit_count()
    return 1.0 - (dist / 64.0)


# -----------------------------
# Image similarity (template+hash+hist) with lightweight cache
# -----------------------------

# Shared template-matching options to keep local and remote behavior aligned
_TM_OPTIONS: Dict[str, float] = {
    "tm_weight": 0.48,      # Reduced: edge-based TM no longer dominates
    "hash_weight": 0.17,     # Mild pHash contribution
    "hist_weight": 0.35,     # Boosted: HSV color now critical for discrimination
    "tm_edge_weight": 0.25,  # Edge contribution within TM
    "ms_min_scale": 0.90,    # Tighter scale range to prevent spurious matches
    "ms_max_scale": 1.10,
    "ms_steps": 12,
}

_CV_TEMPLATE_TEXT_THRESHOLD = 0.9
_CV_TEMPLATE_MIN = 1
_CV_TEMPLATE_MAX = 100

# HSV hair-focused histogram configuration
_HSV_H_BINS = 32
_HSV_S_BINS = 32
_HSV_RANGES = [0, 180, 0, 256]  # OpenCV HSV: H in [0,180], S in [0,255]
_hsv_hist_cache: Dict[str, Any] = {}

try:
    _portrait_matcher: Optional[TemplateMatcherBase] = TemplateMatcherBase(
        tm_weight=_TM_OPTIONS["tm_weight"],
        hash_weight=_TM_OPTIONS["hash_weight"],
        hist_weight=_TM_OPTIONS["hist_weight"],
        tm_edge_weight=_TM_OPTIONS["tm_edge_weight"],
        ms_min_scale=_TM_OPTIONS["ms_min_scale"],
        ms_max_scale=_TM_OPTIONS["ms_max_scale"],
        ms_steps=int(_TM_OPTIONS["ms_steps"]),
    )
except Exception:  # OpenCV may be unavailable in some environments
    _portrait_matcher = None

_tmpl_cache: Dict[str, Any] = {}
_template_b64_cache: Dict[str, str] = {}

ImageEntry = Tuple[Optional[str], Optional[int], Tuple[str, ...]]


def _to_bgr(img_pil: PILImage) -> Any:
    """Convert PIL Image to OpenCV BGR array."""
    if not _CV2_AVAILABLE:
        return None
    arr = np.array(img_pil.convert("RGB"))
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def _gray_world_white_balance(bgr: Any) -> Any:
    """Simple white balance: scale channels so their means are equal."""
    if bgr is None or not _CV2_AVAILABLE:
        return bgr
    eps = 1e-6
    means = bgr.reshape(-1, 3).mean(axis=0) + eps
    scale = means.mean() / means
    balanced = np.clip(bgr * scale, 0, 255).astype(np.uint8)
    return balanced


def _hair_mask_from_hsv(hsv: Any) -> Any:
    """Create mask focused on hair region (upper 60%, chroma >= 40, V in [35,235])."""
    if hsv is None or not _CV2_AVAILABLE:
        return None
    H, W = hsv.shape[:2]
    s = hsv[:, :, 1]
    v = hsv[:, :, 2]

    sat_mask = (s >= 40).astype(np.uint8)  # Keep chromatic pixels
    v_mask = ((v >= 35) & (v <= 235)).astype(np.uint8)  # Exclude pure black/white

    # Coarse hair ROI: top 60%, 5% horizontal margins
    y0, y1 = int(0.05 * H), int(0.60 * H)
    x0, x1 = int(0.05 * W), int(0.95 * W)
    roi = np.zeros((H, W), np.uint8)
    roi[y0:y1, x0:x1] = 1

    mask = (sat_mask & v_mask & roi).astype(np.uint8) * 255
    # Clean speckles with morphology
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    return mask


def _hsv_hs_hist(bgr: Any, mask: Any) -> Any:
    """Compute normalized 2D HSV histogram over H×S channels."""
    if bgr is None or mask is None or not _CV2_AVAILABLE:
        return None
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], mask, [_HSV_H_BINS, _HSV_S_BINS], _HSV_RANGES)
    cv2.normalize(hist, hist, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)
    return hist.astype(np.float32)


def _hsv_similarity(portrait_img: PILImage, template_path: Optional[str]) -> Optional[float]:
    """Compute HSV hair-focused histogram similarity using Bhattacharyya distance.
    
    Returns similarity in [0,1], or None if unavailable/error.
    Focus on hair region with color discrimination (H×S) to distinguish look-alike portraits.
    """
    if not _CV2_AVAILABLE or template_path is None:
        return None
    try:
        # Process portrait (query)
        p_bgr = _to_bgr(portrait_img)
        if p_bgr is None:
            return None
        p_bgr = _gray_world_white_balance(p_bgr)
        p_hsv = cv2.cvtColor(p_bgr, cv2.COLOR_BGR2HSV)
        p_mask = _hair_mask_from_hsv(p_hsv)
        if p_mask is None:
            return None
        p_hist = _hsv_hs_hist(p_bgr, p_mask)
        if p_hist is None:
            return None

        # Process template (cached)
        tpl_hist = _hsv_hist_cache.get(template_path)
        if tpl_hist is None:
            with Image.open(template_path) as im:
                t_bgr = _to_bgr(im)
            if t_bgr is None:
                return None
            t_bgr = _gray_world_white_balance(t_bgr)
            t_hsv = cv2.cvtColor(t_bgr, cv2.COLOR_BGR2HSV)
            t_mask = _hair_mask_from_hsv(t_hsv)
            if t_mask is None:
                return None
            tpl_hist = _hsv_hs_hist(t_bgr, t_mask)
            if tpl_hist is not None:
                _hsv_hist_cache[template_path] = tpl_hist

        if tpl_hist is None:
            return None

        # Bhattacharyya distance → similarity
        dist = cv2.compareHist(p_hist, tpl_hist, cv2.HISTCMP_BHATTACHARYYA)
        sim = 1.0 - float(np.clip(dist, 0.0, 1.0))
        return sim
    except Exception:
        return None


def _cv_image_similarity(portrait_img: PILImage, template_path: Optional[str]) -> Optional[float]:
    """Return fused similarity in [0,1] using TemplateMatcherBase + HSV hair-focused histogram.
    
    Uses HSV histogram (hair-focused) instead of plain RGB histogram for better color discrimination.
    """
    # When remote processing is enabled, skip local CV entirely.
    if Settings.USE_EXTERNAL_PROCESSOR:
        return None
    if _portrait_matcher is None or not template_path:
        return None
    try:
        # Cache prepared template per path
        tmpl = _tmpl_cache.get(template_path)
        if tmpl is None:
            entry = TemplateEntry(name=str(template_path), path=str(template_path))
            prepared = _portrait_matcher.prepare_templates([entry])
            tmpl = prepared[0] if prepared else None
            if tmpl is not None:
                _tmpl_cache[template_path] = tmpl
        if tmpl is None:
            return None

        region = _portrait_matcher._prepare_region(portrait_img)
        tm_sc = _portrait_matcher._template_score(
            region.gray, region.edges, tmpl.gray, tmpl.edges, region.shape, tmpl.mask
        )
        hash_sc = _portrait_matcher._hash_score(region.hash, tmpl.hash)

        # Use HSV hair-focused histogram for color discrimination
        hsv_sc = _hsv_similarity(portrait_img, template_path)
        if hsv_sc is None:
            # Fallback to original histogram if HSV fails
            hist_sc = _portrait_matcher._hist_compare(region.hist, tmpl.hist)
        else:
            hist_sc = hsv_sc

        final = (
            _portrait_matcher.tm_weight * tm_sc
            + _portrait_matcher.hash_weight * hash_sc
            + _portrait_matcher.hist_weight * hist_sc
        )
        # Clamp to [0,1] conservatively
        # if tmpl.name.lower().__contains__("silence") or tmpl.name.lower().__contains__("grass"):
        #     pass
        return float(max(0.0, min(1.0, final)))
    except Exception:
        return None


def _cv_image_similarity_variant(
    portrait_img: PILImage, template_paths: Sequence[str]
) -> Optional[float]:
    if not template_paths:
        return None
    scores: List[float] = []
    for path in template_paths:
        score = _cv_image_similarity(portrait_img, path)
        if score is not None:
            scores.append(float(score))
    if scores:
        return max(scores)
    return None


def _public_path_from_image_path(image_path: Optional[str]) -> Optional[str]:
    if not image_path:
        return None
    p = str(image_path).replace("\\", "/")
    # Expect paths like 'web/public/events/trainee_icon_event/Name.png'
    marker = "web/public/"
    idx = p.find(marker)
    if idx >= 0:
        rel = p[idx + len(marker) :]
    else:
        # Already relative?
        rel = p.lstrip("/")
    return rel or None


def _list_trainee_variant_paths(name: str) -> List[Path]:
    base_dir = ASSETS_EVENTS_DIR / "trainee_icon_event"
    exts = (".png", ".jpg", ".jpeg", ".webp")
    candidates: List[Path] = []

    variant_dir = base_dir / name
    if variant_dir.is_dir():
        for ext in exts:
            for path in sorted(variant_dir.glob(f"*{ext}")):
                if path.is_file():
                    candidates.append(path)

    # Include flat files (legacy layouts)
    for ext in exts:
        direct = base_dir / f"{name}{ext}"
        if direct.exists():
            candidates.append(direct)
    for ext in exts:
        for path in sorted(base_dir.glob(f"{name}_*{ext}")):
            if path.is_file():
                candidates.append(path)

    seen: Set[str] = set()
    unique: List[Path] = []
    for path in candidates:
        key = str(path.resolve())
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _template_spec_for_remote(
    rec: EventRecord,
    variant_paths: Optional[Sequence[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Build template descriptor for remote matching. Returns None if no valid image data."""
    variant_paths = list(variant_paths or [])
    if not variant_paths:
        image_path = rec.image_path
        if image_path:
            variant_paths = [image_path]
        else:
            return None

    public_path = _public_path_from_image_path(variant_paths[0])
    if not public_path:
        return None

    payload: Dict[str, Any] = {
        "id": rec.key,
        "public_path": f"/{public_path}",
        "metadata": {
            "name": rec.name,
            "public_path": f"/{public_path}",
        },
    }

    if rec.phash64 is not None:
        payload["hash_hex"] = f"{int(rec.phash64) & ((1 << 64) - 1):016x}"

    encoded_images: List[str] = []
    for image_path in variant_paths[:10]:
        cached = _template_b64_cache.get(image_path)
        if cached is None:
            try:
                with Image.open(image_path) as img:
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    cached = base64.b64encode(buf.getvalue()).decode("ascii")
                    _template_b64_cache[image_path] = cached
            except Exception:
                cached = None
        if cached:
            encoded_images.append(cached)

    if not encoded_images:
        return None

    payload["metadata"]["variant_count"] = len(encoded_images)
    payload["metadata"]["variant_paths"] = [
        _public_path_from_image_path(path) for path in variant_paths
    ]
    if len(encoded_images) == 1:
        payload["img"] = encoded_images[0]
    else:
        payload["variant_imgs"] = encoded_images

    return payload


def _title_similarity(q_title_norm: str, rec: EventRecord) -> float:
    if not q_title_norm:
        return 0.0
    ts_token = fuzz.token_set_ratio(q_title_norm, rec.title_norm) / 100.0
    ts_ratio = fuzz.ratio(q_title_norm, rec.title_norm) / 100.0
    ts_partial = fuzz.partial_ratio(q_title_norm, rec.title_norm) / 100.0
    text_sim = 0.5 * ts_token + 0.3 * ts_ratio + 0.2 * ts_partial
    if q_title_norm == rec.title_norm:
        text_sim = 1.0
    return float(text_sim)


def _select_cv_candidates(q: "Query", pool: List[EventRecord]) -> List[EventRecord]:
    q_title_norm = normalize_text(q.ocr_title)
    if not q_title_norm:
        return pool

    scored: List[Tuple[EventRecord, float]] = []
    for rec in pool:
        sim = _title_similarity(q_title_norm, rec)
        if sim > 0.0:
            scored.append((rec, sim))

    if not scored:
        return pool

    scored.sort(key=lambda item: item[1], reverse=True)
    filtered = [rec for rec, sim in scored if sim >= _CV_TEMPLATE_TEXT_THRESHOLD]

    if len(filtered) < _CV_TEMPLATE_MIN:
        logger_uma.warning("Not enough candidates for %s. filtering with half of the Threshold", q_title_norm)
        
        filtered = [rec for rec, sim in scored if sim >= _CV_TEMPLATE_TEXT_THRESHOLD/2]

        if len(filtered) < _CV_TEMPLATE_MIN:
            logger_uma.warning("Fallback to top %d candidates", _CV_TEMPLATE_MIN)
            filtered = [rec for rec, _ in scored[:_CV_TEMPLATE_MAX]]
    else:
        filtered = filtered[:_CV_TEMPLATE_MAX]

    return filtered or pool


def find_event_image_path(
    ev_type: str, name: str, rarity: str, attribute: str
) -> Optional[Path]:
    """
    Find an image under assets/events/{ev_type}/<name>_<attribute>_<rarity>.(png|jpg|jpeg|webp).
    ev_type is one of: support|trainee|scenario
    """
    folder = ASSETS_EVENTS_DIR / ev_type
    exts = (".png", ".jpg", ".jpeg", ".webp")

    if ev_type == "trainee_icon_event":
        variants = _list_trainee_variant_paths(name)
        if variants:
            return variants[0]

    attr = (attribute or "None").strip()
    rar = (rarity or "None").strip()
    attr_up = attr.upper()

    candidates: List[str] = []

    # Primary (new convention): <name>_<ATTRIBUTE>_<rarity>
    if attr.lower() not in ("none", "null") and rar.lower() not in ("none", "null"):
        candidates.append(f"{name}_{attr_up}_{rar}")

    # Variants in case a pack is missing one dimension:
    #  - <name>_<ATTRIBUTE>
    if attr.lower() not in ("none", "null"):
        candidates.append(f"{name}_{attr_up}")
    #  - <name>_<rarity>
    if rar.lower() not in ("none", "null"):
        candidates.append(f"{name}_{rar}")

    # Legacy: just <name>
    candidates.append(name)

    if ev_type == "trainee":
        candidates.insert(0, f"{name}")

    for base in candidates:
        for ext in exts:
            p = folder / f"{base}{ext}"
            if p.exists():
                return p
    return None


# -----------------------------
# Data structures
# -----------------------------


@dataclass
class EventRecord:
    # Stable key: "type/name/rarity/event_name"
    key: str
    key_step: str  # new: step-aware key → ".../event_name#s<step>"
    type: str  # support|trainee|scenario
    name: str  # e.g., "Kitasan Black", "Vodka", "Ura Finale", or "general"
    rarity: str  # e.g., "SSR", "SR", "R", "None"
    attribute: str  # e.g., "SPD", "STA", "PWR", "GUTS", "WIT", "None"
    event_name: str  # e.g., "Paying It Forward"
    chain_step: Optional[int]
    default_preference: Optional[int]  # which option number to pick by default
    options: Dict[str, List[Dict]]  # as-is from JSON (stringified keys for safety)
    title_norm: str  # normalized event_name for fast match
    image_path: Optional[str]  # representative icon path (per name+rarity)
    phash64: Optional[int]  # 64-bit pHash int
    image_variants: Tuple[str, ...] = ()
    # Optional: per-event kind from raw dataset (e.g., 'date', 'chain', 'random')
    event_kind: Optional[str] = None

    @staticmethod
    def from_json_item(
        parent: Dict,
        ev_item: Dict,
        phash_map: Dict[Tuple[str, ...], "ImageEntry"],
    ) -> "EventRecord":
        ev_type = parent.get("type", "")
        name = parent.get("name", "")
        rarity = parent.get("rarity", "None") or "None"
        ev_name = ev_item.get("name", "")
        attribute = parent.get("attribute", "None")
        chain_step = ev_item.get("chain_step", None)
        default_pref = ev_item.get("default_preference", None)
        options = ev_item.get("options", {})
        event_kind = ev_item.get("type")

        # Make options keys strings for JSON stability
        options_str_keys = {str(k): v for k, v in options.items()}

        title_norm = normalize_text(ev_name)
        key = f"{ev_type}/{name}/{attribute}/{rarity}/{ev_name}"

        img_path, phash, variants = phash_map.get(
            (ev_type, name, rarity, attribute), (None, None, ())
        )
        variants_tuple: Tuple[str, ...]
        if variants:
            variants_tuple = tuple(str(v) for v in variants)
        elif img_path:
            variants_tuple = (str(img_path),)
        else:
            variants_tuple = ()
        return EventRecord(
            key=key,
            key_step=f"{key}#s{chain_step if chain_step is not None else 1}",
            type=ev_type,
            name=name,
            rarity=rarity,
            attribute=attribute,
            event_name=ev_name,
            chain_step=chain_step,
            default_preference=default_pref,
            options=options_str_keys,
            title_norm=title_norm,
            image_path=(str(img_path) if img_path else None),
            phash64=phash,
            image_variants=variants_tuple,
            event_kind=event_kind,
        )


# -----------------------------
# Build step (offline, local)
# -----------------------------


def build_catalog() -> None:
    """
    Parse datasets/in_game/events.json, compute representative image pHashes once per (type,name,rarity,attribute),
    and produce datasets/in_game/event_catalog.json with one row per *event* (choice_event).
    """
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    if not DATASETS_EVENTS.exists():
        raise FileNotFoundError(f"Missing {DATASETS_EVENTS}")

    with DATASETS_EVENTS.open("r", encoding="utf-8") as f:
        root = json.load(f)

    # Precompute phash for each (type,name,rarity)
    set_data_to_file_and_phash: Dict[Tuple[str, str, str, str], ImageEntry] = {}
    seen_set_data = set()

    for parent in root:
        ev_type = parent.get("type", "")
        name = parent.get("name", "")
        rarity = parent.get("rarity", "None") or "None"
        attribute = parent.get("attribute", "None")
        set_data = (ev_type, name, rarity, attribute)
        if set_data in seen_set_data:
            continue
        seen_set_data.add(set_data)

        variants: Tuple[str, ...] = ()
        if ev_type == "trainee" and normalize_text(name) != "general":
            variant_paths = _list_trainee_variant_paths(name)
            variant_hashes: List[int] = []
            variant_paths_str: List[str] = []
            for path in variant_paths:
                ph = safe_phash(path)
                if ph is not None:
                    variant_hashes.append(ph)
                variant_paths_str.append(str(path))
            img_path = variant_paths[0] if variant_paths else find_event_image_path("trainee_icon_event", name, rarity, attribute)
            combined_hash = None
            if variant_hashes:
                combined_hash = sum(variant_hashes) // len(variant_hashes)
            phash = combined_hash if combined_hash is not None else (safe_phash(img_path) if img_path else None)
            variants = tuple(variant_paths_str)
        else:
            img_path = find_event_image_path(ev_type, name, rarity, attribute)
            phash = safe_phash(img_path) if img_path else None

        set_data_to_file_and_phash[set_data] = (
            str(img_path) if img_path else None,
            phash,
            variants,
        )

    # Expand to event records
    records: List[EventRecord] = []
    for parent in root:
        choice_events = parent.get("choice_events", []) or []
        for ev in choice_events:
            # Skip events with only one outcome
            if len(ev.get("options", {})) <= 1:
                continue
            rec = EventRecord.from_json_item(parent, ev, set_data_to_file_and_phash)
            records.append(rec)

    # Save compact JSON for runtime
    payload = [asdict(r) for r in records]
    with CATALOG_JSON.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"[build_catalog] Wrote {len(records)} events -> {CATALOG_JSON}")


# -----------------------------
# Preferences (overrides)
# -----------------------------


def _generalize_trainee_key(key: str) -> Optional[str]:
    """Map a trainee-specific override key (with optional step suffix) to its general equivalent."""
    try:
        base, sep, step = key.partition("#")
        parts = base.split("/")
        if len(parts) < 5:
            return None
        typ = str(parts[0]).strip().lower()
        name = str(parts[1]).strip().lower()
        attribute = str(parts[2]).strip().lower()
        rarity = str(parts[3]).strip().lower()
        if typ != "trainee":
            return None
        if name in {"general", "", "none", "null"}:
            return None
        if attribute not in {"none", "", "null"}:
            return None
        if rarity not in {"none", "", "null"}:
            return None
        generalized_parts = [
            "trainee",
            "general",
            "None",
            "None",
        ] + parts[4:]
        generalized = "/".join(generalized_parts)
        if sep:
            generalized += sep + step
        return generalized
    except Exception:
        return None


def _build_alias_overrides(overrides: Dict[str, int]) -> Dict[str, int]:
    alias_overrides: Dict[str, int] = {}
    for key, pick in overrides.items():
        alias = _generalize_trainee_key(key)
        if not alias:
            continue
        base_alias, sep, step = alias.partition("#")

        def _store(candidate: str) -> None:
            if not candidate:
                return
            if candidate in overrides or candidate in alias_overrides:
                return
            alias_overrides[candidate] = pick
            logger_uma.debug(
                "[event_prefs] alias override mapped %s → %s (pick=%s)",
                candidate,
                key,
                pick,
            )

        _store(alias)
        _store(base_alias)
        if sep != "#" and base_alias:
            _store(f"{base_alias}#s1")
        elif sep == "#" and base_alias and step and not step.lower().startswith("s"):
            _store(f"{base_alias}#s{step}")
    return alias_overrides


def _match_specific_trainee_override(
    overrides: Dict[str, int], rec: "EventRecord"
) -> Optional[int]:
    target_name = normalize_text(rec.event_name)
    target_step = rec.chain_step or 1
    for key, pick in overrides.items():
        base, _, step = key.partition("#")
        parts = base.split("/")
        if len(parts) < 5:
            continue
        if parts[0].strip().lower() != "trainee":
            continue
        trainee_name = parts[1].strip().lower()
        if trainee_name in {"general", "", "none", "null"}:
            continue
        attribute = parts[2].strip().lower()
        rarity = parts[3].strip().lower()
        if attribute not in {"none", "", "null"}:
            continue
        if rarity not in {"none", "", "null"}:
            continue
        override_event = normalize_text(parts[4])
        if override_event != target_name:
            continue
        step_norm = step.strip().lower()
        step_idx: Optional[int]
        if not step_norm:
            step_idx = 1
        elif step_norm.startswith("s"):
            try:
                step_idx = int(step_norm[1:])
            except ValueError:
                step_idx = None
        else:
            try:
                step_idx = int(step_norm)
            except ValueError:
                step_idx = None
        if step_idx is not None and step_idx != target_step:
            continue
        return int(pick)
    return None


@dataclass
class UserPrefs:
    # exact key → option_number
    overrides: Dict[str, int]
    # wildcard patterns (fnmatch) checked in order
    patterns: List[Tuple[str, int]]
    # fallback per type if nothing else found
    default_by_type: Dict[str, int]
    # alias keys (e.g., trainee/general) derived from overrides
    alias_overrides: Dict[str, int] = field(default_factory=dict)
    # default toggle for avoiding energy overflow rotations
    avoid_energy_overflow: bool = True
    # per-support override for energy overflow avoidance
    avoid_energy_overflow_by_support: Dict[Tuple[str, str, str], bool] = field(
        default_factory=dict
    )
    # per-scenario override for energy overflow avoidance
    avoid_energy_overflow_by_scenario: Dict[str, bool] = field(default_factory=dict)
    # per-trainee override for energy overflow avoidance
    avoid_energy_overflow_by_trainee: Dict[str, bool] = field(default_factory=dict)
    # ranked reward preference used when avoiding energy overflow
    reward_priority: List[str] = field(default_factory=lambda: list(DEFAULT_REWARD_PRIORITY))
    # per-entity reward priority overrides
    reward_priority_by_support: Dict[Tuple[str, str, str], List[str]] = field(
        default_factory=dict
    )
    reward_priority_by_scenario: Dict[str, List[str]] = field(default_factory=dict)
    reward_priority_by_trainee: Dict[str, List[str]] = field(default_factory=dict)
    # Preferred trainee name from config (for portrait disambiguation)
    preferred_trainee_name: Optional[str] = None
    weak_turn_sv: Optional[int] = None
    race_precheck_sv: Optional[int] = None
    lobby_precheck_enable: Optional[bool] = None
    junior_minimal_mood: Optional[int] = None
    goal_race_force_turns: Optional[int] = None

    @staticmethod
    def load(path: Path) -> "UserPrefs":
        if not path.exists():
            # sensible defaults
            return UserPrefs(
                overrides={},
                patterns=[],
                default_by_type={"support": 1, "trainee": 1, "scenario": 1},
                avoid_energy_overflow=True,
                avoid_energy_overflow_by_support={},
                weak_turn_sv=None,
                race_precheck_sv=None,
                lobby_precheck_enable=None,
                junior_minimal_mood=None,
                goal_race_force_turns=None,
            )
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        overrides = raw.get("overrides", {}) or {}
        # patterns as ordered list; dict order is preserved in modern Python, but accept both
        patt_src = raw.get("patterns", [])
        if isinstance(patt_src, dict):
            patterns = list(patt_src.items())
        else:
            # expect list of {"pattern": "...", "pick": 2}
            patterns = [(d.get("pattern", ""), int(d.get("pick", 1))) for d in patt_src]
        default_by_type = raw.get("defaults", {})
        if not default_by_type:
            default_by_type = {"support": 1, "trainee": 1, "scenario": 1}
        alias_overrides = _build_alias_overrides(overrides)

        avoid_energy_overflow = _coerce_bool(
            raw.get("avoidEnergyOverflow")
            or raw.get("avoid_energy_overflow", True),
            default=True,
        )

        reward_priority = normalize_reward_priority_list(
            raw.get("rewardPriority") or raw.get("reward_priority")
        )

        weak_turn_sv = raw.get("weakTurnSv")
        race_precheck_sv = raw.get("racePrecheckSv")
        lobby_precheck_enable = _coerce_bool(raw.get("lobbyPrecheckEnable"), default=None)
        junior_minimal_mood = raw.get("juniorMinimalMood")
        goal_race_force_turns = raw.get("goalRaceForceTurns")

        avoid_energy_by_support: Dict[Tuple[str, str, str], bool] = {}
        reward_priority_by_support: Dict[Tuple[str, str, str], List[str]] = {}
        supports = raw.get("supports", []) or []
        if isinstance(supports, list):
            for entry in supports:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name")
                rarity = entry.get("rarity")
                attribute = entry.get("attribute")
                if not (name and rarity and attribute):
                    continue
                raw_flag = entry.get("avoidEnergyOverflow")
                if raw_flag is None:
                    raw_flag = entry.get("avoid_energy_overflow")
                flag = _coerce_bool(raw_flag, default=True)
                key = _support_key(name, attribute, rarity)
                avoid_energy_by_support[key] = flag

                raw_priority = entry.get("rewardPriority")
                if raw_priority is None:
                    raw_priority = entry.get("reward_priority")
                if raw_priority is not None:
                    priority_list = normalize_reward_priority_list(raw_priority)
                    reward_priority_by_support[key] = priority_list

        scenario_flags: Dict[str, bool] = {}
        reward_priority_by_scenario: Dict[str, List[str]] = {}
        scenario_entry = raw.get("scenario")
        if isinstance(scenario_entry, dict):
            name = scenario_entry.get("name")
            if name:
                raw_flag = scenario_entry.get("avoidEnergyOverflow")
                if raw_flag is None:
                    raw_flag = scenario_entry.get("avoid_energy_overflow")
                scen_key = _scenario_key(str(name))
                scenario_flags[scen_key] = _coerce_bool(raw_flag, default=True)

                raw_priority = scenario_entry.get("rewardPriority")
                if raw_priority is None:
                    raw_priority = scenario_entry.get("reward_priority")
                if raw_priority is not None:
                    reward_priority_by_scenario[scen_key] = normalize_reward_priority_list(
                        raw_priority
                    )

        trainee_flags: Dict[str, bool] = {}
        reward_priority_by_trainee: Dict[str, List[str]] = {}
        preferred_trainee_name: Optional[str] = None
        trainee_entry = raw.get("trainee")
        if isinstance(trainee_entry, dict):
            name = trainee_entry.get("name")
            if name:
                preferred_trainee_name = str(name).strip()
                raw_flag = trainee_entry.get("avoidEnergyOverflow")
                if raw_flag is None:
                    raw_flag = trainee_entry.get("avoid_energy_overflow")
                trainee_key = _trainee_key(str(name))
                trainee_flags[trainee_key] = _coerce_bool(raw_flag, default=True)

                raw_priority = trainee_entry.get("rewardPriority")
                if raw_priority is None:
                    raw_priority = trainee_entry.get("reward_priority")
                if raw_priority is not None:
                    reward_priority_by_trainee[trainee_key] = normalize_reward_priority_list(
                        raw_priority
                    )

        return UserPrefs(
            overrides=overrides,
            patterns=patterns,
            default_by_type=default_by_type,
            alias_overrides=alias_overrides,
            avoid_energy_overflow=avoid_energy_overflow,
            avoid_energy_overflow_by_support=avoid_energy_by_support,
            avoid_energy_overflow_by_scenario=scenario_flags,
            avoid_energy_overflow_by_trainee=trainee_flags,
            reward_priority=reward_priority,
            reward_priority_by_support=reward_priority_by_support,
            reward_priority_by_scenario=reward_priority_by_scenario,
            reward_priority_by_trainee=reward_priority_by_trainee,
            preferred_trainee_name=preferred_trainee_name,
            weak_turn_sv=weak_turn_sv,
            race_precheck_sv=race_precheck_sv,
            lobby_precheck_enable=lobby_precheck_enable,
            junior_minimal_mood=junior_minimal_mood,
            goal_race_force_turns=goal_race_force_turns,
        )

    # ---- build UserPrefs from the active preset inside config.json ----
    @staticmethod
    def from_config(cfg: dict | None) -> "UserPrefs":
        """
        Pull event prefs from config['scenarios'][active]['presets'][active]['event_setup']['prefs'].
        Falls back to legacy config['presets'] structure for backwards compatibility.
        If anything is missing or malformed, we return sensible defaults.
        """
        cfg = cfg or {}
        _, _, preset = Settings._get_active_preset_from_config(cfg)
        if not preset:
            # no presets at all
            return UserPrefs(
                overrides={},
                patterns=[],
                default_by_type={"support": 1, "trainee": 1, "scenario": 1},
                avoid_energy_overflow=True,
                avoid_energy_overflow_by_support={},
                weak_turn_sv=None,
                race_precheck_sv=None,
                lobby_precheck_enable=None,
                junior_minimal_mood=None,
                goal_race_force_turns=None,
            )

        setup = preset.get("event_setup") or {}
        prefs = setup.get("prefs") or {}

        # --- overrides ---
        overrides_in = prefs.get("overrides", {}) or {}
        overrides: Dict[str, int] = {}
        if isinstance(overrides_in, dict):
            for k, v in overrides_in.items():
                try:
                    n = int(v)
                    if n >= 1:  # don't accept 0/negatives
                        overrides[str(k)] = n
                except Exception:
                    # ignore malformed values
                    continue

        alias_overrides = _build_alias_overrides(overrides)

        # --- patterns ---
        patt_src = prefs.get("patterns", []) or []
        patterns: List[Tuple[str, int]] = []
        if isinstance(patt_src, dict):
            # tolerate dict form as well
            for pat, pick in patt_src.items():
                try:
                    patterns.append((str(pat), int(pick)))
                except Exception:
                    continue
        else:
            # expect list of {"pattern": "...", "pick": 2}
            for item in patt_src:
                if not isinstance(item, dict):
                    continue
                pat = str(item.get("pattern", "")).strip()
                if not pat:
                    continue
                try:
                    pick = int(item.get("pick", 1))
                except Exception:
                    pick = 1
                patterns.append((pat, pick if pick >= 1 else 1))

        # --- defaults ---
        d = prefs.get("defaults", {}) or {}
        default_by_type = {
            "support": int(d.get("support", 1) or 1),
            "trainee": int(d.get("trainee", 1) or 1),
            "scenario": int(d.get("scenario", 1) or 1),
        }
        raw_toggle = prefs.get("avoidEnergyOverflow")
        if raw_toggle is None:
            raw_toggle = prefs.get("avoid_energy_overflow")
        avoid_energy_overflow = _coerce_bool(raw_toggle, default=True)

        reward_priority = normalize_reward_priority_list(
            prefs.get("rewardPriority") or prefs.get("reward_priority")
        )

        avoid_energy_by_support: Dict[Tuple[str, str, str], bool] = {}
        reward_priority_by_support: Dict[Tuple[str, str, str], List[str]] = {}
        supports = setup.get("supports", []) or []
        if isinstance(supports, list):
            for entry in supports:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name")
                rarity = entry.get("rarity")
                attribute = entry.get("attribute")
                if not (name and rarity and attribute):
                    continue
                raw_flag = entry.get("avoidEnergyOverflow")
                if raw_flag is None:
                    raw_flag = entry.get("avoid_energy_overflow")
                flag = _coerce_bool(raw_flag, default=True)
                key = _support_key(name, attribute, rarity)
                avoid_energy_by_support[key] = flag

                raw_priority = entry.get("rewardPriority")
                if raw_priority is None:
                    raw_priority = entry.get("reward_priority")
                if raw_priority is not None:
                    priority_list = normalize_reward_priority_list(raw_priority)
                    reward_priority_by_support[key] = priority_list

        scenario_flags: Dict[str, bool] = {}
        reward_priority_by_scenario: Dict[str, List[str]] = {}
        scenario_entry = setup.get("scenario")
        if isinstance(scenario_entry, dict):
            name = scenario_entry.get("name")
            if name:
                raw_flag = scenario_entry.get("avoidEnergyOverflow")
                if raw_flag is None:
                    raw_flag = scenario_entry.get("avoid_energy_overflow")
                scen_key = _scenario_key(str(name))
                scenario_flags[scen_key] = _coerce_bool(raw_flag, default=True)

                raw_priority = scenario_entry.get("rewardPriority")
                if raw_priority is None:
                    raw_priority = scenario_entry.get("reward_priority")
                if raw_priority is not None:
                    reward_priority_by_scenario[scen_key] = normalize_reward_priority_list(
                        raw_priority
                    )

        trainee_flags: Dict[str, bool] = {}
        reward_priority_by_trainee: Dict[str, List[str]] = {}
        preferred_trainee_name: Optional[str] = None
        trainee_entry = setup.get("trainee")
        if isinstance(trainee_entry, dict):
            name = trainee_entry.get("name")
            if name:
                preferred_trainee_name = str(name).strip()
                raw_flag = trainee_entry.get("avoidEnergyOverflow")
                if raw_flag is None:
                    raw_flag = trainee_entry.get("avoid_energy_overflow")
                trainee_key = _trainee_key(str(name))
                trainee_flags[trainee_key] = _coerce_bool(raw_flag, default=True)

                raw_priority = trainee_entry.get("rewardPriority")
                if raw_priority is None:
                    raw_priority = trainee_entry.get("reward_priority")
                if raw_priority is not None:
                    reward_priority_by_trainee[trainee_key] = normalize_reward_priority_list(
                        raw_priority
                    )

        return UserPrefs(
            overrides=overrides,
            patterns=patterns,
            default_by_type=default_by_type,
            alias_overrides=alias_overrides,
            avoid_energy_overflow=avoid_energy_overflow,
            avoid_energy_overflow_by_support=avoid_energy_by_support,
            avoid_energy_overflow_by_scenario=scenario_flags,
            avoid_energy_overflow_by_trainee=trainee_flags,
            reward_priority=reward_priority,
            reward_priority_by_support=reward_priority_by_support,
            reward_priority_by_scenario=reward_priority_by_scenario,
            reward_priority_by_trainee=reward_priority_by_trainee,
            preferred_trainee_name=preferred_trainee_name,
        )

    def reward_priority_for(self, rec: EventRecord) -> List[str]:
        if rec.type == "support":
            key = _support_key(rec.name, rec.attribute, rec.rarity)
            priority = self.reward_priority_by_support.get(key)
            if priority:
                return list(priority)
        elif rec.type == "scenario":
            key = _scenario_key(rec.name)
            priority = self.reward_priority_by_scenario.get(key)
            if priority:
                return list(priority)
        elif rec.type == "trainee":
            key = _trainee_key(rec.name)
            priority = self.reward_priority_by_trainee.get(key)
            if priority:
                return list(priority)
        return list(self.reward_priority)

    def should_avoid_energy(self, rec: EventRecord) -> bool:
        if rec.type == "support":
            key = _support_key(rec.name, rec.attribute, rec.rarity)
            if key in self.avoid_energy_overflow_by_support:
                return self.avoid_energy_overflow_by_support[key]
        elif rec.type == "scenario":
            key = _scenario_key(rec.name)
            if key in self.avoid_energy_overflow_by_scenario:
                return self.avoid_energy_overflow_by_scenario[key]
        elif rec.type == "trainee":
            key = _trainee_key(rec.name)
            if key in self.avoid_energy_overflow_by_trainee:
                return self.avoid_energy_overflow_by_trainee[key]
        return self.avoid_energy_overflow

    def pick_for(self, rec: EventRecord) -> int:
        """
        Resolve preference in this order:
        1) exact override (step-aware key "#s<step>")
        2) exact override (legacy key without step)
        3) first matching wildcard pattern (try step-aware then legacy)
        4) event.default_preference (from DB)
        5) type default
        """
        # 1) exact (step-aware)
        if rec.key_step in self.overrides:
            return int(self.overrides[rec.key_step])
        # 2) exact (legacy)
        if rec.key in self.overrides:
            return int(self.overrides[rec.key])
        # alias (trainee → general)
        if not self.alias_overrides and self.overrides:
            self.alias_overrides = _build_alias_overrides(self.overrides)
        if rec.key_step in self.alias_overrides:
            return int(self.alias_overrides[rec.key_step])
        if rec.key in self.alias_overrides:
            return int(self.alias_overrides[rec.key])
        if rec.type == "trainee" and normalize_text(rec.name) == "general":
            specific_pick = _match_specific_trainee_override(self.overrides, rec)
            if specific_pick is not None:
                return int(specific_pick)
        # 3) wildcard patterns
        for patt, pick in self.patterns:
            if fnmatch.fnmatch(rec.key_step, patt):
                return int(pick)
            if fnmatch.fnmatch(rec.key, patt):
                return int(pick)

        # 4) event default_preference
        if rec.default_preference is not None:
            return int(rec.default_preference)

        # 5) type default
        return int(self.default_by_type.get(rec.type, 1))


# -----------------------------
# Runtime: load catalog
# -----------------------------


@dataclass
class Catalog:
    records: List[EventRecord]

    @staticmethod
    def load(path: Path = CATALOG_JSON) -> "Catalog":
        if not path.exists():
            raise FileNotFoundError(
                f"Missing catalog {path}. Run build_catalog() first."
            )
        with path.open("r", encoding="utf-8") as f:
            rows = json.load(f)
        recs = [EventRecord(**row) for row in rows]
        return Catalog(records=recs)

    # Lightweight query: does the next "date/chain" step provide energy?
    def next_chain_has_energy(
        self,
        *,
        support_name: str,
        next_step: int,
        attribute: Optional[str] = None,
        rarity: Optional[str] = None,
    ) -> Optional[bool]:
        name_norm = normalize_text(support_name)
        attr_norm = normalize_text(attribute or "")
        rar_norm = normalize_text(rarity or "")
        if not name_norm or not isinstance(next_step, (int, float)):
            return None
        step = int(next_step)
        # Prefer records tagged as 'date' or 'chain' kinds; fall back to any if missing
        def _is_date_kind(r: EventRecord) -> bool:
            k = (r.event_kind or "").strip().lower()
            return k in {"date", "chain"}

        pool = [
            r
            for r in self.records
            if r.type == "support"
            and normalize_text(r.name) == name_norm
            and (not attr_norm or normalize_text(r.attribute) == attr_norm)
            and (not rar_norm or normalize_text(r.rarity) == rar_norm)
            and (r.chain_step or 1) == step
        ]
        if not pool:
            return None
        dated = [r for r in pool if _is_date_kind(r)]
        use = dated if dated else pool
        has_energy = False
        for r in use:
            cats = extract_reward_categories([r.options])
            if "energy" in cats or max_positive_energy([r.options]) > 0:
                has_energy = True
                break
        return has_energy

# Cached raw dataset for fallback lookups
_EVENTS_RAW: Optional[List[Dict[str, Any]]] = None

def _load_events_raw() -> List[Dict[str, Any]]:
    global _EVENTS_RAW
    if _EVENTS_RAW is None:
        try:
            _EVENTS_RAW = json.loads(DATASETS_EVENTS.read_text(encoding="utf-8"))
        except Exception:
            _EVENTS_RAW = []
    return _EVENTS_RAW

def predict_next_chain_has_energy_from_raw(
    *, support_name: str, next_step: int, attribute: Optional[str] = None, rarity: Optional[str] = None
) -> Optional[bool]:
    data = _load_events_raw()
    name_norm = normalize_text(support_name)
    attr_norm = normalize_text(attribute or "")
    rar_norm = normalize_text(rarity or "")
    if not name_norm or not isinstance(next_step, (int, float)):
        return None
    step = int(next_step)
    matches = [
        item for item in data
        if str(item.get("type")) == "support"
        and normalize_text(item.get("name")) == name_norm
        and (not attr_norm or normalize_text(item.get("attribute")) == attr_norm)
        and (not rar_norm or normalize_text(item.get("rarity")) == rar_norm)
    ]
    if not matches:
        return None
    allowed_kinds = {"date", "chain"}
    has_energy = False
    for parent in matches:
        for ev in parent.get("choice_events", []) or []:
            if (ev.get("chain_step") == step) and (str(ev.get("type")).strip().lower() in allowed_kinds):
                cats = extract_reward_categories([ev.get("options", {})])
                if "energy" in cats or max_positive_energy([ev.get("options", {})]) > 0:
                    has_energy = True
                    break
        if has_energy:
            break
    return has_energy

def predict_next_chain_max_energy_from_raw(
    *, support_name: str, next_step: int, attribute: Optional[str] = None, rarity: Optional[str] = None
) -> Optional[int]:
    """Return the maximum positive energy from the next chain step (raw dataset).

    This is a fast, in-process scan over datasets/in_game/events.json using cached
    content. It considers only events of kind 'date' or 'chain' for support cards.
    """
    data = _load_events_raw()
    name_norm = normalize_text(support_name)
    attr_norm = normalize_text(attribute or "")
    rar_norm = normalize_text(rarity or "")
    if not name_norm or not isinstance(next_step, (int, float)):
        return None
    step = int(next_step)
    matches = [
        item for item in data
        if str(item.get("type")) == "support"
        and normalize_text(item.get("name")) == name_norm
        and (not attr_norm or normalize_text(item.get("attribute")) == attr_norm)
        and (not rar_norm or normalize_text(item.get("rarity")) == rar_norm)
    ]
    if not matches:
        return None
    allowed_kinds = {"date", "chain"}
    max_energy = 0
    found = False
    for parent in matches:
        for ev in parent.get("choice_events", []) or []:
            if (ev.get("chain_step") == step) and (str(ev.get("type")).strip().lower() in allowed_kinds):
                gain = max_positive_energy([ev.get("options", {})])
                max_energy = max(max_energy, int(gain))
                found = True
    return (max_energy if found else None)


# -----------------------------
# Retrieval + Reranking
# -----------------------------


@dataclass
class Query:
    # Minimal info you'll have from OCR/UI
    ocr_title: str
    # Optional hints (help scoring if provided)
    type_hint: Optional[str] = None  # support|trainee|scenario
    name_hint: Optional[str] = None  # e.g., "Kitasan Black"
    rarity_hint: Optional[str] = None  # "SSR"/"SR"/"R"/"None"
    attribute_hint: Optional[str] = None  # "SPD"/"STA"/"PWR"/"GUTS"/"WIT"/"None"
    chain_step_hint: Optional[int] = None  # e.g., 1/2/3 for chain events
    portrait_path: Optional[str] = None  # optional: path to portrait/icon
    portrait_image: Optional[PILImage] = None  # optional: PIL image crop (in-memory)
    portrait_phash: Optional[int] = None  # optional: precomputed 64-bit pHash
    preferred_trainee_name: Optional[str] = None  # optional: trainee name from config for disambiguation


@dataclass
class MatchResult:
    rec: EventRecord
    score: float
    text_sim: float
    img_sim: float
    hint_bonus: float


def score_candidate(
    q: Query,
    rec: EventRecord,
    portrait_phash: Optional[int],
    cv_sim_override: Optional[float] = None,
    cv_allowed_keys: Optional[Set[str]] = None,
) -> MatchResult:
    # 1) text similarity on titles (normalized)
    qt = normalize_text(q.ocr_title)
    if qt:
        ts_token = fuzz.token_set_ratio(qt, rec.title_norm) / 100.0
        ts_ratio = fuzz.ratio(qt, rec.title_norm) / 100.0
        ts_partial = fuzz.partial_ratio(qt, rec.title_norm) / 100.0
        text_sim = (
            0.5 * ts_token + 0.3 * ts_ratio + 0.2 * ts_partial
        )
        if qt == rec.title_norm:
            text_sim = 1.0
    else:
        text_sim = 0.0

    # 2) image similarity: prefer CV matcher when available; blend with pHash for stability
    ph_sim = (
        hamming_similarity64(portrait_phash, rec.phash64) if portrait_phash else 0.0
    )
    cv_sim: Optional[float]
    if cv_sim_override is not None:
        cv_sim = float(cv_sim_override)
    elif q.portrait_image is not None and (cv_allowed_keys is None or rec.key in cv_allowed_keys):
        # Use all variants for this trainee/support/scenario
        if rec.image_variants:
            cv_sim = _cv_image_similarity_variant(q.portrait_image, rec.image_variants)
        else:
            cv_sim = _cv_image_similarity(q.portrait_image, rec.image_path)
    else:
        cv_sim = None
    if cv_sim is not None:
        img_sim = 0.9 * float(cv_sim) + 0.1 * float(ph_sim)
    else:
        img_sim = float(ph_sim)

    # 3) hint bonus (soft constraints, deck-agnostic)
    hint_bonus = 0.0
    if q.type_hint and q.type_hint == rec.type:
        hint_bonus += 0.04
    if q.name_hint and normalize_text(q.name_hint) == normalize_text(rec.name):
        hint_bonus += 0.08
    if q.rarity_hint and normalize_text(q.rarity_hint) == normalize_text(rec.rarity):
        hint_bonus += 0.12
    if q.attribute_hint and normalize_text(q.attribute_hint) == normalize_text(
        rec.attribute
    ):
        hint_bonus += 0.12
    if q.chain_step_hint is not None and (rec.chain_step or 1) == q.chain_step_hint:
        hint_bonus += 0.12
    # Weighted sum (tuneable, but conservative)
    # Text carries most of the weight; image breaks ties when portrait is present.
    score = 0.82 * text_sim + 0.11 * img_sim + hint_bonus

    return MatchResult(
        rec=rec, score=score, text_sim=text_sim, img_sim=img_sim, hint_bonus=hint_bonus
    )


def retrieve_best(
    catalog: Catalog,
    q: Query,
    top_k: int = 5,
    min_score: float = 0.5,
) -> List[MatchResult]:
    """
    Apply hint-driven *pre-filters* first (type → name → rarity). Each filter is
    only kept if it doesn't collapse the pool to empty; otherwise we gracefully
    fall back to the previous pool. This lets 'rarity=R' surface R variants when
    they exist for the same title, without breaking cases where they don't.
    After scoring, results are filtered by `min_score` (default 0.80) to drop
    spurious matches; callers can detect "no candidates" and fall back.
    """
    # Compute portrait pHash once (if provided)
    portrait_phash = None
    # priority: explicit pHash > PIL image > path
    if q.portrait_phash is not None:
        portrait_phash = q.portrait_phash
    elif q.portrait_image is not None:
        portrait_phash = safe_phash_from_image(q.portrait_image)
    elif q.portrait_path and os.path.exists(q.portrait_path):
        portrait_phash = safe_phash(Path(q.portrait_path))

    pool = list(catalog.records)
    # Keep a backup at each stage in case a filter would drop everything.
    if q.type_hint:
        subset = [r for r in pool if r.type == q.type_hint]
        if subset:
            pool = subset
    if q.name_hint:
        subset = [
            r for r in pool if normalize_text(r.name) == normalize_text(q.name_hint)
        ]
        if subset:
            pool = subset
    if q.rarity_hint:
        subset = [
            r for r in pool if normalize_text(r.rarity) == normalize_text(q.rarity_hint)
        ]
        if subset:
            pool = subset
    if q.attribute_hint:
        subset = [
            r
            for r in pool
            if normalize_text(r.attribute) == normalize_text(q.attribute_hint)
        ]
        if subset:
            pool = subset
    if q.chain_step_hint is not None:
        subset = [r for r in pool if (r.chain_step or 1) == q.chain_step_hint]
        if subset:
            pool = subset
    # If all filters emptied the pool (rare), revert to full catalog.
    if not pool:
        pool = list(catalog.records)

    # Prepare optional remote CV similarities once per query to avoid local OpenCV on thin clients
    remote_cv: Dict[str, float] = {}
    cv_allowed_keys: Optional[Set[str]] = None
    cv_candidate_records: List[EventRecord] = []
    
    if q.portrait_image is not None:
        cv_candidate_records = _select_cv_candidates(q, pool)
        cv_allowed_keys = {rec.key for rec in cv_candidate_records}
    else:
        cv_candidate_records = pool

    if (
        Settings.USE_EXTERNAL_PROCESSOR
        and q.portrait_image is not None
    ):
        try:
            templates: List[Dict[str, Any]] = []
            for rec in cv_candidate_records:
                # Send all variants for this record
                variant_paths = list(rec.image_variants) if rec.image_variants else None
                spec = _template_spec_for_remote(rec, variant_paths)
                if spec:  # Only include templates with valid image data
                    templates.append(spec)

            # Only call remote if we have valid templates with images
            if templates:
                logger_uma.debug(
                    "[event_processor] OCR Title: %s, Type Hint: %s, Name Hint: %s, Rarity Hint: %s, Attribute Hint: %s, Chain Step Hint: %s, Preferred Trainee Name: %s",
                    q.ocr_title,
                    q.type_hint,
                    q.name_hint,
                    q.rarity_hint,
                    q.attribute_hint,
                    q.chain_step_hint,
                    q.preferred_trainee_name,
                )

                remote = _RemoteTMB(templates, min_confidence=0.0, options=_TM_OPTIONS)
                remote.mode = "generic"
                matches = remote.match(q.portrait_image)
                for match in matches:
                    remote_cv[str(match.name)] = float(match.score)
                logger_uma.debug(
                    "[event_processor] Remote match returned %d scores", len(remote_cv)
                )
            else:
                logger_uma.debug(
                    "[event_processor] No valid templates with images after filtering (%d of pool %d), skipping remote match",
                    len(cv_candidate_records), len(pool)
                )
        except Exception as exc:
            logger_uma.debug("[event_processor] Remote template match failed: %s", exc)
            remote_cv = {}

    # Score only the CV-filtered candidates (or full pool if no portrait)
    scoring_pool = cv_candidate_records if cv_candidate_records else pool
    results: List[MatchResult] = [
        score_candidate(
            q,
            rec,
            portrait_phash,
            remote_cv.get(rec.key),
            cv_allowed_keys,
        )
        for rec in scoring_pool
    ]
    # Sort by total score, then text similarity, then image similarity for deterministic tie-breaks
    results.sort(key=lambda r: (r.score, r.text_sim, r.img_sim), reverse=True)
    results = [r for r in results if r.score >= float(min_score)]
    
    # Trainee name preference override: if configured trainee name matches any result, prefer it
    if q.type_hint == "trainee" and q.preferred_trainee_name and len(results) > 1:
        preferred_norm = normalize_text(q.preferred_trainee_name)
        preferred_found = False
        for i, result in enumerate(results):
            result_name_norm = normalize_text(result.rec.name)
            if result_name_norm == preferred_norm:
                # Found a match: move it to position 0 if not already there
                if i > 0:
                    logger_uma.info(
                        "[retrieve_best] Trainee preference override: '%s' (score=%.3f) promoted over '%s' (score=%.3f)",
                        result.rec.name,
                        result.score,
                        results[0].rec.name,
                        results[0].score,
                    )
                    results.insert(0, results.pop(i))
                preferred_found = True
                break
        
        # If preferred trainee not found, fallback to 'trainee/general/None/None' if available
        if not preferred_found:
            for i, result in enumerate(results):
                if (result.rec.type == "trainee" and 
                    result.rec.name == "general" and 
                    result.rec.rarity == "None" and 
                    result.rec.attribute == "None"):
                    if i > 0:
                        logger_uma.info(
                            "[retrieve_best] Trainee preference failed, using general fallback: '%s' (score=%.3f) promoted over '%s' (score=%.3f)",
                            result.rec.event_name,
                            result.score,
                            results[0].rec.name,
                            results[0].score,
                        )
                        results.insert(0, results.pop(i))
                    break
    
    results_k = results[:top_k]
    return results_k
