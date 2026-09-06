from __future__ import annotations

import os
import sys
from pathlib import Path

MARKER = "# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER = "# === ONEWAYPICKZ V5.9.3 DIRECT HLTV MAP CARD PARSER ==="

OVERLAY = r'''
# === ONEWAYPICKZ V5.9.3 DIRECT HLTV MAP CARD PARSER ===
# Team/map data parser only. Projection math is unchanged.
AUTOFEED_MAPS_V593_VERSION = "5.9.3"
V593_MAP_HEALTH_FILE = os.path.join(STORAGE_DIR, "cs2_hltv_map_health.json")
V593_MAP_HEALTH = {"requests": 0, "ok": 0, "maps_parsed": 0, "sample_maps": 0, "last_error": ""}


def _v593_save_health(extra=None):
    try:
        payload={"version":"5.9.3","updated_at":now_iso(),**dict(V593_MAP_HEALTH)}
        if isinstance(extra,dict): payload.update(extra)
        save_json(V593_MAP_HEALTH_FILE,payload,force=True)
    except Exception: pass


def _v593_map_label(name):
    return "Dust2" if name == "Dust2" else name


def _v591_team_map_pool(team_id, slug, days=120):
    tid=str(team_id or "").strip()
    if not tid.isdigit(): return {}, {"ok":False,"warning":"numeric HLTV team id required"}
    start,end=_period_dates(max(int(days or 120),1))
    url=f"https://www.hltv.org/stats/teams/maps/{tid}/{slug or 'team'}"
    V593_MAP_HEALTH["requests"]=int(V593_MAP_HEALTH.get("requests") or 0)+1
    page,status=_v591_direct_get(url,params={"startDate":start,"endDate":end,"csVersion":"CS2"},ttl=4*3600,timeout=24)
    if not page:
        V593_MAP_HEALTH["last_error"]=str((status or {}).get("warning") or "team map page unavailable")
        _v593_save_health({"last_status":status}); return {}, {**dict(status or {}),"ok":False,"maps_found":0,"sample_maps":0}
    text=strip_tags(page).replace("\xa0"," ")
    marker=re.search(r"Map\s+overview",text,re.I)
    if marker: text=text[marker.end():]
    pool={}
    for map_name in KNOWN_MAPS:
        label="Dust2" if map_name=="Dust2" else map_name
        # Current HLTV map cards expose exact W/D/L and win rate.  W+D+L is an
        # auditable played-map sample and is safer than guessing from unrelated numbers.
        pat=rf"\b{re.escape(label)}\b[\s\S]{{0,1300}}?Wins\s*/\s*draws\s*/\s*losses\s*(\d+)\s*/\s*(\d+)\s*/\s*(\d+)[\s\S]{{0,500}}?Win\s*rate\s*(\d+(?:\.\d+)?)\s*%"
        m=re.search(pat,text,re.I)
        if not m and map_name=="Dust2":
            pat=rf"\bDust\s*II\b[\s\S]{{0,1300}}?Wins\s*/\s*draws\s*/\s*losses\s*(\d+)\s*/\s*(\d+)\s*/\s*(\d+)[\s\S]{{0,500}}?Win\s*rate\s*(\d+(?:\.\d+)?)\s*%"
            m=re.search(pat,text,re.I)
        if not m: continue
        wins,draws,losses=[int(m.group(i)) for i in (1,2,3)]
        played=wins+draws+losses
        if played<=0: continue
        pool[map_name]={"maps":played,"wins":wins,"draws":draws,"losses":losses,"win_pct":float(m.group(4)),"source":str((status or {}).get("url") or url),"period_days":int(days or 120)}
    sample=sum(int((v or {}).get("maps") or 0) for v in pool.values())
    ok=bool(pool and sample>0)
    if ok:
        V593_MAP_HEALTH["ok"]=int(V593_MAP_HEALTH.get("ok") or 0)+1
        V593_MAP_HEALTH["maps_parsed"]=int(V593_MAP_HEALTH.get("maps_parsed") or 0)+len(pool)
        V593_MAP_HEALTH["sample_maps"]=int(V593_MAP_HEALTH.get("sample_maps") or 0)+sample
    else:
        V593_MAP_HEALTH["last_error"]="No W/D/L map cards parsed from current HLTV team page"
    out_status={**dict(status or {}),"ok":ok,"provider":"HLTV direct current map cards v5.9.3","maps_found":len(pool),"sample_maps":sample,"period_days":int(days or 120)}
    _v593_save_health({"last_status":out_status,"last_team_id":tid,"last_slug":slug})
    return pool,out_status


try: APP_VERSION="CS2 v5.9.3 — DIRECT HLTV MAP CARD PARSER"
except Exception: pass
# === END ONEWAYPICKZ V5.9.3 DIRECT HLTV MAP CARD PARSER ===
'''


def patch_text(source: str) -> str:
    if PATCH_MARKER in source: return source
    if MARKER not in source: raise RuntimeError("SESSION BOARD LOAD marker not found")
    return source.replace(MARKER,OVERLAY+"\n\n"+MARKER,1)


def patch_app(path="app.py"):
    p=Path(path); old=p.read_text(encoding="utf-8"); new=patch_text(old); changed=new!=old
    if changed:
        tmp=p.with_suffix(p.suffix+".v593.tmp"); tmp.write_text(new,encoding="utf-8"); os.replace(tmp,p)
    return changed


if __name__=="__main__":
    p=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).with_name("app.py")
    changed=patch_app(p); compile(p.read_text(encoding="utf-8"),str(p),"exec")
    print(f"v5.9.3 patch {'applied' if changed else 'already present'}: {p}")