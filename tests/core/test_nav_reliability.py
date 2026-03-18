from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from PIL import Image

import core.actions.lobby as lobby_module
import core.agent_nav as agent_nav_module
import core.perception.ocr.ocr_local as ocr_local_module
import core.perception.yolo.yolo_local as yolo_local_module
import core.utils.nav as nav_module
from core.actions.lobby import LobbyFlow
from core.agent_nav import AgentNav
from core.perception.ocr.ocr_local import LocalOCREngine
from core.perception.yolo.yolo_local import LocalYOLOEngine


class DummyWaiter:
    def __init__(
        self,
        *,
        click_results: Optional[List[bool]] = None,
        seen_results: Optional[List[bool]] = None,
    ) -> None:
        self._click_results = iter(click_results or [])
        self._seen_results = iter(seen_results or [])
        self.click_calls: List[Dict[str, Any]] = []
        self.seen_calls: List[Dict[str, Any]] = []

    def click_when(self, **kwargs):
        self.click_calls.append(kwargs)
        try:
            return next(self._click_results)
        except StopIteration:
            return False

    def seen(self, **kwargs):
        self.seen_calls.append(kwargs)
        try:
            return next(self._seen_results)
        except StopIteration:
            return False


class DummyCtrl:
    def __init__(self) -> None:
        self.clicks: List[Tuple[Any, int]] = []

    def click_xyxy_center(self, xyxy, clicks: int = 1, **_kwargs) -> None:
        self.clicks.append((xyxy, clicks))


class DummyLobbyFlow(LobbyFlow):
    def process_turn(self):
        return "CONTINUE"

    def _update_state(self, img, dets) -> None:
        return None

    def _process_turns_left(self, img, dets):
        return None


def _make_lobby_flow(waiter: DummyWaiter) -> DummyLobbyFlow:
    flow = DummyLobbyFlow.__new__(DummyLobbyFlow)
    flow.waiter = waiter
    flow.process_on_demand = True
    flow._stats_refresh_counter = 0
    flow._update_stats_calls: List[Tuple[Any, Any]] = []
    flow._update_stats = lambda img, dets: flow._update_stats_calls.append((img, dets))
    return flow


def _det(name: str, box: tuple[int, int, int, int], conf: float = 0.9) -> Dict[str, Any]:
    return {"name": name, "conf": conf, "xyxy": box}


def test_go_training_screen_returns_false_when_click_fails() -> None:
    waiter = DummyWaiter(click_results=[False])
    flow = _make_lobby_flow(waiter)

    assert flow._go_training_screen_from_lobby(img=None, dets=None) is False
    assert flow._update_stats_calls == []


def test_go_training_screen_only_succeeds_after_confirmation() -> None:
    waiter = DummyWaiter(click_results=[True], seen_results=[True])
    flow = _make_lobby_flow(waiter)

    assert flow._go_training_screen_from_lobby(img="img", dets="dets") is True
    assert flow._update_stats_calls == [("img", "dets")]
    assert any(call.get("classes") == ("training_button",) for call in waiter.seen_calls)


def test_handle_shop_exchange_preserves_partial_purchases(monkeypatch) -> None:
    snapshots: List[List[Dict[str, Any]]] = [
        [
            _det("shop_row", (0, 0, 100, 50)),
            _det("shop_clock", (10, 10, 30, 30)),
            _det("shop_exchange", (70, 10, 95, 30)),
        ],
        [_det("shop_row", (0, 0, 100, 50))],
        [_det("shop_row", (0, 0, 100, 50))],
        [_det("shop_row", (0, 0, 100, 50))],
    ]
    end_sale_calls: List[str] = []

    monkeypatch.setattr(
        nav_module,
        "_shop_item_order",
        lambda: iter(
            [
                ("shop_clock", "alarm_clock"),
                ("shop_parfait", "parfait"),
            ]
        ),
    )
    monkeypatch.setattr(
        nav_module,
        "collect_snapshot",
        lambda *args, **kwargs: (Image.new("RGB", (100, 50), "white"), snapshots.pop(0)),
    )
    monkeypatch.setattr(nav_module, "_confirm_exchange_dialog", lambda *args, **kwargs: True)
    monkeypatch.setattr(nav_module, "smart_scroll_small", lambda *args, **kwargs: None)
    monkeypatch.setattr(nav_module, "cooperative_sleep", lambda *args, **kwargs: True)
    monkeypatch.setattr(nav_module, "abort_requested", lambda: False)
    monkeypatch.setattr(
        nav_module,
        "end_sale_dialog",
        lambda *args, **kwargs: end_sale_calls.append("end_sale") or True,
    )

    result = nav_module.handle_shop_exchange(
        waiter=DummyWaiter(),
        yolo_engine=object(),
        ctrl=DummyCtrl(),
        tag_prefix="test_shop",
        ensure_enter=False,
    )

    assert result is True
    assert end_sale_calls == ["end_sale"]


