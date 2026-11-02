#!/usr/bin/env python3
"""Authenticated on-demand runner for the existing MASTER mobile update pipeline."""
import fcntl
import json
import os
import secrets
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

ROOT = Path(os.getenv("WORKSPACE", "/data/workspace"))
REPO = "Kamtor34/MASTER_Mobile_Update_Channel"
BRANCH = os.getenv("GIT_BRANCH", "main")
TOKEN = os.environ["UPDATE_TRIGGER_TOKEN"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
LOCK = Path("/data/update.lock")
STATE = Path("/data/update-state.json")
PIPELINE = ROOT / "tools/master_update_pipeline.py"
MANIFEST = ROOT / "updates/manifest.json"
CURRENT = ROOT / "updates/releases/master_delta_current.csv.gz"
INCREMENTAL = ROOT / "updates/releases/master_delta_incremental.csv.gz"
RAW = ROOT / ".tmp/raw"
BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/updates/releases"
app = FastAPI(title="MASTER Update Service", docs_url=None, redoc_url=None)


def git(*args, timeout=120):
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    p = subprocess.run(["git", *args], cwd=ROOT, env=env, text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    if p.returncode:
        raise RuntimeError("git command failed: " + p.stdout[-2500:])
    return p.stdout.strip()


def ensure_checkout():
    ROOT.parent.mkdir(parents=True, exist_ok=True)
    if not (ROOT / ".git").exists():
        auth_url = f"https://x-access-token:{GITHUB_TOKEN}@github.com/{REPO}.git"
        subprocess.run(["git", "clone", "--branch", BRANCH, "--depth", "1", auth_url, str(ROOT)],
                       check=True, timeout=180, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    else:
        git("fetch", "origin", BRANCH)
        git("checkout", BRANCH)
        git("reset", "--hard", f"origin/{BRANCH}")


def read_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


@app.get("/health")
def health():
    return {"ok": True, "service": "master-update", "time": datetime.now(ZoneInfo("Europe/Istanbul")).isoformat()}


@app.post("/v1/update")
def update(authorization: str = Header(default="")):
    supplied = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(supplied, TOKEN):
        raise HTTPException(status_code=401, detail="unauthorized")

    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("w") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise HTTPException(status_code=409, detail="update already running")

        state = read_state()
        now = time.time()
        if now - float(state.get("last_started_epoch", 0)) < 180:
            raise HTTPException(status_code=429, detail="please wait before retrying")
        state["last_started_epoch"] = now
        STATE.write_text(json.dumps(state))

        try:
            ensure_checkout()
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
            latest = datetime.strptime(manifest["latest_date"][:10], "%Y-%m-%d").date()
            today = datetime.now(ZoneInfo("Europe/Istanbul")).date()
            start = latest - timedelta(days=7) if CURRENT.exists() and CURRENT.stat().st_size else latest + timedelta(days=1)
            if start > today:
                start = today
            end = today
            RAW.mkdir(parents=True, exist_ok=True)
            cmd = [
                "python", str(PIPELINE), "--start", start.isoformat(), "--end", end.isoformat(),
                "--existing-delta", str(CURRENT), "--out", str(CURRENT),
                "--incremental-out", str(INCREMENTAL), "--raw-dir", str(RAW),
                "--manifest", str(MANIFEST),
                "--public-url", f"{BASE}/master_delta_current.csv.gz",
                "--incremental-public-url", f"{BASE}/master_delta_incremental.csv.gz",
                "--sleep", "0.5",
            ]
            p = subprocess.run(cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, timeout=900)
            if p.returncode:
                raise RuntimeError(p.stdout[-5000:])

            # Verify package hashes and row counts before publishing.
            check = subprocess.run(["python", "-c", """
import csv,gzip,hashlib,json
from pathlib import Path
m=json.loads(Path('updates/manifest.json').read_text())
for path,key,rows_key in [
 ('updates/releases/master_delta_current.csv.gz','sha256','package_rows'),
 ('updates/releases/master_delta_incremental.csv.gz','incremental_sha256','incremental_rows')]:
 p=Path(path); assert p.exists() and hashlib.sha256(p.read_bytes()).hexdigest()==m[key]
 with gzip.open(p,'rt',encoding='utf-8',newline='') as f: n=sum(1 for _ in csv.DictReader(f))
 assert n==m[rows_key], (n,m[rows_key])
assert m['enabled'] is True
"""], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if check.returncode:
                raise RuntimeError("package validation failed: " + check.stdout[-2000:])

            git("config", "user.name", "MASTER Update Service")
            git("config", "user.email", "master-update-service@users.noreply.github.com")
            git("add", "updates/manifest.json", "updates/releases/master_delta_current.csv.gz",
                "updates/releases/master_delta_incremental.csv.gz")
            changed = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode != 0
            if changed:
                git("commit", "-m", f"MASTER mobile delta: {end.isoformat()}")
                git("push", "origin", BRANCH, timeout=180)

            published = json.loads(MANIFEST.read_text(encoding="utf-8"))
            result = {
                "ok": True, "published": changed, "start_date": start.isoformat(),
                "end_date": end.isoformat(), "latest_date": published.get("latest_date"),
                "version": published.get("version"), "total_matches": published.get("total_matches"),
                "new_matches": published.get("new_matches"), "updated_matches": published.get("updated_matches"),
                "incremental_rows": published.get("incremental_rows"),
                "message": "validated and published" if changed else "already up to date",
            }
            state.update({"last_success_epoch": time.time(), "last_result": result})
            STATE.write_text(json.dumps(state))
            return result
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="update timed out; existing published package was retained")
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)[-2500:])
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
