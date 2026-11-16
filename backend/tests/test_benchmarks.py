from datetime import datetime
import importlib.util
import pytest
import pyarrow as pa
import pyarrow.parquet as pq
from fastapi.testclient import TestClient
from app import benchmark as b
from app.main import app

@pytest.fixture
def fixture(tmp_path):
    path = tmp_path / "taxi.parquet"
    pq.write_table(pa.table({"tpep_pickup_datetime":pa.array([datetime(2023,1,1),datetime(2023,1,1),datetime(2023,1,2),None],type=pa.timestamp("us")),"trip_distance":[5.,6.,None,10.],"fare_amount":[10.,20.,None,-3.]}),path)
    return path

@pytest.mark.parametrize("engine",["pandas","polars","duckdb","datafusion"])
@pytest.mark.parametrize("mode",["end-to-end","in-memory"])
@pytest.mark.parametrize("operation",["daily","distance","average"])
def test_engines(fixture,engine,mode,operation):
    import json, subprocess, sys
    if importlib.util.find_spec(engine) is None:
        pytest.skip("Optional engine unavailable")
    p = subprocess.run([sys.executable,"-m","app.worker",engine,operation,str(fixture),"--mode",mode],capture_output=True,text=True,check=True)
    value = json.loads(p.stdout)
    assert b.equivalent(value["value"],b.reference(fixture,operation))
    assert value["seconds"] >= 0
    assert value["peak_rss_mb"] > 0

def test_validation():
    assert not b.equivalent({"2023-01-01":3},{"2023-01-02":3})
    assert not b.equivalent(None,0)
    assert b.equivalent(1.,1.+1e-12)

@pytest.mark.parametrize("payload",[{"operation":"bad"},{"size":"../../secret"},{"mode":"bad"},{"engines":["bad"]},{"repeats":0}])
def test_api_rejects_bad_request(payload):
    assert TestClient(app).post("/api/benchmark",json=payload).status_code in (400,422)

def test_history_and_metadata(fixture,tmp_path,monkeypatch):
    monkeypatch.setattr(b,"dataset",lambda size:fixture)
    monkeypatch.setitem(b.SIZES,"100k",4)
    monkeypatch.setattr(b,"HISTORY",tmp_path/"history")
    result = b.run("daily",1,"100k",engines=["duckdb"],warmups=0)
    assert result["validated"] and len(result["dataset"]["sha256"]) == 64
    assert b.history()[0]["id"] == result["id"]

def test_explorer():
    client = TestClient(app)
    assert client.get("/api/explore?size=bad").status_code == 400
    assert client.get("/api/explore?limit=1000").status_code == 422


def test_explorer_success(fixture,monkeypatch):
    monkeypatch.setattr(b,"dataset",lambda size:fixture)
    response=TestClient(app).get("/api/explore?size=100k&min_distance=5")
    assert response.status_code == 200
    assert response.json()["count"] == 3
    assert len(response.json()["rows"]) == 3
