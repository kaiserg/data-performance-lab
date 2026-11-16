import importlib.util
import threading
import subprocess
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from . import benchmark as runner

ROOT = runner.ROOT
DATA = ROOT / "data" / "taxi-10m.parquet"
app = FastAPI(title="Data Performance Lab", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000","http://127.0.0.1:3000"], allow_methods=["GET","POST"], allow_headers=["*"])
lock = threading.Lock()

class RunRequest(BaseModel):
    operation: str = "average"
    repeats: int = Field(default=3, ge=1, le=7)
    size: str = "10m"
    mode: str = "end-to-end"
    engines: list[str] = Field(default_factory=lambda: list(runner.ENGINES))
    warmups: int = Field(default=1, ge=0, le=3)
    threads: int = Field(default=4, ge=1, le=64)

@app.get("/api/status")
def status():
    return {"ready": DATA.exists(), "file": str(DATA), "size_mb": round(DATA.stat().st_size/1048576,1) if DATA.exists() else 0,
            "datasets": {size:(ROOT/"data"/f"taxi-{size}.parquet").exists() for size in runner.SIZES},
            "engines": runner.ENGINES + (["datafusion"] if importlib.util.find_spec("datafusion") else [])}

@app.post("/api/benchmark")
def benchmark(req: RunRequest):
    if not lock.acquire(blocking=False):
        raise HTTPException(409,"A benchmark is already running")
    try:
        return runner.run(**req.model_dump())
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc
    except (RuntimeError, TimeoutError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(500,str(exc)) from exc
    finally:
        lock.release()

@app.get("/api/history")
def history():
    return runner.history()

@app.get("/api/explore")
def explore(size: str = "100k", min_distance: float = Query(0, ge=0, le=10000), limit: int = Query(25, ge=1, le=100)):
    import duckdb
    try:
        path = runner.dataset(size)
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc
    if not lock.acquire(blocking=False):
        raise HTTPException(409,"Wait for the benchmark to finish before exploring")
    try:
        with duckdb.connect(config={"threads":4}) as con:
            args = [str(path), min_distance]
            total, fare, distance = con.execute("SELECT COUNT(*), AVG(fare_amount), AVG(trip_distance) FROM read_parquet(?) WHERE trip_distance >= ?",args).fetchone()
            daily = con.execute("SELECT CAST(tpep_pickup_datetime AS DATE) AS pickup_date, COUNT(*) AS n FROM read_parquet(?) WHERE trip_distance >= ? GROUP BY pickup_date ORDER BY pickup_date",args).fetchall()
            rows = con.execute("SELECT CAST(tpep_pickup_datetime AS VARCHAR), trip_distance, fare_amount FROM read_parquet(?) WHERE trip_distance >= ? LIMIT ?",args+[limit]).fetchall()
        return {"size":size,"count":total,"average_fare":fare,"average_distance":distance,
                "daily":[{"day":str(d) if d else "null","trips":n} for d,n in daily],
                "rows":[dict(zip(["pickup","distance","fare"],row)) for row in rows]}
    finally:
        lock.release()
