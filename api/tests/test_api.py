"""API 端到端测试（TestClient）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

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


def test_zero_cost_cycle_audit_record():
    """报告场景：四点零代价环，公开记录须可复算到 e00、e01、e07。"""
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
    assert body["canonical_ids"] == ["e00", "e01", "e07"]
    assert body["total_cost"] == 0
    assert body["record"]["contractions"] == 0
    # c 的同价最低入口与规范裁决必须随结果公开
    crec = next(
        c for c in body["record"]["levels"][0]["chosen"] if c["node"] == "c"
    )
    assert crec["channel"] == "e07"
    assert crec["reason"] == "canonical_ruling"
    assert {x["channel"] for x in crec["candidates"]} == {"e06", "e07"}
    rulings = {x["channel"]: x["decision"] for x in body["record"]["rulings"]}
    assert rulings == {"e00": "accepted", "e01": "accepted",
                       "e06": "rejected", "e07": "accepted"}


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
