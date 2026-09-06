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


def _v593_num_from_stat(chunk, label):
    m=re.search(rf'<div\s+class=["\']stat["\']>\s*(\d+)\s*</div>\s*<div\s+class=["\']description["\']>\s*{re.escape(label)}\s*</div>',chunk,re.I|re.S)
    return int(m.group(1)) if m else 0


def _v591_team_map_pool(team_id, slug, days=120):
    tid=str(team_id or "").strip()
    if not tid.isdigit(): return {}, {"ok":False,"warning":"numeric HLTV team id required"}
    # The HLTV /stats/ tree can return 403 to cloud runners while the canonical
    # public team page remains available and contains the same last-3-month map
    # cards (map, win %, exact W/D/L, veto usage). Use that verified page.
    url=f"https://www.hltv.org/team/{tid}/{slug or 'team'}"
    V593_MAP_HEALTH["requests"]=int(V593_MAP_HEALTH.get("requests") or 0)+1
    page,status=_v591_direct_get(url,ttl=4*3600,timeout=30)
    if not page:
        V593_MAP_HEALTH["last_error"]=str((status or {}).get("warning") or "team page unavailable")
        _v593_save_health({"last_status":status}); return {}, {**dict(status or {}),"ok":False,"maps_found":0,"sample_maps":0}

    starts=[m.start() for m in re.finditer(r'class=["\']map-statistics-row(?:\s[^"\']*)?["\']',page,re.I)]
    pool={}
    for i,start in enumerate(starts):
        end=starts[i+1] if i+1<len(starts) else min(len(page),start+14000)
        chunk=page[start:end]
        mm=re.search(r'class=["\']map-statistics-row-map-mapname["\'][^>]*>\s*([^<]+)',chunk,re.I|re.S)
        wm=re.search(r'class=["\']map-statistics-row-win-percentage["\'][^>]*>\s*(\d+(?:\.\d+)?)\s*%',chunk,re.I|re.S)
        if not mm or not wm: continue
        raw_name=strip_tags(mm.group(1)).strip()
        map_name=next((m for m in KNOWN_MAPS if normalize_name(m)==normalize_name(raw_name) or (m=="Dust2" and normalize_name(raw_name) in {"dust2","dust ii","dustii"})),None)
        if not map_name: continue
        wins=_v593_num_from_stat(chunk,"Win")
        draws=_v593_num_from_stat(chunk,"Draws")
        losses=_v593_num_from_stat(chunk,"Losses")
        played=wins+draws+losses
        if played<=0: continue
        pickm=re.search(r'Picks\s*</div>\s*<div>\s*(\d+(?:\.\d+)?)%\s+of\s+(\d+)',chunk,re.I|re.S)
        banm=re.search(r'Bans\s*</div>\s*<div>\s*(\d+(?:\.\d+)?)%\s+of\s+(\d+)',chunk,re.I|re.S)
        pool[map_name]={
            "maps":played,"wins":wins,"draws":draws,"losses":losses,
            "win_pct":float(wm.group(1)),
            "pick_pct":float(pickm.group(1)) if pickm else None,
            "pick_opportunities":int(pickm.group(2)) if pickm else 0,
            "ban_pct":float(banm.group(1)) if banm else None,
            "ban_opportunities":int(banm.group(2)) if banm else 0,
            "source":str((status or {}).get("url") or url),
            "period":"HLTV public team page last 3 months",
        }
    sample=sum(int((v or {}).get("maps") or 0) for v in pool.values())
    ok=bool(pool and sample>0)
    if ok:
        V593_MAP_HEALTH["ok"]=int(V593_MAP_HEALTH.get("ok") or 0)+1
        V593_MAP_HEALTH["maps_parsed"]=int(V593_MAP_HEALTH.get("maps_parsed") or 0)+len(pool)
        V593_MAP_HEALTH["sample_maps"]=int(V593_MAP_HEALTH.get("sample_maps") or 0)+sample
        V593_MAP_HEALTH["last_error"]=""
    else:
        V593_MAP_HEALTH["last_error"]="No verified W/D/L map cards parsed from public HLTV team page"
    out_status={**dict(status or {}),"ok":ok,"provider":"HLTV public team-page map cards v5.9.3","maps_found":len(pool),"sample_maps":sample,"period":"last 3 months"}
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