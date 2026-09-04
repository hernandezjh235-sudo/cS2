from __future__ import annotations
import os, sys
from pathlib import Path

MARKER="# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER="# === ONEWAYPICKZ V5.8.6 VERIFIED CS2 SOURCE GATE ==="

OVERLAY=r'''
# === ONEWAYPICKZ V5.8.6 VERIFIED CS2 SOURCE GATE ===
# Data classification/identity/readiness only. Protected projection math unchanged.
AUTOFEED_VERIFIED_V586_VERSION="5.8.6"
V586_CONTEXT={}
V586_CS_IDS={"CS","CS2","CSGO","COUNTER_STRIKE","COUNTERSTRIKE"}


def _v586_is_cs2(row):
    row=row if isinstance(row,dict) else {}
    sid=str(row.get("sport_id") or "").strip().upper().replace("-","_").replace(" ","_")
    if sid in V586_CS_IDS:return True
    raw=" | ".join(str(row.get(k) or "") for k in ("sport","sport_name","league","league_name","evidence")).lower()
    if any(x in raw for x in ("league of legends","dota","valorant","overwatch","rainbow six","rocket league","call of duty")):return False
    return any(x in raw for x in ("counter-strike","counter strike","counterstrike","cs2","cs:go","csgo"))


def _v586_key(row):
    row=row if isinstance(row,dict) else {}
    lid=str(row.get("source_line_id") or row.get("prop_id") or "").strip()
    if lid:return ("id",lid)
    return (normalize_name(row.get("player") or ""),safe_float(row.get("line"),None),str(row.get("start_time") or "")[:16])


def _v586_age(stamp):
    try:
        dt=_parse_iso_datetime(stamp)
        if not dt:return None
        if dt.tzinfo is None:dt=dt.replace(tzinfo=timezone.utc)
        return max(0.0,(datetime.now(timezone.utc)-dt.astimezone(timezone.utc)).total_seconds())
    except Exception:return None


def _v586_source(row):
    out=dict(row or {})
    if not _v586_is_cs2(out):return out,False
    src=dict(out.get("source_identity_ids") or {})
    pid=str(src.get("player_id") or out.get("underdog_player_id") or "").strip()
    mid=str(src.get("match_id") or out.get("underdog_match_id") or out.get("game_id") or "").strip()
    tid=str(src.get("team_id") or out.get("underdog_team_id") or "").strip()
    oid=str(src.get("opponent_id") or out.get("underdog_opponent_id") or "").strip()
    player=str(out.get("player") or "").strip();team=str(out.get("team") or "").strip();opp=str(out.get("opponent") or "").strip()
    ok=bool(player and team and opp and pid and mid and tid and oid)
    roster=[str(x or "").strip() for x in list(out.get("source_roster_names") or out.get("current_roster_names") or []) if str(x or "").strip()]
    pin=bool(roster and max([name_similarity(player,x) for x in roster] or [0])>=.84)
    if ok:
        ids=dict(out.get("identity_ids") or {})
        ids.setdefault("player_id",f"ud:{pid}");ids.setdefault("match_id",f"ud:{mid}");ids.setdefault("team_id",f"ud:{tid}");ids.setdefault("opponent_id",f"ud:{oid}")
        out.update({"identity_ids":ids,"provider_team_verified":True,"v55_preprojection_identity_verified":True,
                    "identity_reconciled":True,"identity_reconcile_source":"Underdog exact player/game/team IDs",
                    "source_match_verified":True,"v57_match_context_verified":True,"v585_premodel_context":True,"v586_premodel_context":True})
        if not str(out.get("match_url") or "") or str(out.get("match_url") or "").startswith(("mirror://","bridge://")):out["match_url"]=f"underdog://{mid}"
        if str(out.get("match_format") or "").upper() in {"","UNKNOWN"} and out.get("market_scope_verified"):out["match_format"]="MULTI_MAP"
        try:_v55_save_team(player,team,opp,"Underdog exact player/game/team IDs")
        except Exception:pass
    if len(roster)==5 and pin:
        out.update({"current_roster_names":roster,"current_roster_verified":True,"lineup_verified":True,"player_in_lineup":True,"roster_overlap":5})
        groups=[dict(x) for x in list(out.get("source_lineup_groups") or []) if isinstance(x,dict)]
        if groups:out["confirmed_lineup_groups"]=groups
    age=_v586_age(out.get("source_pulled_at"));fresh=dict(out.get("source_freshness") or {})
    if age is not None:
        fresh["line_age_seconds"]=age
        if ok:fresh["match_age_seconds"]=age;fresh["lineup_age_seconds"]=age
    out["source_freshness"]=fresh
    V586_CONTEXT[_v586_key(out)]=dict(out)
    try:
        if ok and "V551_PREMODEL_IDENTITY" in globals():V551_PREMODEL_IDENTITY[_v551_identity_key(out)]=True
    except Exception:pass
    return out,ok


def _v586_restore(row):
    out=dict(row or {});src=V586_CONTEXT.get(_v586_key(out))
    if not isinstance(src,dict):return out
    for f in ("source_line_id","game_id","sport_id","stat_name","evidence","source_pulled_at","underdog_player_id","underdog_match_id","underdog_team_id","underdog_opponent_id","source_identity_ids","source_roster_names","source_lineup_groups","source_match_verified","v55_preprojection_identity_verified","provider_team_verified","identity_reconciled","identity_reconcile_source","v585_premodel_context","v586_premodel_context"):
        if src.get(f) not in (None,"",[],{}):out[f]=src[f]
    for f in ("team","opponent","matchup","match_format","current_roster_names","confirmed_lineup_groups","current_roster_verified","lineup_verified","player_in_lineup","roster_overlap"):
        if not out.get(f) and src.get(f) not in (None,"",[],{}):out[f]=src[f]
    if str(out.get("match_url") or "").startswith(("","mirror://","bridge://")) and src.get("match_url"):out["match_url"]=src["match_url"]
    ids=dict(src.get("identity_ids") or {});ids.update(dict(out.get("identity_ids") or {}));out["identity_ids"]=ids
    fresh=dict(src.get("source_freshness") or {});fresh.update(dict(out.get("source_freshness") or {}));out["source_freshness"]=fresh
    return out


if "_v55_resolve_prop" in globals():
    _v586_resolve_base=_v55_resolve_prop
    def _v55_resolve_prop(prop):
        p,_=_v586_source(prop);out=_v586_resolve_base(p);out=_v586_restore(out);out,_=_v586_source(out);return out

if "_v55_ready" in globals():
    _v586_ready_base=_v55_ready
    def _v55_ready(row):
        x=_v586_restore(row);row.clear();row.update(x);return _v586_ready_base(row)

if "fetch_underdog_cs2_board" in globals():
    _v586_ud_base=fetch_underdog_cs2_board
    def fetch_underdog_cs2_board():
        rows,meta=_v586_ud_base();rows=[dict(x) for x in list(rows or []) if isinstance(x,dict)];meta=dict(meta or {})
        path=globals().get("V582_LIVE_CATALOG_FILE") or globals().get("V58_LIVE_CATALOG_FILE")
        cat=load_json(path,{}) if path else {};raw=[dict(x) for x in list((cat or {}).get("rows") or []) if isinstance(x,dict)];clean=[x for x in raw if _v586_is_cs2(x)]
        byid={str(x.get("source_line_id") or x.get("prop_id") or ""):x for x in clean if str(x.get("source_line_id") or x.get("prop_id") or "")}
        kept=[]
        for r in rows:
            lid=str(r.get("source_line_id") or r.get("prop_id") or "");src=byid.get(lid)
            if src:
                for f in ("team","opponent","matchup","start_time","market","market_scope","market_scope_verified","model_supported","projection_eligible_market","source_line_id","game_id","sport_id","stat_name","evidence","source_pulled_at","underdog_player_id","underdog_match_id","underdog_team_id","underdog_opponent_id","source_identity_ids","source_lineup_groups","source_roster_names","source_match_verified","match_format"):
                    if src.get(f) not in (None,"",[],{}):r[f]=src[f]
            if not _v586_is_cs2(r):continue
            if not (r.get("model_supported") and r.get("market_scope_verified")):continue
            r,_=_v586_source(r);kept.append(r)
        if path:
            out={"version":"5.8.6","updated_at":now_iso(),"rows":clean,"all_cs2_lines":len(clean),"model_supported_lines":sum(bool(x.get("model_supported") and x.get("market_scope_verified")) for x in clean),"unsupported_visible_lines":sum(not bool(x.get("model_supported") and x.get("market_scope_verified")) for x in clean),"source_exact_five_rows":sum(bool(x.get("source_five_player_lineup")) for x in clean),"source_exact_match_rows":sum(bool(x.get("source_match_verified")) for x in clean),"non_cs2_rows_removed":max(0,len(raw)-len(clean)),"source_gate":"explicit Counter-Strike metadata"}
            try:save_json(path,out,force=True);st.session_state["cs2_all_live_lines"]=clean
            except Exception:pass
            meta["v586_verified_cs2_source_gate"]={k:v for k,v in out.items() if k!="rows"}
        meta["rows"]=len(kept);return kept,meta

if "build_full_board" in globals():
    _v586_board_base=build_full_board
    def build_full_board(props,deep_enabled=True):
        prepared=[]
        for r in list(props or []):
            if isinstance(r,dict) and _v586_is_cs2(r):
                r,_=_v586_source(r);prepared.append(r)
        board,status=_v586_board_base(prepared,deep_enabled);board=[dict(x) for x in list(board or []) if isinstance(x,dict) and _v586_is_cs2(x)];status=dict(status or {})
        pc=oc=pre=exact=five=pin=prov=srcm=0;missing=Counter()
        for i,r in enumerate(board):
            r=_v586_restore(r)
            if callable(globals().get("_v55_ready")) and (r.get("model_supported") or r.get("market_scope_verified")):
                try:
                    rd=_v55_ready(r);r["data_readiness"]=rd;r["projection_data_ready"]=bool(rd.get("projection_ready"));r["official_data_ready"]=bool(rd.get("official_ready"));r["data_readiness_score"]=rd.get("readiness_score")
                    pc+=int(bool(rd.get("projection_ready")));oc+=int(bool(rd.get("official_ready")))
                    if not rd.get("projection_ready"):missing.update(rd.get("missing_projection") or [])
                except Exception:pass
            ids=r.get("identity_ids") if isinstance(r.get("identity_ids"),dict) else {};exact+=int(bool(ids.get("player_id") and ids.get("match_id")));ro=list(r.get("current_roster_names") or []);five+=int(len(ro)==5);pin+=int(bool(r.get("player_in_lineup")));pre+=int(bool(r.get("v586_premodel_context") or r.get("v585_premodel_context")))
            u=str(r.get("match_url") or "");prov+=int(u.startswith(("bo3://","pandascore://","https://bo3.gg/","https://www.hltv.org/")));srcm+=int(bool(r.get("source_match_verified") and u.startswith("underdog://")));board[i]=r
        health={"version":"5.8.6","runtime_layer":"5.8.6","updated_at":now_iso(),"board_rows":len(board),"exact_match_player_ids":exact,"five_player_lineups":five,"players_in_lineup":pin,"real_provider_match_rows":prov,"real_source_match_rows":srcm,"real_match_rows":prov+srcm,"projection_ready_rows":pc,"official_ready_rows":oc,"premodel_context_rows":pre,"non_cs2_rows_visible":0,"projection_math_changed":False}
        readiness={"version":"5.8.6","updated_at":now_iso(),"board_rows":len(board),"projection_ready_rows":pc,"official_ready_rows":oc,"verified_identity_rows":sum(bool(x.get("v55_preprojection_identity_verified")) for x in board),"missing_projection_requirements":dict(missing),"source_gate":"explicit CS2 only"}
        try:save_json(V57_CONTEXT_HEALTH_FILE,health,force=True);save_json(V55_READINESS_FILE,readiness,force=True)
        except Exception:pass
        status["v57_context_health"]=health;status["v55_data_readiness"]=readiness;status["v586_verified_pipeline"]={"input_rows":len(prepared),"board_rows":len(board),"projection_ready_rows":pc,"official_ready_rows":oc,"premodel_context_rows":pre,"provider_match_rows":prov,"source_match_rows":srcm,"projection_math_changed":False}
        return board,status

try:APP_VERSION="CS2 v5.8.6 — VERIFIED CS2 SOURCE + PREMODEL/GRADING PIPELINE"
except Exception:pass
# === END ONEWAYPICKZ V5.8.6 VERIFIED CS2 SOURCE GATE ===
'''


