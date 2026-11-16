"""One fresh process per measured sample. Legacy positional CLI is preserved."""
import argparse
import json
import os
import sys
import threading
import time

COLUMNS = ["tpep_pickup_datetime", "trip_distance", "fare_amount"]


def execute(engine, operation, path, mode="end-to-end", threads=4):
    import psutil
    peak = [psutil.Process().memory_info().rss]
    stop = threading.Event()
    def sample():
        while not stop.wait(0.005):
            peak[0] = max(peak[0], psutil.Process().memory_info().rss)
    monitor = threading.Thread(target=sample, daemon=True)
    monitor.start()
    start = time.perf_counter()
    try:
        if engine == "pandas":
            import pandas as pd
            df = pd.read_parquet(path, columns=COLUMNS)
            query_start = time.perf_counter()
            if operation == "daily":
                groups = df.groupby(df.tpep_pickup_datetime.dt.date, dropna=False).size()
                value = {str(k) if pd.notna(k) else "null": int(v) for k, v in groups.items()}
            elif operation == "average":
                value = float(df.fare_amount.mean()) if df.fare_amount.notna().any() else None
            else:
                value = int((df.trip_distance > 5).sum())
        elif engine == "polars":
            import polars as pl
            df = pl.read_parquet(path, columns=COLUMNS).lazy() if mode == "in-memory" else pl.scan_parquet(path)
            query_start = time.perf_counter()
            if operation == "daily":
                rows = df.group_by(pl.col(COLUMNS[0]).dt.date().alias("day")).agg(pl.len().alias("n")).collect().rows()
                value = {str(k) if k is not None else "null": int(v) for k, v in rows}
            elif operation == "average":
                value = df.select(pl.col("fare_amount").mean()).collect().item()
            else:
                value = df.filter(pl.col("trip_distance") > 5).select(pl.len()).collect().item()
        else:
            sql = {"average": "SELECT AVG(fare_amount) FROM trips", "distance": "SELECT COUNT(*) FROM trips WHERE trip_distance > 5", "daily": "SELECT CAST(tpep_pickup_datetime AS DATE) AS day, COUNT(*) AS n FROM trips GROUP BY day"}[operation]
            if engine == "duckdb":
                import duckdb
                con = duckdb.connect(config={"threads": threads})
                if mode == "in-memory":
                    con.execute("CREATE TABLE trips AS SELECT tpep_pickup_datetime, trip_distance, fare_amount FROM read_parquet(?)", [path])
                else:
                    con.read_parquet(path).create_view("trips")
                query_start = time.perf_counter()
                rows = con.execute(sql).fetchall()
                con.close()
            elif engine == "datafusion":
                from datafusion import SessionContext, SessionConfig
                ctx = SessionContext(SessionConfig().with_target_partitions(threads))
                if mode == "in-memory":
                    import pyarrow.parquet as pq
                    ctx.register_record_batches("trips", [pq.read_table(path, columns=COLUMNS).to_batches()])
                else:
                    ctx.register_parquet("trips", path)
                query_start = time.perf_counter()
                batches = ctx.sql(sql).collect()
                rows = [tuple(row.values()) for batch in batches for row in batch.to_pylist()]
            else:
                raise ValueError("Unknown engine")
            value = {str(k) if k is not None else "null": int(v) for k,v in rows} if operation == "daily" else rows[0][0]
        end = time.perf_counter()
        peak[0] = max(peak[0], psutil.Process().memory_info().rss)
        # OS high-water mark captures short allocation spikes on Unix; sampled RSS supports Windows.
        try:
            import resource
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            peak[0] = max(peak[0], rss if sys.platform == "darwin" else rss * 1024)
        except ImportError:
            pass
        scalar = sum(value.values()) if operation == "daily" else value
        return {"engine": engine, "operation": operation, "mode": mode,
                "seconds": end - (query_start if mode == "in-memory" else start),
                "setup_seconds": query_start-start, "query_seconds": end-query_start,
                "result": scalar, "value": value, "peak_rss_mb": round(peak[0]/1048576, 2)}
    finally:
        stop.set()
        monitor.join()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("engine", choices=["pandas", "polars", "duckdb", "datafusion"])
    p.add_argument("operation", choices=["average", "daily", "distance"])
    p.add_argument("path")
    p.add_argument("--mode", choices=["end-to-end", "in-memory"], default="end-to-end")
    p.add_argument("--threads", type=int, default=4)
    a = p.parse_args()
    os.environ["POLARS_MAX_THREADS"] = str(a.threads)
    os.environ["ARROW_NUM_THREADS"] = str(a.threads)
    print(json.dumps(execute(a.engine, a.operation, a.path, a.mode, a.threads), allow_nan=False))

if __name__ == "__main__":
    main()
