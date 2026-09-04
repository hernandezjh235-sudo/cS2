from __future__ import annotations
import os, sys
from pathlib import Path

MARKER = "# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER = "# === ONEWAYPICKZ CS2 COMPLETE DATA PIPELINE V5.5 ==="

OVERLAY = r'''
# === ONEWAYPICKZ CS2 COMPLETE DATA PIPELINE V5.5 ===
# Identity/readiness/grading/persistence only. Projection math is unchanged.
AUTOFEED_READINESS_VERSION = "5.5"
V55_CURSOR_FILE = os.path.join(STORAGE_DIR, "cs2_profile_recovery_cursor.json")
V55_READINESS_FILE = os.path.join(STORAGE_DIR, "cs2_data_readiness.json")
V55_GRADING_HEALTH_FILE = os.path.join(STORAGE_DIR, "cs2_grading_health.json")
V55_MATCH_CACHE = {}


def _v55_matchup(row):
    try: return _teams_from_matchup(str(row.get("matchup") or row.get("evidence") or row.get("event") or ""))
    except Exception: return "", ""


def _v55_db_team(player):
    key = normalize_name(player)
    try:
        aliases = load_json(PLAYER_ALIAS_FILE,{})
        if isinstance(aliases,dict) and isinstance(aliases.get(key),dict) and aliases[key].get("team"):
            return str(aliases[key]["team"])
    except Exception: pass
    try: return str((lookup_database_player(player) or {}).get("team") or "")
    except Exception: return ""


def _v55_team_from_groups(player, groups):
    found=[]
    for g in list(groups or []):
        if not isinstance(g,dict): continue
        team=str(g.get("team") or g.get("name") or "").strip()
        score=max([name_similarity(player,str(x or "")) for x in list(g.get("players") or g.get("roster") or [])] or [0])
        if team and score>=.84: found.append((score,team))
    found.sort(reverse=True)
    return found[0][1] if found and (len(found)==1 or found[0][0]-found[1][0]>=.03) else ""


def _v55_save_team(player, team, opponent, source="verified current roster"):
    if not player or not team: return
    key=normalize_name(player); now=now_iso()
    try:
        aliases=load_json(PLAYER_ALIAS_FILE,{}) or {}; old=aliases.get(key) if isinstance(aliases.get(key),dict) else {}
        aliases[key]={**old,"alias":player,"team":team,"source":source,"saved_at":now}; save_json(PLAYER_ALIAS_FILE,aliases,force=True)
    except Exception: pass
    try:
        db=load_json(PLAYER_DATABASE_FILE,{}) or {}; old=db.get(key) if isinstance(db.get(key),dict) else {}
        if old:
            db[key]={**old,"team":team,"provider_team_verified":True,"identity_verified_at":now,"identity_verified_source":source}
            save_json(PLAYER_DATABASE_FILE,db,force=True)
    except Exception: pass
    try:
        tdb=load_json(TEAM_DATABASE_FILE,{}) or {}; tk=normalize_team(team); old=tdb.get(tk) if isinstance(tdb.get(tk),dict) else {}
        roster=list(old.get("current_roster") or [])
        if max([name_similarity(player,x) for x in roster] or [0])<.84: roster=(roster+[player])[-8:]
        tdb[tk]={**old,"team":team,"current_roster":roster,"updated_at":now,"identity_source":source}; save_json(TEAM_DATABASE_FILE,tdb,force=True)
    except Exception: pass


def _v55_context(a,b,player):
    key="|".join(sorted([normalize_team(a),normalize_team(b)]))
    if key in V55_MATCH_CACHE: return V55_MATCH_CACHE[key]
    try:
        url,disc=discover_hltv_match(a,b,player)
        ctx,st=fetch_match_context(url) if url else ({},{"ok":False})
        ctx=dict(ctx or {}); ctx.setdefault("match_url",url); V55_MATCH_CACHE[key]=(ctx,dict(st or {})); return V55_MATCH_CACHE[key]
    except Exception as exc:
        V55_MATCH_CACHE[key]=({}, {"ok":False,"warning":str(exc)}); return V55_MATCH_CACHE[key]


def _v55_resolve_prop(prop):
    out=dict(prop or {}); player=str(out.get("player") or ""); a,b=_v55_matchup(out)
    if not player or not a or not b: out["v55_preprojection_identity_verified"]=False; return out
    team=_v55_db_team(player)
    if team and (_team_name_matches(team,a) or _team_name_matches(team,b)):
        t,o=(a,b) if _team_name_matches(team,a) else (b,a)
        out.update({"team":t,"opponent":o,"provider_team_verified":True,"v55_preprojection_identity_verified":True,"identity_reconciled":True,"identity_reconcile_source":"persistent verified team"}); return out
    ctx,_=_v55_context(a,b,player); team=_v55_team_from_groups(player,ctx.get("lineup_groups") or [])
    if team and (_team_name_matches(team,a) or _team_name_matches(team,b)):
        t,o=(a,b) if _team_name_matches(team,a) else (b,a)
        out.update({"team":t,"opponent":o,"match_url":ctx.get("match_url") or out.get("match_url"),"provider_team_verified":True,"v55_preprojection_identity_verified":True,"identity_reconciled":True,"identity_reconcile_source":"current provider roster"}); _v55_save_team(player,t,o); return out
    out["provider_team_verified"]=False; out["v55_preprojection_identity_verified"]=False
    out["flags"]=list(dict.fromkeys(list(out.get("flags") or [])+["CURRENT PLAYER TEAM/OPPONENT UNVERIFIED"])); return out


def _v55_ready(row):
    fresh=row.get("source_freshness") if isinstance(row.get("source_freshness"),dict) else {}
    base={
      "real_line":safe_float(row.get("line"),None) is not None,
      "market_scope":bool(row.get("market_scope_verified")),
      "profile":(safe_int(row.get("profile_maps"),0) or 0)>=MIN_PROFILE_MAPS and safe_float(row.get("base_kpr"),None) is not None,
      "core_kpr":bool(row.get("core_kpr_verified")),
      "identity_before_model":bool(row.get("v55_preprojection_identity_verified")),
      "team":bool(row.get("provider_team_verified") and row.get("team")),
      "opponent":bool(row.get("opponent")),
      "real_match":bool(str(row.get("match_url") or "") and not str(row.get("match_url") or "").startswith(("mirror://","bridge://"))),
      "format":str(row.get("match_format") or "").upper() not in {"","UNKNOWN","BO1"},
    }
    official={**base,
      "roster":bool(row.get("current_roster_verified") or (safe_int(row.get("current_roster_maps"),0) or 0)>=MIN_CURRENT_ROSTER_MAPS),
      "team_maps":(safe_int(row.get("team_recent_maps"),0) or 0)>0,
      "opponent_maps":(safe_int(row.get("opponent_mapstats_samples"),0) or 0)>0,
      "player_fresh":bool(row.get("player_source_fresh")),
      "line_fresh":safe_float(fresh.get("line_age_seconds"),999999)<=300,
      "match_fresh":safe_float(fresh.get("match_age_seconds"),999999)<=600,
      "calibration":bool(row.get("calibration_ready")),
    }
    return {"projection_ready":all(base.values()),"official_ready":all(official.values()),"missing_projection":[k for k,v in base.items() if not v],"missing_official":[k for k,v in official.items() if not v],"checks":base,"official_checks":official,"readiness_score":round(100*sum(bool(v) for v in official.values())/len(official),1)}


_v55_board_base=build_full_board
def build_full_board(props,deep_enabled=True):
    prepared=[_v55_resolve_prop(x) for x in list(props or [])]
    board,status=_v55_board_base(prepared,deep_enabled); pc=oc=ic=0; missing=Counter()
    for row in board:
        player=str(row.get("player") or ""); a,b=_v55_matchup(row); groups=row.get("confirmed_lineup_groups") or []
        rt=_v55_team_from_groups(player,groups) or _v55_db_team(player)
        if a and b and rt and (_team_name_matches(rt,a) or _team_name_matches(rt,b)):
            t,o=(a,b) if _team_name_matches(rt,a) else (b,a); row["team"],row["opponent"],row["provider_team_verified"]=t,o,True; _v55_save_team(player,t,o); ic+=1
        ready=_v55_ready(row); row["data_readiness"]=ready; row["projection_data_ready"]=ready["projection_ready"]; row["official_data_ready"]=ready["official_ready"]; row["data_readiness_score"]=ready["readiness_score"]
        if ready["projection_ready"]: pc+=1
        else:
            missing.update(ready["missing_projection"])
            if row.get("projection") is not None: row["status"],row["status_label"],row["pick_action"]="PASS","⏳ DATA BUILDING — REQUIRED DATA MISSING","WAIT FOR VERIFIED DATA"
        if ready["official_ready"]: oc+=1
        elif row.get("status") in {"OFFICIAL","PLAYABLE"}: row["status"],row["status_label"]="TRACK","⚠️ TRACK — FULL DATA NOT READY"
    health={"version":"5.5","updated_at":now_iso(),"board_rows":len(board),"projection_ready_rows":pc,"official_ready_rows":oc,"verified_identity_rows":ic,"missing_projection_requirements":dict(missing)}
    try: save_json(V55_READINESS_FILE,health,force=True)
    except Exception: pass
    status=dict(status or {}); status["v55_data_readiness"]=health; return board,status


if "_autofeed_direct_profile_recovery" in globals():
    _v55_recovery_base=_autofeed_direct_profile_recovery
    def _autofeed_direct_profile_recovery(players,max_new=None):
        unique=list(dict.fromkeys(str(x or "").strip() for x in players if str(x or "").strip()))
        if not unique: return _v55_recovery_base(unique,max_new=max_new)
        state=load_json(V55_CURSOR_FILE,{}) or {}; cur=(safe_int(state.get("cursor"),0) or 0)%len(unique); rotated=unique[cur:]+unique[:cur]
        out=dict(_v55_recovery_base(rotated,max_new=max_new) or {}); adv=max(1,safe_int(out.get("attempted"),0) or 0); state={"cursor":(cur+adv)%len(unique),"updated_at":now_iso(),"unique_players":len(unique)}; save_json(V55_CURSOR_FILE,state,force=True); out["recovery_cursor"]=state; return out


if "_v54_seed_databases_from_bridge" in globals():
    _v55_seed_base=_v54_seed_databases_from_bridge
    def _v54_seed_databases_from_bridge(payload):
        out=dict(_v55_seed_base(payload) or {}); links=0
        for m in list((payload or {}).get("matches") or []):
            teams=[str((x or {}).get("name") or "") for x in list(m.get("teams") or []) if isinstance(x,dict)]
            for g in list(m.get("lineup_groups") or []):
                if not isinstance(g,dict): continue
                team=str(g.get("team") or g.get("name") or ""); opp=next((x for x in teams if x and not _team_name_matches(x,team)),"")
                for p in list(g.get("players") or g.get("roster") or []): _v55_save_team(str(p),team,opp,"GitHub cached roster"); links+=1
        out["identity_links"]=links; return out


if "save_official_snapshots" in globals():
    _v55_snap_base=save_official_snapshots
    def save_official_snapshots(board,include_playable=False,include_track=False):
        good=[x for x in list(board or []) if x.get("projection_data_ready") is not False and safe_float(x.get("projection"),None) is not None]
        return _v55_snap_base(good,include_playable,include_track)


def auto_freeze_verified_pregame(board):
    now=datetime.now(timezone.utc); good=[]
    for row in list(board or []):
        if row.get("projection_data_ready") is not True or row.get("lean") not in {"OVER","UNDER"} or row.get("status") not in {"OFFICIAL","PLAYABLE","TRACK"}: continue
        start=_parse_iso_datetime(row.get("start_time"))
        if start and start<now-timedelta(minutes=5): continue
        good.append(dict(row))
    if not good: return {"added":0,"skipped":0,"eligible":0}
    out=_v55_snap_base(good,True,True) if "_v55_snap_base" in globals() else save_official_snapshots(good,True,True); return {**out,"eligible":len(good)}


_v55_grade_base=fetch_actual_maps12_kills
def _v55_bo3_url(url):
    raw=str(url or "")
    if raw.startswith("bo3://"):
        r=raw.split("bo3://",1)[1].strip("/"); parts=r.split("/",1); return "https://bo3.gg/matches/"+(parts[1] if len(parts)>1 else parts[0])
    return raw.rstrip("/") if raw.startswith("https://bo3.gg/matches/") else ""

def _v55_bo3_maps(page):
    text=strip_tags(page or ""); hits=[]
    for name in KNOWN_MAPS:
        v="Dust II" if name=="Dust2" else name
        m=re.search(rf"\b{re.escape(v)}\b",text,re.I)
        if m and re.search(r"\b\d{1,2}\s*[-:]\s*\d{1,2}\b",text[m.end():m.end()+180]): hits.append((m.start(),name))
    return [x[1] for x in sorted(hits)][:3]

def _v55_bo3_kills(page,player):
    best=(0,None,"")
    for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>",page or "",re.I|re.S):
        a=re.search(r'href=["\'](?:https?://bo3\.gg)?/players/([^"\'/?#]+)[^"\']*["\'][^>]*>(.*?)</a>',tr,re.I|re.S)
        if not a: continue
        slug=a.group(1); name=strip_tags(a.group(2)).replace("\n"," ").strip() or slug.replace("-"," "); score=max(name_similarity(player,name),name_similarity(player,slug.replace("-"," ")))
        if score<.84: continue
        text=strip_tags(tr).replace("\n"," "); pos=text.lower().find(name.lower()); tail=text[pos+len(name):] if pos>=0 else text; nums=[safe_int(x,None) for x in re.findall(r"(?<![\d.])\b\d{1,2}\b(?![\d.])",tail)]; nums=[x for x in nums if x is not None]
        if nums and score>best[0]: best=(score,int(nums[0]),name)
    return best[1],{"matched":best[1] is not None,"score":round(best[0],3),"name":best[2]}

def _v55_grade_bo3(url,player):
    base=_v55_bo3_url(url)
    if not base: return None,{"ok":False,"message":"not BO3 URL"}
    page,st=http_get_text(base,"BO3 grade match",ttl=240,timeout=24,allow_stale=False)
    if not page: return None,{"ok":False,"message":"BO3 match unavailable","status":st}
    low=strip_tags(page).lower(); void=[x for x in ["walkover","forfeit","technical win","cancelled","postponed"] if x in low]
    if void: return None,{"ok":False,"void_reason":", ".join(void),"message":"void/manual review"}
    maps=_v55_bo3_maps(page)
    if len(maps)<2: return None,{"ok":False,"message":"two maps not completed","maps":maps}
    slugs={"Dust2":"dust-ii"}; total=0; details=[]; conf=1.0
    for m in maps[:2]:
        u=f"{base}/{slugs.get(m,normalize_name(m).replace(' ','-'))}"; mp,ms=http_get_text(u,"BO3 grade map",ttl=240,timeout=24,allow_stale=False); k,meta=_v55_bo3_kills(mp or "",player); details.append({"map":m,"url":u,"kills":k,"meta":meta,"status":ms})
        if k is None: return None,{"ok":False,"message":"player missing on BO3 map","details":details}
        total+=k; conf=min(conf,safe_float(meta.get("score"),0) or 0)
    return (total,{"ok":conf>=.84,"confidence":conf,"details":details,"map_results":maps[:2],"total_kills":total,"grade_provider":"BO3.gg"}) if conf>=.84 else (None,{"ok":False,"confidence":conf,"details":details,"message":"identity confidence below .84"})

def fetch_actual_maps12_kills(match_url,player,player_id=""):
    if str(match_url or "").startswith(("bo3://","https://bo3.gg/matches/")):
        actual,meta=_v55_grade_bo3(match_url,player)
        if actual is not None or meta.get("void_reason"): return actual,meta
    return _v55_grade_base(match_url,player,player_id)


if "grade_pending_automatically" in globals():
    _v55_grade_pending_base=grade_pending_automatically
    def grade_pending_automatically():
        out=dict(_v55_grade_pending_base() or {}); health={"updated_at":now_iso(),"graded_now":safe_int(out.get("graded"),0) or 0,"pending":safe_int(out.get("pending"),0) or 0,"errors":safe_int(out.get("errors"),0) or 0,"result_rows":len(load_json(RESULT_LOG,[]) or []),"saved_snapshots":len(load_json(PICK_LOG,[]) or [])}
        try: save_json(V55_GRADING_HEALTH_FILE,health,force=True)
        except Exception: pass
        out["v55_grading_health"]=health; return out

try: APP_VERSION="CS2 v5.5 — COMPLETE VERIFIED DATA PIPELINE"
except Exception: pass
# === END ONEWAYPICKZ CS2 COMPLETE DATA PIPELINE V5.5 ===
'''


def patch_text(source: str) -> str:
    if PATCH_MARKER in source: return source
    if MARKER not in source: raise RuntimeError("SESSION BOARD LOAD marker not found")
    return source.replace(MARKER, OVERLAY+"\n\n"+MARKER,1)

def patch_app(path: Path | str = "app.py") -> bool:
    p=Path(path); old=p.read_text(encoding="utf-8"); new=patch_text(old); changed=new!=old
    if changed:
        tmp=p.with_suffix(p.suffix+".v55.tmp"); tmp.write_text(new,encoding="utf-8"); os.replace(tmp,p)
    return changed

def main() -> int:
    p=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).with_name("app.py")
    try:
        changed=patch_app(p); compile(p.read_text(encoding="utf-8"),str(p),"exec"); print(f"CS2 v5.5 patch: {'updated' if changed else 'already applied'} -> {p}"); return 0
    except Exception as exc:
        print(f"CS2 v5.5 patch failed: {exc}",file=sys.stderr); return 2
if __name__=="__main__": raise SystemExit(main())
