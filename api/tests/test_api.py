"""API 端到端测试（TestClient）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.arborescence import Channel, replay_record
from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_solve_ok():
    payload = {
        "points": ["r", "a", "b", "c", "d"],
        "root": "r",
        "channels": [
            {"id": "e1", "from": "r", "to": "a", "cost": 5},
            {"id": "e2", "from": "a", "to": "b", "cost": 1},
            {"id": "e3", "from": "b", "to": "a", "cost": 1},
            {"id": "e4", "from": "b", "to": "c", "cost": 1},
            {"id": "e5", "from": "c", "to": "a", "cost": 1},
            {"id": "e6", "from": "c", "to": "d", "cost": 1},
        ],
    }
    r = client.post("/api/solve", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["total_cost"] == 8
    assert body["canonical_ids"] == ["e1", "e2", "e4", "e6"]
    assert len(body["tree"]) == 4
    assert body["record"]["contractions"] == 2
    assert len(body["record"]["expansions"]) == 2


def test_solve_tie_cycle_zero_cost():
    """同代价四点环：规范树 e00/e01/e07，记录公开可复算的选择依据。"""
    payload = {
        "points": ["r", "a", "b", "c"],
        "root": "r",
        "channels": [
            {"id": "e00", "from": "b", "to": "a", "cost": 0},
            {"id": "e01", "from": "c", "to": "b", "cost": 0},
            {"id": "e06", "from": "a", "to": "c", "cost": 0},
            {"id": "e07", "from": "r", "to": "c", "cost": 0},
        ],
    }
    r = client.post("/api/solve", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["total_cost"] == 0
    assert body["canonical_ids"] == ["e00", "e01", "e07"]
    rec = body["record"]
    assert rec["contractions"] == 0
    # 公开比较规则与规范裁决序列
    assert rec["key_scheme"]
    assert [d["channel"] for d in rec["canonical_decisions"]] == ["e00", "e01", "e06", "e07"]
    # 第 0 层公开 c 的全部候选及其比较键
    level0 = rec["levels"][0]
    c_in = {
        i["channel"]: i
        for c in level0["candidates"]
        if c["node"] == "c"
        for i in c["inlets"]
    }
    assert c_in["e07"]["canonical"] and c_in["e07"]["selected"]
    assert not c_in["e06"]["canonical"] and not c_in["e06"]["selected"]
    # 仅凭输入与响应记录即可逐步重放出最终规范树
    channels = [
        Channel(id=c["id"], u=c["from"], v=c["to"], cost=c["cost"])
        for c in payload["channels"]
    ]
    assert replay_record(payload["points"], "r", channels, rec) == ["e00", "e01", "e07"]


def test_solve_unsolvable():
    payload = {
        "points": ["r", "a", "z"],
        "root": "r",
        "channels": [{"id": "u1", "from": "r", "to": "a", "cost": 1}],
    }
    r = client.post("/api/solve", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "unsolvable"
    assert body["unreachable"] == ["z"]
    assert body["reason"]


def test_invalid_self_loop():
    payload = {
        "points": ["r", "a"],
        "root": "r",
        "channels": [{"id": "c1", "from": "a", "to": "a", "cost": 1}],
    }
    r = client.post("/api/solve", json=payload)
    assert r.status_code == 422
    body = r.json()
    assert body["status"] == "invalid"
    assert any("自环" in e for e in body["errors"])


def test_invalid_schema():
    r = client.post("/api/solve", json={"points": ["r"], "root": "r"})
    assert r.status_code == 422
    assert r.json()["status"] == "invalid"


def test_negative_cost_rejected():
    payload = {
        "points": ["r", "a"],
        "root": "r",
        "channels": [{"id": "c1", "from": "r", "to": "a", "cost": -2}],
    }
    r = client.post("/api/solve", json=payload)
    assert r.status_code == 422
    assert r.json()["status"] == "invalid"
