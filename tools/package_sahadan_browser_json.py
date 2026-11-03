#!/usr/bin/env python3
"""Convert the established Sahadan browser-rescue JSON into the channel ZIP format."""
import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import zipfile
from datetime import date, timedelta
from pathlib import Path

FIELDS = [
    "match_id", "date", "time", "league_id", "league", "home", "away",
    "home_score", "away_score", "ms1", "msx", "ms2", "kg_var", "kg_yok",
    "alt25", "ust25",
]


def clean(value):
    return "" if value is None else str(value).strip()


def numeric(value, label):
    if value in (None, "", "-"):
        return ""
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{label}: invalid numeric value {value!r}") from exc
    if not math.isfinite(number):
        raise RuntimeError(f"{label}: non-finite numeric value")
    return f"{number:.6f}".rstrip("0").rstrip(".")


def final_score(value, label):
    result = numeric(value, label)
    if not result:
        raise RuntimeError(f"{label}: finished match is missing a score")
    number = float(result)
    if number < 0 or not number.is_integer():
        raise RuntimeError(f"{label}: final score must be a nonnegative integer")
    return str(int(number))


def resolve_market(market_id, market_name, outcome_name):
    mid = clean(market_id)
    name = clean(market_name).lower().replace(",", ".")
    option = clean(outcome_name)
    low = option.lower().replace(",", ".")
    if mid == "6" or "kg" in name or "karşılıklı" in name:
        if low in ("var", "v"):
            return "kg_var"
        if low in ("yok", "y"):
            return "kg_yok"
    if mid == "1" or name in ("ms", "maç sonucu"):
        if option == "1":
            return "ms1"
        if option.upper() == "X":
            return "msx"
        if option == "2":
            return "ms2"
    if low in ("alt", "a", "under", "üst", "ust", "ü", "over") and (mid == "10" or "2.5" in name or not name):
        return "alt25" if low in ("alt", "a", "under") else "ust25"
    return None


def extract_odds(match, market_names):
    out = {}
    for market in match.get("markets") or []:
        if not isinstance(market, dict):
            continue
        market_id = clean(market.get("i"))
        market_name = market.get("n") or market_names.get(market_id, "")
        for provider in market.get("o") or []:
            if not isinstance(provider, dict) or clean(provider.get("b")) != "14":
                continue
            for outcome in provider.get("l") or []:
                if not isinstance(outcome, dict):
                    continue
                column = resolve_market(market_id, market_name, outcome.get("n"))
                if column and column not in out:
                    odd = numeric(outcome.get("v"), f"odds {column}")
                    if odd and float(odd) > 1:
                        out[column] = odd
    return out


def validate_row(row):
    try:
        date.fromisoformat(row["date"])
    except Exception as exc:
        raise RuntimeError(f"Invalid match date: {row.get('date')!r}") from exc
    for field in ("match_id", "league", "home", "away"):
        if not clean(row.get(field)):
            raise RuntimeError(f"Missing required match identity: {field}")
    for field in ("home_score", "away_score"):
        value = clean(row.get(field))
        if not value or not value.isdigit():
            raise RuntimeError(f"Invalid final score: {field}")
    for field in FIELDS[9:]:
        value = clean(row.get(field))
        if value and float(value) <= 1:
            raise RuntimeError(f"Invalid decimal odds in {field}")


