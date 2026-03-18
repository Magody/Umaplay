# core/perception/ocr.py
from __future__ import annotations
import cv2
import numpy as np
import importlib
import os
import re
import threading
from typing import Any, Dict, List, cast
from core.perception.ocr.interface import OCRInterface
from core.types import OCRItem

# Disable if facing multi-process error
os.environ["OMP_NUM_THREADS"] = "4"
os.environ["MKL_NUM_THREADS"] = "4"

from paddleocr import PaddleOCR
import paddle

from core.utils.img import to_bgr
from core.utils.logger import logger_uma

_OCR_READER_CACHE: Dict[tuple, PaddleOCR] = {}
_OCR_READER_CACHE_LOCK = threading.Lock()


class LocalOCREngine(OCRInterface):
    """
    Minimal PaddleOCR wrapper:
      - raw(...) -> normalized [(box, text, score), ...]
      - text(...) -> single string of concatenated words
      - digits(...) -> digits-only string (handy for counters)
    """

    def __init__(
        self,
        text_detection_model_name="PP-OCRv5_mobile_det",
        text_recognition_model_name="en_PP-OCRv5_mobile_rec",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        return_word_box=False,
    ):
        lang = "en"
        gpu = False
        # enable_hpi = False
        # TODO: split environments (zero conflicts)
        # env_paddle (GPU): PaddlePaddle + PaddleOCR/PaddleX + modelscope, no Torch (or Torch CPU).
        # env_torch (GPU): Torch + Ultralytics + friends, no Paddle.
        # For Paddle gpu install: paddlepaddle-gpu
        # Coerce language
        if isinstance(lang, (list, tuple)):
            lang = lang[0] if len(lang) > 0 else "en"
        self.lang = str(lang)

        requested_gpu = bool(gpu)
        device_str = f"gpu:{int(0)}" if requested_gpu else "cpu"

        # Try to warn/auto-fallback if CUDA isn’t available
        try:
            has_cuda = bool(getattr(paddle, "is_compiled_with_cuda", lambda: False)())
            if requested_gpu and not has_cuda:
                logger_uma.warning(
                    "OCRInterface: GPU requested but PaddlePaddle is not CUDA-enabled → falling back to CPU."
                )
                device_str = "cpu"
        except Exception:
            # If Paddle import check fails, we’ll still try device=... below and catch errors there.
            logger_uma.warning("Error while checking GPU in Paddle")

        self.device = device_str

        # Instantiate PaddleOCR, turning off unneeded subpipelines and using the mobile detector.
        # Also shrink det input and bump rec batch for small crops.
        self.reader: PaddleOCR | None = None
        init_kwargs = dict(
            # speed: disable extras you don't need for tiny stat crops
            # use_doc_orientation_classify: Disables the document-level orientation classifier (the thing that decides if a whole page/photo is rotated 90°/180°/270°). You saw it as PP-LCNet_x1_0_doc_ori in the logs. Useful for scanned pages; unnecessary for small screen snippets that are already upright.
            use_doc_orientation_classify=use_doc_orientation_classify,
            # Disables document unwarping/dewarping (the UVDoc model). That corrects perspective/curvature in camera photos of paper. Again, great for scans—wasted cycles for flat game UI crops.
            use_doc_unwarping=use_doc_unwarping,
            # Disables the text-line orientation classifier (per-line rotation check; you saw PP-LCNet_x1_0_textline_ori). It helps when lines might be upside-down or vertical. If your segments are consistently horizontal/upright, you can skip it (faster, less model load).
            use_textline_orientation=use_textline_orientation,
            # Tells the pipeline not to return word-level boxes in the output (fewer boxes/fields to compute and serialize). You still get the recognized text; you just don’t get fine-grained per-word bounding boxes. That trims a bit of post-processing and reduces payload size.
            return_word_box=return_word_box,
            # speed: mobile detector instead of server
            text_detection_model_name=text_detection_model_name,
            text_recognition_model_name=text_recognition_model_name,
            # PP-OCRv4 Server model: ~85% accuracy on English text, but around 36.9 ms per image on CPU (model size ~173 MB)
            # PP-OCRv4 Mobile model: ~78–79% accuracy, but only ~17.5 ms per image on CPU (model size ~10.5 MB)
            # throughput when you benchmark many small images
            text_recognition_batch_size=16,
        )
        if not text_detection_model_name and not text_recognition_model_name:
            init_kwargs["lang"] = self.lang
        cache_key = (
            text_detection_model_name,
            text_recognition_model_name,
            self.lang,
            self.device,
            bool(use_doc_orientation_classify),
            bool(use_doc_unwarping),
            bool(use_textline_orientation),
            bool(return_word_box),
        )

        with _OCR_READER_CACHE_LOCK:
            cached_reader = _OCR_READER_CACHE.get(cache_key)
            if cached_reader is not None:
                self.reader = cached_reader
                logger_uma.info(
                    "Reusing cached OCR reader | lang=%s device=%s",
                    self.lang,
                    self.device,
                )
            else:
                try:
                    # Newer API (device=...)
                    self.reader = PaddleOCR(device=self.device, enable_hpi=False, **init_kwargs)
                except TypeError:
                    # Older API style (use_gpu flag)
                    use_gpu = self.device.startswith("gpu")
                    try:
                        self.reader = PaddleOCR(lang=self.lang, use_gpu=use_gpu)
                    except Exception as e:
                        logger_uma.exception(
                            "OCRInterface: failed to initialize PaddleOCR (use_gpu=%s). Error: %s",
                            use_gpu,
                            e,
                        )
                        raise
                except Exception as e:
                    # If 'device' fails for any other reason, try CPU as last resort
                    logger_uma.warning(
                        "OCRInterface: device='%s' failed (%s). Retrying with CPU.",
                        self.device,
                        e,
                    )
                    try:
                        self.reader = PaddleOCR(lang=self.lang, device="cpu", enable_hpi=False)
                        self.device = "cpu"
                    except Exception as e2:
                        # Helpful guidance if it's the well-known Paddle/PaddleX mismatch
                        msg = str(e2)
                        if "set_optimization_level" in msg or "AnalysisConfig" in msg:
                            try:
                                paddle_ver = importlib.import_module("paddle").__version__
                            except Exception:
                                paddle_ver = "unknown"
                            try:
                                paddlex_ver = importlib.import_module("paddlex").__version__
                            except Exception:
                                paddlex_ver = "unknown"
                            try:
                                import paddleocr as _pocr

                                paddleocr_ver = getattr(_pocr, "__version__", "unknown")
                            except Exception:
                                paddleocr_ver = "unknown"
                            logger_uma.error(
                                "Paddle/PaddleOCR/PaddleX possible version mismatch. "
                                "Installed: paddle=%s, paddleocr=%s, paddlex=%s. "
                                "PaddleOCR 3.x + PaddleX 3.x require PaddlePaddle >= 3.0.",
                                paddle_ver,
                                paddleocr_ver,
                                paddlex_ver,
                            )
                        logger_uma.exception("OCRInterface: CPU fallback also failed: %s", e2)
                        raise

                if self.reader is not None:
                    final_cache_key = (
                        text_detection_model_name,
                        text_recognition_model_name,
                        self.lang,
                        self.device,
                        bool(use_doc_orientation_classify),
                        bool(use_doc_unwarping),
                        bool(use_textline_orientation),
                        bool(return_word_box),
                    )
                    _OCR_READER_CACHE[final_cache_key] = self.reader

        logger_uma.info(
            "OCRInterface initialized | lang=%s device=%s", self.lang, self.device
        )

    @staticmethod
    def _ensure_bgr3(img: Any) -> np.ndarray:
        """Return a 3-channel BGR image without double-swapping channels."""
        if isinstance(img, np.ndarray):
            bgr = img
        else:
            bgr = to_bgr(img)  # handles PIL/path/etc.
        if bgr.ndim == 2:
            bgr = cv2.cvtColor(bgr, cv2.COLOR_GRAY2BGR)
        elif bgr.shape[2] == 4:
            bgr = cv2.cvtColor(bgr, cv2.COLOR_BGRA2BGR)
        return bgr

    # ---- Core inference ----
    def raw(self, img: Any) -> dict:
        """Return normalized Paddle JSON: {'res': {...}}"""
        if self.reader is None:
            raise RuntimeError("PaddleOCR reader is not initialized.")
        bgr = self._ensure_bgr3(img)
        out = self.reader.predict(bgr)
        raw_items = out[0] if isinstance(out, list) and out else out
        if isinstance(raw_items, list) and raw_items:
            raw_items = raw_items[0]
        raw_any: Any = raw_items
        to_json = getattr(raw_any, "_to_json", None)
        if callable(to_json):
            return cast(Dict[str, Any], to_json())
        if isinstance(raw_any, dict):
            return cast(Dict[str, Any], raw_any)
        return {}

    def text(self, img: Any, joiner: str = " ", min_conf: float = 0.2) -> str:
        j = self.raw(img)
        res = j.get("res", {})
        rec_texts = res.get("rec_texts", []) or []
        rec_scores = res.get("rec_scores", []) or []
        kept = []
        for i, t in enumerate(rec_texts):
            if i < len(rec_scores):
                if rec_scores[i] >= min_conf:
                    kept.append(t)
                elif t.strip():
                    logger_uma.debug(f"Low rec score for: {rec_scores[i]:.3f} | {t}")
        return (joiner.join(kept)).strip()

    def digits(self, img: Any) -> int:
        s = self.text(img)
        only = re.sub(r"[^\d]", "", s).strip()
        if not only:
            return -1
        try:
            return int(only)
        except Exception as e:
            logger_uma.warning(f"Couldn't parse digits: {only}. {e}")
            return -1

    # -------- Batch APIs --------
    def batch_text(
        self, imgs: List[Any], *, joiner: str = " ", min_conf: float = 0.2
    ) -> List[str]:
        if not imgs:
            return []
        bgr_list = [self._ensure_bgr3(im) for im in imgs]
        if self.reader is None:
            raise RuntimeError("PaddleOCR reader is not initialized.")
        outs = list(self.reader.predict(bgr_list))
        texts: List[str] = []
        for o in outs:
            if isinstance(o, list) and o:
                o = o[0]
            raw_any: Any = o
            to_json = getattr(raw_any, "_to_json", None)
            if callable(to_json):
                j: Dict[str, Any] = cast(Dict[str, Any], to_json())
            elif isinstance(raw_any, dict):
                j = cast(Dict[str, Any], raw_any)
            else:
                j = {}
            res = j.get("res", {})
            rec_texts = res.get("rec_texts", []) or []
            rec_scores = res.get("rec_scores", []) or []
            kept = [
                t
                for i, t in enumerate(rec_texts)
                if i < len(rec_scores) and rec_scores[i] >= min_conf
            ]
            texts.append((joiner.join(kept)).strip())
        return texts

    def batch_digits(self, imgs: List[Any]) -> List[str]:
        """
        Run OCR over a list of images, returning digits-only strings for each.
        """
        outs = self.batch_text(imgs)
        return [re.sub(r"[^\d]", "", s or "") for s in outs]
