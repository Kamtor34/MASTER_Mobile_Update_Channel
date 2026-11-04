#!/usr/bin/env python3
"""Merge a validated Sahadan browser-rescue JSON into the MASTER mobile delta channel."""
import argparse, csv, gzip, hashlib, json, subprocess, sys, tempfile, zipfile
from pathlib import Path
from tools.master_update_pipeline import FIELDS, load, norm, key, merge, write, sha, write_manifest

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--current',type=Path,required=True)
    p.add_argument('--incremental',type=Path,required=True)
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--public-url',required=True)
    p.add_argument('--incremental-public-url',required=True)
    a=p.parse_args()
    root=json.loads(a.input.read_text(encoding='utf-8-sig'))
    start=root.get('start_date'); end=root.get('end_date')
    if not start or not end: raise RuntimeError('Rescue JSON is missing date coverage')
    with tempfile.TemporaryDirectory() as td:
        out=Path(td)
        subprocess.run([sys.executable,'tools/package_sahadan_browser_json.py','--input',str(a.input),
          '--start',start,'--end',end,'--out-dir',str(out)],check=True)
        zpath=next(out.glob('*.zip'))
        with zipfile.ZipFile(zpath) as z:
            csv_name=next(n for n in z.namelist() if n.endswith('.csv.gz'))
            with z.open(csv_name) as src, tempfile.NamedTemporaryFile(suffix='.csv.gz') as tmp:
                tmp.write(src.read()); tmp.flush()
                incoming=load(Path(tmp.name))
    previous=json.loads(a.manifest.read_text(encoding='utf-8'))
    current=load(a.current) if a.current.exists() else {}
    if not incoming: raise RuntimeError('Rescue JSON produced zero finished rows')
    changed={}; new_count=updated_count=0
    for row in incoming.values():
        ik='id:'+row['match_id'] if row.get('match_id') else ''
        fk='fb:'+'\\x1f'.join(row.get(k,'') for k in ('date','league','home','away'))
        old_key=ik if ik and ik in current else (fk if fk in current else None)
        if old_key is None:
            nk=key(row); current[nk]=row; changed[nk]=row; new_count+=1
        else:
            old=current[old_key]; combined=merge(old,row); nk=key(combined)
            if norm(old)!=norm(combined):
                changed.pop(old_key,None); changed[nk]=combined; updated_count+=1
            if nk!=old_key: current.pop(old_key,None)
            current[nk]=combined
    rows=sorted(current.values(),key=lambda r:(r['date'],r['league'],r['home'],r['away']))
    delta=sorted(changed.values(),key=lambda r:(r['date'],r['league'],r['home'],r['away']))
    write(a.current,rows); write(a.incremental,delta)
    prev_version=previous.get('version','')
    latest=max([previous.get('latest_date',''),end])
    digest=sha(a.current); inc_digest=sha(a.incremental)
    changed_any=bool(delta)
    manifest={
      'schema':4,'enabled':bool(rows),
      'version':f'{latest}-rescue-{digest[:12]}' if changed_any else prev_version,
      'previous_version':prev_version if changed_any else previous.get('previous_version',''),
      'base_date':previous.get('base_date',''),
      'base_total_matches':int(previous.get('base_total_matches') or previous.get('total_matches') or 0),
      'latest_date':latest,
      'total_matches':int(previous.get('base_total_matches') or previous.get('total_matches') or 0)+len(rows),
      'new_matches':new_count,'updated_matches':updated_count,
      'package_rows':len(rows),'source_rows_seen':sum(v['match_count'] for v in root['days'].values()),
      'finished_rows_seen':len(incoming),'size_bytes':a.current.stat().st_size,
      'delta_url':a.public_url,'sha256':digest,
      'incremental_rows':len(delta),'incremental_size_bytes':a.incremental.stat().st_size,
      'incremental_url':a.incremental_public_url,'incremental_sha256':inc_digest,
      'key_strategy':'match_id_then_date_league_home_away','columns':FIELDS,
      'update_source':'sahadan_browser_rescue_json','rescue_start_date':start,'rescue_end_date':end
    }
    write_manifest(a.manifest,manifest)
    print(json.dumps({'status':'RESCUE_MERGED','start':start,'end':end,'incoming_rows':len(incoming),
      'new_matches':new_count,'updated_matches':updated_count,'package_rows':len(rows),
      'incremental_rows':len(delta),'version':manifest['version']},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
