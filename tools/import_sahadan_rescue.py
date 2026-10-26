#!/usr/bin/env python3
"""One-time, validated importer for the Sahadan browser-rescue CSV package."""
import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

FIELDS = [
    "match_id", "date", "time", "league_id", "league", "home", "away",
    "home_score", "away_score", "ms1", "msx", "ms2", "kg_var", "kg_yok",
    "alt25", "ust25",
]


def clean(value):
    return "" if value is None else str(value).strip()


def number(value):
    value = clean(value)
    if value in ("", "-"):
        return ""
    try:
        result = float(value.replace(",", "."))
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Invalid numeric value in imported CSV") from exc
    if not math.isfinite(result):
        raise RuntimeError("Non-finite numeric value in imported CSV")
    return f"{result:.6f}".rstrip("0").rstrip(".")


def validate_row(row):
    try:
        datetime.strptime(row["date"], "%Y-%m-%d")
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeError("Invalid date in imported/current row") from exc
    if not all(clean(row.get(k)) for k in ("match_id", "league", "home", "away")):
        raise RuntimeError("Missing match ID / league / team identity")
    for k in ("home_score", "away_score"):
        v = number(row.get(k))
        if not v:
            raise RuntimeError("Imported/current match is missing a full-time score")
        f = float(v)
        if f < 0 or not f.is_integer():
            raise RuntimeError("Full-time score must be a nonnegative integer")
    for k in FIELDS[9:]:
        v = number(row.get(k))
        if v and float(v) <= 1:
            raise RuntimeError("Stored odds must be greater than 1")


def normalize(row):
    return {k: clean(row.get(k, "")) for k in FIELDS}


def identity(row):
    mid = clean(row.get("match_id"))
    if mid:
        return "id:" + mid
    return "fb:" + "\x1f".join(clean(row.get(k)) for k in ("date", "league", "home", "away"))


def read_gzip_csv(path):
    result = {}
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if any(field not in (reader.fieldnames or []) for field in FIELDS):
            raise RuntimeError("Imported/current CSV has an unexpected schema")
        for raw in reader:
            row = normalize(raw)
            validate_row(row)
            key = identity(row)
            if key in result:
                if result[key] != row:
                    raise RuntimeError("Conflicting duplicate identity in CSV: " + key)
                continue
            result[key] = row
    return result


