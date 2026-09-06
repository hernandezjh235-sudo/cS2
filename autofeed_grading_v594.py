from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urljoin as _v594_urljoin

MARKER = "# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER = "# === ONEWAYPICKZ V5.9.4 DIRECT VERIFIED MAPS 1-2 GRADING ==="

OVERLAY = r'''
# === ONEWAYPICKZ V5.9.4 DIRECT VERIFIED MAPS 1-2 GRADING ===
# Grading transport/identity only. Frozen line, lean, projection, tags, protected
# projection math, probabilities, thresholds, and confidence are unchanged.
AUTOFEED_GRADING_V594_VERSION = "5.9.4"
V594_GRADE_HEALTH_FILE = os.path.join(STORAGE_DIR, "cs2_direct_grading_health.json")
V594_GRADE_HEALTH = {
    "attempts": 0, "completed": 0, "pending": 0, "void_review": 0,
    "identity_fail": 0, "fetch_fail": 0, "last_error": "",
}


def _v594_save_health(extra=None):
    try:
        payload={"version":"5.9.4","updated_at":now_iso(),**dict(V594_GRADE_HEALTH)}
        if isinstance(extra,dict): payload.update(extra)
        save_json(V594_GRADE_HEALTH_FILE,payload,force=True)
    except Exception:
        pass


def _v594_numeric_hltv_player_id(player, supplied=""):
    raw=str(supplied or "").strip()
    if raw.isdigit():
        return raw
    try:
        rec=lookup_database_player(player) or {}
        for key in ("hltv_player_id","player_id","id"):
            val=str(rec.get(key) or "").strip()
            if val.isdigit(): return val
        for key in ("href","url","profile_url"):
            m=re.search(r"/(?:stats/players?|player)/(\d+)/",str(rec.get(key) or ""))
            if m:return m.group(1)
    except Exception:
        pass
    try:
        table,_=fetch_hltv_player_table(180)
        key=normalize_name(player)
        rec=(table or {}).get(key) if isinstance(table,dict) else None
        if isinstance(rec,dict):
            val=str(rec.get("player_id") or rec.get("id") or "").strip()
            if val.isdigit(): return val
            m=re.search(r"/(?:stats/players?|player)/(\d+)/",str(rec.get("href") or ""))
            if m:return m.group(1)
    except Exception:
        pass
    return ""


def _v594_direct_page(url,label,ttl=240,timeout=22):
    full=_v594_urljoin("https://www.hltv.org",str(url or ""))
    return _v591_direct_get(full,ttl=ttl,timeout=timeout)


_v594_grade_base = fetch_actual_maps12_kills

def fetch_actual_maps12_kills(match_url, player, player_id=""):
    raw=str(match_url or "").strip()
    if not raw.startswith(("https://www.hltv.org/","https://hltv.org/")):
        return _v594_grade_base(match_url,player,player_id)

    V594_GRADE_HEALTH["attempts"]=int(V594_GRADE_HEALTH.get("attempts") or 0)+1
    url=raw.replace("https://hltv.org/","https://www.hltv.org/")
    match_page,match_status=_v594_direct_page(url,"HLTV direct grade match",ttl=180,timeout=24)
    if not match_page:
        V594_GRADE_HEALTH["fetch_fail"]=int(V594_GRADE_HEALTH.get("fetch_fail") or 0)+1
        V594_GRADE_HEALTH["last_error"]="match page unavailable"
        meta={"ok":False,"message":"HLTV match page unavailable for verified grading","match_status":match_status,"grade_provider":"HLTV direct v5.9.4"}
        _v594_save_health({"last_meta":meta}); return None,meta

    lower=strip_tags(match_page).lower()
    void_terms=[x for x in ["walkover","forfeit","technical win","match cancelled","match postponed","match canceled"] if x in lower]
    if void_terms:
        V594_GRADE_HEALTH["void_review"]=int(V594_GRADE_HEALTH.get("void_review") or 0)+1
        meta={"ok":False,"void_reason":", ".join(void_terms),"message":"Match requires manual/void review","grade_provider":"HLTV direct v5.9.4"}
        _v594_save_health({"last_meta":meta}); return None,meta

    map_results=_extract_map_results(match_page)
    links=_mapstats_links(match_page)
    links=list(dict.fromkeys(_v594_urljoin("https://www.hltv.org",str(x or "")) for x in links if str(x or "").strip()))
    if len(map_results)<2:
        V594_GRADE_HEALTH["pending"]=int(V594_GRADE_HEALTH.get("pending") or 0)+1
        meta={"ok":False,"message":"Two chronological completed maps not confirmed","map_results":map_results,"grade_provider":"HLTV direct v5.9.4"}
        _v594_save_health({"last_meta":meta}); return None,meta
    if len(links)<2:
        V594_GRADE_HEALTH["pending"]=int(V594_GRADE_HEALTH.get("pending") or 0)+1
        meta={"ok":False,"message":"Fewer than two completed map-stat links","links":links,"map_results":map_results[:2],"grade_provider":"HLTV direct v5.9.4"}
        _v594_save_health({"last_meta":meta}); return None,meta

    fetched=[]
    for link in links[:5]:
        page,status=_v594_direct_page(link,"HLTV direct grade map",ttl=180,timeout=24)
        fetched.append({"url":link,"map_id":_mapstats_id(link),"map":parse_mapstats_map_name(page or ""),"page":page or "","status":status})

    ordered=[]; used=set()
    for result in map_results[:2]:
        target=result.get("map")
        candidates=[x for x in fetched if x["map_id"] not in used and x.get("map")==target and x.get("page")]
        if len(candidates)!=1:
            V594_GRADE_HEALTH["pending"]=int(V594_GRADE_HEALTH.get("pending") or 0)+1
            meta={"ok":False,"message":f"Could not uniquely match chronological {target} map-stat page","map_results":map_results[:2],"fetched_maps":[{k:x.get(k) for k in ["url","map_id","map"]} for x in fetched],"grade_provider":"HLTV direct v5.9.4"}
            _v594_save_health({"last_meta":meta}); return None,meta
        ordered.append(candidates[0]); used.add(candidates[0]["map_id"])

    exact_pid=_v594_numeric_hltv_player_id(player,player_id)
    kills_total=0; team_total=0; team_verified=True; details=[]; scores=[]
    for rec in ordered:
        kills,meta=parse_map_player_kills(rec["page"],player,exact_pid)
        tk,tm=parse_player_team_total_kills(rec["page"],player)
        scores.append(safe_float(meta.get("score"),0) or 0)
        if tk is None: team_verified=False
        else: team_total+=int(tk)
        detail={"url":rec["url"],"map_id":rec["map_id"],"map":rec["map"],"kills":kills,"meta":meta,"team_total_kills":tk,"team_meta":tm,"status":rec["status"]}
        details.append(detail)
        if kills is None:
            V594_GRADE_HEALTH["identity_fail"]=int(V594_GRADE_HEALTH.get("identity_fail") or 0)+1
            out={"ok":False,"message":"Player not matched on one of first two chronological maps","details":details,"hltv_player_id":exact_pid,"grade_provider":"HLTV direct v5.9.4"}
            _v594_save_health({"last_meta":out}); return None,out
        kills_total+=int(kills)

    confidence=min(scores) if scores else 0.0
    if exact_pid and not all((x.get("meta") or {}).get("exact_id") for x in details): confidence=min(confidence,.70)
    meta={
        "ok":confidence>=.84,"confidence":confidence,"details":details,
        "map_links":[x["url"] for x in ordered],"map_results":map_results[:2],
        "team_total_kills":team_total if team_verified else None,
        "observed_player_share":kills_total/team_total if team_verified and team_total>0 else None,
        "total_kills":kills_total,"match_id":_match_id_from_url(url),
        "hltv_player_id":exact_pid,"grade_provider":"HLTV direct v5.9.4",
        "source_verified":True,
    }
    if confidence<.84:
        V594_GRADE_HEALTH["identity_fail"]=int(V594_GRADE_HEALTH.get("identity_fail") or 0)+1
        out={**meta,"message":"Grading identity confidence below 0.84"}; _v594_save_health({"last_meta":out}); return None,out
    V594_GRADE_HEALTH["completed"]=int(V594_GRADE_HEALTH.get("completed") or 0)+1
    _v594_save_health({"last_meta":meta})
    return kills_total,meta


try: APP_VERSION="CS2 v5.9.4 — DIRECT VERIFIED MAPS 1-2 GRADING"
except Exception: pass
# === END ONEWAYPICKZ V5.9.4 DIRECT VERIFIED MAPS 1-2 GRADING ===
'''


def patch_text(source: str) -> str:
    if PATCH_MARKER in source:return source
    if MARKER not in source:raise RuntimeError("SESSION BOARD LOAD marker not found")
    return source.replace(MARKER,OVERLAY+"\n\n"+MARKER,1)


def patch_app(path="app.py"):
    p=Path(path); old=p.read_text(encoding="utf-8"); new=patch_text(old); changed=new!=old
    if changed:
        tmp=p.with_suffix(p.suffix+".v594.tmp"); tmp.write_text(new,encoding="utf-8"); os.replace(tmp,p)
    return changed


if __name__=="__main__":
    p=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).with_name("app.py")
    changed=patch_app(p); compile(p.read_text(encoding="utf-8"),str(p),"exec")
    print(f"v5.9.4 patch {'applied' if changed else 'already present'}: {p}")