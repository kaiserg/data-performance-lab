"""Reproducible runner shared by HTTP and CLI."""
import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import random
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
SIZES = {"100k": 100_000, "1m": 1_000_000, "10m": 10_000_000}
ENGINES = ["pandas", "polars", "duckdb"]
HISTORY = ROOT / "results"


def dataset(size):
    if size not in SIZES:
        raise ValueError("Invalid dataset size")
    path = ROOT / "data" / f"taxi-{size}.parquet"
    if not path.exists():
        raise ValueError(f"Missing {path.name}; run python ../scripts/prepare.py")
    return path


def equivalent(a, b):
    if isinstance(a, dict) or isinstance(b, dict):
        return isinstance(a, dict) and isinstance(b, dict) and a == b
    if a is None or b is None:
        return a is b
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)


def reference(path, operation):
    # Independent Arrow implementation, outside all measured subprocesses.
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    col = {"daily":"tpep_pickup_datetime", "distance":"trip_distance", "average":"fare_amount"}[operation]
    table = pq.read_table(path, columns=[col])
    if operation == "average":
        return pc.mean(table[col]).as_py()
    if operation == "distance":
        return pc.sum(pc.cast(pc.greater(table[col], 5), "int64")).as_py() or 0
    dates = pc.cast(table[col], "date32")
    counts = pc.value_counts(dates).to_pylist()
    return {str(x["values"]) if x["values"] is not None else "null": x["counts"] for x in counts}


def run(operation="average", repeats=3, size="10m", mode="end-to-end", engines=None, warmups=1, threads=4, seed=42, persist=True):
    engines = ENGINES if engines is None else engines
    if operation not in {"average", "daily", "distance"} or mode not in {"end-to-end", "in-memory"}:
        raise ValueError("Invalid operation or mode")
    if not 1 <= repeats <= 7 or not 0 <= warmups <= 3 or not 1 <= threads <= 64:
        raise ValueError("Invalid repeats, warmups or threads")
    if not engines or len(set(engines)) != len(engines) or any(e not in ENGINES+["datafusion"] for e in engines):
        raise ValueError("Invalid engines")
    if "datafusion" in engines and importlib.util.find_spec("datafusion") is None:
        raise ValueError("Install optional Rust engine: uv sync --extra rust")
    path = dataset(size)
    import pyarrow.parquet as pq
    rows = pq.read_metadata(path).num_rows
    if rows != SIZES[size]:
        raise ValueError(f"Expected {SIZES[size]} rows, got {rows}")
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8*1024*1024), b""):
            digest.update(block)
    expected = reference(path, operation)
    samples = {e: [] for e in engines}
    rng = random.Random(seed)
    order = []
    for iteration in range(warmups+repeats):
        current = list(engines)
        rng.shuffle(current)
        order.append(current)
        for engine in current:
            proc = subprocess.run([sys.executable, "-m", "app.worker", engine, operation, str(path), "--mode", mode, "--threads", str(threads)], cwd=ROOT/"backend", capture_output=True, text=True, timeout=300)
            if proc.returncode:
                raise RuntimeError(f"{engine}: {proc.stderr[-2000:]}")
            sample = json.loads(proc.stdout)
            if not equivalent(sample["value"], expected):
                raise RuntimeError(f"Validation failed: {engine}, {operation}")
            if iteration >= warmups:
                samples[engine].append(sample)
    results = [{"engine": e, "seconds": statistics.median(s["seconds"] for s in ss),
                "min_seconds": min(s["seconds"] for s in ss), "max_seconds": max(s["seconds"] for s in ss),
                "peak_rss_mb": statistics.median(s["peak_rss_mb"] for s in ss),
                "result": ss[0]["result"], "validated": True, "runs": ss} for e,ss in samples.items()]
    record = {"id": str(uuid4()), "created_at": datetime.now(timezone.utc).isoformat(), "schema_version": 2,
              "operation": operation, "repeats": repeats, "size": size, "rows": rows, "mode": mode,
              "warmups": warmups, "threads": threads, "seed": seed, "order": order, "validated": True,
              "dataset": {"name": path.name, "sha256": digest.hexdigest(), "bytes": path.stat().st_size},
              "environment": {"platform": platform.platform(), "machine": platform.machine(), "python": platform.python_version(), "cpu_count": os.cpu_count(), "versions": {e: importlib.metadata.version(e) for e in engines}}, "results": results}
    if persist:
        HISTORY.mkdir(exist_ok=True)
        target = HISTORY / (record["id"]+".json")
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(record, indent=2, allow_nan=False))
        temp.replace(target)
    return record


def history():
    records = []
    for path in sorted(HISTORY.glob("*.json"), key=lambda p:p.stat().st_mtime, reverse=True)[:100]:
        try:
            records.append(json.loads(path.read_text()))
        except (ValueError, OSError):
            continue
    return records


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sizes", nargs="+", choices=list(SIZES), default=list(SIZES))
    p.add_argument("--operations", nargs="+", choices=["average","daily","distance"], default=["average","daily","distance"])
    p.add_argument("--modes", nargs="+", choices=["end-to-end","in-memory"], default=["end-to-end","in-memory"])
    p.add_argument("--engines", nargs="+", default=ENGINES)
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--warmups", type=int, default=1)
    p.add_argument("--threads", type=int, default=4)
    a = p.parse_args()
    for size in a.sizes:
        for op in a.operations:
            for mode in a.modes:
                print(json.dumps(run(op,a.repeats,size,mode,a.engines,a.warmups,a.threads)), flush=True)

if __name__ == "__main__":
    main()