def patch_text(source:str)->str:
    text=source
    text=text.replace('        if not (_v58_specific_cs2(sport_obj, game_obj, appearance, player_obj, line_obj, over_under) or model_supported):\n            continue','        if not (_v58_specific_cs2(sport_obj, game_obj, appearance, player_obj, line_obj, over_under) or str(sport_id or "").strip().upper() in {"CS","CS2","CSGO","COUNTER_STRIKE","COUNTERSTRIKE"}):\n            continue')
    text=text.replace('        if not (specific_cs2 or model_supported):\n            continue','        if not (specific_cs2 or str(sport_id or "").strip().upper() in {"CS","CS2","CSGO","COUNTER_STRIKE","COUNTERSTRIKE"}):\n            continue')
    text=text.replace('batch = max(4, min(60, int(max_new if max_new is not None else batch_default)))','batch = max(4, min(120, int(max_new if max_new is not None else batch_default)))')
    text=text.replace('"runtime_layer": "5.8.4",','"runtime_layer": "5.8.6",').replace('health["runtime_layer"] = "5.8.4"','health["runtime_layer"] = "5.8.6"')
    # Do not let source identity short-circuit real provider match/deep-data recovery.
    text=text.replace('    def _v57_enrich_row(row):\n        source_row, ok = _v582_source_context(row)\n        if ok: return source_row\n        return _v582_v57_enrich_base(row)','    def _v57_enrich_row(row):\n        source_row, ok = _v582_source_context(row)\n        return _v582_v57_enrich_base(source_row if ok else row)')
    if PATCH_MARKER not in text:
        if MARKER not in text:raise RuntimeError("SESSION BOARD LOAD marker not found")
        text=text.replace(MARKER,OVERLAY+"\n\n"+MARKER,1)
    return text


def patch_app(path="app.py"):
    p=Path(path);old=p.read_text(encoding="utf-8");new=patch_text(old);changed=new!=old
    if changed:
        tmp=p.with_suffix(p.suffix+".v586.tmp");tmp.write_text(new,encoding="utf-8");os.replace(tmp,p)
    return changed

if __name__=="__main__":
    p=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).with_name("app.py");changed=patch_app(p);compile(p.read_text(encoding="utf-8"),str(p),"exec");print(f"v5.8.6 patch {'applied' if changed else 'already present'}: {p}")