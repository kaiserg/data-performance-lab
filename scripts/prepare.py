"""Deterministic prefixes of the existing 10M dataset; never downloads."""
from pathlib import Path
import duckdb
ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    source = ROOT / "data" / "taxi-10m.parquet"
    con = duckdb.connect(config={"threads":1})
    if con.execute("SELECT COUNT(*) FROM read_parquet(?)", [str(source)]).fetchone()[0] != 10_000_000:
        raise SystemExit("Source must contain exactly 10M rows")
    for label, count in [("100k",100_000),("1m",1_000_000)]:
        dest = ROOT / "data" / f"taxi-{label}.parquet"
        temp = dest.with_suffix(".tmp.parquet")
        con.execute(f"COPY (SELECT * FROM read_parquet(?) LIMIT {count}) TO '{str(temp).replace(chr(39),chr(39)*2)}' (FORMAT PARQUET, COMPRESSION ZSTD)", [str(source)])
        temp.replace(dest)
        print(dest.name, count)
