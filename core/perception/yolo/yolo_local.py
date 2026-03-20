# core/perception/yolo/yolo_local.py
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
from PIL import Image
from ultralytics.models import YOLO

from core.perception.yolo.interface import IDetector
from core.controllers.base import IController, RegionXYWH
from core.controllers.steam import SteamController
from core.settings import Settings
from core.types import DetectionDict
from core.utils.img import pil_to_bgr
from core.utils.logger import logger_uma

_YOLO_MODEL_CACHE: Dict[Tuple[str, bool], YOLO] = {}
_YOLO_MODEL_CACHE_LOCK = threading.Lock()


class LocalYOLOEngine(IDetector):
    """
    Ultralytics-backed detector. Keeps API parity with the interface and mirrors
    your previous helpers, but encapsulated in a class.
    """

    def __init__(
        self,
        ctrl: Optional[IController] = None,
        *,
        weights: Optional[str] = None,
        use_gpu: Optional[bool] = None,
    ):
        self.ctrl = ctrl
        self.weights_path = str(weights or Settings.YOLO_WEIGHTS_URA)
        self.use_gpu = Settings.USE_GPU if use_gpu is None else bool(use_gpu)
        cache_key = (self.weights_path, self.use_gpu)

        with _YOLO_MODEL_CACHE_LOCK:
            cached_model = _YOLO_MODEL_CACHE.get(cache_key)
            if cached_model is None:
                logger_uma.info(f"Loading YOLO weights from: {self.weights_path}")
                cached_model = YOLO(self.weights_path)
                if self.use_gpu:
                    try:
                        cached_model.to("cuda:0")
                    except Exception as e:
                        logger_uma.error(f"Couldn't set YOLO model to CUDA: {e}")
                _YOLO_MODEL_CACHE[cache_key] = cached_model
            else:
                logger_uma.info(
                    "Reusing cached YOLO weights from: %s", self.weights_path
                )

        self.model = cached_model

    # ---------- internals ----------
    @staticmethod
    def _extract_dets(res, conf_min: float = 0.25) -> List[DetectionDict]:
        boxes = getattr(res, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return []

        names = (
            res.names
            if isinstance(res.names, dict)
            else {i: n for i, n in enumerate(res.names)}
        )
        xyxy = boxes.xyxy.cpu().numpy()
        cls = boxes.cls.cpu().numpy().astype(int)
        conf = boxes.conf.cpu().numpy()

        out: List[DetectionDict] = []
        for i in range(len(cls)):
            if conf[i] < conf_min:
                continue
            out.append(
                {
                    "idx": i,
                    "name": names.get(int(cls[i]), str(cls[i])),
                    "conf": float(conf[i]),
                    "xyxy": tuple(map(float, xyxy[i])),
                }
            )
        return out

    @staticmethod
    def _maybe_store_debug(
        pil_img: Image.Image,
        dets: List[DetectionDict],
        *,
        tag: str,
        thr: float,
        agent: Optional[str] = None,
    ) -> None:
        import os, time

        if not Settings.STORE_FOR_TRAINING or not dets:
            return
        lows = [d for d in dets if float(d.get("conf", 0.0)) <= float(thr)]
        if not lows:
            return
        try:
            agent_segment = (agent or "").strip()
            base_dir = Settings.DEBUG_DIR / agent_segment if agent_segment else Settings.DEBUG_DIR
            out_dir_raw = base_dir / tag / "raw"
            os.makedirs(out_dir_raw, exist_ok=True)

            ts = (
                time.strftime("%Y%m%d-%H%M%S") + f"_{int((time.time() % 1) * 1000):03d}"
            )

            lowest = min(lows, key=lambda d: float(d.get("conf", 0.0)))
            conf_line = f"{float(lowest.get('conf', 0.0)):.2f}"
            raw_name = str(lowest.get("name", "unknown")).strip()
            class_segment = "".join(
                ch if ch.isalnum() or ch in "-_" else "-" for ch in raw_name
            ) or "unknown"

            raw_path = out_dir_raw / f"{tag}_{ts}_{class_segment}_{conf_line}.png"
            pil_img.save(raw_path)
            logger_uma.debug("saved low-conf training debug -> %s", raw_path)
        except Exception as e:
            logger_uma.debug("failed saving training debug: %s", e)

    # ---------- public API ----------
    def detect_bgr(
        self,
        bgr: np.ndarray,
        *,
        imgsz: Optional[int] = None,
        conf: Optional[float] = None,
        iou: Optional[float] = None,
        original_pil_img=None,
        tag="general",
        agent: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], List[DetectionDict]]:
        imgsz = imgsz if imgsz is not None else Settings.YOLO_IMGSZ
        conf = conf if conf is not None else Settings.YOLO_CONF
        iou = iou if iou is not None else Settings.YOLO_IOU

        res_list = self.model.predict(
            source=bgr, imgsz=imgsz, conf=conf, iou=iou, verbose=False
        )
        result = res_list[0]
        dets = self._extract_dets(result, conf_min=conf)

        if original_pil_img is not None:
            self._maybe_store_debug(
                original_pil_img,
                dets,
                tag=tag,
                thr=Settings.STORE_FOR_TRAINING_THRESHOLD,
                agent=agent,
            )

        meta = {"names": result.names, "imgsz": imgsz, "conf": conf, "iou": iou}
        return meta, dets

    def detect_pil(
        self,
        pil_img: Image.Image,
        *,
        imgsz: Optional[int] = None,
        conf: Optional[float] = None,
        iou: Optional[float] = None,
        tag="general",
        agent: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], List[DetectionDict]]:
        bgr = pil_to_bgr(pil_img)

        meta, dets = self.detect_bgr(
            bgr,
            imgsz=imgsz,
            conf=conf,
            iou=iou,
            original_pil_img=pil_img,
            tag=tag,
            agent=agent,
        )
        return meta, dets

    def recognize(
        self,
        *,
        region: Optional[RegionXYWH] = None,
        imgsz: Optional[int] = None,
        conf: Optional[float] = None,
        iou: Optional[float] = None,
        tag: str = "general",
        agent: Optional[str] = None,
    ) -> Tuple[Image.Image, Dict[str, Any], List[DetectionDict]]:
        if self.ctrl is None:
            raise RuntimeError(
                "LocalYOLOEngine.recognize() requires a controller injected in the constructor."
            )

        if isinstance(self.ctrl, SteamController):
            img = self.ctrl.screenshot_left_half()
        else:
            img = self.ctrl.screenshot(region=region)

        meta, dets = self.detect_pil(
            img,
            imgsz=imgsz,
            conf=conf,
            iou=iou,
            tag=tag,
            agent=agent,
        )
        return img, meta, dets
