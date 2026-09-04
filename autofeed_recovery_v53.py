from __future__ import annotations

import os
import sys
from pathlib import Path

MARKER = "# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER = "# === ONEWAYPICKZ CS2 AUTOFEED RECOVERY PATCH V5.3 ==="

OVERLAY = r'''
# === ONEWAYPICKZ CS2 AUTOFEED RECOVERY PATCH V5.3 ===
# Provider recovery + identity persistence only. Protected projection math is unchanged.
AUTOFEED_RECOVERY_VERSION = "5.3"

if "_v50_record_from_profile" in globals():
    _autofeed_v53_record_base = _v50_record_from_profile

    def _v50_record_from_profile(profile: PlayerStats, meta: Dict[str, Any]) -> Dict[str, Any]:
        rec = dict(_autofeed_v53_record_base(profile, meta) or {})
        team = str(getattr(profile, "team", "") or (meta or {}).get("team") or "").strip()
        if team:
            rec["team"] = team
        rec["provider_team_verified"] = bool(team)
        rec["autofeed_record_version"] = "5.3"
        return rec


def _autofeed_v53_alias_team(player: str) -> str:
    try:
        alias = _alias_record(player) or {}
    except Exception:
        alias = {}
    return str(alias.get("team") or "").strip()


def _autofeed_v53_db_team(player: str) -> str:
    try:
        rec = lookup_database_player(player) or {}
    except Exception:
        rec = {}
    return str(rec.get("team") or "").strip()


def _autofeed_v53_save_mapping(player: str, profile: Any, meta: Dict[str, Any]) -> None:
    if not player or profile is None:
        return
    team = str(getattr(profile, "team", "") or (meta or {}).get("team") or "").strip()
    player_id = str(getattr(profile, "player_id", "") or "").strip()
    href = str(getattr(profile, "href", "") or (meta or {}).get("profile_url") or "").strip()
    slug = str((meta or {}).get("bo3_slug") or "").strip()
    if not slug and href and "/players/" in href:
        slug = href.split("/players/", 1)[1].split("?", 1)[0].split("/", 1)[0].strip()
    try:
        aliases = load_json(PLAYER_ALIAS_FILE, {})
        aliases = aliases if isinstance(aliases, dict) else {}
        key = normalize_name(player)
        old = aliases.get(key) if isinstance(aliases.get(key), dict) else {}
        aliases[key] = {
            **old,
            "alias": str(player).strip(),
            "bo3_slug": slug or old.get("bo3_slug"),
            "player_id": player_id or old.get("player_id"),
            "team": team or old.get("team", ""),
            "profile_url": href or old.get("profile_url", ""),
            "source": "automatic verified BO3 profile",
            "saved_at": now_iso(),
        }
        save_json(PLAYER_ALIAS_FILE, aliases, force=True)
    except Exception:
        pass


def _autofeed_direct_profile_recovery(players: Sequence[str], max_new: Optional[int] = None) -> Dict[str, Any]:
    unique = list(dict.fromkeys(str(x or "").strip() for x in players if str(x or "").strip()))
    if not unique:
        return {"ok": True, "requested": 0, "attempted": 0, "loaded": 0, "remaining": 0}

    required = [
        "_v50_load_saved_profiles",
        "_v50_build_bo3_index",
        "_v50_profile_available",
        "_v50_fetch_direct_profile",
        "_v50_store_runtime_profile",
    ]
    if not all(name in globals() and callable(globals()[name]) for name in required):
        return {"ok": False, "requested": len(unique), "warning": "direct BO3 recovery helpers unavailable"}

    try:
        loaded_saved = _v50_load_saved_profiles(unique)
    except Exception:
        loaded_saved = 0

    candidates = []
    for player in unique:
        try:
            has_profile = bool(_v50_profile_available(player))
        except Exception:
            has_profile = False
        team = _autofeed_v53_db_team(player) or _autofeed_v53_alias_team(player)
        if (not has_profile) or (not team):
            candidates.append(player)

    batch_default = int(float(os.getenv("CS2_AUTOFEED_DIRECT_PROFILE_BATCH", "24") or 24))
    batch = max(4, min(60, int(max_new if max_new is not None else batch_default)))
    selected = candidates[:batch]
    successes = 0
    team_backfills = 0
    failures: List[Dict[str, Any]] = []

    if not selected:
        covered = sum(bool(_v50_profile_available(p)) for p in unique)
        return {
            "ok": True,
            "requested": len(unique),
            "loaded_from_saved_cache": loaded_saved,
            "attempted": 0,
            "loaded": 0,
            "team_backfills": 0,
            "verified_profiles": covered,
            "remaining": max(0, len(unique) - covered),
            "message": "Direct BO3 recovery already satisfied for the current cached board.",
        }

    try:
        circuit_open = bool(_source_circuit_open("bo3"))
    except Exception:
        circuit_open = False
    if circuit_open:
        return {
            "ok": False,
            "requested": len(unique),
            "attempted": 0,
            "loaded": 0,
            "provider_circuit_open": True,
            "warning": "BO3 direct provider circuit is open; next collector cycle will retry after cooldown.",
        }

    try:
        index, index_status = _v50_build_bo3_index(force=False)
    except Exception as exc:
        index, index_status = {}, {"ok": False, "warning": str(exc)}

    workers = max(1, min(4, int(float(os.getenv("CS2_AUTOFEED_DIRECT_WORKERS", "3") or 3))))
    with ThreadPoolExecutor(max_workers=min(workers, len(selected))) as executor:
        futures = [executor.submit(_v50_fetch_direct_profile, player, index) for player in selected]
        for future in as_completed(futures):
            try:
                player, profile, meta = future.result()
            except Exception as exc:
                failures.append({"player": "", "warning": str(exc)})
                continue
            if profile is None or int(getattr(profile, "maps", 0) or 0) < MIN_PROFILE_MAPS:
                failures.append({"player": player, "warning": str((meta or {}).get("warning") or "profile unavailable")})
                continue
            before_team = _autofeed_v53_db_team(player)
            try:
                _v50_store_runtime_profile(profile, meta or {})
                _autofeed_v53_save_mapping(player, profile, meta or {})
                successes += 1
                if not before_team and str(getattr(profile, "team", "") or "").strip():
                    team_backfills += 1
            except Exception as exc:
                failures.append({"player": player, "warning": str(exc)})

    covered = 0
    for player in unique:
        try:
            covered += int(bool(_v50_profile_available(player)))
        except Exception:
            pass
    remaining = max(0, len(unique) - covered)
    return {
        "ok": successes > 0 or covered > 0,
        "provider": "direct BO3 progressive autofeed",
        "requested": len(unique),
        "loaded_from_saved_cache": loaded_saved,
        "attempted": len(selected),
        "loaded": successes,
        "team_backfills": team_backfills,
        "verified_profiles": covered,
        "remaining": remaining,
        "batch_limit": batch,
        "index": index_status,
        "failures_sample": failures[:12],
        "message": f"Direct BO3 autofeed: {covered}/{len(unique)} profiles cached; {successes} refreshed this cycle; {team_backfills} team mappings backfilled.",
    }


if "_autofeed_reconcile_identity" in globals():
    _autofeed_v53_identity_base = _autofeed_reconcile_identity

    def _autofeed_reconcile_identity(row: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(_autofeed_v53_identity_base(row) or {})
        player = str(out.get("player") or "").strip()
        matchup = str(out.get("matchup") or out.get("evidence") or "").strip()
        a, b = _teams_from_matchup(matchup)
        verified_team = _autofeed_v53_db_team(player) or _autofeed_v53_alias_team(player)
        if a and b and verified_team:
            if _team_name_matches(verified_team, a):
                out["team"], out["opponent"] = a, b
                out["identity_reconciled"] = True
                out["identity_reconcile_source"] = "verified provider team + matchup"
                out["provider_team_verified"] = True
            elif _team_name_matches(verified_team, b):
                out["team"], out["opponent"] = b, a
                out["identity_reconciled"] = True
                out["identity_reconcile_source"] = "verified provider team + matchup"
                out["provider_team_verified"] = True
            else:
                out["identity_reconciled"] = False
                out["provider_team_verified"] = False
                out["flags"] = list(dict.fromkeys(list(out.get("flags") or []) + [
                    "PROFILE TEAM DOES NOT MATCH CURRENT MATCHUP"
                ]))
        elif a and b and str(out.get("identity_reconcile_source") or "").startswith("market"):
            out["identity_reconciled"] = False
            out["provider_team_verified"] = False
            out["flags"] = list(dict.fromkeys(list(out.get("flags") or []) + [
                "PLAYER SIDE UNVERIFIED — WAITING FOR PROVIDER TEAM"
            ]))
        return out


try:
    APP_VERSION = "CS2 v5.3 — AUTOFEED DIRECT RECOVERY + VERIFIED TEAM MAPPING"
except Exception:
    pass

# === END ONEWAYPICKZ CS2 AUTOFEED RECOVERY PATCH V5.3 ===
'''


def patch_text(source: str) -> str:
    text = source
    if PATCH_MARKER in text:
        return text
    if MARKER not in text:
        raise RuntimeError("SESSION BOARD LOAD marker not found; refusing to patch an unknown app layout")
    return text.replace(MARKER, OVERLAY + "\n\n" + MARKER, 1)


def patch_app(path: Path | str = "app.py") -> bool:
    p = Path(path)
    original = p.read_text(encoding="utf-8")
    patched = patch_text(original)
    changed = patched != original
    if changed:
        tmp = p.with_suffix(p.suffix + ".autofeed53.tmp")
        tmp.write_text(patched, encoding="utf-8")
        os.replace(tmp, p)
    return changed


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("app.py")
    try:
        changed = patch_app(path)
        print(f"CS2 autofeed recovery patch v5.3: {'updated' if changed else 'already applied'} -> {path}")
        return 0
    except Exception as exc:
        print(f"CS2 autofeed recovery patch failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
