#!/usr/bin/env python3
import csv, gzip, hashlib, io, json, os, zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(".")
INCOMING = ROOT / "incoming" / "sahadan_rescue_2026-10-31_2026-11-04.zip"
MANIFEST = ROOT / "updates" / "manifest.json"
CURRENT = ROOT / "updates/releases/master_delta_current.csv.gz"
INCREMENTAL = ROOT / "updates/releases/master_delta_incremental.csv.gz"
FIELDS = ["match_id","date","time","league_id","league","home","away","home_score","away_score","ms1","msx","ms2","kg_var","kg_yok","alt25","ust25"]

def read_gz_csv(path):
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))

def write_gz_csv(rows, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = io.StringIO(newline="")
    w = csv.DictWriter(raw, fieldnames=FIELDS, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for row in rows:
        w.writerow({k: (row.get(k) or "") for k in FIELDS})
    data = raw.getvalue().encode("utf-8")
    with open(path, "wb") as out:
        with gzip.GzipFile(fileobj=out, mode="wb", mtime=0, filename="") as gz:
            gz.write(data)
    return path.read_bytes()

def key(row):
    mid = (row.get("match_id") or "").strip()
    if mid:
        return "id:" + mid
    return "fallback:" + "|".join((row.get(k) or "").strip().casefold() for k in ("date","league","home","away"))

def main():
    if not INCOMING.exists():
        raise SystemExit(f"Incoming package missing: {INCOMING}")
    with zipfile.ZipFile(INCOMING) as z:
        csv_names = [n for n in z.namelist() if n.endswith(".csv.gz")]
        meta_names = [n for n in z.namelist() if n.endswith(".meta.json")]
        if len(csv_names) != 1 or len(meta_names) != 1:
            raise SystemExit("Package must contain exactly one CSV.GZ and one metadata JSON")
        payload = z.read(csv_names[0])
        meta = json.loads(z.read(meta_names[0]).decode("utf-8"))
    if hashlib.sha256(payload).hexdigest() != meta.get("csv_sha256"):
        raise SystemExit("Incoming CSV SHA-256 does not match metadata")
    if int(meta.get("finished_rows_seen", 0)) < 100 or float(meta.get("complete_ms_pct", 0)) < 90:
        raise SystemExit("Incoming data quality gate failed")
    with gzip.GzipFile(fileobj=io.BytesIO(payload), mode="rb") as gz:
        text = io.TextIOWrapper(gz, encoding="utf-8", newline="")
        incoming = list(csv.DictReader(text))
    if len(incoming) != int(meta["finished_rows_seen"]):
        raise SystemExit("Incoming finished row count mismatch")
    if not incoming or not all((r.get("date") or "").strip() for r in incoming):
        raise SystemExit("Incoming rows missing dates")
    days = set((r.get("date") or "").strip() for r in incoming)
    expected_days = set(meta.get("days", {}).keys())
    if not expected_days.issubset(days):
        # A date may have no finished matches only if metadata explicitly says zero.
        missing = [d for d in expected_days if d not in days and int(meta["days"][d].get("finished_count", 0)) > 0]
        if missing:
            raise SystemExit("Incoming package is missing finished dates: " + ",".join(missing))

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    existing = read_gz_csv(CURRENT)
    by_key = {}
    for row in existing:
        by_key[key(row)] = {k: (row.get(k) or "") for k in FIELDS}
    incremental = {}
    new_count = 0
    updated_count = 0
    for src in incoming:
        row = {k: (src.get(k) or "").strip() for k in FIELDS}
        k = key(row)
        if k not in by_key:
            by_key[k] = row
            incremental[k] = row
            new_count += 1
        else:
            old = by_key[k]
            merged = dict(old)
            changed = False
            for field in FIELDS:
                value = row.get(field, "")
                if value and value != old.get(field, ""):
                    merged[field] = value
                    changed = True
            if changed:
                by_key[k] = merged
                incremental[k] = merged
                updated_count += 1

    all_rows = list(by_key.values())
    all_rows.sort(key=lambda r: ((r.get("date") or ""), (r.get("time") or ""), (r.get("match_id") or "")))
    inc_rows = list(incremental.values())
    inc_rows.sort(key=lambda r: ((r.get("date") or ""), (r.get("time") or ""), (r.get("match_id") or "")))
    current_bytes = write_gz_csv(all_rows, CURRENT)
    incremental_bytes = write_gz_csv(inc_rows, INCREMENTAL)
    current_sha = hashlib.sha256(current_bytes).hexdigest()
    incremental_sha = hashlib.sha256(incremental_bytes).hexdigest()

    dates = [r["date"] for r in all_rows if r.get("date")]
    latest = max([manifest.get("latest_date", "")] + dates + [meta.get("end_date", "")])
    old_version = manifest.get("version", "")
    version_stamp = int(datetime.now(timezone.utc).timestamp())
    manifest.update({
        "schema": 4,
        "enabled": True,
        "version": f"{latest}-{version_stamp}-{current_sha[:12]}",
        "previous_version": old_version,
        "latest_date": latest,
        "total_matches": int(manifest.get("total_matches", 0)) + new_count,
        "new_matches": new_count,
        "updated_matches": updated_count,
        "package_rows": len(all_rows),
        "source_rows_seen": int(meta.get("source_rows_seen", 0)),
        "finished_rows_seen": int(meta.get("finished_rows_seen", 0)),
        "size_bytes": len(current_bytes),
        "sha256": current_sha,
        "incremental_rows": len(inc_rows),
        "incremental_size_bytes": len(incremental_bytes),
        "incremental_sha256": incremental_sha,
        "key_strategy": "match_id_then_date_league_home_away",
        "columns": FIELDS
    })
    manifest["delta_url"] = "https://raw.githubusercontent.com/Kamtor34/MASTER_Mobile_Update_Channel/main/updates/releases/master_delta_current.csv.gz"
    manifest["incremental_url"] = "https://raw.githubusercontent.com/Kamtor34/MASTER_Mobile_Update_Channel/main/updates/releases/master_delta_incremental.csv.gz"
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    INCOMING.unlink()
    print(json.dumps({
        "new_matches": new_count,
        "updated_matches": updated_count,
        "cumulative_rows": len(all_rows),
        "incremental_rows": len(inc_rows),
        "latest_date": latest,
        "current_sha256": current_sha,
        "incremental_sha256": incremental_sha,
        "manifest_version": manifest["version"]
    }, ensure_ascii=False))

if __name__ == "__main__":
    main()
