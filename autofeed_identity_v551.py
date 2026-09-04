from __future__ import annotations
import os, sys
from pathlib import Path

MARKER="# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER="# === ONEWAYPICKZ V5.5.1 PRE-MODEL IDENTITY CARRY ==="
OVERLAY=r'''
# === ONEWAYPICKZ V5.5.1 PRE-MODEL IDENTITY CARRY ===
# Carries the identity decision made before projection through wrappers that
# rebuild the returned row. No projection calculation is changed.
V551_PREMODEL_IDENTITY={}

def _v551_identity_key(row):
    return (normalize_name(row.get("player") or ""), safe_float(row.get("line"),None), str(row.get("stat_type") or row.get("market") or ""))

if "_v55_resolve_prop" in globals():
    _v551_resolve_base=_v55_resolve_prop
    def _v55_resolve_prop(prop):
        out=_v551_resolve_base(prop)
        V551_PREMODEL_IDENTITY[_v551_identity_key(out)]=bool(out.get("v55_preprojection_identity_verified"))
        return out

if "_v55_ready" in globals():
    _v551_ready_base=_v55_ready
    def _v55_ready(row):
        if V551_PREMODEL_IDENTITY.get(_v551_identity_key(row)):
            row["v55_preprojection_identity_verified"]=True
        return _v551_ready_base(row)

try: APP_VERSION="CS2 v5.5.1 — COMPLETE VERIFIED DATA PIPELINE"
except Exception: pass
# === END ONEWAYPICKZ V5.5.1 PRE-MODEL IDENTITY CARRY ===
'''

def patch_text(source):
    if PATCH_MARKER in source:return source
    if MARKER not in source:raise RuntimeError("SESSION BOARD LOAD marker not found")
    return source.replace(MARKER,OVERLAY+"\n\n"+MARKER,1)
def patch_app(path="app.py"):
    p=Path(path); old=p.read_text(encoding="utf-8"); new=patch_text(old); changed=new!=old
    if changed:
        tmp=p.with_suffix(p.suffix+".v551.tmp"); tmp.write_text(new,encoding="utf-8"); os.replace(tmp,p)
    return changed
if __name__=="__main__":
    p=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).with_name("app.py")
    try: patch_app(p); compile(p.read_text(encoding="utf-8"),str(p),"exec"); print(p)
    except Exception as exc: print(exc,file=sys.stderr); raise
