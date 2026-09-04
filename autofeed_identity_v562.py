from __future__ import annotations
import os, sys
from pathlib import Path

MARKER="# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER="# === ONEWAYPICKZ V5.6.2 REAL IDENTITY CARRY ==="
OVERLAY=r'''
# === ONEWAYPICKZ V5.6.2 REAL IDENTITY CARRY ===
# v5.6 can resolve a real roster after the v5.5.1 carry wrapper has already run.
# Re-store that final decision so later board wrappers cannot drop it.
if "_v55_resolve_prop" in globals():
    _v562_resolve_base=_v55_resolve_prop
    def _v55_resolve_prop(prop):
        out=_v562_resolve_base(prop)
        if bool(out.get("v55_preprojection_identity_verified")) and "V551_PREMODEL_IDENTITY" in globals() and callable(globals().get("_v551_identity_key")):
            V551_PREMODEL_IDENTITY[_v551_identity_key(out)]=True
        return out

if "_v55_ready" in globals():
    _v562_ready_base=_v55_ready
    def _v55_ready(row):
        if "V551_PREMODEL_IDENTITY" in globals() and callable(globals().get("_v551_identity_key")) and V551_PREMODEL_IDENTITY.get(_v551_identity_key(row)):
            row["v55_preprojection_identity_verified"]=True
        return _v562_ready_base(row)

try: APP_VERSION="CS2 v5.6.2 — PRODUCTION VERIFIED DATA PIPELINE"
except Exception: pass
# === END ONEWAYPICKZ V5.6.2 REAL IDENTITY CARRY ===
'''

def patch_text(source):
    if PATCH_MARKER in source:return source
    if MARKER not in source:raise RuntimeError("SESSION BOARD LOAD marker not found")
    return source.replace(MARKER,OVERLAY+"\n\n"+MARKER,1)
def patch_app(path="app.py"):
    p=Path(path); old=p.read_text(encoding="utf-8"); new=patch_text(old); changed=new!=old
    if changed:
        tmp=p.with_suffix(p.suffix+".v562.tmp"); tmp.write_text(new,encoding="utf-8"); os.replace(tmp,p)
    return changed
if __name__=="__main__":
    p=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).with_name("app.py")
    try:
        changed=patch_app(p); compile(p.read_text(encoding="utf-8"),str(p),"exec"); print(f"v5.6.2 patch {'applied' if changed else 'already present'}: {p}")
    except Exception as exc:
        print(f"v5.6.2 patch failed: {exc}",file=sys.stderr); raise