def test_handle_shop_exchange_stops_quickly_when_abort_requested(monkeypatch) -> None:
    snapshots: List[List[Dict[str, Any]]] = [[_det("shop_row", (0, 0, 100, 50))]]
    abort_values = iter([False, True, True, True])

    monkeypatch.setattr(
        nav_module,
        "_shop_item_order",
        lambda: iter([("shop_clock", "alarm_clock")]),
    )
    monkeypatch.setattr(
        nav_module,
        "collect_snapshot",
        lambda *args, **kwargs: (Image.new("RGB", (100, 50), "white"), snapshots[0]),
    )
    monkeypatch.setattr(nav_module, "smart_scroll_small", lambda *args, **kwargs: None)
    monkeypatch.setattr(nav_module, "cooperative_sleep", lambda *args, **kwargs: True)
    monkeypatch.setattr(nav_module, "abort_requested", lambda: next(abort_values))
    monkeypatch.setattr(nav_module, "end_sale_dialog", lambda *args, **kwargs: False)

    result = nav_module.handle_shop_exchange(
        waiter=DummyWaiter(),
        yolo_engine=object(),
        ctrl=DummyCtrl(),
        tag_prefix="test_shop_abort",
        ensure_enter=False,
    )

    assert result is False


def test_agent_nav_classify_finished_uses_current_frame_without_waiter_seen() -> None:
    class WaiterBomb:
        def seen(self, **kwargs):
            raise AssertionError("waiter.seen should not be called during nav classification")

    class OCRStub:
        def text(self, img, joiner=" ", min_conf=0.2):
            return "RESTORE"

    agent = AgentNav.__new__(AgentNav)
    agent.action = "team_trials"
    agent._thr = {
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
    agent.waiter = WaiterBomb()
    agent.ocr = OCRStub()

    img = Image.new("RGB", (40, 40), "white")
    dets = [_det("button_green", (0, 0, 20, 20), conf=0.9)]

    screen, info = agent.classify_nav_screen(img, dets)

    assert screen == "TeamTrialsFinished"
    assert "counts" in info


def test_local_yolo_engine_reuses_cached_model(monkeypatch) -> None:
    created: List[str] = []

    class DummyYOLO:
        def __init__(self, weights_path: str):
            created.append(weights_path)

        def to(self, *_args, **_kwargs):
            return self

    yolo_local_module._YOLO_MODEL_CACHE.clear()
    monkeypatch.setattr(yolo_local_module, "YOLO", DummyYOLO)

    first = LocalYOLOEngine(ctrl=None, weights="weights_a.pt", use_gpu=False)
    second = LocalYOLOEngine(ctrl=None, weights="weights_a.pt", use_gpu=False)

    assert len(created) == 1
    assert first.model is second.model


def test_local_yolo_recognize_passes_tag_to_detect_pil() -> None:
    class DummyCtrl:
        def screenshot(self, region=None):
            return Image.new("RGB", (20, 20), "white")

    captured: Dict[str, Any] = {}
    engine = LocalYOLOEngine.__new__(LocalYOLOEngine)
    engine.ctrl = DummyCtrl()

    def fake_detect_pil(self, pil_img, *, imgsz=None, conf=None, iou=None, tag="general", agent=None):
        captured["tag"] = tag
        return {}, []

    engine.detect_pil = fake_detect_pil.__get__(engine, LocalYOLOEngine)

    engine.recognize(tag="custom_tag")

    assert captured["tag"] == "custom_tag"


def test_local_ocr_engine_reuses_cached_reader(monkeypatch) -> None:
    created: List[Tuple[Any, Dict[str, Any]]] = []

    class DummyReader:
        def __init__(self, *args, **kwargs):
            created.append((args, kwargs))

        def predict(self, _img):
            return []

    ocr_local_module._OCR_READER_CACHE.clear()
    monkeypatch.setattr(ocr_local_module, "PaddleOCR", DummyReader)
    monkeypatch.setattr(ocr_local_module.paddle, "is_compiled_with_cuda", lambda: False)

    first = LocalOCREngine("det_model", "rec_model")
    second = LocalOCREngine("det_model", "rec_model")

    assert len(created) == 1
    assert first.reader is second.reader