def expected_dates(start, end):
    first = date.fromisoformat(start)
    last = date.fromisoformat(end)
    if first > last:
        raise RuntimeError("Start date is after end date")
    return [(first + timedelta(days=i)).isoformat() for i in range((last-first).days+1)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("."))
    args = parser.parse_args()
    dates = expected_dates(args.start, args.end)
    with args.input.open("r", encoding="utf-8-sig") as stream:
        root = json.load(stream)
    if not isinstance(root, dict) or not isinstance(root.get("days"), dict):
        raise RuntimeError("JSON root must contain a days object")
    if clean(root.get("start_date")) != args.start or clean(root.get("end_date")) != args.end:
        raise RuntimeError("JSON start_date/end_date do not match its filename")
    if set(root["days"]) != set(dates):
        missing = sorted(set(dates)-set(root["days"]))
        extra = sorted(set(root["days"])-set(dates))
        raise RuntimeError(f"Requested day coverage mismatch; missing={missing}, extra={extra}")

    rows_by_id = {}
    day_meta = {}
    source_rows_seen = 0
    total_finished_source = 0
    for day in dates:
        entry = root["days"][day]
        if not isinstance(entry, dict) or entry.get("ok") is not True or int(entry.get("http_status") or 0) != 200:
            raise RuntimeError(f"{day}: browser capture was not HTTP 200 / ok")
        payload = entry.get("payload")
        data = payload.get("data") if isinstance(payload, dict) else None
        soccer = data.get("soccer") if isinstance(data, dict) else None
        if not isinstance(soccer, list):
            raise RuntimeError(f"{day}: payload.data.soccer is missing")
        market_names = {
            clean(item.get("i")): clean(item.get("n"))
            for item in (data.get("markets") or []) if isinstance(item, dict)
        }
        raw_count = finished_count = ms_complete = 0
        for league in soccer:
            if not isinstance(league, dict):
                continue
            for match in league.get("matches") or []:
                if not isinstance(match, dict):
                    continue
                raw_count += 1
                a, b = match.get("ft_A"), match.get("ft_B")
                if a in (None, "") or b in (None, ""):
                    continue
                finished_count += 1
                row = {field: "" for field in FIELDS}
                row.update({
                    "match_id": clean(match.get("id")),
                    "date": day,
                    "time": clean(match.get("time") or match.get("time_str") or league.get("time")),
                    "league_id": clean(league.get("c_id")),
                    "league": clean(league.get("title")),
                    "home": clean(match.get("team_A")),
                    "away": clean(match.get("team_B")),
                    "home_score": final_score(a, f"{day} home score"),
                    "away_score": final_score(b, f"{day} away score"),
                })
                row.update(extract_odds(match, market_names))
                validate_row(row)
                if row["ms1"] and row["msx"] and row["ms2"]:
                    ms_complete += 1
                key = "id:" + row["match_id"]
                if key in rows_by_id:
                    if rows_by_id[key] != row:
                        raise RuntimeError(f"Conflicting duplicate match ID {row['match_id']}")
                    continue
                rows_by_id[key] = row
        if raw_count != int(entry.get("match_count", raw_count)):
            raise RuntimeError(f"{day}: source match_count metadata does not match payload ({entry.get('match_count')} vs {raw_count})")
        if finished_count != int(entry.get("finished_count", finished_count)):
            raise RuntimeError(f"{day}: source finished_count metadata does not match payload ({entry.get('finished_count')} vs {finished_count})")
        if finished_count == 0:
            raise RuntimeError(f"{day}: no finished matches in capture")
        source_rows_seen += raw_count
        total_finished_source += finished_count
        day_meta[day] = {
            "ok": True,
            "http_status": 200,
            "league_count": len(soccer),
            "match_count": raw_count,
            "finished_count": finished_count,
            "export_rows": finished_count,
            "export_rows_complete_ms_odds": ms_complete,
        }

    rows = sorted(rows_by_id.values(), key=lambda r: (r["date"], r["league"], r["home"], r["away"]))
    if len(rows) < 100:
        raise RuntimeError(f"Rescue file unexpectedly small: {len(rows)} rows")
    id_count = sum(bool(r["match_id"]) for r in rows)
    ms_count = sum(bool(r["ms1"] and r["msx"] and r["ms2"]) for r in rows)
    if id_count / len(rows) < 0.99:
        raise RuntimeError(f"Match_ID quality too low: {id_count}/{len(rows)}")
    if ms_count / len(rows) < 0.90:
        raise RuntimeError(f"MS odds quality too low: {ms_count}/{len(rows)}")
    if sorted({r["date"] for r in rows}) != dates:
        raise RuntimeError("Exported CSV does not contain every requested date")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"sahadan_rescue_rows_{args.start}_{args.end}"
    csv_name = stem + ".csv.gz"
    meta_name = stem + ".meta.json"
    package_name = f"sahadan_rescue_package_{args.start}_{args.end}.zip"
    csv_path = args.out_dir / csv_name
    meta_path = args.out_dir / meta_name
    package_path = args.out_dir / package_name

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    csv_buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=csv_buffer, mtime=0) as compressed:
        compressed.write(buffer.getvalue().encode("utf-8"))
    csv_bytes = csv_buffer.getvalue()

    meta = {
        "source": "Sahadan browser rescue JSON",
        "start_date": args.start,
        "end_date": args.end,
        "csv_file": csv_name,
        "csv_sha256": hashlib.sha256(csv_bytes).hexdigest(),
        "days": day_meta,
        "source_rows_seen": source_rows_seen,
        "finished_rows_seen": len(rows),
        "finished_source_rows_seen": total_finished_source,
        "complete_ms_rows": ms_count,
        "complete_ms_pct": round(ms_count * 100.0 / len(rows), 2),
    }
    meta_bytes = (json.dumps(meta, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    csv_path.write_bytes(csv_bytes)
    meta_path.write_bytes(meta_bytes)
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.write(csv_path, arcname=csv_name)
        archive.write(meta_path, arcname=meta_name)

    print(json.dumps({
        "status": "PACKAGE_READY",
        "package": str(package_path),
        "dates": f"{args.start}..{args.end}",
        "source_matches": source_rows_seen,
        "finished_rows": len(rows),
        "complete_ms_rows": ms_count,
        "complete_ms_pct": meta["complete_ms_pct"],
        "package_bytes": package_path.stat().st_size,
        "csv_sha256": meta["csv_sha256"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