def atomic_write_gzip_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=FIELDS)
                    writer.writeheader()
                    for row in rows:
                        row = normalize(row)
                        validate_row(row)
                        writer.writerow(row)
            raw.flush()
            os.fsync(raw.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_manifest(path, manifest):
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--source-rows-seen", type=int, required=True)
    args = parser.parse_args()
    try:
        start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
        end_date = datetime.strptime(args.end, "%Y-%m-%d").date()
    except ValueError as exc:
        raise RuntimeError("Start/end must be YYYY-MM-DD") from exc
    if start_date > end_date:
        raise RuntimeError("Start date is after end date")

    current = Path("updates/releases/master_delta_current.csv.gz")
    incremental = Path("updates/releases/master_delta_incremental.csv.gz")
    manifest_path = Path("updates/manifest.json")
    if not args.input.exists() or not args.input.stat().st_size:
        raise RuntimeError("Rescue import file is missing or empty")
    if not current.exists() or not current.stat().st_size:
        raise RuntimeError("Current published package is missing; nothing will be overwritten")

    previous = json.loads(manifest_path.read_text(encoding="utf-8"))
    old_version = clean(previous.get("version"))
    base_total = int(previous.get("base_total_matches") or previous.get("total_matches") or 0)

    existing = read_gzip_csv(current)
    imported = read_gzip_csv(args.input)
    if len(imported) < 100:
        raise RuntimeError(f"Rescue file unexpectedly small: {len(imported)} rows")

    imported_dates = sorted({row["date"] for row in imported.values()})
    if not imported_dates or imported_dates[0] != args.start or imported_dates[-1] != args.end:
        raise RuntimeError(f"Unexpected imported date coverage: {imported_dates[:1]} -> {imported_dates[-1:]}")
    expected_dates = {
        (start_date + timedelta(days=i)).isoformat()
        for i in range((end_date - start_date).days + 1)
    }
    if set(imported_dates) != expected_dates:
        raise RuntimeError("At least one expected day is missing from the rescue file")

    # Requiring IDs and full-time scores prevents bulletin/live-only entries entering the archive.
    ids = sum(bool(row["match_id"]) for row in imported.values())
    if ids / len(imported) < 0.99:
        raise RuntimeError(f"Rescue file ID quality too low: {ids}/{len(imported)}")
    main_market = sum(bool(row["ms1"] and row["msx"] and row["ms2"]) for row in imported.values())
    if main_market / len(imported) < 0.90:
        raise RuntimeError(f"Rescue file MS market quality too low: {main_market}/{len(imported)}")

    changed_rows = {}
    new_count = 0
    updated_count = 0
    for imported_row in imported.values():
        import_key = identity(imported_row)
        fallback_key = "fb:" + "\x1f".join(
            clean(imported_row.get(k)) for k in ("date", "league", "home", "away")
        )
        old_key = import_key if import_key in existing else (
            fallback_key if fallback_key in existing else None
        )
        if old_key is None:
            existing[import_key] = imported_row
            changed_rows[import_key] = imported_row
            new_count += 1
            continue

        old_row = existing[old_key]
        merged = dict(old_row)
        for field in FIELDS:
            new_value = clean(imported_row.get(field))
            # Match the production pipeline behavior: source blanks do not erase known values.
            if new_value:
                merged[field] = new_value
        new_key = identity(merged)
        if normalize(old_row) != normalize(merged):
            updated_count += 1
            changed_rows.pop(old_key, None)
            changed_rows[new_key] = merged
        if new_key != old_key:
            existing.pop(old_key, None)
        existing[new_key] = merged

    rows = sorted(existing.values(), key=lambda row: (row["date"], row["league"], row["home"], row["away"]))
    changed = sorted(changed_rows.values(), key=lambda row: (row["date"], row["league"], row["home"], row["away"]))
    if not changed:
        raise RuntimeError("Import caused no validated changes; published package and manifest were not updated")

    atomic_write_gzip_csv(current, rows)
    atomic_write_gzip_csv(incremental, changed)
    latest = max(row["date"] for row in rows)
    current_sha = sha256(current)
    incremental_sha = sha256(incremental)
    new_version = f"{latest}-{int(time.time())}-{current_sha[:12]}"

    manifest = {
        "schema": 4,
        "enabled": bool(rows),
        "version": new_version,
        "previous_version": old_version,
        "base_date": previous.get("base_date") or EXPECTED_START,
        "base_total_matches": base_total,
        "latest_date": latest,
        "total_matches": base_total + len(rows),
        "new_matches": new_count,
        "updated_matches": updated_count,
        "package_rows": len(rows),
        "source_rows_seen": args.source_rows_seen,
        "finished_rows_seen": len(imported),
        "size_bytes": current.stat().st_size,
        "delta_url": previous["delta_url"],
        "sha256": current_sha,
        "incremental_rows": len(changed),
        "incremental_size_bytes": incremental.stat().st_size,
        "incremental_url": previous["incremental_url"],
        "incremental_sha256": incremental_sha,
        "key_strategy": "match_id_then_date_league_home_away",
        "columns": FIELDS,
    }
    write_manifest(manifest_path, manifest)

    # Read-back validation before allowing the workflow to commit.
    check = read_gzip_csv(current)
    delta_check = read_gzip_csv(incremental)
    if len(check) != len(rows) or len(delta_check) != len(changed):
        raise RuntimeError("Generated package row count validation failed")
    final_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if sha256(current) != final_manifest["sha256"] or sha256(incremental) != final_manifest["incremental_sha256"]:
        raise RuntimeError("Generated package hash validation failed")

    print(json.dumps({
        "status": "IMPORT_VALIDATED",
        "previous_version": old_version,
        "version": new_version,
        "latest_date": latest,
        "new_matches": new_count,
        "updated_matches": updated_count,
        "package_rows": len(rows),
        "incremental_rows": len(changed),
        "source_rows_seen": args.source_rows_seen,
        "finished_rows_seen": len(imported),
        "sha256": current_sha,
        "incremental_sha256": incremental_sha,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
