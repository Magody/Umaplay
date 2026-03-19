from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from core.agent_nav import AgentNav


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "tests" / "data" / "team_trials" / "manifest.json"
FIXTURE_DIR = ROOT / "tests" / "data" / "team_trials"


def _build_agent():
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
    return agent


def _det(name: str, idx: int):
    x1 = idx * 30
    y1 = 20
    x2 = x1 + 20
    y2 = 40
    return {"name": name, "conf": 0.95, "xyxy": (x1, y1, x2, y2)}


def test_team_trials_reference_manifest_files_exist() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    for item in manifest:
        assert (FIXTURE_DIR / item["filename"]).exists(), item["filename"]


def test_team_trials_reference_manifest_expected_screens() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    img = Image.new("RGB", (300, 300), "white")

    for item in manifest:
        ocr_map = item.get("ocr_text", {})

        agent = _build_agent()
        agent.ocr = None
        dets = [_det(name, idx) for idx, name in enumerate(item["detections"])]

        def fake_button_text_seen(_img, _dets, *, cls_name, target_text, conf_min=0.0, threshold=0.58):
            return ocr_map.get(cls_name, "").strip().upper() == target_text.strip().upper()

        agent._button_text_seen = fake_button_text_seen

        screen, _info = agent.classify_nav_screen(img, dets)

        assert screen == item["expected_screen"], item["id"]
