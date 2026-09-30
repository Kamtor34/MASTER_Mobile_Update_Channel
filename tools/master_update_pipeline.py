#!/usr/bin/env python3
import argparse,csv,gzip,hashlib,json,time
from datetime import datetime,timedelta
from pathlib import Path
import requests

FIELDS=['match_id','date','time','league_id','league','home','away','home_score','away_score','ms1','msx','ms2','kg_var','kg_yok','alt25','ust25']
URL='https://www.sahadan.com/api/index/betting-service-bulletin-soccer?a=bs&e=bsbp&u=soccer&application=mackolik.com&language=tr&country=tr&date={}'
HEADERS={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36'}

def clean(v): return '' if v is None else str(v).strip()
def num(v):
    if v in (None,'','-'): return ''
    try:return f'{float(v):.6f}'.rstrip('0').rstrip('.')
    except:return clean(v)

def dates(a,b):
    x=datetime.strptime(a,'%Y-%m-%d').date(); y=datetime.strptime(b,'%Y-%m-%d').date()
    while x<=y: yield x.isoformat(); x+=timedelta(days=1)

def resolve(mid,name,opt):
    mid=clean(mid); name=clean(name).lower().replace(',','.'); opt=clean(opt); low=opt.lower().replace(',','.')
    if mid=='6' or 'kg' in name or 'karşılıklı' in name:
        if low in ('var','v'): return 'kg_var'
        if low in ('yok','y'): return 'kg_yok'
    if mid=='1' or name in ('ms','maç sonucu'):
        if opt=='1': return 'ms1'
        if opt.upper()=='X': return 'msx'
        if opt=='2': return 'ms2'
    if low in ('alt','a','under','üst','ust','ü','over') and (mid=='10' or '2.5' in name or not name):
        return 'alt25' if low in ('alt','a','under') else 'ust25'

def odds(match,names):
    out={}
    for m in match.get('markets',[]) or []:
        if not isinstance(m,dict): continue
        mid=clean(m.get('i')); mn=m.get('n','') or names.get(mid,'')
        for g in m.get('o',[]) or []:
            if not isinstance(g,dict): continue
            for o in g.get('l',[]) or []:
                if not isinstance(o,dict): continue
                c=resolve(mid,mn,o.get('n'))
                if c and c not in out: out[c]=num(o.get('v'))
    return out

def get_json(s,url):
    err=None
    for n in range(4):
        try:
            r=s.get(url,headers=HEADERS,timeout=30)
            if r.status_code==429 or r.status_code>=500:
                raise RuntimeError('HTTP '+str(r.status_code))
            r.raise_for_status(); j=r.json()
            if not isinstance(j,dict): raise RuntimeError('JSON root')
            return j
        except Exception as e:
            err=e
            if n<3: time.sleep(min(20,2**(n+1)))
    raise RuntimeError(str(err))

def fetch_day(s,d,rawdir=None):
    j=get_json(s,URL.format(d)); data=j.get('data')
    if not isinstance(data,dict) or not isinstance(data.get('soccer',[]),list): raise RuntimeError(d+': schema')
    if rawdir:
        rawdir.mkdir(parents=True,exist_ok=True)
        with gzip.open(rawdir/(d+'.json.gz'),'wt',encoding='utf-8') as f: json.dump(j,f,ensure_ascii=False,separators=(',',':'))
    names={clean(x.get('i')):x.get('n','') for x in (data.get('markets',[]) or []) if isinstance(x,dict)}
    rows=[]; raw=0
    for lg in data.get('soccer',[]) or []:
        if not isinstance(lg,dict): continue
        for m in lg.get('matches',[]) or []:
            if not isinstance(m,dict): continue
            raw+=1; a=m.get('ft_A'); b=m.get('ft_B')
            if a in (None,'') or b in (None,''): continue
            r={k:'' for k in FIELDS}
            r.update(match_id=clean(m.get('id')),date=d,time=clean(m.get('time') or m.get('time_str') or lg.get('time')),league_id=clean(lg.get('c_id')),league=clean(lg.get('title')),home=clean(m.get('team_A')),away=clean(m.get('team_B')),home_score=num(a),away_score=num(b))
            r.update(odds(m,names))
            if r['league'] and r['home'] and r['away']: rows.append(r)
    return rows,raw

def idkey(r): return ('id:'+clean(r.get('match_id'))) if clean(r.get('match_id')) else ''
def fbkey(r): return 'fb:'+'\x1f'.join(clean(r.get(k)) for k in ('date','league','home','away'))
def key(r): return idkey(r) or fbkey(r)
def norm(r): return {k:clean(r.get(k,'')) for k in FIELDS}
def merge(old,new):
    z=norm(old)
    for k in FIELDS:
        v=clean(new.get(k,''))
        if v!='': z[k]=v
    return z

def load(path):
    if not path.exists() or not path.stat().st_size:return {}
    out={}
    with gzip.open(path,'rt',encoding='utf-8-sig',newline='') as f:
        rd=csv.DictReader(f)
        if any(c not in (rd.fieldnames or []) for c in FIELDS): raise RuntimeError('delta schema')
        for r in rd:
            r=norm(r); out[key(r)]=r
    return out

def write(path,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(path,'wt',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=FIELDS); w.writeheader()
        for r in rows:w.writerow(norm(r))

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()

def main():
    p=argparse.ArgumentParser(); p.add_argument('--start',required=True);p.add_argument('--end',required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--existing-delta',type=Path);p.add_argument('--raw-dir',type=Path);p.add_argument('--manifest',type=Path,required=True);p.add_argument('--public-url',required=True);p.add_argument('--sleep',type=float,default=.5);a=p.parse_args()
    prev=json.loads(a.manifest.read_text(encoding='utf-8')); base_total=int(prev.get('base_total_matches') or prev.get('total_matches') or 0); base_date=prev.get('base_date') or prev.get('latest_date') or ''
    cur=load(a.existing_delta) if a.existing_delta and a.existing_delta.exists() else {}; newn=updn=rawtotal=0; fetched=[]; s=requests.Session()
    for d in dates(a.start,a.end):
        rr,raw=fetch_day(s,d,a.raw_dir); rawtotal+=raw; fetched+=rr; print(f'[OK] {d}: raw={raw} finished={len(rr)}'); time.sleep(max(0,a.sleep))
    if len(fetched)>=20:
        ids=sum(bool(x['match_id']) for x in fetched); ms=sum(bool(x['ms1'] and x['msx'] and x['ms2']) for x in fetched)
        if ids/len(fetched)<.95: raise RuntimeError(f'Maç_ID quality {ids}/{len(fetched)}')
        if ms==0: raise RuntimeError('MS market quality=0')
    for inc in fetched:
        inc=norm(inc); ik=idkey(inc); fk=fbkey(inc); ok=ik if ik and ik in cur else (fk if fk in cur else None)
        if not ok: cur[ik or fk]=inc; newn+=1; continue
        old=cur[ok]; m=merge(old,inc); nk=key(m)
        if norm(old)!=norm(m): updn+=1
        if nk!=ok: cur.pop(ok,None)
        cur[nk]=m
    rows=sorted(cur.values(),key=lambda r:(r['date'],r['league'],r['home'],r['away'])); write(a.out,rows)
    latest=max((r['date'] for r in rows),default=prev.get('latest_date') or a.start); digest=sha(a.out)
    manifest={'schema':3,'enabled':bool(rows),'version':f'{latest}-{int(time.time())}-{digest[:12]}','base_date':base_date,'base_total_matches':base_total,'latest_date':latest,'total_matches':base_total+len(rows),'new_matches':newn,'updated_matches':updn,'package_rows':len(rows),'source_rows_seen':rawtotal,'finished_rows_seen':len(fetched),'size_bytes':a.out.stat().st_size,'delta_url':a.public_url,'sha256':digest,'key_strategy':'match_id_then_date_league_home_away','columns':FIELDS}
    a.manifest.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps(manifest,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
