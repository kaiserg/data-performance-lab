"""Download official NYC TLC Yellow Taxi monthly Parquet files and create a 10M-row local dataset."""
from pathlib import Path
import requests, duckdb
ROOT=Path(__file__).resolve().parents[1]
out=ROOT/"data"/"taxi-10m.parquet"
if out.exists():
    raise SystemExit("10M dataset already exists; preserved. Run prepare.py for smaller sizes.")
raw=ROOT/"data"/"raw"
raw.mkdir(parents=True,exist_ok=True)
# 2023 months: older stable published schema, source: NYC TLC official data portal.
for month in range(1,9):
    name=f"yellow_tripdata_2023-{month:02d}.parquet"
    dest=raw/name
    if dest.exists(): continue
    url=f"https://d37ci6vzurychx.cloudfront.net/trip-data/{name}"
    print("Downloading",url,flush=True)
    with requests.get(url,stream=True,timeout=90) as res:
        res.raise_for_status()
        with dest.with_suffix(".part").open("wb") as f:
            for chunk in res.iter_content(1024*1024):
                if chunk:f.write(chunk)
    dest.with_suffix(".part").replace(dest)
files=sorted(raw.glob("*.parquet"))
con=duckdb.connect(config={"threads":1})
source="["+",".join("'"+str(f).replace("'","''")+"'" for f in files)+"]"
temp=out.with_suffix(".tmp.parquet")
print("Building",out,flush=True)
con.execute(f"COPY (SELECT tpep_pickup_datetime, trip_distance, fare_amount FROM read_parquet({source}, union_by_name=true) LIMIT 10000000) TO '{str(temp).replace(chr(39), chr(39)*2)}' (FORMAT PARQUET, COMPRESSION ZSTD)")
if con.execute("SELECT COUNT(*) FROM read_parquet(?)",[str(temp)]).fetchone()[0] != 10_000_000:
    temp.unlink()
    raise SystemExit("Insufficient source rows for 10M dataset")
temp.replace(out)
print("Rows:",con.execute("SELECT COUNT(*) FROM read_parquet(?)",[str(out)]).fetchone()[0])
