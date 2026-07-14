# -*- coding: utf-8 -*-
"""
ONEWAYPICKZ CS2 MAPS 1-2 KILL PROJECTION ENGINE
Single-file Streamlit/Railway application.

Design goals
------------
* Reuse the MLB master application's red/black card workflow.
* Pull real CS2 pick'em lines only. Never create synthetic prop lines.
* Work without a paid data provider by using public board endpoints and
  publicly accessible CS2 statistics pages, with transparent source status.
* Keep all credentials outside source code (Railway variables / st.secrets).
* Save official pre-match snapshots, grade Maps 1-2 kills, learn cautiously,
  and preserve history through optional GitHub backup or a Railway volume.

Important source note
---------------------
Public website HTML can change or block automated requests. The app therefore
uses layered fallbacks, caches successful pulls, supports manual CSV fallback,
and lowers its data-quality score whenever a required source is unavailable.
"""

from __future__ import annotations

import base64
import csv
import difflib
import hashlib
import html as html_lib
import io
import json
import math
import os
import random
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import quote, urljoin

import numpy as np
import pandas as pd
import requests
import streamlit as st

# ============================================================
# APP / MODEL CONFIGURATION
# ============================================================

APP_NAME = "ONE WAY PICKZ — CS2"
APP_VERSION = "CS2 v2.0 — DEEP MAP/VETO/ROSTER ENGINE"
MODEL_VERSION = "OWP_CS2_KILLS_M12_2.0"
SEED_VERSION = "CS2_DEEP_DATA_SEED_2026_07"

# The Underdog board endpoint is public but undocumented and can change.
# UNDERDOG_URL_OVERRIDE lets Railway use a replacement endpoint without a code edit.
_UNDERDOG_OVERRIDE = os.getenv("UNDERDOG_URL_OVERRIDE", "").strip()
UNDERDOG_URLS = [
    _UNDERDOG_OVERRIDE,
    "https://api.underdogfantasy.com/beta/v6/over_under_lines",
    "https://api.underdogfantasy.com/beta/v5/over_under_lines",
    "https://api.underdogfantasy.com/beta/v4/over_under_lines",
    "https://api.underdogfantasy.com/beta/v3/over_under_lines",
    "https://api.underdogfantasy.com/beta/v2/over_under_lines",
    "https://api.underdogfantasy.com/v1/over_under_lines",
]
UNDERDOG_URLS = list(dict.fromkeys(url for url in UNDERDOG_URLS if url))
UNDERDOG_API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://underdogsports.com",
    "Referer": "https://underdogsports.com/",
}
PRIZEPICKS_URL = "https://api.prizepicks.com/projections"
HLTV_BASE = "https://www.hltv.org"
PANDASCORE_BASE = "https://api.pandascore.co"

# Strict initial thresholds. These should be changed only after graded backtests.
MIN_OFFICIAL_PROB = 0.620
MIN_OFFICIAL_EDGE = 2.00
MIN_OFFICIAL_DATA_SCORE = 80
MIN_PLAYABLE_PROB = 0.575
MIN_PLAYABLE_EDGE = 1.20
MIN_PLAYABLE_DATA_SCORE = 66
MIN_TRACK_PROB = 0.535
MIN_PROFILE_MAPS = 15
MIN_OFFICIAL_PROFILE_MAPS = 25
MIN_MAP_CONFIDENCE = 52
MAX_LEARNING_PROJECTION_SHIFT = 1.00
MAX_LEARNING_PROB_SHIFT = 0.045
SIMULATIONS = 30000
DEEP_PULL_MATCH_LIMIT = int(os.getenv("CS2_DEEP_MATCH_LIMIT", "6") or 6)
DEEP_PULL_MAPSTATS_LIMIT = int(os.getenv("CS2_DEEP_MAPSTATS_LIMIT", "6") or 6)
DEEP_DATA_ENABLED_DEFAULT = os.getenv("CS2_DEEP_DATA", "true").strip().lower() not in {"0", "false", "no", "off"}
MIN_MAP_PROFILE_MAPS = 4
MIN_CURRENT_ROSTER_MAPS = 4

# Baseline values are used only when a real player profile was located but a
# nonessential split is missing. They never create a player or prop line.
LEAGUE_KPR = 0.680
LEAGUE_DPR = 0.680
LEAGUE_ADR = 72.0
LEAGUE_RATING = 1.00
DEFAULT_TWO_MAP_ROUNDS = 42.8

KNOWN_MAPS = [
    "Ancient", "Anubis", "Dust2", "Inferno", "Mirage", "Nuke",
    "Overpass", "Train", "Vertigo", "Cache", "Cobblestone"
]
HLTV_MAP_KEYS = {
    "Ancient": "de_ancient", "Anubis": "de_anubis", "Dust2": "de_dust2",
    "Inferno": "de_inferno", "Mirage": "de_mirage", "Nuke": "de_nuke",
    "Overpass": "de_overpass", "Train": "de_train", "Vertigo": "de_vertigo",
    "Cache": "de_cache", "Cobblestone": "de_cbble",
}
MAP_ROUND_BASE = {
    "Ancient": 21.2,
    "Anubis": 21.5,
    "Dust2": 20.9,
    "Inferno": 21.4,
    "Mirage": 21.3,
    "Nuke": 20.9,
    "Overpass": 21.2,
    "Train": 20.6,
    "Vertigo": 20.8,
    "Cache": 21.0,
}

# ============================================================
# STORAGE — RAILWAY VOLUME / LOCAL / OPTIONAL GITHUB
# ============================================================

def _choose_storage_dir() -> str:
    configured = os.getenv("CS2_DATA_DIR", "").strip()
    candidates = [configured] if configured else []
    candidates += ["/data/cs2_engine", "cs2_engine"]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            os.makedirs(candidate, exist_ok=True)
            probe = os.path.join(candidate, ".write_test")
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("ok")
            os.remove(probe)
            return candidate
        except Exception:
            continue
    return "cs2_engine"

STORAGE_DIR = _choose_storage_dir()
CACHE_DIR = os.path.join(STORAGE_DIR, "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

PICK_LOG = os.path.join(STORAGE_DIR, "cs2_official_snapshots.json")
RESULT_LOG = os.path.join(STORAGE_DIR, "cs2_graded_results.json")
LEARNING_FILE = os.path.join(STORAGE_DIR, "cs2_learning.json")
LINE_HISTORY_FILE = os.path.join(STORAGE_DIR, "cs2_line_history.json")
MANUAL_ODDS_FILE = os.path.join(STORAGE_DIR, "cs2_manual_odds.json")
ROLE_OVERRIDES_FILE = os.path.join(STORAGE_DIR, "cs2_role_overrides.json")
PLAYER_OVERRIDES_FILE = os.path.join(STORAGE_DIR, "cs2_player_profile_overrides.json")
REQUEST_LOG_FILE = os.path.join(STORAGE_DIR, "cs2_request_log.json")
SOURCE_CACHE_FILE = os.path.join(STORAGE_DIR, "cs2_source_cache.json")
MATCH_ALIAS_FILE = os.path.join(STORAGE_DIR, "cs2_match_aliases.json")
DEEP_TEAM_PROFILE_FILE = os.path.join(STORAGE_DIR, "cs2_deep_team_profiles.json")
DEEP_PLAYER_MAP_FILE = os.path.join(STORAGE_DIR, "cs2_player_map_profiles.json")
VETO_HISTORY_FILE = os.path.join(STORAGE_DIR, "cs2_veto_history.json")
ROSTER_HISTORY_FILE = os.path.join(STORAGE_DIR, "cs2_roster_history.json")
MARKET_CONSENSUS_FILE = os.path.join(STORAGE_DIR, "cs2_market_consensus.json")

PROTECTED_FILES = {
    os.path.basename(PICK_LOG),
    os.path.basename(RESULT_LOG),
    os.path.basename(LEARNING_FILE),
    os.path.basename(LINE_HISTORY_FILE),
}

# ============================================================
# STREAMLIT PAGE / MLB-LIKE WHITE + RED UI
# ============================================================

st.set_page_config(
    page_title="OneWayPickz CS2 — Maps 1-2 Kills",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
:root {
  --owp-red:#ff2f3d;
  --owp-red2:#b50012;
  --owp-black:#050506;
  --owp-panel:#0d0d10;
  --owp-white:#ffffff;
  --owp-muted:#c9c9cf;
  --owp-border:rgba(255,47,61,.42);
}
.stApp {
  background:radial-gradient(circle at top,#3a0008 0%,#0a0a0c 39%,#020203 100%);
  color:#fff;
}
.block-container {padding-top:1.0rem;max-width:1550px;}
h1,h2,h3,h4 {color:#fff;}
[data-testid="stMetric"] {
  background:linear-gradient(145deg,#ffffff,#f1f1f3);
  border:2px solid rgba(255,47,61,.70);
  border-radius:18px;
  padding:14px;
  box-shadow:0 0 22px rgba(255,31,50,.20);
}
[data-testid="stMetricLabel"], [data-testid="stMetricValue"], [data-testid="stMetricDelta"] {color:#09090b!important;}
.hero-panel {
  background:linear-gradient(135deg,rgba(255,255,255,.99),rgba(240,240,243,.96));
  color:#08080a;
  border:2px solid rgba(255,47,61,.78);
  border-radius:26px;
  padding:22px;
  box-shadow:0 0 36px rgba(255,0,28,.24);
  margin-bottom:18px;
}
.hero-panel * {color:#08080a;}
.pick-card {
  background:linear-gradient(145deg,#fff,#f2f2f4);
  color:#08080a;
  border:2px solid rgba(255,47,61,.60);
  border-radius:22px;
  padding:20px;
  box-shadow:0 0 26px rgba(255,0,30,.18);
  margin-bottom:16px;
}
.pick-card * {color:#08080a;}
.official-card {
  background:linear-gradient(145deg,#160006,#070708);
  color:#fff;
  border:2px solid rgba(255,47,61,.90);
  border-radius:22px;
  padding:20px;
  box-shadow:0 0 32px rgba(255,0,30,.30);
  margin-bottom:16px;
}
.official-card * {color:#fff;}
.warn-card {
  background:linear-gradient(145deg,#281b00,#0c0900);
  border:1px solid rgba(255,191,54,.55);
  border-radius:20px;
  padding:18px;
  margin-bottom:14px;
}
.green-card {
  background:linear-gradient(145deg,#032214,#06110b);
  border:1px solid rgba(0,255,137,.55);
  border-radius:20px;
  padding:18px;
  margin-bottom:14px;
}
.big-title {font-size:42px;font-weight:950;letter-spacing:-1px;color:#070709;}
.sub-title {color:#3f3f44;font-size:15px;margin-top:-6px;}
.player-name {font-size:25px;font-weight:950;}
.big-number {font-size:42px;font-weight:950;line-height:1.02;}
.red {color:#e00020!important;}
.green {color:#00a84f!important;}
.orange {color:#b96d00!important;}
.white {color:#fff!important;}
.muted {color:#74747d!important;font-size:13px;}
.small-muted {color:#74747d!important;font-size:12px;}
.official-card .muted,.official-card .small-muted {color:#c8c8cf!important;}
.badge {
  display:inline-block;padding:6px 11px;border-radius:999px;
  background:#fff0f2;border:1px solid rgba(224,0,32,.45);
  color:#9b0016!important;font-weight:850;margin:3px 4px 3px 0;
}
.badge-good {background:#e9fff3;border-color:rgba(0,168,79,.45);color:#007b3a!important;}
.badge-warn {background:#fff4dc;border-color:rgba(185,109,0,.45);color:#8a5000!important;}
.badge-dark {background:#170006;border-color:rgba(255,47,61,.70);color:#fff!important;}
.metric-grid {display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;margin:12px 0;}
.metric-box {background:rgba(255,255,255,.88);border:1px solid rgba(20,20,24,.14);border-radius:15px;padding:12px;min-height:82px;}
.official-card .metric-box {background:rgba(255,255,255,.06);border-color:rgba(255,255,255,.14);}
.metric-label {font-size:11px;font-weight:850;letter-spacing:.04em;text-transform:uppercase;color:#6c6c74!important;}
.official-card .metric-label {color:#bdbdc5!important;}
.metric-value {font-size:22px;font-weight:950;margin-top:5px;}
.section-title-pro {margin:21px 0 10px;font-size:24px;font-weight:950;color:#fff;border-left:5px solid #ff2f3d;padding-left:12px;}
.hr-soft {border-top:1px solid rgba(100,100,110,.25);margin:13px 0;}
.stTabs [data-baseweb="tab"] {color:#d0d0d6;font-weight:900;}
.stTabs [aria-selected="true"] {color:#fff!important;border-bottom:3px solid #ff2f3d!important;}
[data-testid="stSidebar"] {background:linear-gradient(180deg,#100004,#050506);}
[data-testid="stDataFrame"] {border:1px solid rgba(255,47,61,.35);border-radius:14px;overflow:hidden;}
@media(max-width:1100px){.metric-grid{grid-template-columns:repeat(3,minmax(0,1fr));}}
@media(max-width:850px){
 .big-title{font-size:29px}.big-number{font-size:31px}.player-name{font-size:21px}
 .pick-card,.official-card{padding:14px;border-radius:17px}
 .metric-grid{grid-template-columns:repeat(2,minmax(0,1fr));}
 .pick-card div[style*="grid-template-columns"],.official-card div[style*="grid-template-columns"]{grid-template-columns:1fr!important;}
}
</style>
""",
    unsafe_allow_html=True,
)

# ============================================================
# GENERAL HELPERS
# ============================================================

def get_secret(key: str, default: str = "") -> str:
    try:
        val = st.secrets.get(key, default)
        if val not in [None, ""]:
            return str(val)
    except Exception:
        pass
    return str(os.getenv(key, default) or default)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def local_now() -> datetime:
    # User is in America/Los_Angeles. zoneinfo is in Python 3.9+.
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Los_Angeles"))
    except Exception:
        return datetime.now() - timedelta(hours=7)


def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None:
            return default
        if isinstance(value, str):
            value = value.strip().replace(",", "").replace("%", "")
            if value in {"", "—", "-", "N/A", "n/a", "None", "null"}:
                return default
        out = float(value)
        if not math.isfinite(out):
            return default
        return out
    except Exception:
        return default


def safe_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    f = safe_float(value, None)
    return int(f) if f is not None else default


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def normalize_name(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("_", " ").replace("-", " ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return " ".join(text.split())


def normalize_team(value: Any) -> str:
    text = normalize_name(value)
    aliases = {
        "natus vincere": "navi", "navi": "navi", "team vitality": "vitality",
        "faze clan": "faze", "g2 esports": "g2", "team liquid": "liquid",
        "mousesports": "mouz", "virtus pro": "virtus pro", "vp": "virtus pro",
        "the mongolz": "mongolz", "mongolz": "mongolz", "team spirit": "spirit",
        "ninjas in pyjamas": "nip", "nip": "nip", "complexity gaming": "complexity",
        "furia esports": "furia", "astralis": "astralis", "heroic": "heroic",
    }
    return aliases.get(text, text)


def name_similarity(a: Any, b: Any) -> float:
    aa, bb = normalize_name(a), normalize_name(b)
    if not aa or not bb:
        return 0.0
    if aa == bb:
        return 1.0
    if aa in bb or bb in aa:
        return 0.94
    a_parts, b_parts = aa.split(), bb.split()
    if a_parts and b_parts and a_parts[-1] == b_parts[-1]:
        first_bonus = 0.08 if a_parts[0][:1] == b_parts[0][:1] else 0.0
        return min(0.93, 0.82 + first_bonus)
    return difflib.SequenceMatcher(None, aa, bb).ratio()


def stable_seed(*parts: Any) -> int:
    raw = SEED_VERSION + "|" + "|".join(str(x) for x in parts)
    return int(hashlib.md5(raw.encode("utf-8")).hexdigest()[:8], 16)


def strip_tags(raw: str) -> str:
    if not raw:
        return ""
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", raw, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(?:div|p|tr|td|th|li|span|a|section|h\d)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    lines = [" ".join(x.split()) for x in text.splitlines()]
    return "\n".join(x for x in lines if x)


def flatten_json(obj: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from flatten_json(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from flatten_json(item)


def attrs(obj: Any) -> Dict[str, Any]:
    if not isinstance(obj, dict):
        return {}
    out = {}
    if isinstance(obj.get("attributes"), dict):
        out.update(obj["attributes"])
    for key, value in obj.items():
        if key not in {"attributes", "relationships", "included", "data"} and key not in out:
            out[key] = value
    return out


def object_type(obj: Any) -> str:
    if not isinstance(obj, dict):
        return ""
    return str(obj.get("type") or attrs(obj).get("type") or "").lower().replace("-", "_")


def object_id(obj: Any) -> str:
    if not isinstance(obj, dict):
        return ""
    return str(obj.get("id") or attrs(obj).get("id") or "")


def load_json(path: str, default: Any) -> Any:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
    except Exception:
        pass
    return default


def _payload_len(obj: Any) -> int:
    if isinstance(obj, (list, dict)):
        return len(obj)
    return 0


def save_json(path: str, payload: Any, github_backup: bool = False, force: bool = False) -> bool:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        name = os.path.basename(path)
        if (not force) and name in PROTECTED_FILES and os.path.exists(path):
            old = load_json(path, None)
            old_n, new_n = _payload_len(old), _payload_len(payload)
            if old_n >= 30 and (new_n == 0 or new_n < int(old_n * 0.85)):
                log_request("storage", path, 0, f"blocked suspicious shrink {old_n}->{new_n}")
                return False
            try:
                with open(path + ".bak", "w", encoding="utf-8") as fh:
                    json.dump(old, fh, indent=2, ensure_ascii=False)
            except Exception:
                pass
        temp = path + ".tmp"
        with open(temp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
        os.replace(temp, path)
        if github_backup:
            github_backup_file(path)
        return True
    except Exception as exc:
        log_request("storage", path, 0, f"save error: {exc}")
        return False


def log_request(source: str, url: str, status: int, message: str = "") -> None:
    try:
        rows = load_json(REQUEST_LOG_FILE, [])
        rows.append({
            "time": now_iso(), "source": source, "url": url[:500],
            "status": int(status or 0), "message": str(message)[:700]
        })
        save_json(REQUEST_LOG_FILE, rows[-400:])
    except Exception:
        pass


def github_backup_file(path: str) -> Dict[str, Any]:
    token = get_secret("GITHUB_TOKEN")
    repo = get_secret("GITHUB_REPO")  # owner/repo
    branch = get_secret("GITHUB_BRANCH", "main")
    prefix = get_secret("GITHUB_DATA_PATH", "learning_data/cs2")
    if not token or not repo or not os.path.exists(path):
        return {"ok": False, "message": "GitHub backup variables not configured"}
    target = f"{prefix.strip('/')}/{os.path.basename(path)}"
    url = f"https://api.github.com/repos/{repo}/contents/{target}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    try:
        current = requests.get(url, headers=headers, params={"ref": branch}, timeout=15)
        sha = current.json().get("sha") if current.ok and isinstance(current.json(), dict) else None
        with open(path, "rb") as fh:
            content = base64.b64encode(fh.read()).decode("ascii")
        payload = {
            "message": f"Update CS2 data {os.path.basename(path)}",
            "content": content,
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha
        response = requests.put(url, headers=headers, json=payload, timeout=25)
        ok = response.status_code in {200, 201}
        log_request("github", url, response.status_code, "backup" if ok else response.text[:300])
        return {"ok": ok, "status": response.status_code, "message": "Backed up" if ok else response.text[:300]}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}

# ============================================================
# NETWORK LAYER / PUBLIC SOURCE CACHE
# ============================================================

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8",
    "Cache-Control": "no-cache",
}


def cache_path(key: str, ext: str = "txt") -> str:
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()
    return os.path.join(CACHE_DIR, f"{digest}.{ext}")


def read_cache(key: str, max_age_seconds: int) -> Optional[str]:
    path = cache_path(key)
    try:
        if os.path.exists(path) and time.time() - os.path.getmtime(path) <= max_age_seconds:
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read()
    except Exception:
        pass
    return None


def write_cache(key: str, content: str) -> None:
    try:
        with open(cache_path(key), "w", encoding="utf-8") as fh:
            fh.write(content)
    except Exception:
        pass


def http_get_text(
    url: str,
    source: str,
    ttl: int = 900,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 18,
    allow_stale: bool = True,
) -> Tuple[Optional[str], Dict[str, Any]]:
    full_key = url + "?" + json.dumps(params or {}, sort_keys=True)
    cached = read_cache(full_key, ttl)
    if cached is not None:
        return cached, {"ok": True, "source": source, "cache": "fresh", "status": 200, "url": url}
    merged = dict(DEFAULT_HEADERS)
    if headers:
        merged.update(headers)
    try:
        response = requests.get(url, params=params, headers=merged, timeout=timeout)
        log_request(source, response.url, response.status_code, response.reason)
        if response.ok and response.text:
            write_cache(full_key, response.text)
            return response.text, {"ok": True, "source": source, "cache": "live", "status": response.status_code, "url": response.url}
        message = f"HTTP {response.status_code}"
    except Exception as exc:
        message = str(exc)
        log_request(source, url, 0, message)
    if allow_stale:
        stale = read_cache(full_key, 60 * 60 * 24 * 14)
        if stale is not None:
            return stale, {"ok": True, "source": source, "cache": "stale", "status": 200, "url": url, "warning": message}
    return None, {"ok": False, "source": source, "status": 0, "url": url, "warning": message}


def http_get_json(
    url: str,
    source: str,
    ttl: int = 300,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 18,
    allow_stale: bool = True,
) -> Tuple[Optional[Any], Dict[str, Any]]:
    text, status = http_get_text(url, source, ttl, params, headers, timeout, allow_stale)
    if text is None:
        return None, status
    try:
        return json.loads(text), status
    except Exception as exc:
        status.update({"ok": False, "warning": f"JSON parse failed: {exc}"})
        return None, status

# ============================================================
# REAL PICK'EM LINE PULLS — UNDERDOG + PRIZEPICKS
# ============================================================

CS2_MARKET_PATTERNS = [
    r"maps?\s*1\s*[-+&/]\s*2.*kills?",
    r"kills?.*maps?\s*1\s*[-+&/]\s*2",
    r"map\s*1\s*\+\s*2\s*kills?",
    r"first\s*2\s*maps?.*kills?",
    r"kills?\s*on\s*maps?\s*1.*2",
    r"m1\s*[-+&/]\s*m2.*kills?",
]
BAD_MARKET_TERMS = ["headshot", "fantasy", "assists", "deaths", "rounds", "map 3", "maps 1-3", "series kills"]
CS2_SPORT_TERMS = ["cs2", "counter strike", "counter-strike", "csgo", "cs:go", "esports"]


def _object_text(*objects: Any) -> str:
    wanted = [
        "title", "display_title", "name", "display_name", "full_name", "player_name",
        "first_name", "last_name", "stat", "stat_type", "appearance_stat", "display_stat",
        "market", "market_name", "projection_type", "label", "description", "sport",
        "sport_name", "league", "league_name", "team", "team_name", "opponent",
        "opponent_name", "position", "status", "game_title", "matchup"
    ]
    parts: List[str] = []
    for obj in objects:
        a = attrs(obj)
        for key in wanted:
            value = a.get(key)
            if isinstance(value, dict):
                for nested in wanted:
                    if value.get(nested) not in [None, ""]:
                        parts.append(str(value[nested]))
            elif value not in [None, ""]:
                parts.append(str(value))
    return " | ".join(parts)


def _is_cs2_m12_kills(text: str) -> bool:
    blob = normalize_name(text)
    raw = str(text).lower()
    sport_ok = any(term in raw for term in CS2_SPORT_TERMS)
    market_ok = any(re.search(pattern, raw, re.I) for pattern in CS2_MARKET_PATTERNS)
    bad = any(term in raw for term in BAD_MARKET_TERMS)
    # Some feeds omit sport from the local object but include an explicit CS2 market title.
    explicit = "maps 1 2" in blob and "kill" in blob
    return (sport_ok or explicit) and market_ok and not bad


def _relationship_id(obj: Dict[str, Any], names: Sequence[str]) -> str:
    rels = obj.get("relationships") if isinstance(obj, dict) else {}
    if not isinstance(rels, dict):
        return ""
    for name in names:
        for candidate in {name, name.replace("_", "-"), name.replace("-", "_")}:
            node = rels.get(candidate)
            data = node.get("data") if isinstance(node, dict) else node
            if isinstance(data, dict) and data.get("id") not in [None, ""]:
                return str(data["id"])
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("id") not in [None, ""]:
                        return str(item["id"])
    return ""


def _best_related(obj: Dict[str, Any], names: Sequence[str], by_id: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    rid = _relationship_id(obj, names)
    return by_id.get(rid) if rid else None


def _extract_line(*objects: Any) -> Optional[float]:
    keys = ["stat_value", "line_score", "over_under_line", "target_value", "line", "projection"]
    for obj in objects:
        a = attrs(obj)
        for key in keys:
            val = safe_float(a.get(key), None)
            if val is not None and 5 <= val <= 80:
                return float(val)
    text = _object_text(*objects)
    patterns = [
        r"(?:line|projection|stat value|target)\s*[:=]?\s*(\d{1,2}(?:\.5)?)",
        r"\b(\d{1,2}\.5)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            val = safe_float(match.group(1), None)
            if val is not None and 5 <= val <= 80:
                return val
    return None


def _extract_player_name(*objects: Any) -> str:
    candidate_keys = ["display_name", "full_name", "player_name", "name", "title", "short_name"]
    for obj in objects:
        a = attrs(obj)
        for key in candidate_keys:
            value = a.get(key)
            if isinstance(value, str):
                clean = re.sub(r"\s+(?:maps?|m)\s*1.*$", "", value, flags=re.I).strip(" -|:")
                clean = re.sub(r"\s+(?:kills?|headshots?|fantasy).*", "", clean, flags=re.I).strip(" -|:")
                if clean and len(normalize_name(clean).split()) <= 5 and not _is_cs2_m12_kills(clean):
                    return clean
        first, last = a.get("first_name"), a.get("last_name")
        if first or last:
            return f"{first or ''} {last or ''}".strip()
    # Fallback from title: "ZywOo Maps 1-2 Kills"
    text = _object_text(*objects)
    match = re.search(r"^\s*([^|]{2,40}?)\s+(?:maps?|m)\s*1\s*[-+&/]\s*(?:maps?|m)?\s*2", text, re.I)
    return match.group(1).strip(" -|:") if match else ""


def _first_attr(objects: Sequence[Any], keys: Sequence[str]) -> Any:
    for obj in objects:
        a = attrs(obj)
        for key in keys:
            if a.get(key) not in [None, ""]:
                value = a.get(key)
                if isinstance(value, dict):
                    value = value.get("name") or value.get("display_name") or value.get("title")
                if value not in [None, ""]:
                    return value
    return None



def _record_map(items: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(items, list):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for item in items:
        if isinstance(item, dict) and item.get("id") not in [None, ""]:
            out[str(item.get("id"))] = item
    return out


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def _current_line_time(start_time: Any) -> bool:
    """Reject clearly completed events while allowing missing/unparseable start times."""
    parsed = _parse_iso_datetime(start_time)
    if parsed is None:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc) >= datetime.now(timezone.utc) - timedelta(hours=8)


def _parse_underdog_top_level(data: Any) -> List[Dict[str, Any]]:
    """
    Parse Underdog's common top-level v5/v6 shape:
    players + appearances + games/matches + teams + over_under_lines.
    A generic JSON:API parser remains below as a second fallback.
    """
    if not isinstance(data, dict):
        return []
    line_items = data.get("over_under_lines")
    appearance_items = data.get("appearances")
    player_items = data.get("players")
    if not isinstance(line_items, list) or not isinstance(appearance_items, list) or not isinstance(player_items, list):
        return []

    appearances = _record_map(appearance_items)
    players = _record_map(player_items)
    games = _record_map(data.get("games") or data.get("matches") or data.get("events") or [])
    teams = _record_map(data.get("teams") or [])
    sports = _record_map(data.get("sports") or [])
    rows: List[Dict[str, Any]] = []

    for line_obj in line_items:
        if not isinstance(line_obj, dict):
            continue
        over_under = line_obj.get("over_under") if isinstance(line_obj.get("over_under"), dict) else {}
        appearance_stat = over_under.get("appearance_stat") if isinstance(over_under.get("appearance_stat"), dict) else {}
        appearance_id = (
            appearance_stat.get("appearance_id") or line_obj.get("appearance_id") or
            over_under.get("appearance_id")
        )
        appearance = appearances.get(str(appearance_id), {}) if appearance_id not in [None, ""] else {}
        player_id = appearance.get("player_id") or appearance_stat.get("player_id")
        player_obj = players.get(str(player_id), {}) if player_id not in [None, ""] else {}
        game_id = appearance.get("match_id") or appearance.get("game_id") or appearance.get("event_id")
        game_obj = games.get(str(game_id), {}) if game_id not in [None, ""] else {}
        team_id = appearance.get("team_id") or player_obj.get("team_id")
        team_obj = teams.get(str(team_id), {}) if team_id not in [None, ""] else {}
        sport_id = appearance.get("sport_id") or player_obj.get("sport_id") or game_obj.get("sport_id")
        sport_obj = sports.get(str(sport_id), {}) if sport_id not in [None, ""] else {}

        stat_name = appearance_stat.get("stat") or over_under.get("stat") or line_obj.get("stat") or ""
        text = _object_text(line_obj, over_under, appearance_stat, appearance, player_obj, game_obj, team_obj, sport_obj)
        text = f"{text} | {stat_name} | {appearance.get('sport_id','')} | {game_obj.get('sport_id','')}"
        if not _is_cs2_m12_kills(text):
            continue

        status_blob = " ".join(str(x.get(k, "")) for x in [line_obj, over_under, appearance, game_obj] for k in ["status", "state", "active", "hidden"]).lower()
        if any(term in status_blob for term in ["suspended", "removed", "closed", "inactive", "hidden", "disabled", "settled"]):
            continue

        first = str(player_obj.get("first_name") or "").strip()
        last = str(player_obj.get("last_name") or "").strip()
        player = str(player_obj.get("full_name") or player_obj.get("display_name") or f"{first} {last}").strip()
        if not player:
            player = _extract_player_name(player_obj, appearance, appearance_stat, over_under, line_obj)
        line = _extract_line(line_obj, over_under, appearance_stat)
        if not player or line is None:
            continue

        team = str(team_obj.get("name") or team_obj.get("display_name") or player_obj.get("team_name") or appearance.get("team_name") or "").strip()
        home_id = game_obj.get("home_team_id")
        away_id = game_obj.get("away_team_id")
        opponent_obj: Dict[str, Any] = {}
        if team_id not in [None, ""]:
            other_id = away_id if str(home_id) == str(team_id) else home_id if str(away_id) == str(team_id) else None
            if other_id not in [None, ""]:
                opponent_obj = teams.get(str(other_id), {})
        opponent = str(opponent_obj.get("name") or opponent_obj.get("display_name") or appearance.get("opponent_name") or "").strip()
        matchup = str(game_obj.get("title") or game_obj.get("display_title") or game_obj.get("name") or appearance.get("matchup") or "").strip()
        start_time = str(
            game_obj.get("scheduled_at") or game_obj.get("starts_at") or game_obj.get("start_time") or
            appearance.get("scheduled_at") or appearance.get("starts_at") or ""
        ).strip()
        if not _current_line_time(start_time):
            continue

        rows.append({
            "source": "Underdog",
            "prop_id": str(line_obj.get("id") or hashlib.md5(f"{player}|{line}|{start_time}".encode()).hexdigest()[:12]),
            "player": player,
            "team": team,
            "opponent": opponent,
            "matchup": matchup,
            "start_time": start_time,
            "market": "Maps 1-2 Kills",
            "line": float(line),
            "evidence": text[:900],
            "source_pulled_at": now_iso(),
        })
    return rows


def fetch_underdog_cs2_board() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    all_status: List[Dict[str, Any]] = []
    for url in UNDERDOG_URLS:
        data, status = http_get_json(
            url, "Underdog", ttl=90, headers=UNDERDOG_API_HEADERS,
            timeout=12, allow_stale=False
        )
        all_status.append(status)
        if not data:
            continue

        direct_rows = _parse_underdog_top_level(data)
        if direct_rows:
            dedup_direct: Dict[Tuple[str, float, str], Dict[str, Any]] = {}
            for row in direct_rows:
                key = (normalize_name(row["player"]), row["line"], str(row.get("start_time", ""))[:16])
                dedup_direct[key] = row
            direct_result = list(dedup_direct.values())
            return direct_result, {
                "ok": True, "provider": "Underdog", "url": url, "rows": len(direct_result),
                "parser": "top-level-v5/v6", "cache": status.get("cache"), "statuses": all_status
            }

        objects = list(flatten_json(data))
        by_id = {object_id(obj): obj for obj in objects if object_id(obj)}
        rows: List[Dict[str, Any]] = []
        rejected = 0
        for line_obj in objects:
            a = attrs(line_obj)
            typ = object_type(line_obj)
            looks_like_line = "over_under_line" in typ or any(a.get(k) not in [None, ""] for k in ["stat_value", "line_score", "over_under_line", "target_value"])
            if not looks_like_line:
                continue
            ou_obj = _best_related(line_obj, ["over_under", "over_unders"], by_id)
            appearance_obj = _best_related(ou_obj or line_obj, ["appearance", "appearances"], by_id)
            player_obj = _best_related(appearance_obj or ou_obj or line_obj, ["player", "players"], by_id)
            game_obj = _best_related(appearance_obj or ou_obj or line_obj, ["game", "match", "event"], by_id)
            linked = [line_obj, ou_obj, appearance_obj, player_obj, game_obj]
            text = _object_text(*linked)
            if not _is_cs2_m12_kills(text):
                rejected += 1
                continue
            status_blob = " ".join(str(attrs(x).get(k, "")) for x in linked if isinstance(x, dict) for k in ["status", "state", "active", "hidden"]).lower()
            if any(x in status_blob for x in ["suspended", "removed", "closed", "inactive", "hidden", "disabled"]):
                continue
            player = _extract_player_name(player_obj, appearance_obj, ou_obj, line_obj)
            line = _extract_line(line_obj, ou_obj, appearance_obj)
            if not player or line is None:
                continue
            team = _first_attr(linked, ["team_name", "team", "organization", "current_team"])
            opponent = _first_attr(linked, ["opponent_name", "opponent", "versus", "away_team", "home_team"])
            matchup = _first_attr(linked, ["matchup", "game_title", "title", "description"])
            start_time = _first_attr(linked, ["scheduled_at", "start_time", "starts_at", "start_at", "game_date"])
            if not _current_line_time(start_time):
                continue
            rows.append({
                "source": "Underdog",
                "prop_id": object_id(line_obj) or hashlib.md5(f"{player}|{line}|{start_time}".encode()).hexdigest()[:12],
                "player": player,
                "team": str(team or "").strip(),
                "opponent": str(opponent or "").strip(),
                "matchup": str(matchup or "").strip(),
                "start_time": str(start_time or "").strip(),
                "market": "Maps 1-2 Kills",
                "line": float(line),
                "evidence": text[:900],
                "source_pulled_at": now_iso(),
            })
        dedup: Dict[Tuple[str, float, str], Dict[str, Any]] = {}
        for row in rows:
            key = (normalize_name(row["player"]), row["line"], str(row.get("start_time", ""))[:16])
            dedup[key] = row
        result = list(dedup.values())
        if result:
            return result, {
                "ok": True, "provider": "Underdog", "url": url, "rows": len(result),
                "objects": len(objects), "rejected": rejected, "parser": "generic-json", "statuses": all_status
            }
    return [], {"ok": False, "provider": "Underdog", "rows": 0, "statuses": all_status}


def fetch_prizepicks_cs2_board() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    params = {"league_id": "", "per_page": 250, "single_stat": "true"}
    data, status = http_get_json(PRIZEPICKS_URL, "PrizePicks", ttl=90, params=params, timeout=20, allow_stale=False)
    if not data or not isinstance(data, dict):
        return [], {"ok": False, "provider": "PrizePicks", "status": status}
    included = data.get("included") if isinstance(data.get("included"), list) else []
    by_id = {str(x.get("id")): x for x in included if isinstance(x, dict) and x.get("id") is not None}
    rows: List[Dict[str, Any]] = []
    for item in data.get("data", []):
        if not isinstance(item, dict):
            continue
        a = attrs(item)
        rels = item.get("relationships") or {}
        new_player = None
        league = None
        for rel_name, target in [("new_player", "player"), ("league", "league")]:
            node = rels.get(rel_name) if isinstance(rels, dict) else None
            rid = node.get("data", {}).get("id") if isinstance(node, dict) and isinstance(node.get("data"), dict) else None
            if rid is not None:
                if target == "player":
                    new_player = by_id.get(str(rid))
                else:
                    league = by_id.get(str(rid))
        text = _object_text(item, new_player, league)
        stat_type = str(a.get("stat_type") or a.get("projection_type") or "")
        raw = f"{text} | {stat_type}"
        if not _is_cs2_m12_kills(raw):
            continue
        line = safe_float(a.get("line_score"), None)
        player = _extract_player_name(new_player, item)
        if line is None or not player:
            continue
        pa = attrs(new_player)
        rows.append({
            "source": "PrizePicks",
            "prop_id": str(item.get("id") or hashlib.md5(f"{player}|{line}".encode()).hexdigest()[:12]),
            "player": player,
            "team": str(pa.get("team") or pa.get("team_name") or ""),
            "opponent": "",
            "matchup": str(a.get("description") or ""),
            "start_time": str(a.get("start_time") or a.get("starts_at") or ""),
            "market": "Maps 1-2 Kills",
            "line": float(line),
            "evidence": raw[:900],
        })
    dedup = {(normalize_name(x["player"]), x["line"], x["source"]): x for x in rows}
    return list(dedup.values()), {"ok": bool(rows), "provider": "PrizePicks", "rows": len(rows), "status": status}


def parse_manual_board_dataframe(df: pd.DataFrame) -> List[Dict[str, Any]]:
    if df is None or df.empty:
        return []
    columns = {normalize_name(c): c for c in df.columns}
    def col(*names: str) -> Optional[str]:
        for name in names:
            if normalize_name(name) in columns:
                return columns[normalize_name(name)]
        return None
    pcol = col("Player", "Player Name")
    lcol = col("Line", "UD Line", "Projection Line")
    if not pcol or not lcol:
        return []
    rows = []
    for _, raw in df.iterrows():
        player = str(raw.get(pcol, "")).strip()
        line = safe_float(raw.get(lcol), None)
        if not player or line is None:
            continue
        rows.append({
            "source": str(raw.get(col("Source") or "", "Manual") or "Manual"),
            "prop_id": hashlib.md5(f"manual|{player}|{line}|{raw.get(col('Start Time') or '', '')}".encode()).hexdigest()[:12],
            "player": player,
            "team": str(raw.get(col("Team") or "", "") or ""),
            "opponent": str(raw.get(col("Opponent") or "", "") or ""),
            "matchup": str(raw.get(col("Matchup") or "", "") or ""),
            "start_time": str(raw.get(col("Start Time", "Game Time") or "", "") or ""),
            "market": "Maps 1-2 Kills",
            "line": float(line),
            "match_url": str(raw.get(col("Match URL", "HLTV URL") or "", "") or ""),
            "evidence": "manual real-line import",
        })
    return rows

# ============================================================
# FREE HLTV PUBLIC-DATA ADAPTER
# ============================================================

@dataclass
class PlayerStats:
    player: str
    player_id: str = ""
    slug: str = ""
    team: str = ""
    maps: int = 0
    rounds: int = 0
    kills: int = 0
    deaths: int = 0
    kpr: float = LEAGUE_KPR
    dpr: float = LEAGUE_DPR
    adr: float = LEAGUE_ADR
    rating: float = LEAGUE_RATING
    kd: float = 1.0
    hs_pct: Optional[float] = None
    opening_kpr: Optional[float] = None
    opening_deaths_pr: Optional[float] = None
    opening_ratio: Optional[float] = None
    kast_pct: Optional[float] = None
    impact: Optional[float] = None
    rounds_with_kill_pct: Optional[float] = None
    multi_kill_rate: Optional[float] = None
    rifle_kills: int = 0
    sniper_kills: int = 0
    pistol_kills: int = 0
    assists: int = 0
    flash_assists: int = 0
    trade_kills: int = 0
    traded_deaths: int = 0
    clutches_won: int = 0
    trade_kill_rate: Optional[float] = None
    traded_death_rate: Optional[float] = None
    awp_kill_share: Optional[float] = None
    ct_kpr: Optional[float] = None
    t_kpr: Optional[float] = None
    source: str = ""
    href: str = ""
    data_warnings: List[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        if out["data_warnings"] is None:
            out["data_warnings"] = []
        return out


def _period_dates(days: int) -> Tuple[str, str]:
    end = local_now().date()
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()


def _extract_hltv_player_rows(page: str) -> Dict[str, Dict[str, Any]]:
    rows: Dict[str, Dict[str, Any]] = {}
    if not page:
        return rows
    for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", page, flags=re.I | re.S):
        anchor = re.search(r'href=["\'](/stats/players/(\d+)/([^"\'?]+)[^"\']*)["\']', tr, flags=re.I)
        if not anchor:
            continue
        href, pid, slug = anchor.group(1), anchor.group(2), anchor.group(3)
        name_match = re.search(r'class=["\'][^"\']*(?:statsPlayerName|playerCol)[^"\']*["\'][^>]*>(.*?)</', tr, flags=re.I | re.S)
        if name_match:
            name = strip_tags(name_match.group(1)).split("\n")[0]
        else:
            anchor_text = re.search(r'href=["\']' + re.escape(href) + r'["\'][^>]*>(.*?)</a>', tr, flags=re.I | re.S)
            name = strip_tags(anchor_text.group(1)).split("\n")[0] if anchor_text else slug.replace("-", " ")
        team_match = re.search(r'class=["\'][^"\']*teamCol[^"\']*["\'][^>]*>(.*?)</td>', tr, flags=re.I | re.S)
        team = strip_tags(team_match.group(1)).split("\n")[-1] if team_match else ""
        text = strip_tags(tr).replace("%", "")
        numbers = [safe_float(x) for x in re.findall(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?", text)]
        numbers = [x for x in numbers if x is not None]
        # Standard table ending: maps, rounds, K-D diff, K/D, rating.
        maps = rounds = 0
        kd_diff, kd, rating = 0.0, 1.0, 1.0
        if len(numbers) >= 5:
            maps = int(numbers[-5])
            rounds = int(numbers[-4])
            kd_diff = float(numbers[-3])
            kd = float(numbers[-2])
            rating = float(numbers[-1])
        rows[normalize_name(name)] = {
            "player": name, "player_id": pid, "slug": slug, "team": team,
            "href": href, "maps": maps, "rounds": rounds, "kd_diff": kd_diff,
            "kd": kd, "rating": rating,
        }
    return rows


@st.cache_data(ttl=60 * 60, show_spinner=False)
def fetch_hltv_player_table(days: int) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    start, end = _period_dates(days)
    url = f"{HLTV_BASE}/stats/players"
    params = {"startDate": start, "endDate": end, "minMapCount": 1}
    page, status = http_get_text(url, "HLTV player table", ttl=60 * 60, params=params, timeout=20)
    rows = _extract_hltv_player_rows(page or "")
    status.update({"rows": len(rows), "period_days": days})
    return rows, status


def fuzzy_lookup_player(name: str, table: Dict[str, Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], float]:
    target = normalize_name(name)
    if target in table:
        return table[target], 1.0
    best, best_score = None, 0.0
    for key, row in table.items():
        score = name_similarity(target, key)
        if score > best_score:
            best, best_score = row, score
    return (best, best_score) if best_score >= 0.78 else (None, best_score)


def _extract_labeled_metric(page: str, labels: Sequence[str]) -> Optional[float]:
    text = strip_tags(page)
    lines = text.splitlines()
    normalized_labels = [normalize_name(x) for x in labels]
    for idx, line in enumerate(lines):
        norm = normalize_name(line)
        if any(label == norm or label in norm for label in normalized_labels):
            same = re.findall(r"-?\d+(?:\.\d+)?", line.replace(",", ""))
            if same:
                return safe_float(same[-1], None)
            for nxt in lines[idx + 1: idx + 4]:
                vals = re.findall(r"-?\d+(?:\.\d+)?", nxt.replace(",", ""))
                if vals:
                    return safe_float(vals[0], None)
    # Raw HTML adjacency fallback.
    for label in labels:
        pattern = re.escape(label) + r".{0,220}?(-?\d+(?:\.\d+)?)\s*%?"
        match = re.search(pattern, strip_tags(page).replace("\n", " | "), re.I)
        if match:
            return safe_float(match.group(1), None)
    return None



def _extract_labeled_metrics_all(page: str, label: str, limit: int = 12) -> List[float]:
    """Return numeric values appearing immediately after repeated metric labels.

    HLTV's current player overview renders Both/CT/T values with the same label,
    so retaining repeated occurrences lets the model recover side splits without
    assuming a fixed HTML table shape.
    """
    text = strip_tags(page)
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    target = normalize_name(label)
    values: List[float] = []
    for idx, line in enumerate(lines):
        if target not in normalize_name(line):
            continue
        # Same-line values first.
        same = re.findall(r"-?\d+(?:\.\d+)?", line.replace(",", ""))
        if same:
            val = safe_float(same[-1], None)
            if val is not None:
                values.append(float(val))
                if len(values) >= limit:
                    return values
                continue
        for nxt in lines[idx + 1: idx + 4]:
            nums = re.findall(r"-?\d+(?:\.\d+)?", nxt.replace(",", ""))
            if nums:
                val = safe_float(nums[0], None)
                if val is not None:
                    values.append(float(val))
                break
        if len(values) >= limit:
            break
    return values


def _normalize_pct(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return float(value * 100.0 if 0 <= value <= 1 else value)


def _extract_round_and_weapon_stats(page: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    labels = {
        "opening_kills": ["Total opening kills"],
        "opening_deaths": ["Total opening deaths"],
        "opening_ratio": ["Opening kill ratio"],
        "rounds_with_kill_pct": ["Rounds with a kill", "Rounds with kills"],
        "kast_pct": ["KAST", "KAST %"],
        "impact": ["Impact rating", "Impact"],
        "rifle_kills": ["Rifle kills"],
        "sniper_kills": ["Sniper kills"],
        "pistol_kills": ["Pistol kills"],
        "assists": ["Total assists", "Assists"],
        "flash_assists": ["Flash assists"],
        "trade_kills": ["Trade kills", "Traded kills"],
        "traded_deaths": ["Traded deaths", "Deaths traded"],
        "clutches_won": ["Clutches won", "Clutch rounds won"],
        "zero_kill_rounds": ["0 kill rounds"],
        "one_kill_rounds": ["1 kill rounds"],
        "two_kill_rounds": ["2 kill rounds"],
        "three_kill_rounds": ["3 kill rounds"],
        "four_kill_rounds": ["4 kill rounds"],
        "five_kill_rounds": ["5 kill rounds"],
    }
    for key, candidates in labels.items():
        out[key] = next((_extract_labeled_metric(page, [label]) for label in candidates if _extract_labeled_metric(page, [label]) is not None), None)
    for key in ["opening_kills", "opening_deaths", "rifle_kills", "sniper_kills", "pistol_kills",
                "assists", "flash_assists", "trade_kills", "traded_deaths", "clutches_won",
                "zero_kill_rounds", "one_kill_rounds", "two_kill_rounds", "three_kill_rounds",
                "four_kill_rounds", "five_kill_rounds"]:
        out[key] = safe_int(out.get(key), 0) or 0
    out["rounds_with_kill_pct"] = _normalize_pct(safe_float(out.get("rounds_with_kill_pct"), None))
    out["kast_pct"] = _normalize_pct(safe_float(out.get("kast_pct"), None))
    total_weapon = sum(int(out.get(k, 0) or 0) for k in ["rifle_kills", "sniper_kills", "pistol_kills"])
    out["awp_kill_share"] = (out["sniper_kills"] / total_weapon) if total_weapon > 0 else None
    total_rounds = sum(int(out.get(k, 0) or 0) for k in ["zero_kill_rounds", "one_kill_rounds", "two_kill_rounds", "three_kill_rounds", "four_kill_rounds", "five_kill_rounds"])
    multi_rounds = sum(int(out.get(k, 0) or 0) for k in ["two_kill_rounds", "three_kill_rounds", "four_kill_rounds", "five_kill_rounds"])
    out["multi_kill_rate"] = (multi_rounds / total_rounds) if total_rounds > 0 else None
    out["trade_kill_rate"] = (out["trade_kills"] / max(total_rounds, 1)) if out["trade_kills"] > 0 else None
    out["traded_death_rate"] = (out["traded_deaths"] / max(total_rounds, 1)) if out["traded_deaths"] > 0 else None
    return out


@st.cache_data(ttl=60 * 60 * 4, show_spinner=False)
def fetch_hltv_player_overview_profile(player_id: str, slug: str, days: int = 180, map_name: str = "") -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not player_id:
        return {}, {"ok": False, "warning": "no player id"}
    start, end = _period_dates(days)
    params: Dict[str, Any] = {"startDate": start, "endDate": end, "csVersion": "CS2"}
    if map_name and map_name in HLTV_MAP_KEYS:
        params["maps"] = HLTV_MAP_KEYS[map_name]
    url = f"{HLTV_BASE}/stats/players/{player_id}/{slug}"
    page, status = http_get_text(url, "HLTV player overview", ttl=60 * 60 * 4, params=params, timeout=20)
    if not page:
        return {}, status
    kpr_values = _extract_labeled_metrics_all(page, "Kills per round", limit=6)
    advanced = _extract_round_and_weapon_stats(page)
    advanced.update({
        "kpr": kpr_values[0] if kpr_values else None,
        "ct_kpr": kpr_values[1] if len(kpr_values) >= 3 else None,
        "t_kpr": kpr_values[2] if len(kpr_values) >= 3 else None,
        "rating": _extract_labeled_metric(page, ["Rating 3.0", "Rating 2.1", "Rating 2.0", "Rating"]),
        "adr": _extract_labeled_metric(page, ["Damage per round", "Average damage per round"]),
        "maps": _extract_labeled_metric(page, ["maps"]),
        "href": url,
        "map_name": map_name,
    })
    status.update({"metric_count": sum(v not in [None, 0, ""] for v in advanced.values()), "map_name": map_name})
    return advanced, status


@st.cache_data(ttl=60 * 60 * 4, show_spinner=False)
def fetch_hltv_filtered_player_profile(player_id: str, slug: str, days: int = 180, map_name: str = "", side: str = "") -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Pull a real filtered individual-stat page for one player/map/side."""
    if not player_id:
        return {}, {"ok": False, "warning": "no player id"}
    start, end = _period_dates(days)
    params: Dict[str, Any] = {"startDate": start, "endDate": end, "csVersion": "CS2"}
    if map_name and map_name in HLTV_MAP_KEYS:
        params["maps"] = HLTV_MAP_KEYS[map_name]
    if side:
        params["side"] = side
    url = f"{HLTV_BASE}/stats/players/individual/{player_id}/{slug}"
    page, status = http_get_text(url, "HLTV filtered individual", ttl=60 * 60 * 4, params=params, timeout=20)
    if not page:
        return {}, status
    profile = {
        "kills": safe_int(_extract_labeled_metric(page, ["Kills", "Total kills"]), 0) or 0,
        "deaths": safe_int(_extract_labeled_metric(page, ["Deaths", "Total deaths"]), 0) or 0,
        "rounds": safe_int(_extract_labeled_metric(page, ["Total rounds played", "Rounds played"]), 0) or 0,
        "maps": safe_int(_extract_labeled_metric(page, ["Maps played"]), 0) or 0,
        "kpr": _extract_labeled_metric(page, ["Kill / Round", "Kills / round", "Kills per round"]),
        "dpr": _extract_labeled_metric(page, ["Deaths / round", "Deaths per round"]),
        "adr": _extract_labeled_metric(page, ["Damage / round", "Average damage per round"]),
        "rating": _extract_labeled_metric(page, ["Rating 3.0", "Rating 2.1", "Rating 2.0", "Rating"]),
        "opening_kills": safe_int(_extract_labeled_metric(page, ["Total opening kills"]), 0) or 0,
        "opening_deaths": safe_int(_extract_labeled_metric(page, ["Total opening deaths"]), 0) or 0,
        "opening_ratio": _extract_labeled_metric(page, ["Opening kill ratio"]),
        "map_name": map_name,
        "side": side,
        "href": url,
    }
    if profile["kpr"] is None and profile["rounds"] > 0 and profile["kills"] > 0:
        profile["kpr"] = profile["kills"] / profile["rounds"]
    if profile["dpr"] is None and profile["rounds"] > 0 and profile["deaths"] > 0:
        profile["dpr"] = profile["deaths"] / profile["rounds"]
    status.update({"map_name": map_name, "side": side, "maps": profile["maps"], "rounds": profile["rounds"]})
    return profile, status


def build_player_map_profiles(profile: PlayerStats, likely_maps: Sequence[str]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    statuses: Dict[str, Any] = {}
    if not profile.player_id or not likely_maps:
        return out, {"ok": False, "message": "player id or likely maps unavailable"}
    for map_name in list(dict.fromkeys(likely_maps))[:3]:
        long_row, long_status = fetch_hltv_filtered_player_profile(profile.player_id, profile.slug, 180, map_name)
        recent_row, recent_status = fetch_hltv_filtered_player_profile(profile.player_id, profile.slug, 60, map_name)
        long_maps = safe_int(long_row.get("maps"), 0) or 0
        recent_maps = safe_int(recent_row.get("maps"), 0) or 0
        long_kpr = safe_float(long_row.get("kpr"), None)
        recent_kpr = safe_float(recent_row.get("kpr"), None)
        if long_kpr is None:
            statuses[map_name] = {"long": long_status, "recent": recent_status, "usable": False}
            continue
        sample_weight = clamp(long_maps / (long_maps + 12.0), 0.0, 0.88)
        recent_weight = clamp(recent_maps / (recent_maps + 8.0), 0.0, 0.45) if recent_kpr is not None else 0.0
        shrunk_long = profile.kpr * (1.0 - sample_weight) + long_kpr * sample_weight
        blended = shrunk_long * (1.0 - recent_weight) + (recent_kpr or shrunk_long) * recent_weight
        out[map_name] = {
            "map": map_name,
            "maps": long_maps,
            "rounds": safe_int(long_row.get("rounds"), 0) or 0,
            "kpr": round(float(long_kpr), 4),
            "recent_maps": recent_maps,
            "recent_kpr": round(float(recent_kpr), 4) if recent_kpr is not None else None,
            "blended_kpr": round(clamp(float(blended), 0.44, 0.98), 4),
            "dpr": safe_float(long_row.get("dpr"), None),
            "adr": safe_float(long_row.get("adr"), None),
            "rating": safe_float(long_row.get("rating"), None),
            "opening_kpr": (safe_float(long_row.get("opening_kills"), 0) or 0) / max(safe_float(long_row.get("rounds"), 0) or 0, 1),
            "source": long_row.get("href", ""),
        }
        statuses[map_name] = {"long": long_status, "recent": recent_status, "usable": True}
    # Persist the latest real map profile for inspection and fallback learning.
    saved = load_json(DEEP_PLAYER_MAP_FILE, {})
    if not isinstance(saved, dict):
        saved = {}
    if out:
        saved[normalize_name(profile.player)] = {"updated_at": now_iso(), "maps": out}
        save_json(DEEP_PLAYER_MAP_FILE, saved)
    return out, {"ok": bool(out), "maps": list(out), "statuses": statuses}


@st.cache_data(ttl=60 * 60 * 4, show_spinner=False)
def fetch_hltv_individual_profile(player_id: str, slug: str, days: int = 180) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not player_id:
        return {}, {"ok": False, "warning": "no player id"}
    start, end = _period_dates(days)
    url = f"{HLTV_BASE}/stats/players/individual/{player_id}/{slug}"
    page, status = http_get_text(url, "HLTV individual", ttl=60 * 60 * 4, params={"startDate": start, "endDate": end}, timeout=20)
    if not page:
        return {}, status
    profile = {
        "kills": safe_int(_extract_labeled_metric(page, ["Total kills"]), 0),
        "deaths": safe_int(_extract_labeled_metric(page, ["Total deaths"]), 0),
        "rounds": safe_int(_extract_labeled_metric(page, ["Total rounds played", "Rounds played"]), 0),
        "maps": safe_int(_extract_labeled_metric(page, ["Maps played"]), 0),
        "kpr": _extract_labeled_metric(page, ["Kills / round", "Kills per round"]),
        "dpr": _extract_labeled_metric(page, ["Deaths / round", "Deaths per round"]),
        "adr": _extract_labeled_metric(page, ["Damage / round", "Average damage per round"]),
        "kd": _extract_labeled_metric(page, ["K/D ratio", "Kill / death ratio"]),
        "rating": _extract_labeled_metric(page, ["Rating 2.1", "Rating 2.0", "Rating"]),
        "hs_pct": _extract_labeled_metric(page, ["Headshot %", "Headshots"]),
        "opening_kpr": _extract_labeled_metric(page, ["Opening kills / round", "Opening kills per round"]),
        "href": url,
    }
    # Some values are percentages represented without a percent sign in parsed text.
    if profile["hs_pct"] is not None and profile["hs_pct"] <= 1:
        profile["hs_pct"] *= 100
    status.update({"metrics": sum(v not in [None, 0, ""] for v in profile.values())})
    return profile, status


def build_player_profile(
    player_name: str,
    long_table: Dict[str, Dict[str, Any]],
    medium_table: Dict[str, Dict[str, Any]],
    recent_table: Dict[str, Dict[str, Any]],
) -> Tuple[PlayerStats, Dict[str, Any]]:
    long_row, match_score = fuzzy_lookup_player(player_name, long_table)
    medium_row, medium_score = fuzzy_lookup_player(player_name, medium_table)
    recent_row, recent_score = fuzzy_lookup_player(player_name, recent_table)
    overrides = load_json(PLAYER_OVERRIDES_FILE, {})
    override = overrides.get(normalize_name(player_name), {}) if isinstance(overrides, dict) else {}
    warnings: List[str] = []

    if not long_row and override:
        long_row = {"player": player_name, "player_id": "", "slug": "", "team": override.get("team", ""),
                    "maps": safe_int(override.get("maps"), 0), "rounds": safe_int(override.get("rounds"), 0),
                    "kd": safe_float(override.get("kd"), 1.0), "rating": safe_float(override.get("rating"), 1.0)}
        match_score = 1.0
        warnings.append("Manual profile override used")

    if not long_row:
        return PlayerStats(player=player_name, source="NO PROFILE", data_warnings=["Player not found in free statistics source"]), {
            "matched": False, "match_score": match_score, "recent_score": recent_score,
        }

    individual, individual_status = fetch_hltv_individual_profile(str(long_row.get("player_id", "")), str(long_row.get("slug", "")), 180)
    overview, overview_status = fetch_hltv_player_overview_profile(str(long_row.get("player_id", "")), str(long_row.get("slug", "")), 180)
    maps = safe_int(individual.get("maps"), safe_int(long_row.get("maps"), 0)) or 0
    rounds = safe_int(individual.get("rounds"), safe_int(long_row.get("rounds"), 0)) or 0
    kills = safe_int(individual.get("kills"), 0) or 0
    deaths = safe_int(individual.get("deaths"), 0) or 0
    kd = safe_float(individual.get("kd"), safe_float(long_row.get("kd"), 1.0)) or 1.0
    rating = safe_float(individual.get("rating"), safe_float(long_row.get("rating"), 1.0)) or 1.0
    kpr = safe_float(individual.get("kpr"), None)
    dpr = safe_float(individual.get("dpr"), None)
    adr = safe_float(individual.get("adr"), None)

    if kpr is None and rounds > 0 and kills > 0:
        kpr = kills / rounds
    if dpr is None and rounds > 0 and deaths > 0:
        dpr = deaths / rounds
    if kpr is None:
        # Data-driven fallback from the real player's K/D and rating.
        kpr = LEAGUE_KPR * clamp(0.50 * kd + 0.50 * rating, 0.78, 1.28)
        warnings.append("KPR estimated from player K/D and rating")
    if dpr is None:
        dpr = clamp(kpr / max(kd, 0.65), 0.52, 0.86)
        warnings.append("DPR estimated")
    if adr is None:
        adr = LEAGUE_ADR * clamp(0.55 * rating + 0.45 * (kpr / LEAGUE_KPR), 0.78, 1.30)
        warnings.append("ADR estimated")

    # Apply current-form signal from free aggregated tables, capped tightly.
    long_rating = safe_float(long_row.get("rating"), rating) or rating
    medium_rating = safe_float((medium_row or {}).get("rating"), long_rating) or long_rating
    recent_rating = safe_float((recent_row or {}).get("rating"), medium_rating) or medium_rating
    medium_maps = safe_int((medium_row or {}).get("maps"), 0) or 0
    recent_maps = safe_int((recent_row or {}).get("maps"), 0) or 0
    medium_weight = clamp(medium_maps / 30.0, 0.0, 1.0)
    recent_weight = clamp(recent_maps / 18.0, 0.0, 1.0)
    form_rating = (
        0.58 * long_rating +
        0.27 * (medium_weight * medium_rating + (1 - medium_weight) * long_rating) +
        0.15 * (recent_weight * recent_rating + (1 - recent_weight) * medium_rating)
    )
    form_factor = clamp(form_rating / max(long_rating, 0.75), 0.92, 1.08)
    kpr = clamp(kpr * form_factor, 0.47, 0.92)

    # Manual profile fields override only the supplied metric, never the line.
    if override:
        kpr = safe_float(override.get("kpr"), kpr) or kpr
        dpr = safe_float(override.get("dpr"), dpr) or dpr
        adr = safe_float(override.get("adr"), adr) or adr
        rating = safe_float(override.get("rating"), rating) or rating
        maps = safe_int(override.get("maps"), maps) or maps
        rounds = safe_int(override.get("rounds"), rounds) or rounds

    profile = PlayerStats(
        player=player_name,
        player_id=str(long_row.get("player_id", "")),
        slug=str(long_row.get("slug", "")),
        team=str(override.get("team") or long_row.get("team") or ""),
        maps=maps,
        rounds=rounds,
        kills=kills,
        deaths=deaths,
        kpr=float(kpr),
        dpr=float(dpr),
        adr=float(adr),
        rating=float(rating),
        kd=float(kd),
        hs_pct=safe_float(override.get("hs_pct"), safe_float(individual.get("hs_pct"), None)),
        opening_kpr=safe_float(override.get("opening_kpr"), safe_float(individual.get("opening_kpr"), None)),
        opening_deaths_pr=(safe_float(overview.get("opening_deaths"), 0) or 0) / max(rounds, 1),
        opening_ratio=safe_float(overview.get("opening_ratio"), None),
        kast_pct=safe_float(overview.get("kast_pct"), None),
        impact=safe_float(overview.get("impact"), None),
        rounds_with_kill_pct=safe_float(overview.get("rounds_with_kill_pct"), None),
        multi_kill_rate=safe_float(overview.get("multi_kill_rate"), None),
        rifle_kills=safe_int(overview.get("rifle_kills"), 0) or 0,
        sniper_kills=safe_int(overview.get("sniper_kills"), 0) or 0,
        pistol_kills=safe_int(overview.get("pistol_kills"), 0) or 0,
        assists=safe_int(overview.get("assists"), 0) or 0,
        flash_assists=safe_int(overview.get("flash_assists"), 0) or 0,
        trade_kills=safe_int(overview.get("trade_kills"), 0) or 0,
        traded_deaths=safe_int(overview.get("traded_deaths"), 0) or 0,
        clutches_won=safe_int(overview.get("clutches_won"), 0) or 0,
        trade_kill_rate=safe_float(overview.get("trade_kill_rate"), None),
        traded_death_rate=safe_float(overview.get("traded_death_rate"), None),
        awp_kill_share=safe_float(overview.get("awp_kill_share"), None),
        ct_kpr=safe_float(overview.get("ct_kpr"), None),
        t_kpr=safe_float(overview.get("t_kpr"), None),
        source="HLTV public stats + advanced profile" if (individual_status.get("ok") or overview_status.get("ok")) else "HLTV aggregate + estimated KPR",
        href=str(individual.get("href") or long_row.get("href") or ""),
        data_warnings=warnings,
    )
    return profile, {
        "matched": True,
        "match_score": round(match_score, 3),
        "medium_score": round(medium_score, 3),
        "recent_score": round(recent_score, 3),
        "long_row": long_row,
        "medium_row": medium_row,
        "recent_row": recent_row,
        "individual_status": individual_status,
        "overview_status": overview_status,
        "overview": overview,
        "form_factor": round(form_factor, 4),
    }

# ============================================================
# MATCH DISCOVERY / MAP POOL / FORMAT / ROSTER
# ============================================================

@st.cache_data(ttl=10 * 60, show_spinner=False)
def fetch_hltv_matches_page() -> Tuple[str, Dict[str, Any]]:
    page, status = http_get_text(f"{HLTV_BASE}/matches", "HLTV matches", ttl=10 * 60, timeout=20)
    return page or "", status


def discover_hltv_match(team: str, opponent: str, player: str = "") -> Tuple[str, Dict[str, Any]]:
    page, status = fetch_hltv_matches_page()
    if not page:
        return "", status
    aliases = load_json(MATCH_ALIAS_FILE, {})
    alias_key = "|".join(sorted([normalize_team(team), normalize_team(opponent)]))
    if isinstance(aliases, dict) and aliases.get(alias_key):
        return str(aliases[alias_key]), {**status, "method": "saved alias"}
    links = list(re.finditer(r'href=["\'](/matches/(\d+)/[^"\']+)["\']', page, flags=re.I))
    target_team = normalize_team(team)
    target_opp = normalize_team(opponent)
    best_url, best_score, best_evidence = "", 0.0, ""
    for match in links:
        left = max(0, match.start() - 1400)
        right = min(len(page), match.end() + 2400)
        block = strip_tags(page[left:right])
        norm_block = normalize_name(block)
        score = 0.0
        if target_team:
            score += max(name_similarity(target_team, token) for token in [norm_block] + norm_block.split(" vs "))
            if target_team in norm_block:
                score += 1.0
        if target_opp:
            score += max(name_similarity(target_opp, token) for token in [norm_block] + norm_block.split(" vs "))
            if target_opp in norm_block:
                score += 1.0
        if player and normalize_name(player) in norm_block:
            score += 0.25
        if score > best_score:
            best_score = score
            best_url = urljoin(HLTV_BASE, match.group(1))
            best_evidence = block[:700]
    threshold = 2.45 if target_team and target_opp else 1.55
    if best_score < threshold:
        return "", {**status, "method": "not matched", "best_score": round(best_score, 3)}
    return best_url, {**status, "method": "public match page", "best_score": round(best_score, 3), "evidence": best_evidence}


def _extract_team_links(page: str) -> List[Dict[str, str]]:
    found: List[Dict[str, str]] = []
    seen = set()
    for match in re.finditer(r'href=["\'](/team/(\d+)/([^"\'?]+))[^"\']*["\'][^>]*>(.*?)</a>', page, flags=re.I | re.S):
        href, tid, slug, content = match.groups()
        name = strip_tags(content).replace("\n", " ").strip() or slug.replace("-", " ")
        key = tid
        if key not in seen:
            seen.add(key)
            found.append({"team_id": tid, "slug": slug, "name": name, "href": urljoin(HLTV_BASE, href)})
    return found[:4]


def _extract_confirmed_maps(page: str) -> List[str]:
    text = strip_tags(page)
    maps: List[str] = []
    for map_name in KNOWN_MAPS:
        if re.search(rf"\b{re.escape(map_name)}\b", text, re.I):
            # Prefer picks/veto/map score area, but keep only unique known maps.
            maps.append(map_name)
    # Veto pages can mention all maps. Try map holder elements first.
    holder_maps = []
    for pattern in [
        r'class=["\'][^"\']*(?:mapname|map-name)[^"\']*["\'][^>]*>\s*([^<]+)',
        r'data-map-name=["\']([^"\']+)',
    ]:
        for value in re.findall(pattern, page, flags=re.I | re.S):
            clean = strip_tags(value).strip().title()
            if clean in KNOWN_MAPS and clean not in holder_maps:
                holder_maps.append(clean)
    return holder_maps[:3] if holder_maps else maps[:3]


def _extract_world_ranks(page: str) -> List[int]:
    text = strip_tags(page)
    values = [safe_int(x) for x in re.findall(r"(?:World ranking|World rank|Ranking)\s*#?\s*(\d{1,3})", text, flags=re.I)]
    return [x for x in values if x is not None][:2]


def _extract_format(page: str) -> str:
    text = strip_tags(page)
    match = re.search(r"Best\s+of\s+(\d)", text, flags=re.I)
    if match:
        return f"BO{match.group(1)}"
    if re.search(r"\bBO3\b", text, re.I):
        return "BO3"
    if re.search(r"\bBO1\b", text, re.I):
        return "BO1"
    return "UNKNOWN"


def _extract_lineup_names(page: str) -> List[str]:
    names: List[str] = []
    for pattern in [
        r'href=["\']/player/\d+/[^"\']+["\'][^>]*>(.*?)</a>',
        r'class=["\'][^"\']*text-ellipsis[^"\']*["\'][^>]*>([^<]+)',
    ]:
        for content in re.findall(pattern, page, flags=re.I | re.S):
            clean = strip_tags(content).replace("\n", " ").strip()
            if clean and len(clean) <= 40 and normalize_name(clean) not in [normalize_name(x) for x in names]:
                names.append(clean)
    return names[:16]



def _extract_lineup_groups(page: str) -> List[Dict[str, Any]]:
    groups: List[Dict[str, Any]] = []
    starts = list(re.finditer(r'class=["\'][^"\']*\blineup\b[^"\']*["\']', page, flags=re.I))
    for idx, start in enumerate(starts):
        segment = page[start.start(): starts[idx + 1].start() if idx + 1 < len(starts) else min(len(page), start.start() + 9000)]
        players = []
        for raw in re.findall(r'href=["\']/player/\d+/[^"\']+["\'][^>]*>(.*?)</a>', segment, flags=re.I | re.S):
            name = strip_tags(raw).replace("\n", " ").strip()
            if name and normalize_name(name) not in {normalize_name(x) for x in players}:
                players.append(name)
        team_match = re.search(r'href=["\']/team/(\d+)/([^"\']+)["\'][^>]*>(.*?)</a>', segment, flags=re.I | re.S)
        team = strip_tags(team_match.group(3)).replace("\n", " ").strip() if team_match else ""
        team_id = team_match.group(1) if team_match else ""
        if len(players) >= 3:
            key = tuple(sorted(normalize_name(x) for x in players[:7]))
            if not any(tuple(sorted(normalize_name(x) for x in g.get("players", [])[:7])) == key for g in groups):
                groups.append({"team": team, "team_id": team_id, "players": players[:7]})
    return groups[:4]


def _extract_veto_actions(page: str) -> List[Dict[str, Any]]:
    text = strip_tags(page)
    actions: List[Dict[str, Any]] = []
    pattern = re.compile(
        r"(?:^|\n|\s)(\d{1,2})?\.?\s*([^\n]{1,55}?)\s+(removed|picked)\s+"
        r"(Ancient|Anubis|Dust2|Inferno|Mirage|Nuke|Overpass|Train|Vertigo|Cache|Cobblestone)\b",
        flags=re.I,
    )
    for match in pattern.finditer(text):
        team = re.sub(r"^\d+\.?\s*", "", match.group(2)).strip(" -*:|\t")
        if len(team) > 45 or not team:
            continue
        map_name = next((m for m in KNOWN_MAPS if normalize_name(m) == normalize_name(match.group(4))), match.group(4).title())
        action = match.group(3).lower()
        item = {"order": safe_int(match.group(1), len(actions) + 1) or len(actions) + 1, "team": team, "action": action, "map": map_name}
        if not any(x["team"] == item["team"] and x["action"] == item["action"] and x["map"] == item["map"] for x in actions):
            actions.append(item)
    left_pattern = re.compile(r"(Ancient|Anubis|Dust2|Inferno|Mirage|Nuke|Overpass|Train|Vertigo|Cache|Cobblestone)\s+was\s+left\s+over", re.I)
    for match in left_pattern.finditer(text):
        map_name = next((m for m in KNOWN_MAPS if normalize_name(m) == normalize_name(match.group(1))), match.group(1).title())
        actions.append({"order": len(actions) + 1, "team": "", "action": "decider", "map": map_name})
    actions.sort(key=lambda x: x.get("order", 99))
    return actions[:12]


def _extract_match_environment(page: str) -> str:
    text = strip_tags(page)
    match = re.search(r"Best\s+of\s+\d\s*\((LAN|Online)\)", text, flags=re.I)
    if match:
        return match.group(1).upper()
    if re.search(r"\bLAN\b", text, flags=re.I):
        return "LAN"
    if re.search(r"\bOnline\b", text, flags=re.I):
        return "ONLINE"
    return "UNKNOWN"


def _extract_match_stage(page: str) -> str:
    text = strip_tags(page).replace("\r", "")
    match = re.search(r"Best\s+of\s+\d(?:\s*\([^)]*\))?\s*\*\s*([^\n]{1,180})", text, flags=re.I)
    if match:
        return match.group(1).strip(" .*|-_")[:180]
    for token in ["grand final", "semi-final", "quarter-final", "elimination match", "lower bracket", "upper bracket", "swiss round", "group stage"]:
        if token in text.lower():
            return token.title()
    return ""


def _extract_match_datetime(page: str) -> str:
    values = []
    for raw in re.findall(r'data-unix=["\'](\d{10,13})["\']', page, flags=re.I):
        try:
            stamp = int(raw)
            if stamp > 10**12:
                stamp /= 1000
            dt = datetime.fromtimestamp(stamp, tz=timezone.utc)
            if 2015 <= dt.year <= 2035:
                values.append(dt)
        except Exception:
            pass
    if values:
        # Match timestamp is normally the earliest page timestamp before comments.
        return min(values).isoformat()
    text = strip_tags(page)
    month_names = "January|February|March|April|May|June|July|August|September|October|November|December"
    match = re.search(rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+of\s+({month_names})\s+(20\d{{2}})", text, flags=re.I)
    if match:
        try:
            return datetime.strptime(f"{match.group(1)} {match.group(2)} {match.group(3)}", "%d %B %Y").replace(tzinfo=timezone.utc).isoformat()
        except Exception:
            pass
    return ""


def _extract_event_link(page: str) -> str:
    match = re.search(r'href=["\'](/events/\d+/[^"\']+)["\']', page, flags=re.I)
    return urljoin(HLTV_BASE, match.group(1)) if match else ""


def _extract_map_results(page: str) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    starts = list(re.finditer(r'class=["\'][^"\']*\bmapholder\b[^"\']*["\']', page, flags=re.I))
    for idx, start in enumerate(starts):
        segment = page[start.start(): starts[idx + 1].start() if idx + 1 < len(starts) else min(len(page), start.start() + 9000)]
        map_match = re.search(r'class=["\'][^"\']*(?:mapname|map-name)[^"\']*["\'][^>]*>(.*?)</', segment, flags=re.I | re.S)
        map_name = strip_tags(map_match.group(1)).replace("\n", " ").strip().title() if map_match else ""
        map_name = next((m for m in KNOWN_MAPS if normalize_name(m) == normalize_name(map_name)), map_name)
        team_names = [strip_tags(x).replace("\n", " ").strip() for x in re.findall(r'class=["\'][^"\']*results-teamname[^"\']*["\'][^>]*>(.*?)</', segment, flags=re.I | re.S)]
        score_raw = re.findall(r'class=["\'][^"\']*results-team-score[^"\']*["\'][^>]*>\s*(\d{1,2})\s*<', segment, flags=re.I | re.S)
        scores = [safe_int(x, None) for x in score_raw]
        scores = [x for x in scores if x is not None]
        if map_name in KNOWN_MAPS and len(team_names) >= 2 and len(scores) >= 2:
            rounds = int(scores[0] + scores[1])
            results.append({
                "map": map_name, "team1": team_names[0], "team2": team_names[1],
                "score1": int(scores[0]), "score2": int(scores[1]), "rounds": rounds,
                "margin": abs(int(scores[0]) - int(scores[1])), "overtime": max(scores[:2]) > 13 or rounds >= 26,
            })
    return results[:5]


def _classify_event_tier(event: str, stage: str, ranks: Sequence[int]) -> Tuple[str, float]:
    text = f"{event} {stage}".lower()
    if any(x in text for x in ["major", "iem cologne", "iem katowice", "blast premier", "esl pro league"]):
        return "S-TIER", 1.0
    if any(x in text for x in ["challenger", "cct", "masters", "pro league", "world cup"]):
        return "A/B-TIER", 0.88
    if len(ranks) >= 2 and max(ranks[:2]) <= 25:
        return "TOP-25", 0.90
    if len(ranks) >= 2 and max(ranks[:2]) <= 60:
        return "TIER-2", 0.76
    return "LOW/UNKNOWN", 0.58


@st.cache_data(ttl=60 * 60, show_spinner=False)
def fetch_event_context(event_url: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not event_url:
        return {}, {"ok": False, "warning": "no event URL"}
    page, status = http_get_text(event_url, "HLTV event", ttl=60 * 60, timeout=20)
    if not page:
        return {}, status
    text = strip_tags(page)
    location = ""
    loc = re.search(r'(?:Location|Venue)\s*([^\n]{1,80})', text, flags=re.I)
    if loc:
        location = loc.group(1).strip()
    return {
        "event_url": event_url,
        "location": location,
        "lan": bool(re.search(r"\bLAN\b", text, flags=re.I)),
        "online": bool(re.search(r"\bOnline\b", text, flags=re.I)),
        "text_sample": text[:1200],
    }, status


@st.cache_data(ttl=10 * 60, show_spinner=False)
def fetch_match_context(match_url: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not match_url:
        return {}, {"ok": False, "warning": "no match URL"}
    page, status = http_get_text(match_url, "HLTV match", ttl=10 * 60, timeout=20)
    if not page:
        return {}, status
    text = strip_tags(page)
    teams = _extract_team_links(page)
    ranks = _extract_world_ranks(page)
    environment = _extract_match_environment(page)
    stage = _extract_match_stage(page)
    event_url = _extract_event_link(page)
    event_context, event_status = fetch_event_context(event_url) if event_url else ({}, {"ok": False, "warning": "no event URL"})
    context = {
        "match_url": match_url,
        "format": _extract_format(page),
        "confirmed_maps": _extract_confirmed_maps(page),
        "veto_actions": _extract_veto_actions(page),
        "map_results": _extract_map_results(page),
        "world_ranks": ranks,
        "teams": teams,
        "lineup_names": _extract_lineup_names(page),
        "lineup_groups": _extract_lineup_groups(page),
        "standin_warning": bool(re.search(r"\bstand-?in\b|replacement|substitute|ineligible starting roster", text, flags=re.I)),
        "postponed": bool(re.search(r"postponed|cancelled|canceled|invalidated", text, flags=re.I)),
        "environment": environment,
        "stage": stage,
        "match_datetime": _extract_match_datetime(page),
        "event": "",
        "event_url": event_url,
        "event_context": event_context,
        "event_status": event_status,
        "page_text_sample": text[:2400],
    }
    event_match = re.search(r'class=["\'][^"\']*(?:event|event-name)[^"\']*["\'][^>]*>(.*?)</', page, flags=re.I | re.S)
    if event_match:
        context["event"] = strip_tags(event_match.group(1)).replace("\n", " ").strip()
    context["event_tier"], context["event_tier_confidence"] = _classify_event_tier(
        context.get("event", ""), context.get("stage", ""), context.get("world_ranks", [])
    )
    return context, {**status, "event_status": event_status}


@st.cache_data(ttl=60 * 60 * 4, show_spinner=False)
def fetch_team_map_pool(team_id: str, slug: str, days: int = 90) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    if not team_id:
        return {}, {"ok": False, "warning": "no team id"}
    start, end = _period_dates(days)
    url = f"{HLTV_BASE}/stats/teams/maps/{team_id}/{slug}"
    page, status = http_get_text(url, "HLTV team maps", ttl=60 * 60 * 4, params={"startDate": start, "endDate": end}, timeout=20)
    if not page:
        return {}, status
    pool: Dict[str, Dict[str, Any]] = {}
    for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", page, flags=re.I | re.S):
        text = strip_tags(tr).replace("%", "")
        map_name = next((m for m in KNOWN_MAPS if re.search(rf"\b{re.escape(m)}\b", text, re.I)), None)
        if not map_name:
            continue
        percentages = [safe_float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*%", strip_tags(tr))]
        percentages = [float(x) for x in percentages if x is not None and 0 <= x <= 100]
        numbers = [safe_float(x) for x in re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", text)]
        numbers = [x for x in numbers if x is not None]
        maps_played = int(numbers[0]) if numbers and numbers[0] <= 250 else 0
        win_pct = percentages[-1] if percentages else None
        round_win_pct = percentages[0] if len(percentages) >= 2 else None
        pistol_win_pct = percentages[1] if len(percentages) >= 3 else None
        if win_pct is None:
            candidates = [x for x in numbers if 0 <= x <= 100]
            win_pct = candidates[-1] if candidates else 50.0
        pool[map_name] = {
            "maps": maps_played,
            "win_pct": float(win_pct),
            "round_win_pct": float(round_win_pct) if round_win_pct is not None else None,
            "pistol_win_pct": float(pistol_win_pct) if pistol_win_pct is not None else None,
            "all_percentages": percentages,
            "source": url,
        }
    status.update({"maps_found": len(pool)})
    return pool, status



@st.cache_data(ttl=60 * 60 * 4, show_spinner=False)
def fetch_team_roster(team_id: str, slug: str) -> Tuple[List[str], Dict[str, Any]]:
    if not team_id:
        return [], {"ok": False, "warning": "no team id"}
    url = f"{HLTV_BASE}/team/{team_id}/{slug}"
    page, status = http_get_text(url, "HLTV team roster", ttl=60 * 60 * 4, timeout=20)
    if not page:
        return [], status
    players: List[str] = []
    for raw in re.findall(r'href=["\']/player/\d+/[^"\']+["\'][^>]*>(.*?)</a>', page, flags=re.I | re.S):
        name = strip_tags(raw).replace("\n", " ").strip()
        if name and len(name) <= 40 and normalize_name(name) not in {normalize_name(x) for x in players}:
            players.append(name)
    status.update({"players": len(players[:7]), "url": url})
    return players[:7], status


@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def fetch_team_recent_match_links(team_id: str, slug: str, days: int = 120, limit: int = DEEP_PULL_MATCH_LIMIT) -> Tuple[List[str], Dict[str, Any]]:
    if not team_id:
        return [], {"ok": False, "warning": "no team id"}
    start, end = _period_dates(days)
    candidates = [
        f"{HLTV_BASE}/stats/teams/matches/{team_id}/{slug}",
        f"{HLTV_BASE}/results?team={team_id}",
    ]
    links: List[str] = []
    statuses: List[Dict[str, Any]] = []
    for url in candidates:
        page, status = http_get_text(url, "HLTV team recent matches", ttl=60 * 60 * 6,
                                     params={"startDate": start, "endDate": end, "csVersion": "CS2"} if "/stats/" in url else None,
                                     timeout=20)
        statuses.append(status)
        if not page:
            continue
        for href in re.findall(r'href=["\'](/matches/\d+/[^"\']+)["\']', page, flags=re.I):
            full = urljoin(HLTV_BASE, href)
            if full not in links:
                links.append(full)
            if len(links) >= limit:
                break
        if links:
            break
    return links[:limit], {"ok": bool(links), "rows": len(links), "statuses": statuses}


@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def fetch_recent_team_match_summaries(team_id: str, slug: str, limit: int = DEEP_PULL_MATCH_LIMIT) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    links, link_status = fetch_team_recent_match_links(team_id, slug, 120, limit)
    summaries: List[Dict[str, Any]] = []
    statuses: List[Dict[str, Any]] = []
    for match_url in links[:limit]:
        page, status = http_get_text(match_url, "HLTV recent team match", ttl=60 * 60 * 6, timeout=20)
        statuses.append(status)
        if not page:
            continue
        teams = _extract_team_links(page)
        summaries.append({
            "match_url": match_url,
            "teams": teams[:2],
            "lineup_names": _extract_lineup_names(page),
            "lineup_groups": _extract_lineup_groups(page),
            "veto_actions": _extract_veto_actions(page),
            "map_results": _extract_map_results(page),
            "mapstats_links": _mapstats_links(page),
            "match_datetime": _extract_match_datetime(page),
            "environment": _extract_match_environment(page),
            "stage": _extract_match_stage(page),
            "event_url": _extract_event_link(page),
        })
    return summaries, {"ok": bool(summaries), "rows": len(summaries), "link_status": link_status, "statuses": statuses}


def _team_name_matches(target: str, candidate: str) -> bool:
    if not target or not candidate:
        return False
    a, b = normalize_team(target), normalize_team(candidate)
    return a == b or a in b or b in a or name_similarity(a, b) >= 0.79


def parse_mapstats_team_tables(page: str, fallback_team_names: Sequence[str] = ()) -> List[Dict[str, Any]]:
    if not page:
        return []
    table_matches = list(re.finditer(r"<table\b[^>]*>(.*?)</table>", page, flags=re.I | re.S))
    output: List[Dict[str, Any]] = []
    for idx, match in enumerate(table_matches):
        table = match.group(1)
        rows = []
        for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", table, flags=re.I | re.S):
            player_anchor = re.search(r'href=["\']/(?:stats/players?|player)/\d+/[^"\']+["\'][^>]*>(.*?)</a>', tr, flags=re.I | re.S)
            if not player_anchor:
                continue
            player = strip_tags(player_anchor.group(1)).replace("\n", " ").strip()
            row_text = strip_tags(tr).replace("\n", " ")
            kd_match = re.search(r"\b(\d{1,2})\s*[-–]\s*(\d{1,2})\b", row_text)
            kills = safe_int(kd_match.group(1), None) if kd_match else None
            deaths = safe_int(kd_match.group(2), None) if kd_match else None
            if kills is not None:
                rows.append({"player": player, "kills": kills, "deaths": deaths})
        if len(rows) < 3:
            continue
        before = page[max(0, match.start() - 1800): match.start()]
        team_candidates = []
        for raw in re.findall(r'class=["\'][^"\']*(?:teamName|team-name)[^"\']*["\'][^>]*>(.*?)</', before, flags=re.I | re.S):
            clean = strip_tags(raw).replace("\n", " ").strip()
            if clean:
                team_candidates.append(clean)
        for raw in re.findall(r'href=["\']/team/\d+/[^"\']+["\'][^>]*>(.*?)</a>', before, flags=re.I | re.S):
            clean = strip_tags(raw).replace("\n", " ").strip()
            if clean:
                team_candidates.append(clean)
        team = team_candidates[-1] if team_candidates else (fallback_team_names[len(output)] if len(output) < len(fallback_team_names) else "")
        output.append({
            "team": team,
            "players": rows,
            "total_kills": sum(int(x["kills"]) for x in rows),
            "total_deaths": sum(int(x.get("deaths") or 0) for x in rows),
        })
    return output[:2]


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def fetch_mapstats_team_summary(map_url: str, team_names: Tuple[str, ...] = ()) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    page, status = http_get_text(map_url, "HLTV deep mapstats", ttl=60 * 60 * 12, timeout=20)
    tables = parse_mapstats_team_tables(page or "", team_names)
    return tables, {**status, "tables": len(tables)}


def _summarize_map_observations(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {"maps": 0}
    totals = [safe_float(x.get("rounds"), None) for x in rows]
    totals = [float(x) for x in totals if x is not None and x > 0]
    won = sum(safe_float(x.get("rounds_won"), 0) or 0 for x in rows)
    played = sum(safe_float(x.get("rounds"), 0) or 0 for x in rows)
    return {
        "maps": len(rows),
        "avg_rounds": round(float(np.mean(totals)), 3) if totals else None,
        "rounds_sd": round(float(np.std(totals)), 3) if len(totals) >= 2 else 3.9,
        "round_win_pct": round(100.0 * won / played, 2) if played > 0 else None,
        "close_rate": round(sum((safe_float(x.get("margin"), 99) or 99) <= 4 for x in rows) / len(rows), 3),
        "blowout_rate": round(sum((safe_float(x.get("margin"), 0) or 0) >= 7 for x in rows) / len(rows), 3),
        "ot_rate": round(sum(bool(x.get("overtime")) for x in rows) / len(rows), 3),
    }


@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def build_team_deep_profile(team_id: str, slug: str, team_name: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    pool, pool_status = fetch_team_map_pool(team_id, slug, 120)
    roster, roster_status = fetch_team_roster(team_id, slug)
    summaries, summaries_status = fetch_recent_team_match_summaries(team_id, slug, DEEP_PULL_MATCH_LIMIT)
    pick_counts: Counter = Counter()
    ban_counts: Counter = Counter()
    map_rows: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    same_core_matches = 0
    current_roster_maps = 0
    mapstats_used = 0
    mapstats_rounds = 0.0
    deaths_allowed_num = 0.0
    kills_for_num = 0.0
    environment_counts: Counter = Counter()
    match_dates: List[datetime] = []

    roster_norm = {normalize_name(x) for x in roster[:5]}
    for summary in summaries:
        environment_counts[summary.get("environment") or "UNKNOWN"] += 1
        dt = _parse_iso_datetime(summary.get("match_datetime"))
        if dt:
            match_dates.append(dt)
        for action in summary.get("veto_actions") or []:
            if _team_name_matches(team_name, action.get("team", "")):
                if action.get("action") == "picked":
                    pick_counts[action.get("map")] += 1
                elif action.get("action") == "removed":
                    ban_counts[action.get("map")] += 1
        summary_names = {normalize_name(x) for x in summary.get("lineup_names") or []}
        overlap = len(roster_norm & summary_names) if roster_norm else 0
        same_core = overlap >= min(4, max(len(roster_norm) - 1, 1)) if roster_norm else False
        if same_core:
            same_core_matches += 1
        for result in summary.get("map_results") or []:
            if _team_name_matches(team_name, result.get("team1", "")):
                team_score, opp_score = result.get("score1", 0), result.get("score2", 0)
            elif _team_name_matches(team_name, result.get("team2", "")):
                team_score, opp_score = result.get("score2", 0), result.get("score1", 0)
            else:
                continue
            row = {**result, "rounds_won": team_score, "rounds_lost": opp_score}
            map_rows[result.get("map", "Unknown")].append(row)
            if same_core:
                current_roster_maps += 1

        team_names = tuple(x.get("name", "") for x in (summary.get("teams") or [])[:2])
        map_results = summary.get("map_results") or []
        for idx, map_url in enumerate((summary.get("mapstats_links") or [])[:2]):
            if mapstats_used >= DEEP_PULL_MAPSTATS_LIMIT:
                break
            tables, _ = fetch_mapstats_team_summary(map_url, team_names)
            if len(tables) < 2:
                continue
            rounds = safe_float((map_results[idx] if idx < len(map_results) else {}).get("rounds"), None)
            if not rounds or rounds <= 0:
                continue
            target_idx = next((i for i, table in enumerate(tables) if _team_name_matches(team_name, table.get("team", ""))), None)
            if target_idx is None:
                # Fallback to match-page team order.
                target_idx = next((i for i, name in enumerate(team_names[:2]) if _team_name_matches(team_name, name)), None)
            if target_idx not in {0, 1}:
                continue
            opponent_idx = 1 - int(target_idx)
            kills_for_num += float(tables[int(target_idx)].get("total_kills", 0))
            deaths_allowed_num += float(tables[opponent_idx].get("total_kills", 0))
            mapstats_rounds += float(rounds)
            mapstats_used += 1

    map_profiles: Dict[str, Dict[str, Any]] = {}
    all_maps = set(pool) | set(map_rows)
    for map_name in all_maps:
        observed = _summarize_map_observations(map_rows.get(map_name, []))
        base = pool.get(map_name, {})
        map_profiles[map_name] = {
            **base,
            **{k: v for k, v in observed.items() if v is not None},
            "pick_count": int(pick_counts.get(map_name, 0)),
            "ban_count": int(ban_counts.get(map_name, 0)),
        }

    total_recent_maps = sum(len(v) for v in map_rows.values())
    roster_stability = current_roster_maps / max(total_recent_maps, 1)
    latest_match = max(match_dates).isoformat() if match_dates else ""
    rest_days = None
    if match_dates:
        rest_days = max(0.0, (datetime.now(timezone.utc) - max(match_dates)).total_seconds() / 86400.0)
    profile = {
        "team_id": team_id,
        "slug": slug,
        "team": team_name,
        "current_roster": roster[:7],
        "recent_matches": len(summaries),
        "recent_maps": total_recent_maps,
        "same_core_matches": same_core_matches,
        "current_roster_maps": current_roster_maps,
        "roster_stability": round(clamp(roster_stability, 0, 1), 3),
        "pick_counts": dict(pick_counts),
        "ban_counts": dict(ban_counts),
        "map_profiles": map_profiles,
        "kills_for_per_round": round(kills_for_num / max(mapstats_rounds, 1), 4) if kills_for_num > 0 else None,
        "deaths_allowed_per_round": round(deaths_allowed_num / max(mapstats_rounds, 1), 4) if deaths_allowed_num > 0 else None,
        "mapstats_samples": mapstats_used,
        "environment_counts": dict(environment_counts),
        "latest_match": latest_match,
        "rest_days": round(rest_days, 2) if rest_days is not None else None,
        "updated_at": now_iso(),
    }
    status = {
        "ok": bool(pool or summaries),
        "pool": pool_status,
        "roster": roster_status,
        "summaries": summaries_status,
        "mapstats_samples": mapstats_used,
    }
    return profile, status


def enrich_match_context(context: Dict[str, Any], deep_enabled: bool = True) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not deep_enabled:
        return context, {"ok": False, "message": "deep data disabled"}
    teams = context.get("teams") or []
    if len(teams) < 2:
        return context, {"ok": False, "message": "team identities unavailable"}
    profiles: List[Dict[str, Any]] = []
    statuses: List[Dict[str, Any]] = []
    for team in teams[:2]:
        profile, status = build_team_deep_profile(team.get("team_id", ""), team.get("slug", ""), team.get("name", ""))
        profiles.append(profile)
        statuses.append(status)
    enriched = dict(context)
    enriched["team_deep_profiles"] = profiles
    return enriched, {"ok": all(bool(x) for x in profiles), "statuses": statuses}


def _sample_weighted(rng: np.random.Generator, choices: Sequence[str], weights: Sequence[float]) -> str:
    if not choices:
        return ""
    arr = np.array([max(float(x), 0.0001) for x in weights], dtype=float)
    arr = arr / arr.sum()
    return str(rng.choice(list(choices), p=arr))


def simulate_veto_scenarios(team_profiles: Sequence[Dict[str, Any]], simulations: int = 5000) -> List[Dict[str, Any]]:
    if len(team_profiles) < 2:
        return []
    profiles = list(team_profiles[:2])
    candidate_maps = []
    for map_name in KNOWN_MAPS:
        sample = sum(safe_int((p.get("map_profiles") or {}).get(map_name, {}).get("maps"), 0) or 0 for p in profiles)
        picks = sum(safe_int((p.get("pick_counts") or {}).get(map_name), 0) or 0 for p in profiles)
        if sample > 0 or picks > 0:
            candidate_maps.append(map_name)
    # A BO3 veto normally operates on a seven-map pool. Keep the seven maps with
    # the strongest recent evidence instead of relying on a hardcoded active pool.
    if len(candidate_maps) > 7:
        candidate_maps.sort(
            key=lambda m: sum(safe_int((p.get("map_profiles") or {}).get(m, {}).get("maps"), 0) or 0 for p in profiles),
            reverse=True,
        )
        candidate_maps = candidate_maps[:7]
    if len(candidate_maps) < 4:
        return []
    rng = np.random.default_rng(stable_seed("veto", *(p.get("team") for p in profiles), MODEL_VERSION))
    pair_counts: Counter = Counter()
    for _ in range(simulations):
        available = list(candidate_maps)
        picked: List[str] = []
        for team_idx in [0, 1]:
            p = profiles[team_idx]
            weights = []
            for map_name in available:
                row = (p.get("map_profiles") or {}).get(map_name, {})
                play = safe_float(row.get("maps"), 0) or 0
                win = safe_float(row.get("win_pct"), 50) or 50
                bans = safe_float((p.get("ban_counts") or {}).get(map_name), 0) or 0
                weights.append(1.0 + bans * 2.4 + max(0, 6 - play) * 0.32 + max(0, 48 - win) * 0.045)
            banned = _sample_weighted(rng, available, weights)
            if banned in available:
                available.remove(banned)
        for team_idx in [0, 1]:
            p = profiles[team_idx]
            opp = profiles[1 - team_idx]
            weights = []
            for map_name in available:
                row = (p.get("map_profiles") or {}).get(map_name, {})
                opp_row = (opp.get("map_profiles") or {}).get(map_name, {})
                play = safe_float(row.get("maps"), 0) or 0
                win = safe_float(row.get("win_pct"), 50) or 50
                round_win = safe_float(row.get("round_win_pct"), 50) or 50
                pick_count = safe_float((p.get("pick_counts") or {}).get(map_name), 0) or 0
                opp_bans = safe_float((opp.get("ban_counts") or {}).get(map_name), 0) or 0
                opp_win = safe_float(opp_row.get("win_pct"), 50) or 50
                weights.append(
                    1.0 + pick_count * 3.1 + play * 0.42 + max(0, win - 45) * 0.12 +
                    max(0, round_win - 47) * 0.10 + max(0, 52 - opp_win) * 0.05 - opp_bans * 0.10
                )
            choice = _sample_weighted(rng, available, weights)
            if choice:
                picked.append(choice)
                available.remove(choice)
        if len(picked) == 2:
            pair_counts[tuple(picked)] += 1
    top_pairs = pair_counts.most_common(12)
    top_total = sum(count for _, count in top_pairs)
    if top_total <= 0:
        return []
    return [
        {"maps": list(pair), "probability": round(count / top_total, 5)}
        for pair, count in top_pairs
    ]


def infer_likely_maps(context: Dict[str, Any]) -> Tuple[List[str], float, Dict[str, Any]]:
    veto_actions = context.get("veto_actions") or []
    veto_picks = [x.get("map") for x in veto_actions if x.get("action") == "picked" and x.get("map") in KNOWN_MAPS]
    if len(veto_picks) >= 2:
        scenarios = [{"maps": veto_picks[:2], "probability": 1.0}]
        return veto_picks[:2], 98.0, {"method": "confirmed veto picks", "veto_actions": veto_actions, "scenarios": scenarios}
    confirmed = context.get("confirmed_maps") or []
    if len(confirmed) >= 2:
        scenarios = [{"maps": confirmed[:2], "probability": 1.0}]
        return confirmed[:2], 95.0, {"method": "confirmed match maps", "veto_actions": veto_actions, "scenarios": scenarios}

    deep_profiles = context.get("team_deep_profiles") or []
    if len(deep_profiles) >= 2:
        scenarios = simulate_veto_scenarios(deep_profiles)
        if veto_picks and scenarios:
            # Preserve an announced first pick and renormalize compatible second maps.
            first = veto_picks[0]
            conditioned = [x for x in scenarios if x.get("maps") and x["maps"][0] == first]
            if not conditioned:
                conditioned = [{"maps": [first, x["maps"][1] if len(x.get("maps", [])) > 1 else x["maps"][0]], "probability": x["probability"]} for x in scenarios]
            total = sum(x["probability"] for x in conditioned) or 1.0
            scenarios = [{**x, "probability": x["probability"] / total} for x in conditioned]
        if scenarios:
            top = scenarios[0]["maps"][:2]
            concentration = sum(x["probability"] for x in scenarios[:3])
            sample = sum(safe_int(p.get("recent_maps"), 0) or 0 for p in deep_profiles)
            confidence = clamp(58 + concentration * 22 + min(sample, 36) * 0.45, 58, 91)
            return top, confidence, {
                "method": "recent-veto Monte Carlo",
                "veto_actions": veto_actions,
                "scenarios": scenarios,
                "team_profiles": deep_profiles,
            }

    teams = context.get("teams") or []
    if len(teams) < 2:
        return [], 42.0, {"method": "no team IDs", "scenarios": []}
    pools = []
    pool_statuses = []
    for team in teams[:2]:
        pool, status = fetch_team_map_pool(team.get("team_id", ""), team.get("slug", ""), 90)
        pools.append(pool)
        pool_statuses.append(status)
    if not all(pools):
        return [], 45.0, {"method": "map pool unavailable", "statuses": pool_statuses, "scenarios": []}
    available = [m for m in KNOWN_MAPS if m in pools[0] or m in pools[1]]
    bans = []
    for pool in pools:
        sampled = [(m, v) for m, v in pool.items() if safe_int(v.get("maps"), 0) >= 3]
        if sampled:
            bans.append(min(sampled, key=lambda x: (x[1].get("win_pct", 50), x[1].get("maps", 0)))[0])
    remaining = [m for m in available if m not in bans]
    picks = []
    for pool in pools:
        choices = [(m, v) for m, v in pool.items() if m in remaining and safe_int(v.get("maps"), 0) >= 2]
        if choices:
            pick = max(choices, key=lambda x: (x[1].get("maps", 0) * 0.7 + x[1].get("win_pct", 50) * 0.3))[0]
            if pick not in picks:
                picks.append(pick)
    for map_name in sorted(remaining, key=lambda m: sum(safe_int(p.get(m, {}).get("maps"), 0) or 0 for p in pools), reverse=True):
        if map_name not in picks:
            picks.append(map_name)
        if len(picks) == 2:
            break
    scenarios = [{"maps": picks[:2], "probability": 1.0}] if len(picks) >= 2 else []
    total_sample = sum(safe_int(pools[i].get(m, {}).get("maps"), 0) or 0 for i in range(2) for m in picks[:2])
    confidence = clamp(48 + total_sample * 0.75, 48, 76)
    return picks[:2], confidence, {"method": "team map-pool fallback", "bans": bans, "statuses": pool_statuses, "scenarios": scenarios}

# ============================================================
# OPTIONAL FREE PANDASCORE PREMATCH ADAPTER
# ============================================================

@st.cache_data(ttl=10 * 60, show_spinner=False)
def fetch_pandascore_upcoming() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    token = get_secret("PANDASCORE_TOKEN")
    if not token:
        return [], {"ok": False, "configured": False, "message": "Optional free PandaScore token not configured"}
    begin = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    end = (datetime.now(timezone.utc) + timedelta(days=4)).isoformat(timespec="seconds").replace("+00:00", "Z")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    params = {"range[begin_at]": f"{begin},{end}", "per_page": 100, "sort": "begin_at"}
    data, status = http_get_json(f"{PANDASCORE_BASE}/csgo/matches/upcoming", "PandaScore", ttl=10 * 60, params=params, headers=headers, timeout=20, allow_stale=False)
    rows = data if isinstance(data, list) else []
    return rows, {**status, "configured": True, "rows": len(rows)}

# ============================================================
# ROLE / OPPONENT / EXPECTED ROUNDS ENGINES
# ============================================================


def get_player_role(player: str, profile: PlayerStats) -> Tuple[str, float, str]:
    overrides = load_json(ROLE_OVERRIDES_FILE, {})
    row = overrides.get(normalize_name(player), {}) if isinstance(overrides, dict) else {}
    if isinstance(row, str):
        return row, 1.0, "manual override"
    if isinstance(row, dict) and row.get("role"):
        return str(row["role"]), safe_float(row.get("confidence"), 1.0) or 1.0, "manual override"

    awp_share = safe_float(profile.awp_kill_share, None)
    opening = safe_float(profile.opening_kpr, None)
    opening_ratio = safe_float(profile.opening_ratio, None)
    hs = safe_float(profile.hs_pct, None)
    if awp_share is not None and awp_share >= 0.48:
        return "Primary AWPer", clamp(0.72 + (awp_share - 0.48) * 0.45, 0.72, 0.96), "verified from sniper-kill share"
    if awp_share is not None and awp_share >= 0.22:
        return "Secondary AWPer / Hybrid", clamp(0.60 + (awp_share - 0.22) * 0.35, 0.60, 0.80), "inferred from weapon share"
    if opening is not None and opening >= 0.145 and (opening_ratio or 1.0) <= 1.15:
        return "Entry Fragger", 0.74, "opening-duel volume + risk"
    if opening is not None and opening >= 0.135 and profile.kpr >= 0.72:
        return "Aggressive Star Rifler", 0.70, "opening volume + KPR"
    if profile.kpr >= 0.755 and profile.rating >= 1.12:
        return "Star Rifler", 0.78, "KPR + rating + firepower"
    if profile.kpr <= 0.615 and profile.rating <= 0.96:
        return "Support / IGL", 0.66, "low frag share + rating"
    if hs is not None and hs >= 58 and profile.kpr >= 0.68:
        return "Aim-Heavy Rifler", 0.61, "high headshot share"
    if profile.multi_kill_rate is not None and profile.multi_kill_rate >= 0.16 and profile.kpr >= 0.70:
        return "High-Impact Rifler", 0.62, "multi-kill rate + KPR"
    return "Rifler / Unknown", 0.45, "advanced role evidence incomplete"


def _resolve_team_profiles(context: Dict[str, Any], team: str, opponent: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    profiles = context.get("team_deep_profiles") or []
    team_profile: Dict[str, Any] = {}
    opponent_profile: Dict[str, Any] = {}
    for profile in profiles:
        name = profile.get("team", "")
        if team and _team_name_matches(team, name):
            team_profile = profile
        if opponent and _team_name_matches(opponent, name):
            opponent_profile = profile
    if not team_profile and profiles:
        # Use player/team ordering only as a last fallback.
        team_profile = profiles[0]
    if not opponent_profile and len(profiles) >= 2:
        opponent_profile = profiles[1] if profiles[1] is not team_profile else profiles[0]
    return team_profile, opponent_profile


def match_competitiveness(context: Dict[str, Any], team: str = "", opponent: str = "", likely_maps: Sequence[str] = ()) -> Tuple[float, Dict[str, Any]]:
    components: List[Tuple[float, float, str]] = []
    ranks = context.get("world_ranks") or []
    if len(ranks) >= 2 and all(x > 0 for x in ranks[:2]):
        gap = abs(math.log((ranks[0] + 4) / (ranks[1] + 4)))
        rank_comp = clamp(math.exp(-1.08 * gap), 0.18, 1.0)
        components.append((rank_comp, 0.42, "world ranks"))
    deep_profiles = context.get("team_deep_profiles") or []
    if len(deep_profiles) >= 2:
        diffs = []
        close_rates = []
        for map_name in likely_maps[:2]:
            rows = [(p.get("map_profiles") or {}).get(map_name, {}) for p in deep_profiles[:2]]
            rw = [safe_float(x.get("round_win_pct"), None) for x in rows]
            if all(x is not None for x in rw):
                diffs.append(abs(float(rw[0]) - float(rw[1])) / 100.0)
            for row in rows:
                if safe_float(row.get("close_rate"), None) is not None:
                    close_rates.append(float(row["close_rate"]))
        if diffs:
            map_comp = clamp(1.0 - float(np.mean(diffs)) * 3.0, 0.20, 1.0)
            if close_rates:
                map_comp = clamp(0.78 * map_comp + 0.22 * float(np.mean(close_rates)), 0.20, 1.0)
            components.append((map_comp, 0.48, "map round strength"))
    if not components:
        return 0.66, {"method": "neutral default; matchup strength unavailable", "components": []}
    weight = sum(x[1] for x in components)
    comp = sum(x[0] * x[1] for x in components) / max(weight, 1e-9)
    return clamp(comp, 0.18, 1.0), {"method": "blended competitiveness", "components": [{"value": round(v, 4), "weight": w, "source": n} for v, w, n in components]}


def _map_round_model(context: Dict[str, Any], map_name: str, team: str = "", opponent: str = "") -> Dict[str, Any]:
    base = MAP_ROUND_BASE.get(map_name, 21.1)
    profiles = context.get("team_deep_profiles") or []
    rows = [(p.get("map_profiles") or {}).get(map_name, {}) for p in profiles[:2]]
    observed_means = [safe_float(x.get("avg_rounds"), None) for x in rows]
    observed_means = [float(x) for x in observed_means if x is not None and 14 <= x <= 32]
    close_rates = [safe_float(x.get("close_rate"), None) for x in rows]
    close_rates = [float(x) for x in close_rates if x is not None]
    blowout_rates = [safe_float(x.get("blowout_rate"), None) for x in rows]
    blowout_rates = [float(x) for x in blowout_rates if x is not None]
    ot_rates = [safe_float(x.get("ot_rate"), None) for x in rows]
    ot_rates = [float(x) for x in ot_rates if x is not None]
    round_win = [safe_float(x.get("round_win_pct"), None) for x in rows]
    strength_gap = abs(float(round_win[0]) - float(round_win[1])) / 100.0 if len(round_win) >= 2 and all(x is not None for x in round_win[:2]) else 0.08
    comp, comp_meta = match_competitiveness(context, team, opponent, [map_name])
    observed = float(np.mean(observed_means)) if observed_means else base
    close = float(np.mean(close_rates)) if close_rates else 0.38
    blowout = float(np.mean(blowout_rates)) if blowout_rates else 0.27
    ot = float(np.mean(ot_rates)) if ot_rates else 0.055
    mean = 0.44 * base + 0.56 * observed
    mean += (comp - 0.60) * 3.0 + close * 0.85 - blowout * 1.10 - strength_gap * 4.0 + ot * 2.3
    mean = clamp(mean, 17.0, 25.8)
    sd_values = [safe_float(x.get("rounds_sd"), None) for x in rows]
    sd_values = [float(x) for x in sd_values if x is not None and 1.5 <= x <= 8]
    sd = float(np.mean(sd_values)) if sd_values else 3.75
    sd = clamp(sd + blowout * 0.55 - close * 0.35 + strength_gap * 1.2, 2.8, 5.2)
    return {
        "map": map_name,
        "mean_rounds": round(mean, 4),
        "rounds_sd": round(sd, 4),
        "close_rate": round(close, 4),
        "blowout_rate": round(blowout, 4),
        "ot_rate": round(ot, 4),
        "strength_gap": round(strength_gap, 4),
        "competitiveness": round(comp, 4),
        "competitiveness_meta": comp_meta,
        "sample_maps": sum(safe_int(x.get("maps"), 0) or 0 for x in rows),
    }


def project_expected_rounds(context: Dict[str, Any], likely_maps: Sequence[str], map_meta: Optional[Dict[str, Any]] = None, team: str = "", opponent: str = "") -> Tuple[float, float, Dict[str, Any]]:
    scenarios = list((map_meta or {}).get("scenarios") or [])
    if not scenarios and len(likely_maps) >= 2:
        scenarios = [{"maps": list(likely_maps[:2]), "probability": 1.0}]
    if not scenarios:
        scenarios = [{"maps": [likely_maps[0] if likely_maps else "Unknown", likely_maps[1] if len(likely_maps) > 1 else "Unknown"], "probability": 1.0}]
    normalized = []
    total_prob = sum(safe_float(x.get("probability"), 0) or 0 for x in scenarios) or 1.0
    for scenario in scenarios[:12]:
        maps = list(scenario.get("maps") or [])[:2]
        while len(maps) < 2:
            maps.append("Unknown")
        models = [_map_round_model(context, m, team, opponent) if m in KNOWN_MAPS else {"map": m, "mean_rounds": 21.1, "rounds_sd": 3.8, "ot_rate": 0.05, "sample_maps": 0} for m in maps]
        prob = (safe_float(scenario.get("probability"), 0) or 0) / total_prob
        total_mean = sum(float(x["mean_rounds"]) for x in models)
        total_var = sum(float(x["rounds_sd"]) ** 2 for x in models)
        normalized.append({"maps": maps, "probability": prob, "map_models": models, "mean_rounds": total_mean, "rounds_sd": math.sqrt(total_var)})
    mean = sum(x["probability"] * x["mean_rounds"] for x in normalized)
    second = sum(x["probability"] * (x["rounds_sd"] ** 2 + x["mean_rounds"] ** 2) for x in normalized)
    sd = math.sqrt(max(second - mean ** 2, 0.01))
    if context.get("format") == "BO1":
        return mean, sd, {"invalid_format": True, "format": "BO1", "scenarios": normalized}
    return clamp(mean, 33.5, 51.0), clamp(sd, 3.4, 7.8), {
        "invalid_format": False,
        "format": context.get("format", "UNKNOWN"),
        "scenarios": normalized,
        "environment": context.get("environment", "UNKNOWN"),
        "event_tier": context.get("event_tier", "LOW/UNKNOWN"),
    }


def opponent_kpr_factor(profile: PlayerStats, context: Dict[str, Any], team: str, opponent: str, likely_maps: Sequence[str] = (), role: str = "") -> Tuple[float, Dict[str, Any]]:
    team_profile, opponent_profile = _resolve_team_profiles(context, team, opponent)
    factors: List[Tuple[float, float, str]] = []
    deaths_allowed = safe_float(opponent_profile.get("deaths_allowed_per_round"), None)
    mapstats_samples = safe_int(opponent_profile.get("mapstats_samples"), 0) or 0
    if deaths_allowed is not None and 0.45 <= deaths_allowed <= 0.95:
        sample_weight = clamp(mapstats_samples / 8.0, 0.15, 0.75)
        factors.append((clamp(deaths_allowed / LEAGUE_DPR, 0.90, 1.10), sample_weight, "opponent deaths allowed/round"))
    map_factors: Dict[str, float] = {}
    for map_name in likely_maps[:3]:
        row = (opponent_profile.get("map_profiles") or {}).get(map_name, {})
        round_win = safe_float(row.get("round_win_pct"), None)
        sample = safe_int(row.get("maps"), 0) or 0
        if round_win is not None:
            loss_environment = (100.0 - round_win) / 50.0
            mf = clamp(loss_environment, 0.91, 1.09)
            w = clamp(sample / 12.0, 0.10, 0.55)
            factors.append((mf, w, f"{map_name} opponent round-loss rate"))
            map_factors[map_name] = mf
    ranks = context.get("world_ranks") or []
    if len(ranks) >= 2:
        gap = abs(ranks[0] - ranks[1])
        factors.append((clamp(1.0 - min(gap, 90) * 0.00025, 0.977, 1.0), 0.18, "large mismatch efficiency tax"))
    # Role-aware adjustment is deliberately small unless the player's role is
    # supported by real weapon/opening data.
    role_factor = 1.0
    if "AWPer" in role and profile.awp_kill_share is not None:
        role_factor = clamp(1.0 + (float(profile.awp_kill_share) - 0.50) * 0.025, 0.992, 1.012)
        factors.append((role_factor, 0.20, "verified AWP role interaction"))
    elif "Entry" in role and profile.opening_kpr is not None:
        opening_risk = safe_float(profile.opening_deaths_pr, 0.10) or 0.10
        role_factor = clamp(1.0 - max(0, opening_risk - 0.11) * 0.18, 0.985, 1.01)
        factors.append((role_factor, 0.18, "entry opening-risk interaction"))
    if not factors:
        return 1.0, {"note": "neutral; deep opponent samples unavailable", "factor": 1.0, "map_factors": {}}
    total_w = sum(w for _, w, _ in factors)
    log_factor = sum(math.log(max(v, 0.8)) * w for v, w, _ in factors) / max(total_w, 1e-9)
    factor = clamp(math.exp(log_factor), 0.90, 1.10)
    return factor, {
        "note": "deep opponent/role blend",
        "factor": round(factor, 4),
        "map_factors": {k: round(v, 4) for k, v in map_factors.items()},
        "components": [{"factor": round(v, 4), "weight": round(w, 3), "source": label} for v, w, label in factors],
        "opponent_profile": opponent_profile,
        "team_profile": team_profile,
    }


def match_context_adjustment(profile: PlayerStats, context: Dict[str, Any], team: str, opponent: str) -> Tuple[float, float, Dict[str, Any]]:
    """Return mean factor, variance multiplier, and context notes."""
    team_profile, _ = _resolve_team_profiles(context, team, opponent)
    mean_factor = 1.0
    variance = 1.0
    notes: List[str] = []
    environment = context.get("environment", "UNKNOWN")
    if environment == "LAN":
        # Do not assume LAN makes a player better; only widen uncertainty for
        # small samples where no LAN split is freely available.
        if profile.maps < 40:
            variance *= 1.07
            notes.append("LAN with limited long-term sample")
    stage = str(context.get("stage") or "").lower()
    if any(x in stage for x in ["eliminat", "grand final", "lower bracket", "winner advances", "losing team"]):
        variance *= 1.04
        notes.append("high-leverage match")
    rest_days = safe_float(team_profile.get("rest_days"), None)
    if rest_days is not None:
        if rest_days < 0.35:
            mean_factor *= 0.985
            variance *= 1.05
            notes.append("same-day/back-to-back risk")
        elif rest_days > 20:
            variance *= 1.04
            notes.append("long layoff")
    roster_stability = safe_float(team_profile.get("roster_stability"), None)
    if roster_stability is not None and roster_stability < 0.45:
        variance *= 1.08
        notes.append("low current-roster continuity")
    return clamp(mean_factor, 0.97, 1.02), clamp(variance, 1.0, 1.18), {
        "environment": environment,
        "stage": context.get("stage", ""),
        "event_tier": context.get("event_tier", "LOW/UNKNOWN"),
        "rest_days": rest_days,
        "roster_stability": roster_stability,
        "current_roster_maps": safe_int(team_profile.get("current_roster_maps"), 0) or 0,
        "notes": notes,
    }

# ============================================================
# LEARNING / CALIBRATION
# ============================================================


def build_learning_profiles(results: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    rows = results if results is not None else load_json(RESULT_LOG, [])
    graded = [x for x in rows if x.get("graded_result") in {"WIN", "LOSS", "PUSH"} and safe_float(x.get("actual_kills"), None) is not None]
    profiles: Dict[str, Any] = {"global": {}, "players": {}, "roles": {}, "line_buckets": {}, "maps": {}, "event_tiers": {}, "data_buckets": {}, "updated_at": now_iso()}
    if not graded:
        save_json(LEARNING_FILE, profiles)
        return profiles

    def summarize(group: List[Dict[str, Any]]) -> Dict[str, Any]:
        errors = [float(x["actual_kills"]) - float(x["projection_before_learning"]) for x in group if safe_float(x.get("projection_before_learning"), None) is not None]
        wins = [x for x in group if x.get("graded_result") == "WIN"]
        if not errors:
            return {"samples": 0, "bias": 0.0, "mae": None, "win_rate": None}
        # Shrink bias toward zero. Recent rows get slightly greater weight.
        n = len(errors)
        raw_bias = float(np.mean(errors[-250:]))
        shrunk = raw_bias * n / (n + 18)
        return {
            "samples": n,
            "bias": round(clamp(shrunk, -MAX_LEARNING_PROJECTION_SHIFT, MAX_LEARNING_PROJECTION_SHIFT), 4),
            "raw_bias": round(raw_bias, 4),
            "mae": round(float(np.mean(np.abs(errors))), 4),
            "win_rate": round(len(wins) / n, 4),
        }

    profiles["global"] = summarize(graded)
    for row in graded:
        pkey = normalize_name(row.get("player"))
        role = str(row.get("role") or "Unknown")
        line = safe_float(row.get("line"), 0) or 0
        bucket = f"{int(line // 5) * 5}-{int(line // 5) * 5 + 4.5}"
        profiles["players"].setdefault(pkey, []).append(row)
        profiles["roles"].setdefault(role, []).append(row)
        profiles["line_buckets"].setdefault(bucket, []).append(row)
        for map_name in row.get("likely_maps") or []:
            profiles["maps"].setdefault(str(map_name), []).append(row)
        event_tier = str(row.get("event_tier") or "LOW/UNKNOWN")
        profiles["event_tiers"].setdefault(event_tier, []).append(row)
        data_score = safe_int(row.get("data_score"), 0) or 0
        data_bucket = "80-100" if data_score >= 80 else "65-79" if data_score >= 65 else "0-64"
        profiles["data_buckets"].setdefault(data_bucket, []).append(row)
    for section in ["players", "roles", "line_buckets", "maps", "event_tiers", "data_buckets"]:
        profiles[section] = {k: summarize(v) for k, v in profiles[section].items()}
    save_json(LEARNING_FILE, profiles)
    return profiles


def learning_adjustment(player: str, role: str, line: float, likely_maps: Sequence[str] = (), event_tier: str = "", data_score_hint: int = 0) -> Tuple[float, Dict[str, Any]]:
    profiles = load_json(LEARNING_FILE, {})
    if not profiles:
        profiles = build_learning_profiles()
    pieces = []
    global_row = profiles.get("global", {}) if isinstance(profiles, dict) else {}
    if safe_int(global_row.get("samples"), 0) >= 25:
        pieces.append((safe_float(global_row.get("bias"), 0.0) or 0.0, 0.35, "global"))
    player_row = profiles.get("players", {}).get(normalize_name(player), {}) if isinstance(profiles, dict) else {}
    if safe_int(player_row.get("samples"), 0) >= 8:
        pieces.append((safe_float(player_row.get("bias"), 0.0) or 0.0, 0.40, "player"))
    role_row = profiles.get("roles", {}).get(role, {}) if isinstance(profiles, dict) else {}
    if safe_int(role_row.get("samples"), 0) >= 12:
        pieces.append((safe_float(role_row.get("bias"), 0.0) or 0.0, 0.15, "role"))
    bucket = f"{int(line // 5) * 5}-{int(line // 5) * 5 + 4.5}"
    bucket_row = profiles.get("line_buckets", {}).get(bucket, {}) if isinstance(profiles, dict) else {}
    if safe_int(bucket_row.get("samples"), 0) >= 12:
        pieces.append((safe_float(bucket_row.get("bias"), 0.0) or 0.0, 0.10, "line bucket"))
    map_rows = []
    for map_name in likely_maps[:2]:
        row = profiles.get("maps", {}).get(str(map_name), {}) if isinstance(profiles, dict) else {}
        if safe_int(row.get("samples"), 0) >= 15:
            map_rows.append(safe_float(row.get("bias"), 0.0) or 0.0)
    if map_rows:
        pieces.append((float(np.mean(map_rows)), 0.10, "map context"))
    tier_row = profiles.get("event_tiers", {}).get(str(event_tier), {}) if isinstance(profiles, dict) else {}
    if safe_int(tier_row.get("samples"), 0) >= 15:
        pieces.append((safe_float(tier_row.get("bias"), 0.0) or 0.0, 0.05, "event tier"))
    if not pieces:
        return 0.0, {"applied": False, "pieces": []}
    total_weight = sum(w for _, w, _ in pieces)
    adjustment = sum(v * w for v, w, _ in pieces) / max(total_weight, 0.01)
    adjustment = clamp(adjustment, -MAX_LEARNING_PROJECTION_SHIFT, MAX_LEARNING_PROJECTION_SHIFT)
    return adjustment, {"applied": True, "pieces": pieces, "adjustment": round(adjustment, 4)}

# ============================================================
# ODDS / NO-VIG / LINE MOVEMENT
# ============================================================


def american_to_implied(odds: Optional[float]) -> Optional[float]:
    if odds is None or odds == 0:
        return None
    return 100.0 / (odds + 100.0) if odds > 0 else (-odds) / ((-odds) + 100.0)


def no_vig_probs(over_odds: Optional[float], under_odds: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
    op, up = american_to_implied(over_odds), american_to_implied(under_odds)
    if op is None or up is None or op + up <= 0:
        return None, None
    return op / (op + up), up / (op + up)


def odds_key(player: str, line: float) -> str:
    return f"{normalize_name(player)}|{float(line):.1f}"


def get_saved_odds(player: str, line: float) -> Dict[str, Any]:
    data = load_json(MANUAL_ODDS_FILE, {})
    return data.get(odds_key(player, line), {}) if isinstance(data, dict) else {}


def save_manual_odds_rows(df: pd.DataFrame) -> int:
    data = load_json(MANUAL_ODDS_FILE, {})
    count = 0
    if not isinstance(data, dict):
        data = {}
    for _, row in df.iterrows():
        player = str(row.get("Player", "")).strip()
        line = safe_float(row.get("Line"), None)
        if not player or line is None:
            continue
        data[odds_key(player, line)] = {
            "player": player, "line": line,
            "over_odds": safe_float(row.get("Over Odds"), None),
            "under_odds": safe_float(row.get("Under Odds"), None),
            "book": str(row.get("Book", "Manual")),
            "saved_at": now_iso(),
        }
        count += 1
    save_json(MANUAL_ODDS_FILE, data)
    return count



def annotate_market_consensus(props: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for prop in props:
        key = f"{normalize_name(prop.get('player'))}|{str(prop.get('start_time',''))[:10]}|{normalize_name(prop.get('market',''))}"
        groups[key].append(prop)
    snapshot: Dict[str, Any] = {}
    output: List[Dict[str, Any]] = []
    for key, rows in groups.items():
        source_lines: Dict[str, List[float]] = defaultdict(list)
        for row in rows:
            line = safe_float(row.get("line"), None)
            if line is not None:
                source_lines[str(row.get("source") or "Unknown")].append(float(line))
        representative = {source: float(np.median(lines)) for source, lines in source_lines.items() if lines}
        values = list(representative.values())
        consensus = float(np.median(values)) if values else None
        spread = max(values) - min(values) if len(values) >= 2 else None
        meta = {
            "consensus_line": round(consensus, 3) if consensus is not None else None,
            "market_source_count": len(representative),
            "market_line_spread": round(spread, 3) if spread is not None else None,
            "market_source_lines": representative,
        }
        snapshot[key] = {**meta, "updated_at": now_iso()}
        for row in rows:
            output.append({**row, **meta})
    if snapshot:
        save_json(MARKET_CONSENSUS_FILE, snapshot)
    return output


def update_line_history(props: List[Dict[str, Any]]) -> None:
    history = load_json(LINE_HISTORY_FILE, {})
    if not isinstance(history, dict):
        history = {}
    changed = False
    for prop in props:
        key = f"{normalize_name(prop.get('player'))}|{prop.get('market')}|{str(prop.get('start_time',''))[:10]}"
        rows = history.setdefault(key, [])
        line = safe_float(prop.get("line"), None)
        if line is None:
            continue
        if not rows or safe_float(rows[-1].get("line"), None) != line:
            rows.append({"time": now_iso(), "line": line, "source": prop.get("source")})
            history[key] = rows[-50:]
            changed = True
    if changed:
        save_json(LINE_HISTORY_FILE, history)


def line_movement(player: str, market: str, start_time: str, current_line: float) -> Dict[str, Any]:
    history = load_json(LINE_HISTORY_FILE, {})
    key = f"{normalize_name(player)}|{market}|{str(start_time or '')[:10]}"
    rows = history.get(key, []) if isinstance(history, dict) else []
    opening = safe_float(rows[0].get("line"), current_line) if rows else current_line
    return {"opening_line": opening, "current_line": current_line, "move": round(current_line - opening, 2), "observations": len(rows)}

# ============================================================
# PROJECTION + MONTE CARLO
# ============================================================


def simulation_projection(
    player: str,
    line: float,
    kpr: float,
    expected_rounds: float,
    rounds_sd: float,
    profile_maps: int,
    role: str,
    learning_shift: float,
) -> Dict[str, Any]:
    rng = np.random.default_rng(stable_seed(player, line, kpr, expected_rounds, profile_maps, role, learning_shift, MODEL_VERSION))
    n = SIMULATIONS
    rounds = np.rint(rng.normal(expected_rounds, rounds_sd, size=n)).astype(int)
    rounds = np.clip(rounds, 26, 62)
    # Profile uncertainty shrinks with sample. Role uncertainty remains modest.
    kpr_sd = 0.030 + 0.055 / math.sqrt(max(profile_maps, 1) / 10.0 + 1.0)
    if "Unknown" in role:
        kpr_sd += 0.012
    latent_kpr = np.clip(rng.normal(kpr, kpr_sd, size=n), 0.40, 1.02)
    lam = np.clip(rounds * latent_kpr + learning_shift, 1.0, 70.0)
    # Gamma-Poisson mixture provides realistic overdispersion versus pure Poisson.
    dispersion = 24.0
    gamma_rate = rng.gamma(shape=dispersion, scale=lam / dispersion)
    kills = rng.poisson(gamma_rate)
    projection = float(np.mean(kills))
    median = float(np.median(kills))
    over_prob = float(np.mean(kills > line))
    under_prob = float(np.mean(kills < line))
    push_prob = float(np.mean(kills == line)) if float(line).is_integer() else 0.0
    return {
        "projection": projection,
        "median": median,
        "over_prob": over_prob,
        "under_prob": under_prob,
        "push_prob": push_prob,
        "floor_20": float(np.percentile(kills, 20)),
        "ceiling_80": float(np.percentile(kills, 80)),
        "p10": float(np.percentile(kills, 10)),
        "p90": float(np.percentile(kills, 90)),
        "sim_sd": float(np.std(kills)),
        "expected_rounds": float(np.mean(rounds)),
        "rounds_sd": float(np.std(rounds)),
    }



def simulation_projection_deep(
    player: str,
    line: float,
    base_kpr: float,
    player_map_profiles: Dict[str, Dict[str, Any]],
    rounds_meta: Dict[str, Any],
    opponent_factor: float,
    opponent_meta: Dict[str, Any],
    context_mean_factor: float,
    context_variance_multiplier: float,
    profile_maps: int,
    role: str,
    learning_shift: float,
) -> Dict[str, Any]:
    scenarios = list(rounds_meta.get("scenarios") or [])
    if not scenarios:
        scenarios = [{
            "maps": ["Unknown", "Unknown"], "probability": 1.0,
            "map_models": [
                {"map": "Unknown", "mean_rounds": 21.4, "rounds_sd": 3.8},
                {"map": "Unknown", "mean_rounds": 21.4, "rounds_sd": 3.8},
            ],
        }]
    probs = np.array([max(safe_float(x.get("probability"), 0) or 0, 0) for x in scenarios], dtype=float)
    probs = probs / probs.sum() if probs.sum() > 0 else np.ones(len(scenarios)) / len(scenarios)
    rng = np.random.default_rng(stable_seed(player, line, base_kpr, profile_maps, role, learning_shift, MODEL_VERSION))
    n = SIMULATIONS
    scenario_idx = rng.choice(len(scenarios), size=n, p=probs)
    total_kills = np.zeros(n, dtype=int)
    total_rounds = np.zeros(n, dtype=int)
    map_expected: Dict[str, Dict[str, float]] = defaultdict(lambda: {"rounds": 0.0, "kills": 0.0, "weight": 0.0})

    profile_uncertainty = 0.025 + 0.050 / math.sqrt(max(profile_maps, 1) / 10.0 + 1.0)
    if "Unknown" in role:
        profile_uncertainty += 0.012
    if "Entry" in role:
        profile_uncertainty += 0.008
    profile_uncertainty *= context_variance_multiplier
    common_form = rng.normal(0.0, profile_uncertainty, size=n)
    map_factors = opponent_meta.get("map_factors") or {}

    for scenario_number, scenario in enumerate(scenarios):
        mask = scenario_idx == scenario_number
        count = int(mask.sum())
        if count <= 0:
            continue
        maps = list(scenario.get("maps") or [])[:2]
        models = list(scenario.get("map_models") or [])[:2]
        while len(maps) < 2:
            maps.append("Unknown")
        while len(models) < 2:
            models.append({"map": maps[len(models)], "mean_rounds": 21.4, "rounds_sd": 3.8})
        common = common_form[mask]
        scenario_rounds = np.zeros(count, dtype=int)
        scenario_kills = np.zeros(count, dtype=int)
        for map_name, model in zip(maps, models):
            mean_rounds = safe_float(model.get("mean_rounds"), 21.4) or 21.4
            round_sd = safe_float(model.get("rounds_sd"), 3.8) or 3.8
            map_rounds = np.rint(rng.normal(mean_rounds, round_sd, size=count)).astype(int)
            map_rounds = np.clip(map_rounds, 13, 38)
            map_row = player_map_profiles.get(map_name, {})
            map_kpr = safe_float(map_row.get("blended_kpr"), base_kpr) or base_kpr
            # The global opponent factor already includes all supported matchup
            # evidence; map factor only contributes the map-specific difference.
            local_opponent = safe_float(map_factors.get(map_name), 1.0) or 1.0
            kpr_center = clamp(map_kpr * opponent_factor * local_opponent * context_mean_factor, 0.42, 1.02)
            map_noise = rng.normal(0.0, 0.018 * context_variance_multiplier, size=count)
            latent_kpr = np.clip(kpr_center + common + map_noise, 0.38, 1.08)
            lam = np.clip(map_rounds * latent_kpr, 0.5, 48.0)
            dispersion = 20.0 if "Entry" in role else 24.0
            gamma_rate = rng.gamma(shape=dispersion, scale=lam / dispersion)
            kills = rng.poisson(gamma_rate)
            scenario_rounds += map_rounds
            scenario_kills += kills
            probability_weight = probs[scenario_number]
            map_expected[map_name]["rounds"] += probability_weight * float(np.mean(map_rounds))
            map_expected[map_name]["kills"] += probability_weight * float(np.mean(kills))
            map_expected[map_name]["weight"] += probability_weight
        total_rounds[mask] = scenario_rounds
        total_kills[mask] = scenario_kills

    if learning_shift:
        # Apply the learned bias stochastically instead of adding fractional kills.
        shift = float(clamp(learning_shift, -MAX_LEARNING_PROJECTION_SHIFT, MAX_LEARNING_PROJECTION_SHIFT))
        if shift > 0:
            total_kills += rng.binomial(1, min(shift, 1.0), size=n)
        elif shift < 0:
            total_kills -= rng.binomial(1, min(abs(shift), 1.0), size=n)
            total_kills = np.maximum(total_kills, 0)

    projection = float(np.mean(total_kills))
    median = float(np.median(total_kills))
    over_prob = float(np.mean(total_kills > line))
    under_prob = float(np.mean(total_kills < line))
    push_prob = float(np.mean(total_kills == line)) if float(line).is_integer() else 0.0
    expected_map_breakdown = {}
    for map_name, values in map_expected.items():
        weight = max(values["weight"], 1e-9)
        expected_map_breakdown[map_name] = {
            "expected_rounds": round(values["rounds"] / weight, 3),
            "expected_kills": round(values["kills"] / weight, 3),
            "scenario_weight": round(values["weight"], 4),
            "player_map_kpr": player_map_profiles.get(map_name, {}).get("blended_kpr"),
        }
    return {
        "projection": projection,
        "median": median,
        "over_prob": over_prob,
        "under_prob": under_prob,
        "push_prob": push_prob,
        "floor_20": float(np.percentile(total_kills, 20)),
        "ceiling_80": float(np.percentile(total_kills, 80)),
        "p10": float(np.percentile(total_kills, 10)),
        "p90": float(np.percentile(total_kills, 90)),
        "sim_sd": float(np.std(total_kills)),
        "expected_rounds": float(np.mean(total_rounds)),
        "rounds_sd": float(np.std(total_rounds)),
        "effective_kpr": float(np.mean(total_kills) / max(np.mean(total_rounds), 1)),
        "map_breakdown": expected_map_breakdown,
    }


def calculate_data_score(
    profile: PlayerStats,
    profile_meta: Dict[str, Any],
    match_context: Dict[str, Any],
    map_confidence: float,
    role_confidence: float,
    match_url: str,
) -> Tuple[int, List[str]]:
    score = 0.0
    notes: List[str] = []
    if profile_meta.get("matched"):
        score += 16
        score += 10 * clamp(profile_meta.get("match_score", 0), 0, 1)
    else:
        notes.append("player profile not matched")
    score += min(profile.maps, 60) / 60 * 18
    if profile.kpr != LEAGUE_KPR or profile.kills > 0:
        score += 10
    else:
        notes.append("KPR estimated")
    if match_url:
        score += 8
    else:
        notes.append("match URL not matched")
    if match_context.get("format") in {"BO3", "BO5"}:
        score += 7
    elif match_context.get("format") == "BO1":
        notes.append("BO1 is invalid for a Maps 1-2 prop")
    else:
        notes.append("match format unverified")
    score += 14 * clamp(map_confidence / 100, 0, 1)
    if map_confidence < 60:
        notes.append("map pool uncertainty")
    score += 7 * clamp(role_confidence, 0, 1)
    if role_confidence < 0.5:
        notes.append("role unverified")
    lineup = match_context.get("lineup_names") or []
    if lineup and max(name_similarity(profile.player, x) for x in lineup) >= 0.80:
        score += 7
    elif lineup:
        notes.append("player not found in listed lineup")
    else:
        notes.append("lineup unverified")
    if match_context.get("standin_warning"):
        score -= 12
        notes.append("stand-in / replacement warning")
    if match_context.get("postponed"):
        score -= 30
        notes.append("match postponed/cancelled")
    if profile.data_warnings:
        score -= min(6, len(profile.data_warnings) * 1.5)
    return int(round(clamp(score, 0, 100))), notes



def calculate_data_score_deep(
    profile: PlayerStats,
    profile_meta: Dict[str, Any],
    match_context: Dict[str, Any],
    map_confidence: float,
    role_confidence: float,
    match_url: str,
    player_map_profiles: Dict[str, Dict[str, Any]],
    map_meta: Dict[str, Any],
    opponent_meta: Dict[str, Any],
    context_meta: Dict[str, Any],
    market_source_count: int,
) -> Tuple[int, List[str], Dict[str, float]]:
    score = 0.0
    notes: List[str] = []
    components: Dict[str, float] = {}

    profile_points = 0.0
    if profile_meta.get("matched"):
        profile_points += 8 + 6 * clamp(profile_meta.get("match_score", 0), 0, 1)
    profile_points += min(profile.maps, 80) / 80 * 8
    if profile.kpr != LEAGUE_KPR or profile.kills > 0:
        profile_points += 4
    components["player_profile"] = profile_points
    score += profile_points
    if not profile_meta.get("matched"):
        notes.append("player profile not matched")

    map_samples = sum(safe_int(x.get("maps"), 0) or 0 for x in player_map_profiles.values())
    map_coverage = len([x for x in player_map_profiles.values() if (safe_int(x.get("maps"), 0) or 0) >= MIN_MAP_PROFILE_MAPS])
    map_points = min(map_samples, 35) / 35 * 10 + min(map_coverage, 2) / 2 * 6
    components["player_map_splits"] = map_points
    score += map_points
    if map_coverage < 2:
        notes.append("one or more player map splits missing/small")

    match_points = (5 if match_url else 0) + (5 if match_context.get("format") in {"BO3", "BO5"} else 0)
    components["match_verification"] = match_points
    score += match_points
    if not match_url:
        notes.append("match URL not matched")
    if match_context.get("format") not in {"BO3", "BO5"}:
        notes.append("match format unverified/invalid")

    veto_scenarios = map_meta.get("scenarios") or []
    veto_method = str(map_meta.get("method") or "")
    veto_points = 5 * clamp(map_confidence / 100, 0, 1)
    if "confirmed" in veto_method:
        veto_points += 6
    elif "Monte Carlo" in veto_method or "veto" in veto_method.lower():
        veto_points += min(6, 2 + len(veto_scenarios) * 0.45)
    components["map_veto"] = veto_points
    score += veto_points
    if map_confidence < 60:
        notes.append("map-veto uncertainty")

    role_points = 6 * clamp(role_confidence, 0, 1)
    if profile.awp_kill_share is not None or profile.opening_kpr is not None:
        role_points += 2
    components["role"] = role_points
    score += role_points
    if role_confidence < 0.55:
        notes.append("role not strongly verified")

    lineup = match_context.get("lineup_names") or []
    lineup_points = 0.0
    if lineup and max([name_similarity(profile.player, x) for x in lineup] or [0]) >= 0.80:
        lineup_points = 5
    elif lineup:
        notes.append("player not found in listed lineup")
    else:
        notes.append("lineup unverified")
    components["lineup"] = lineup_points
    score += lineup_points

    team_profile = opponent_meta.get("team_profile") or {}
    current_roster_maps = safe_int(context_meta.get("current_roster_maps"), 0) or 0
    roster_stability = safe_float(context_meta.get("roster_stability"), None)
    roster_points = min(current_roster_maps, 12) / 12 * 6
    if roster_stability is not None:
        roster_points += 4 * clamp(roster_stability, 0, 1)
    components["roster_history"] = roster_points
    score += roster_points
    if current_roster_maps < MIN_CURRENT_ROSTER_MAPS:
        notes.append("limited current-roster map history")

    opp_profile = opponent_meta.get("opponent_profile") or {}
    opponent_samples = safe_int(opp_profile.get("mapstats_samples"), 0) or 0
    opponent_points = min(opponent_samples, 6) / 6 * 6
    if safe_float(opp_profile.get("deaths_allowed_per_round"), None) is not None:
        opponent_points += 3
    components["opponent_matchup"] = opponent_points
    score += opponent_points
    if opponent_samples < 2:
        notes.append("thin opponent kill-environment sample")

    environment_points = 2 if match_context.get("environment") in {"LAN", "ONLINE"} else 0
    if match_context.get("event_tier") not in {"", "LOW/UNKNOWN", None}:
        environment_points += 2
    components["match_environment"] = environment_points
    score += environment_points

    market_points = min(max(market_source_count - 1, 0), 2) * 1.5
    components["market_consensus"] = market_points
    score += market_points

    if match_context.get("standin_warning"):
        score -= 14
        notes.append("stand-in/replacement warning")
    if match_context.get("postponed"):
        score -= 35
        notes.append("match status risk")
    if profile.data_warnings:
        score -= min(7, len(profile.data_warnings) * 1.25)
    return int(round(clamp(score, 0, 100))), notes, {k: round(v, 2) for k, v in components.items()}


def classify_play_deep(
    lean: str,
    probability: float,
    edge: float,
    data_score: int,
    profile: PlayerStats,
    match_context: Dict[str, Any],
    map_confidence: float,
    market_agreement: Optional[bool],
    player_map_profiles: Dict[str, Dict[str, Any]],
    opponent_meta: Dict[str, Any],
    context_meta: Dict[str, Any],
) -> Tuple[str, str, List[str]]:
    flags: List[str] = []
    hard_pass = False
    if match_context.get("format") == "BO1":
        hard_pass = True
        flags.append("INVALID BO1 FORMAT")
    if match_context.get("standin_warning"):
        hard_pass = True
        flags.append("STAND-IN / ROSTER RISK")
    if match_context.get("postponed"):
        hard_pass = True
        flags.append("MATCH STATUS RISK")
    if profile.source == "NO PROFILE":
        hard_pass = True
        flags.append("NO PLAYER PROFILE")
    map_samples = sum(safe_int(x.get("maps"), 0) or 0 for x in player_map_profiles.values())
    map_coverage = len([x for x in player_map_profiles.values() if (safe_int(x.get("maps"), 0) or 0) >= MIN_MAP_PROFILE_MAPS])
    opponent_samples = safe_int((opponent_meta.get("opponent_profile") or {}).get("mapstats_samples"), 0) or 0
    roster_maps = safe_int(context_meta.get("current_roster_maps"), 0) or 0
    roster_stability = safe_float(context_meta.get("roster_stability"), None)
    if profile.maps < MIN_PROFILE_MAPS:
        flags.append("SMALL OVERALL SAMPLE")
    if map_coverage < 2:
        flags.append("MAP SPLIT RISK")
    if map_confidence < MIN_MAP_CONFIDENCE:
        flags.append("LOW VETO CONFIDENCE")
    if opponent_samples < 2:
        flags.append("THIN OPPONENT SAMPLE")
    if roster_maps < MIN_CURRENT_ROSTER_MAPS:
        flags.append("NEW/UNVERIFIED ROSTER")
    if roster_stability is not None and roster_stability < 0.45:
        flags.append("LOW ROSTER CONTINUITY")
    if market_agreement is False:
        flags.append("MARKET DISAGREES")
    abs_edge = abs(edge)
    if hard_pass:
        return "PASS", "🚫 PASS", flags
    official_data_ok = map_coverage >= 1 and opponent_samples >= 1 and roster_maps >= 2
    if (
        probability >= MIN_OFFICIAL_PROB and abs_edge >= MIN_OFFICIAL_EDGE and
        data_score >= MIN_OFFICIAL_DATA_SCORE and profile.maps >= MIN_OFFICIAL_PROFILE_MAPS and
        map_confidence >= MIN_MAP_CONFIDENCE and market_agreement is not False and official_data_ok
    ):
        return "OFFICIAL", "🔥 OFFICIAL PLAY", flags
    if probability >= MIN_PLAYABLE_PROB and abs_edge >= MIN_PLAYABLE_EDGE and data_score >= MIN_PLAYABLE_DATA_SCORE:
        return "PLAYABLE", "✅ PLAYABLE", flags
    if probability >= MIN_TRACK_PROB and abs_edge >= 0.65:
        return "TRACK", "⚠️ TRACK ONLY", flags
    return "PASS", "🚫 PASS", flags


def classify_play(
    lean: str,
    probability: float,
    edge: float,
    data_score: int,
    profile: PlayerStats,
    match_context: Dict[str, Any],
    map_confidence: float,
    market_agreement: Optional[bool],
) -> Tuple[str, str, List[str]]:
    flags: List[str] = []
    hard_pass = False
    if match_context.get("format") == "BO1":
        hard_pass = True
        flags.append("INVALID BO1 FORMAT")
    if match_context.get("standin_warning"):
        hard_pass = True
        flags.append("STAND-IN RISK")
    if match_context.get("postponed"):
        hard_pass = True
        flags.append("MATCH STATUS RISK")
    if profile.source == "NO PROFILE":
        hard_pass = True
        flags.append("NO PLAYER PROFILE")
    if profile.maps < MIN_PROFILE_MAPS:
        flags.append("SMALL MAP SAMPLE")
    if map_confidence < MIN_MAP_CONFIDENCE:
        flags.append("LOW MAP CONFIDENCE")
    if market_agreement is False:
        flags.append("MARKET DISAGREES")
    abs_edge = abs(edge)
    if hard_pass:
        return "PASS", "🚫 PASS", flags
    if (
        probability >= MIN_OFFICIAL_PROB and abs_edge >= MIN_OFFICIAL_EDGE and
        data_score >= MIN_OFFICIAL_DATA_SCORE and profile.maps >= MIN_OFFICIAL_PROFILE_MAPS and
        map_confidence >= MIN_MAP_CONFIDENCE and market_agreement is not False
    ):
        return "OFFICIAL", "🔥 OFFICIAL PLAY", flags
    if probability >= MIN_PLAYABLE_PROB and abs_edge >= MIN_PLAYABLE_EDGE and data_score >= MIN_PLAYABLE_DATA_SCORE:
        return "PLAYABLE", "✅ PLAYABLE", flags
    if probability >= MIN_TRACK_PROB and abs_edge >= 0.65:
        return "TRACK", "⚠️ TRACK ONLY", flags
    return "PASS", "🚫 PASS", flags


def build_projection_for_prop(
    prop: Dict[str, Any],
    long_table: Dict[str, Dict[str, Any]],
    medium_table: Dict[str, Dict[str, Any]],
    recent_table: Dict[str, Dict[str, Any]],
    deep_enabled: bool = True,
) -> Dict[str, Any]:
    player = str(prop.get("player", "")).strip()
    line = safe_float(prop.get("line"), None)
    if not player or line is None:
        return {**prop, "error": "Missing player or real line", "status": "PASS"}

    profile, profile_meta = build_player_profile(player, long_table, medium_table, recent_table)
    team = str(prop.get("team") or profile.team or "")
    opponent = str(prop.get("opponent") or "")
    match_url = str(prop.get("match_url") or "")
    discovery_meta: Dict[str, Any] = {}
    if not match_url:
        match_url, discovery_meta = discover_hltv_match(team, opponent, player)
    context, context_status = fetch_match_context(match_url) if match_url else ({}, {"ok": False, "warning": "unmatched"})

    # Recover missing team/opponent names from the verified match page.
    context_team_names = [str(x.get("name") or "") for x in (context.get("teams") or [])[:2]]
    if context_team_names:
        if not team:
            best = max(context_team_names, key=lambda x: name_similarity(profile.team, x)) if profile.team else context_team_names[0]
            team = best
        if not opponent and len(context_team_names) >= 2:
            opponent = next((x for x in context_team_names if not _team_name_matches(team, x)), context_team_names[1])

    context, deep_status = enrich_match_context(context, deep_enabled=deep_enabled)
    likely_maps, map_confidence, map_meta = infer_likely_maps(context)
    expected_rounds, rounds_sd, rounds_meta = project_expected_rounds(context, likely_maps, map_meta, team, opponent)
    role, role_confidence, role_method = get_player_role(player, profile)
    player_map_profiles, player_map_status = build_player_map_profiles(profile, likely_maps) if deep_enabled else ({}, {"ok": False, "message": "deep data disabled"})
    matchup_factor, matchup_meta = opponent_kpr_factor(profile, context, team, opponent, likely_maps, role)
    context_factor, context_variance, context_meta = match_context_adjustment(profile, context, team, opponent)

    learning_shift, learning_meta = learning_adjustment(player, role, float(line), likely_maps, context.get("event_tier", ""))
    sim = simulation_projection_deep(
        player=player,
        line=float(line),
        base_kpr=profile.kpr,
        player_map_profiles=player_map_profiles,
        rounds_meta=rounds_meta,
        opponent_factor=matchup_factor,
        opponent_meta=matchup_meta,
        context_mean_factor=context_factor,
        context_variance_multiplier=context_variance,
        profile_maps=profile.maps,
        role=role,
        learning_shift=learning_shift,
    )

    projection_before_learning = sim["projection"] - learning_shift
    edge = sim["projection"] - float(line)
    lean = "OVER" if edge > 0 else "UNDER"
    probability = sim["over_prob"] if lean == "OVER" else sim["under_prob"]
    saved_odds = get_saved_odds(player, float(line))
    over_nv, under_nv = no_vig_probs(safe_float(saved_odds.get("over_odds"), None), safe_float(saved_odds.get("under_odds"), None))
    market_prob = over_nv if lean == "OVER" else under_nv
    consensus_line = safe_float(prop.get("consensus_line"), None)
    market_source_count = safe_int(prop.get("market_source_count"), 0) or 0
    consensus_edge = (sim["projection"] - consensus_line) if consensus_line is not None else None
    if market_prob is not None:
        market_agreement: Optional[bool] = market_prob >= 0.50
        market_edge = probability - market_prob
        market_method = "sportsbook no-vig"
    elif consensus_line is not None and market_source_count >= 2:
        market_agreement = (consensus_edge >= 0 and lean == "OVER") or (consensus_edge <= 0 and lean == "UNDER")
        market_edge = None
        market_method = "multi-board line consensus"
    else:
        market_agreement = None
        market_edge = None
        market_method = "single-board/no odds"

    data_score, data_notes, data_components = calculate_data_score_deep(
        profile, profile_meta, context, map_confidence, role_confidence, match_url,
        player_map_profiles, map_meta, matchup_meta, context_meta, market_source_count,
    )
    status, status_label, flags = classify_play_deep(
        lean, probability, edge, data_score, profile, context, map_confidence,
        market_agreement, player_map_profiles, matchup_meta, context_meta,
    )
    movement = line_movement(player, prop.get("market", "Maps 1-2 Kills"), prop.get("start_time", ""), float(line))
    lineup_names = context.get("lineup_names") or []
    lineup_verified = bool(lineup_names and max([name_similarity(player, x) for x in lineup_names] or [0]) >= 0.80)
    opponent_profile = matchup_meta.get("opponent_profile") or {}
    team_profile = matchup_meta.get("team_profile") or {}

    return {
        **prop,
        "team": team,
        "opponent": opponent,
        "match_url": match_url,
        "projection": round(sim["projection"], 2),
        "projection_before_learning": round(projection_before_learning, 2),
        "median": round(sim["median"], 1),
        "line": float(line),
        "edge": round(edge, 2),
        "abs_edge": round(abs(edge), 2),
        "lean": lean,
        "probability": round(probability, 4),
        "over_probability": round(sim["over_prob"], 4),
        "under_probability": round(sim["under_prob"], 4),
        "push_probability": round(sim["push_prob"], 4),
        "floor_20": round(sim["floor_20"], 1),
        "ceiling_80": round(sim["ceiling_80"], 1),
        "p10": round(sim["p10"], 1),
        "p90": round(sim["p90"], 1),
        "sim_sd": round(sim["sim_sd"], 2),
        "expected_rounds": round(sim["expected_rounds"], 2),
        "rounds_sd": round(sim["rounds_sd"], 2),
        "adjusted_kpr": round(sim["effective_kpr"], 4),
        "base_kpr": round(profile.kpr, 4),
        "dpr": round(profile.dpr, 4),
        "adr": round(profile.adr, 1),
        "rating": round(profile.rating, 3),
        "kd": round(profile.kd, 3),
        "kast_pct": round(profile.kast_pct, 1) if profile.kast_pct is not None else None,
        "impact": round(profile.impact, 3) if profile.impact is not None else None,
        "rounds_with_kill_pct": round(profile.rounds_with_kill_pct, 1) if profile.rounds_with_kill_pct is not None else None,
        "multi_kill_rate": round(profile.multi_kill_rate, 4) if profile.multi_kill_rate is not None else None,
        "opening_kpr": round(profile.opening_kpr, 4) if profile.opening_kpr is not None else None,
        "opening_deaths_pr": round(profile.opening_deaths_pr, 4) if profile.opening_deaths_pr is not None else None,
        "assists": profile.assists,
        "flash_assists": profile.flash_assists,
        "trade_kills": profile.trade_kills,
        "traded_deaths": profile.traded_deaths,
        "clutches_won": profile.clutches_won,
        "trade_kill_rate": round(profile.trade_kill_rate, 4) if profile.trade_kill_rate is not None else None,
        "traded_death_rate": round(profile.traded_death_rate, 4) if profile.traded_death_rate is not None else None,
        "awp_kill_share": round(profile.awp_kill_share, 4) if profile.awp_kill_share is not None else None,
        "ct_kpr": round(profile.ct_kpr, 4) if profile.ct_kpr is not None else None,
        "t_kpr": round(profile.t_kpr, 4) if profile.t_kpr is not None else None,
        "profile_maps": int(profile.maps),
        "profile_rounds": int(profile.rounds),
        "profile_source": profile.source,
        "profile_href": profile.href,
        "profile_warnings": profile.data_warnings or [],
        "player_map_profiles": player_map_profiles,
        "map_breakdown": sim.get("map_breakdown") or {},
        "role": role,
        "role_confidence": round(role_confidence, 3),
        "role_method": role_method,
        "likely_maps": likely_maps,
        "map_confidence": round(map_confidence, 1),
        "map_scenarios": (map_meta.get("scenarios") or [])[:8],
        "match_format": context.get("format", "UNKNOWN"),
        "environment": context.get("environment", "UNKNOWN"),
        "stage": context.get("stage", ""),
        "event": context.get("event", ""),
        "event_tier": context.get("event_tier", "LOW/UNKNOWN"),
        "world_ranks": context.get("world_ranks", []),
        "lineup_verified": lineup_verified,
        "standin_warning": bool(context.get("standin_warning")),
        "current_roster_maps": safe_int(context_meta.get("current_roster_maps"), 0) or 0,
        "roster_stability": round(safe_float(context_meta.get("roster_stability"), 0) or 0, 3),
        "rest_days": context_meta.get("rest_days"),
        "opponent_deaths_allowed_pr": opponent_profile.get("deaths_allowed_per_round"),
        "opponent_mapstats_samples": safe_int(opponent_profile.get("mapstats_samples"), 0) or 0,
        "team_recent_maps": safe_int(team_profile.get("recent_maps"), 0) or 0,
        "data_score": data_score,
        "data_notes": data_notes,
        "data_components": data_components,
        "status": status,
        "status_label": status_label,
        "flags": flags,
        "over_odds": saved_odds.get("over_odds"),
        "under_odds": saved_odds.get("under_odds"),
        "market_probability": round(market_prob, 4) if market_prob is not None else None,
        "market_edge": round(market_edge, 4) if market_edge is not None else None,
        "market_agreement": market_agreement,
        "market_method": market_method,
        "consensus_line": consensus_line,
        "consensus_edge": round(consensus_edge, 3) if consensus_edge is not None else None,
        "market_source_count": market_source_count,
        "market_source_lines": prop.get("market_source_lines") or {},
        "market_line_spread": prop.get("market_line_spread"),
        "opening_line": movement["opening_line"],
        "line_move": movement["move"],
        "line_observations": movement["observations"],
        "learning_shift": round(learning_shift, 3),
        "model_version": MODEL_VERSION,
        "projection_time": now_iso(),
        "source_meta": {
            "profile": profile_meta,
            "player_map_status": player_map_status,
            "match_discovery": discovery_meta,
            "match_context": context_status,
            "deep_context": deep_status,
            "map": map_meta,
            "rounds": rounds_meta,
            "matchup": matchup_meta,
            "context_adjustment": context_meta,
            "learning": learning_meta,
        },
    }


def build_full_board(props: List[Dict[str, Any]], deep_enabled: bool = True) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if not props:
        return [], {"profiles": {}, "status": "No real lines"}
    long_table, long_status = fetch_hltv_player_table(180)
    medium_table, medium_status = fetch_hltv_player_table(60)
    recent_table, recent_status = fetch_hltv_player_table(20)
    results: List[Dict[str, Any]] = []
    # Keep concurrency low to be respectful and reduce blocking. Cached global
    # tables make most work local; individual profiles are cached for four hours.
    max_workers = min(5, max(1, len(props)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(build_projection_for_prop, prop, long_table, medium_table, recent_table, deep_enabled): prop
            for prop in props
        }
        for future in as_completed(futures):
            prop = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                results.append({**prop, "error": str(exc), "status": "PASS", "status_label": "🚫 ERROR"})
    status_order = {"OFFICIAL": 0, "PLAYABLE": 1, "TRACK": 2, "PASS": 3}
    results.sort(key=lambda x: (status_order.get(x.get("status"), 9), -safe_float(x.get("probability"), 0), -safe_float(x.get("abs_edge"), 0)))
    return results, {
        "long": long_status, "medium": medium_status, "recent": recent_status,
        "deep_data_enabled": deep_enabled,
        "long_rows": len(long_table), "medium_rows": len(medium_table), "recent_rows": len(recent_table)
    }

# ============================================================
# OFFICIAL SNAPSHOTS / GRADING
# ============================================================


def snapshot_key(row: Dict[str, Any]) -> str:
    return hashlib.md5(
        f"{normalize_name(row.get('player'))}|{row.get('line')}|{row.get('lean')}|{str(row.get('start_time',''))[:16]}|{row.get('source')}".encode()
    ).hexdigest()


def save_official_snapshots(board: List[Dict[str, Any]], include_playable: bool = False) -> Dict[str, int]:
    existing = load_json(PICK_LOG, [])
    if not isinstance(existing, list):
        existing = []
    known = {x.get("snapshot_id") for x in existing}
    added = skipped = 0
    allowed = {"OFFICIAL", "PLAYABLE"} if include_playable else {"OFFICIAL"}
    for row in board:
        if row.get("status") not in allowed:
            continue
        sid = snapshot_key(row)
        if sid in known:
            skipped += 1
            continue
        snap = dict(row)
        snap.update({
            "snapshot_id": sid,
            "saved_at": now_iso(),
            "saved_local_date": local_now().date().isoformat(),
            "graded_result": "PENDING",
            "actual_kills": None,
        })
        existing.append(snap)
        known.add(sid)
        added += 1
    save_json(PICK_LOG, existing, github_backup=bool(get_secret("GITHUB_AUTO_BACKUP", "").lower() in {"1", "true", "yes"}))
    return {"added": added, "skipped": skipped}


def _mapstats_links(match_page: str) -> List[str]:
    links = []
    for href in re.findall(r'href=["\']([^"\']*mapstatsid/\d+/[^"\']+)["\']', match_page, flags=re.I):
        full = urljoin(HLTV_BASE, href)
        if full not in links:
            links.append(full)
    return links


def parse_map_player_kills(page: str, player: str) -> Tuple[Optional[int], Dict[str, Any]]:
    if not page:
        return None, {"matched": False}
    best: Tuple[float, Optional[int], str] = (0.0, None, "")
    for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", page, flags=re.I | re.S):
        player_anchor = re.search(r'href=["\']/stats/players?/\d+/[^"\']+["\'][^>]*>(.*?)</a>', tr, flags=re.I | re.S)
        if not player_anchor:
            player_anchor = re.search(r'href=["\']/player/\d+/[^"\']+["\'][^>]*>(.*?)</a>', tr, flags=re.I | re.S)
        if not player_anchor:
            continue
        candidate = strip_tags(player_anchor.group(1)).replace("\n", " ").strip()
        score = name_similarity(player, candidate)
        if score < 0.75:
            continue
        text = strip_tags(tr).replace("\n", " ")
        kd_match = re.search(r"\b(\d{1,2})\s*[-–]\s*(\d{1,2})\b", text)
        kills = safe_int(kd_match.group(1), None) if kd_match else None
        if kills is None:
            # Standard map table usually has K-D as first numeric pair after name.
            numbers = [safe_int(x) for x in re.findall(r"\b\d{1,2}\b", text)]
            kills = numbers[0] if numbers else None
        if score > best[0] and kills is not None:
            best = (score, kills, candidate)
    return best[1], {"matched": best[1] is not None, "score": round(best[0], 3), "name": best[2]}


def fetch_actual_maps12_kills(match_url: str, player: str) -> Tuple[Optional[int], Dict[str, Any]]:
    match_page, match_status = http_get_text(match_url, "HLTV grade match", ttl=4 * 60, timeout=20, allow_stale=False)
    if not match_page:
        return None, {"ok": False, "match_status": match_status}
    links = _mapstats_links(match_page)
    if len(links) < 2:
        return None, {"ok": False, "message": "Fewer than two completed map-stat links", "links": links}
    kills_total = 0
    details = []
    for map_url in links[:2]:
        page, status = http_get_text(map_url, "HLTV grade map", ttl=4 * 60, timeout=20, allow_stale=False)
        kills, meta = parse_map_player_kills(page or "", player)
        details.append({"url": map_url, "kills": kills, "meta": meta, "status": status})
        if kills is None:
            return None, {"ok": False, "message": "Player not matched on one of first two maps", "details": details}
        kills_total += kills
    return kills_total, {"ok": True, "details": details, "map_links": links[:2]}


def grade_result(lean: str, line: float, actual: float) -> str:
    if actual == line:
        return "PUSH"
    if lean == "OVER":
        return "WIN" if actual > line else "LOSS"
    return "WIN" if actual < line else "LOSS"


def grade_pending_automatically() -> Dict[str, Any]:
    picks = load_json(PICK_LOG, [])
    results = load_json(RESULT_LOG, [])
    if not isinstance(picks, list):
        picks = []
    if not isinstance(results, list):
        results = []
    result_ids = {x.get("snapshot_id") for x in results}
    graded = pending = errors = 0
    diagnostics = []
    for row in picks:
        if row.get("snapshot_id") in result_ids:
            continue
        if row.get("graded_result") in {"WIN", "LOSS", "PUSH"}:
            continue
        match_url = str(row.get("match_url") or "")
        if not match_url:
            pending += 1
            diagnostics.append({"player": row.get("player"), "status": "NO MATCH URL"})
            continue
        actual, meta = fetch_actual_maps12_kills(match_url, str(row.get("player", "")))
        if actual is None:
            pending += 1
            diagnostics.append({"player": row.get("player"), "status": "PENDING", "detail": meta.get("message")})
            continue
        graded_label = grade_result(str(row.get("lean")), float(row.get("line")), float(actual))
        result_row = dict(row)
        result_row.update({
            "actual_kills": int(actual), "graded_result": graded_label,
            "graded_at": now_iso(), "grade_source": "HLTV first two map stat pages",
            "grade_meta": meta,
        })
        results.append(result_row)
        result_ids.add(row.get("snapshot_id"))
        row["actual_kills"] = int(actual)
        row["graded_result"] = graded_label
        row["graded_at"] = now_iso()
        graded += 1
    save_json(PICK_LOG, picks)
    save_json(RESULT_LOG, results, github_backup=bool(get_secret("GITHUB_AUTO_BACKUP", "").lower() in {"1", "true", "yes"}))
    if graded:
        build_learning_profiles(results)
    return {"graded": graded, "pending": pending, "errors": errors, "diagnostics": diagnostics[:100]}


def grade_from_manual_dataframe(df: pd.DataFrame, overwrite: bool = False) -> Dict[str, Any]:
    if df is None or df.empty:
        return {"graded": 0, "unmatched": 0, "message": "No rows"}
    col_map = {normalize_name(c): c for c in df.columns}
    pcol = col_map.get("player") or col_map.get("player name")
    acol = col_map.get("actual kills") or col_map.get("kills") or col_map.get("actual")
    if not pcol or not acol:
        return {"graded": 0, "unmatched": len(df), "message": "Need Player and Actual Kills columns"}
    picks = load_json(PICK_LOG, [])
    results = load_json(RESULT_LOG, [])
    result_ids = {x.get("snapshot_id") for x in results}
    graded = unmatched = 0
    for _, raw in df.iterrows():
        player = str(raw.get(pcol, "")).strip()
        actual = safe_float(raw.get(acol), None)
        line_filter = safe_float(raw.get(col_map.get("line", "")), None) if col_map.get("line") else None
        if not player or actual is None:
            continue
        candidates = []
        for row in picks:
            if not overwrite and row.get("snapshot_id") in result_ids:
                continue
            score = name_similarity(player, row.get("player"))
            if line_filter is not None and abs(float(row.get("line", 0)) - line_filter) > 0.01:
                score -= 0.25
            if score >= 0.78:
                candidates.append((score, row))
        if not candidates:
            unmatched += 1
            continue
        _, row = max(candidates, key=lambda x: x[0])
        graded_label = grade_result(str(row.get("lean")), float(row.get("line")), float(actual))
        result_row = dict(row)
        result_row.update({"actual_kills": actual, "graded_result": graded_label, "graded_at": now_iso(), "grade_source": "manual CSV"})
        if overwrite:
            results = [x for x in results if x.get("snapshot_id") != row.get("snapshot_id")]
        results.append(result_row)
        result_ids.add(row.get("snapshot_id"))
        row.update({"actual_kills": actual, "graded_result": graded_label, "graded_at": now_iso()})
        graded += 1
    save_json(PICK_LOG, picks)
    save_json(RESULT_LOG, results, github_backup=bool(get_secret("GITHUB_AUTO_BACKUP", "").lower() in {"1", "true", "yes"}))
    if graded:
        build_learning_profiles(results)
    return {"graded": graded, "unmatched": unmatched}

# ============================================================
# DATAFRAME / DISPLAY HELPERS
# ============================================================


def board_dataframe(board: List[Dict[str, Any]]) -> pd.DataFrame:
    cols = [
        "status_label", "player", "team", "opponent", "line", "projection", "lean",
        "probability", "edge", "expected_rounds", "adjusted_kpr", "base_kpr",
        "profile_maps", "role", "likely_maps", "map_confidence", "match_format",
        "environment", "event_tier", "current_roster_maps", "roster_stability",
        "opponent_deaths_allowed_pr", "opponent_mapstats_samples", "consensus_line",
        "market_source_count", "data_score", "opening_line", "line_move", "market_probability", "market_edge",
        "source", "start_time", "match_url", "flags"
    ]
    rows = []
    for x in board:
        row = {c: x.get(c) for c in cols}
        row["probability"] = round((safe_float(x.get("probability"), 0) or 0) * 100, 1)
        if x.get("market_probability") is not None:
            row["market_probability"] = round(float(x["market_probability"]) * 100, 1)
        if x.get("market_edge") is not None:
            row["market_edge"] = round(float(x["market_edge"]) * 100, 1)
        row["likely_maps"] = " / ".join(x.get("likely_maps") or []) or "Unconfirmed"
        row["flags"] = " | ".join(x.get("flags") or [])
        rows.append(row)
    return pd.DataFrame(rows)


def _fmt_pct(value: Any) -> str:
    f = safe_float(value, None)
    return "—" if f is None else f"{f * 100:.1f}%"


def _esc(value: Any) -> str:
    return html_lib.escape(str(value if value not in [None, ""] else "—"))


def render_pick_card(row: Dict[str, Any], official_style: bool = False) -> None:
    card_class = "official-card" if official_style else "pick-card"
    lean = row.get("lean", "PASS")
    lean_color = "#00a84f" if lean == "OVER" else "#e00020" if lean == "UNDER" else "#b96d00"
    flags = row.get("flags") or []
    flag_html = "".join(f'<span class="badge badge-warn">{_esc(x)}</span>' for x in flags)
    map_text = " / ".join(row.get("likely_maps") or []) or "Unconfirmed"
    matchup = row.get("matchup") or " @ ".join(x for x in [row.get("team"), row.get("opponent")] if x) or "Match not linked"
    status_class = "badge-dark" if official_style else ("badge-good" if row.get("status") in {"OFFICIAL", "PLAYABLE"} else "badge-warn")
    market_text = "No odds saved"
    edge_value = safe_float(row.get("edge"), None)
    edge_text = "—" if edge_value is None else f"{edge_value:+.2f}"
    line_move_value = safe_float(row.get("line_move"), None)
    line_move_text = "—" if line_move_value is None else f"{line_move_value:+.2f}"
    if row.get("market_probability") is not None:
        market_text = f"{_fmt_pct(row.get('market_probability'))} · {'AGREE' if row.get('market_agreement') else 'DISAGREE'}"
    elif row.get("consensus_line") is not None and safe_int(row.get("market_source_count"), 0) >= 2:
        market_text = f"Line {row.get('consensus_line')} · {safe_int(row.get('market_source_count'), 0)} sources"
    st.markdown(
        f"""
<div class="{card_class}">
  <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap;">
    <div>
      <div class="player-name">{_esc(row.get('player'))}</div>
      <div class="muted">{_esc(matchup)} · {_esc(row.get('market'))} · {_esc(row.get('source'))}</div>
    </div>
    <div><span class="badge {status_class}">{_esc(row.get('status_label'))}</span></div>
  </div>
  <div style="display:grid;grid-template-columns:1.2fr 1fr 1fr;gap:14px;margin-top:15px;align-items:end;">
    <div><div class="metric-label">Projection</div><div class="big-number red">{_esc(row.get('projection'))}</div><div class="muted">Line {_esc(row.get('line'))} · Edge {_esc(edge_text)}</div></div>
    <div><div class="metric-label">Decision</div><div style="font-size:34px;font-weight:950;color:{lean_color}!important;">{_esc(lean)}</div><div class="muted">Model {_fmt_pct(row.get('probability'))}</div></div>
    <div><div class="metric-label">Maps 1-2</div><div style="font-size:23px;font-weight:950;">{_esc(map_text)}</div><div class="muted">Map confidence {_esc(row.get('map_confidence'))}%</div></div>
  </div>
  <div class="metric-grid">
    <div class="metric-box"><div class="metric-label">Expected Rounds</div><div class="metric-value">{_esc(row.get('expected_rounds'))}</div><div class="small-muted">SD {_esc(row.get('rounds_sd'))}</div></div>
    <div class="metric-box"><div class="metric-label">Adjusted KPR</div><div class="metric-value">{_esc(row.get('adjusted_kpr'))}</div><div class="small-muted">Base {_esc(row.get('base_kpr'))}</div></div>
    <div class="metric-box"><div class="metric-label">Role</div><div class="metric-value" style="font-size:17px;">{_esc(row.get('role'))}</div><div class="small-muted">{_esc(row.get('role_method'))}</div></div>
    <div class="metric-box"><div class="metric-label">Current Roster</div><div class="metric-value">{_esc(row.get('current_roster_maps'))} maps</div><div class="small-muted">Stability {_esc(row.get('roster_stability'))}</div></div>
    <div class="metric-box"><div class="metric-label">Opponent DPR Allowed</div><div class="metric-value">{_esc(row.get('opponent_deaths_allowed_pr'))}</div><div class="small-muted">{_esc(row.get('opponent_mapstats_samples'))} map samples</div></div>
    <div class="metric-box"><div class="metric-label">Environment</div><div class="metric-value" style="font-size:17px;">{_esc(row.get('environment'))}</div><div class="small-muted">{_esc(row.get('event_tier'))}</div></div>
    <div class="metric-box"><div class="metric-label">Player Sample</div><div class="metric-value">{_esc(row.get('profile_maps'))} maps</div><div class="small-muted">Rating {_esc(row.get('rating'))}</div></div>
    <div class="metric-box"><div class="metric-label">Data Quality</div><div class="metric-value">{_esc(row.get('data_score'))}/100</div><div class="small-muted">Format {_esc(row.get('match_format'))}</div></div>
    <div class="metric-box"><div class="metric-label">Market Check</div><div class="metric-value" style="font-size:17px;">{_esc(market_text)}</div><div class="small-muted">Line move {_esc(line_move_text)}</div></div>
  </div>
  <div>{flag_html}</div>
</div>
""",
        unsafe_allow_html=True,
    )
    with st.expander(f"Projection breakdown — {row.get('player')}"):
        c1, c2 = st.columns(2)
        with c1:
            st.write({
                "Player profile source": row.get("profile_source"),
                "KPR": row.get("base_kpr"), "Adjusted KPR": row.get("adjusted_kpr"),
                "ADR": row.get("adr"), "K/D": row.get("kd"), "Rating": row.get("rating"),
                "Profile maps": row.get("profile_maps"), "Profile rounds": row.get("profile_rounds"),
                "Role": row.get("role"), "Role confidence": row.get("role_confidence"),
            })
        with c2:
            st.write({
                "Projection": row.get("projection"), "Median": row.get("median"),
                "20% floor": row.get("floor_20"), "80% ceiling": row.get("ceiling_80"),
                "P10": row.get("p10"), "P90": row.get("p90"), "Simulation SD": row.get("sim_sd"),
                "Over probability": _fmt_pct(row.get("over_probability")),
                "Under probability": _fmt_pct(row.get("under_probability")),
                "Push probability": _fmt_pct(row.get("push_probability")),
                "Learning shift": row.get("learning_shift"),
            })
        st.write({
            "Advanced player data": {
                "KAST %": row.get("kast_pct"), "Impact": row.get("impact"),
                "Rounds with kill %": row.get("rounds_with_kill_pct"),
                "Multi-kill rate": row.get("multi_kill_rate"),
                "Opening KPR": row.get("opening_kpr"), "Opening deaths/round": row.get("opening_deaths_pr"),
                "AWP kill share": row.get("awp_kill_share"), "CT KPR": row.get("ct_kpr"), "T KPR": row.get("t_kpr"),
            },
            "Roster/opponent": {
                "Current-roster maps": row.get("current_roster_maps"), "Roster stability": row.get("roster_stability"),
                "Opponent deaths allowed/round": row.get("opponent_deaths_allowed_pr"),
                "Opponent mapstats samples": row.get("opponent_mapstats_samples"), "Rest days": row.get("rest_days"),
            },
            "Market consensus": {
                "Consensus line": row.get("consensus_line"), "Sources": row.get("market_source_count"),
                "Source lines": row.get("market_source_lines"), "Method": row.get("market_method"),
            },
        })
        if row.get("player_map_profiles"):
            st.write("Player map-specific profiles")
            st.dataframe(pd.DataFrame(list(row.get("player_map_profiles", {}).values())), use_container_width=True, hide_index=True)
        if row.get("map_scenarios"):
            st.write("Map-veto scenarios")
            st.dataframe(pd.DataFrame(row.get("map_scenarios") or []), use_container_width=True, hide_index=True)
        if row.get("map_breakdown"):
            st.write("Simulated map breakdown")
            breakdown_rows = [{"Map": k, **v} for k, v in (row.get("map_breakdown") or {}).items()]
            st.dataframe(pd.DataFrame(breakdown_rows), use_container_width=True, hide_index=True)
        st.write("Data-quality components:", row.get("data_components") or {})
        if row.get("match_url"):
            st.markdown(f"[Open matched HLTV page]({row.get('match_url')})")
        st.write("Data notes:", row.get("data_notes") or [])
        st.write("Profile warnings:", row.get("profile_warnings") or [])
        st.json(row.get("source_meta") or {}, expanded=False)

# ============================================================
# SESSION BOARD LOAD
# ============================================================

if "cs2_manual_props" not in st.session_state:
    st.session_state["cs2_manual_props"] = []
if "cs2_board" not in st.session_state:
    st.session_state["cs2_board"] = []
if "cs2_board_status" not in st.session_state:
    st.session_state["cs2_board_status"] = {}
if "cs2_line_source_status" not in st.session_state:
    st.session_state["cs2_line_source_status"] = {}


def load_real_props(use_underdog: bool, use_prizepicks: bool, show_prizepicks_rows: bool = False) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    props: List[Dict[str, Any]] = []
    status: Dict[str, Any] = {}
    if use_underdog:
        rows, meta = fetch_underdog_cs2_board()
        props.extend(rows)
        status["Underdog"] = meta
    if use_prizepicks:
        rows, meta = fetch_prizepicks_cs2_board()
        # Keep separate source rows. A player can have different real lines.
        props.extend(rows)
        status["PrizePicks"] = meta
    props.extend(st.session_state.get("cs2_manual_props") or [])
    dedup = {}
    for prop in props:
        key = (normalize_name(prop.get("player")), float(prop.get("line", 0)), prop.get("source"), str(prop.get("start_time", ""))[:16])
        dedup[key] = prop
    annotated = annotate_market_consensus(list(dedup.values()))
    if use_underdog and use_prizepicks and not show_prizepicks_rows:
        has_underdog = any(str(x.get("source")) == "Underdog" for x in annotated)
        if has_underdog:
            annotated = [x for x in annotated if str(x.get("source")) != "PrizePicks"]
    return annotated, status

# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:
    st.markdown("## 🎯 CS2 Controls")
    st.caption(APP_VERSION)
    use_underdog = st.checkbox("Pull Underdog CS2", value=True)
    use_prizepicks = st.checkbox("Use PrizePicks for free market consensus", value=True)
    show_prizepicks_rows = st.checkbox("Also display PrizePicks rows", value=False)
    deep_data_enabled = st.checkbox("Deep map/veto/roster data", value=DEEP_DATA_ENABLED_DEFAULT)
    show_statuses = st.multiselect(
        "Show play tiers",
        ["OFFICIAL", "PLAYABLE", "TRACK", "PASS"],
        default=["OFFICIAL", "PLAYABLE", "TRACK", "PASS"],
    )
    min_data_filter = st.slider("Minimum displayed data score", 0, 100, 0, 1)
    search_filter = st.text_input("Player/team search", "")
    st.markdown("---")
    st.caption("Real lines only. If a public board blocks the request, use the real-line CSV uploader in Data Manager.")
    refresh_clicked = st.button("🔄 REFRESH REAL BOARD + PROJECTIONS", use_container_width=True, type="primary")
    if st.button("🧹 Clear Streamlit Data Cache", use_container_width=True):
        st.cache_data.clear()
        st.success("Cache cleared.")
    st.markdown("---")
    st.write("Storage")
    st.code(STORAGE_DIR)

# ============================================================
# HERO + REFRESH
# ============================================================

st.markdown(
    f"""
<div class="hero-panel">
  <div class="big-title">ONE WAY PICKZ — CS2</div>
  <div class="sub-title">Maps 1–2 Kill Projection Engine · Real lines · Free public-data pipeline · Official snapshots + grading + learning</div>
  <div style="margin-top:11px;">
    <span class="badge">WHITE + RED EDITION</span>
    <span class="badge">{_esc(MODEL_VERSION)}</span>
    <span class="badge">NO HARDCODED API KEYS</span>
  </div>
</div>
""",
    unsafe_allow_html=True,
)

if refresh_clicked or not st.session_state.get("cs2_board"):
    with st.spinner("Pulling Underdog lines, map splits, veto history, roster continuity, opponent data, and running deep simulations..."):
        props, source_status = load_real_props(use_underdog, use_prizepicks, show_prizepicks_rows)
        update_line_history(props)
        board, board_status = build_full_board(props, deep_enabled=deep_data_enabled)
        st.session_state["cs2_board"] = board
        st.session_state["cs2_board_status"] = board_status
        st.session_state["cs2_line_source_status"] = source_status
        st.session_state["cs2_last_refresh_iso"] = now_iso()

board: List[Dict[str, Any]] = st.session_state.get("cs2_board") or []
filtered_board = [x for x in board if x.get("status") in show_statuses and safe_int(x.get("data_score"), 0) >= min_data_filter]
if search_filter.strip():
    needle = normalize_name(search_filter)
    filtered_board = [x for x in filtered_board if needle in normalize_name(" ".join(str(x.get(k, "")) for k in ["player", "team", "opponent", "matchup", "event"]))]

# Summary metrics
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Real Props", len(board))
c2.metric("Official", sum(x.get("status") == "OFFICIAL" for x in board))
c3.metric("Playable", sum(x.get("status") == "PLAYABLE" for x in board))
c4.metric("Track", sum(x.get("status") == "TRACK" for x in board))
c5.metric("Average Data", f"{np.mean([x.get('data_score',0) for x in board]):.0f}/100" if board else "—")
last_refresh_text = st.session_state.get("cs2_last_refresh_iso") or "—"
if last_refresh_text != "—":
    try:
        parsed_refresh = datetime.fromisoformat(str(last_refresh_text).replace("Z", "+00:00"))
        last_refresh_text = parsed_refresh.astimezone(local_now().tzinfo).strftime("%I:%M %p")
    except Exception:
        pass
c6.metric("Last Refresh", last_refresh_text)

if not board:
    st.warning(
        "No active real Maps 1–2 kill lines were found. The app does not create fake lines. "
        "Check **Debug + Settings → Source Status**. If Underdog blocked Railway or has no CS2 board, "
        "open **Data Manager** to upload/paste the current real board, then refresh projections."
    )
    ud_status = (st.session_state.get("cs2_line_source_status") or {}).get("Underdog") or {}
    if ud_status:
        st.caption(f"Underdog pull status: ok={ud_status.get('ok')} · rows={ud_status.get('rows', 0)} · provider endpoint attempts={len(ud_status.get('statuses') or [])}")

# ============================================================
# MAIN TABS
# ============================================================

tab_live, tab_official, tab_saved, tab_grade, tab_data, tab_debug = st.tabs([
    "🎯 Live Projections",
    "🔥 Official Board",
    "📌 Saved Board",
    "✅ Grading + Learning",
    "🧰 Data Manager",
    "🔍 Debug + Settings",
])

with tab_live:
    st.markdown('<div class="section-title-pro">Live Maps 1–2 Kill Board</div>', unsafe_allow_html=True)
    st.caption("Ranked by Official → Playable → Track → Pass, then by model probability and projection edge.")
    if filtered_board:
        st.dataframe(board_dataframe(filtered_board), use_container_width=True, hide_index=True)
        st.download_button(
            "Download current projection CSV",
            data=board_dataframe(filtered_board).to_csv(index=False).encode("utf-8"),
            file_name=f"cs2_projection_board_{local_now().date().isoformat()}.csv",
            mime="text/csv",
            use_container_width=True,
        )
        st.markdown('<div class="section-title-pro">Mobile Projection Cards</div>', unsafe_allow_html=True)
        for row in filtered_board:
            render_pick_card(row, official_style=row.get("status") == "OFFICIAL")
    else:
        st.info("No rows match the current filters.")

with tab_official:
    official = [x for x in board if x.get("status") == "OFFICIAL"]
    playable = [x for x in board if x.get("status") == "PLAYABLE"]
    st.markdown('<div class="section-title-pro">Strict Official Plays</div>', unsafe_allow_html=True)
    st.caption(
        f"Initial gate: ≥{MIN_OFFICIAL_PROB*100:.1f}% model probability, ≥{MIN_OFFICIAL_EDGE:.1f} kills edge, "
        f"data score ≥{MIN_OFFICIAL_DATA_SCORE}, ≥{MIN_OFFICIAL_PROFILE_MAPS} profile maps, no hard risk flag."
    )
    if official:
        for row in official:
            render_pick_card(row, official_style=True)
    else:
        st.info("No plays passed every Official gate. That is a valid slate outcome.")
    st.markdown('<div class="section-title-pro">Playable — Below Official Gate</div>', unsafe_allow_html=True)
    if playable:
        for row in playable:
            render_pick_card(row, official_style=False)
    else:
        st.info("No additional Playable rows.")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("💾 SAVE OFFICIAL PRE-MATCH SNAPSHOT", use_container_width=True, type="primary"):
            out = save_official_snapshots(board, include_playable=False)
            st.success(f"Saved {out['added']} new Official rows; skipped {out['skipped']} duplicates.")
    with c2:
        if st.button("💾 SAVE OFFICIAL + PLAYABLE SNAPSHOT", use_container_width=True):
            out = save_official_snapshots(board, include_playable=True)
            st.success(f"Saved {out['added']} new rows; skipped {out['skipped']} duplicates.")

with tab_saved:
    st.markdown('<div class="section-title-pro">Saved Before-Game Snapshots</div>', unsafe_allow_html=True)
    picks = load_json(PICK_LOG, [])
    if picks:
        saved_df = pd.DataFrame(picks)
        display_cols = [c for c in ["saved_at", "player", "team", "opponent", "line", "projection", "lean", "probability", "status", "expected_rounds", "adjusted_kpr", "likely_maps", "data_score", "graded_result", "actual_kills", "match_url"] if c in saved_df.columns]
        st.dataframe(saved_df[display_cols].sort_values("saved_at", ascending=False), use_container_width=True, hide_index=True)
        st.download_button("Download saved snapshots", saved_df.to_csv(index=False).encode(), "cs2_official_snapshots.csv", "text/csv", use_container_width=True)
    else:
        st.info("No saved snapshots yet. Save Official rows before matches begin.")

with tab_grade:
    st.markdown('<div class="section-title-pro">Automatic Maps 1–2 Grading</div>', unsafe_allow_html=True)
    st.caption("The grader opens the saved HLTV match page, reads the first two completed map-stat pages, sums the player’s kills, then updates cautious learning profiles.")
    if st.button("✅ GRADE FINISHED MATCHES + UPDATE LEARNING", use_container_width=True, type="primary"):
        with st.spinner("Checking saved matches and first-two-map statistics..."):
            diagnostic = grade_pending_automatically()
        if diagnostic.get("graded"):
            st.success(f"Graded {diagnostic['graded']} saved plays. Pending: {diagnostic['pending']}.")
        else:
            st.warning(f"No new rows graded. Pending/unmatched: {diagnostic.get('pending', 0)}.")
        st.write(diagnostic)

    st.markdown('<div class="section-title-pro">Manual Result Fallback</div>', unsafe_allow_html=True)
    st.code("Player,Actual Kills,Line\nZywOo,38,34.5", language="csv")
    manual_result_upload = st.file_uploader("Upload actual results CSV", type=["csv"], key="cs2_results_upload")
    manual_result_text = st.text_area("Or paste actual results CSV", height=120, key="cs2_results_text")
    overwrite_results = st.checkbox("Overwrite an already graded matching snapshot", value=False)
    result_df = pd.DataFrame()
    try:
        if manual_result_upload is not None:
            result_df = pd.read_csv(manual_result_upload)
        elif manual_result_text.strip():
            result_df = pd.read_csv(io.StringIO(manual_result_text.strip()))
    except Exception as exc:
        st.error(f"Result CSV parse error: {exc}")
    if not result_df.empty:
        st.dataframe(result_df, use_container_width=True, hide_index=True)
    if st.button("🧾 GRADE MANUAL RESULTS", use_container_width=True):
        out = grade_from_manual_dataframe(result_df, overwrite=overwrite_results)
        if out.get("graded"):
            st.success(f"Graded {out['graded']} rows; unmatched {out['unmatched']}.")
        else:
            st.warning(str(out))

    results = load_json(RESULT_LOG, [])
    st.markdown('<div class="section-title-pro">Performance + Learning</div>', unsafe_allow_html=True)
    if results:
        rdf = pd.DataFrame(results)
        finished = rdf[rdf["graded_result"].isin(["WIN", "LOSS", "PUSH"])] if "graded_result" in rdf.columns else pd.DataFrame()
        wins = int((finished["graded_result"] == "WIN").sum()) if not finished.empty else 0
        losses = int((finished["graded_result"] == "LOSS").sum()) if not finished.empty else 0
        pushes = int((finished["graded_result"] == "PUSH").sum()) if not finished.empty else 0
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Graded", len(finished))
        c2.metric("Record", f"{wins}-{losses}-{pushes}")
        c3.metric("Win Rate", f"{wins/max(wins+losses,1)*100:.1f}%")
        if "actual_kills" in finished.columns and "projection_before_learning" in finished.columns:
            err = pd.to_numeric(finished["actual_kills"], errors="coerce") - pd.to_numeric(finished["projection_before_learning"], errors="coerce")
            c4.metric("Projection MAE", f"{err.abs().mean():.2f}")
            c5.metric("Model Bias", f"{err.mean():+.2f}")
        else:
            c4.metric("Projection MAE", "—")
            c5.metric("Model Bias", "—")
        useful_cols = [c for c in ["graded_at", "player", "line", "projection", "lean", "actual_kills", "graded_result", "status", "probability", "data_score", "role", "likely_maps"] if c in rdf.columns]
        st.dataframe(rdf[useful_cols].sort_values("graded_at", ascending=False), use_container_width=True, hide_index=True)
        learning = build_learning_profiles(results)
        st.subheader("Capped learning profiles")
        st.json(learning, expanded=False)
        st.download_button("Download graded history", rdf.to_csv(index=False).encode(), "cs2_graded_history.csv", "text/csv", use_container_width=True)
    else:
        st.info("Learning begins after saved pre-match projections are graded.")

with tab_data:
    st.markdown('<div class="section-title-pro">Real Board Import Fallback</div>', unsafe_allow_html=True)
    st.caption("Use this only when the public pick’em endpoint blocks Railway. These must be real current lines; the engine never manufactures lines.")
    st.code("Player,Team,Opponent,Line,Start Time,Match URL,Source\nZywOo,Vitality,G2,34.5,2026-07-13T12:00:00Z,https://www.hltv.org/matches/...,Underdog", language="csv")
    manual_board_file = st.file_uploader("Upload current real CS2 board CSV", type=["csv"], key="cs2_board_upload")
    manual_board_text = st.text_area("Or paste current real board CSV", height=140, key="cs2_board_text")
    manual_board_df = pd.DataFrame()
    try:
        if manual_board_file is not None:
            manual_board_df = pd.read_csv(manual_board_file)
        elif manual_board_text.strip():
            manual_board_df = pd.read_csv(io.StringIO(manual_board_text.strip()))
    except Exception as exc:
        st.error(f"Board CSV parse error: {exc}")
    if not manual_board_df.empty:
        st.dataframe(manual_board_df, use_container_width=True, hide_index=True)
    c1, c2 = st.columns(2)
    with c1:
        if st.button("ADD REAL BOARD ROWS", use_container_width=True):
            rows = parse_manual_board_dataframe(manual_board_df)
            st.session_state["cs2_manual_props"] = rows
            st.success(f"Loaded {len(rows)} real manual rows. Press Refresh in the sidebar.")
    with c2:
        if st.button("CLEAR MANUAL BOARD ROWS", use_container_width=True):
            st.session_state["cs2_manual_props"] = []
            st.success("Manual rows cleared.")

    st.markdown('<div class="section-title-pro">Manual Sportsbook Odds / No-Vig</div>', unsafe_allow_html=True)
    st.code("Player,Line,Over Odds,Under Odds,Book\nZywOo,34.5,-115,-105,Book", language="csv")
    odds_text = st.text_area("Paste sportsbook odds CSV", height=120, key="cs2_odds_text")
    odds_df = pd.DataFrame()
    if odds_text.strip():
        try:
            odds_df = pd.read_csv(io.StringIO(odds_text.strip()))
            st.dataframe(odds_df, use_container_width=True, hide_index=True)
        except Exception as exc:
            st.error(f"Odds CSV parse error: {exc}")
    if st.button("SAVE MANUAL ODDS", use_container_width=True):
        count = save_manual_odds_rows(odds_df)
        st.success(f"Saved odds for {count} player-lines. Refresh projections to apply no-vig checks.")

    st.markdown('<div class="section-title-pro">Role Overrides</div>', unsafe_allow_html=True)
    st.caption("A role override improves reliability when the free source does not provide a verified role.")
    role_player = st.text_input("Player for role override")
    role_value = st.selectbox("Role", ["Primary AWPer", "Secondary AWPer", "Star Rifler", "Entry Fragger", "Lurker", "Anchor", "Support", "IGL", "Rifler"])
    if st.button("SAVE ROLE OVERRIDE", use_container_width=True):
        data = load_json(ROLE_OVERRIDES_FILE, {})
        data[normalize_name(role_player)] = {"role": role_value, "confidence": 1.0, "saved_at": now_iso()}
        save_json(ROLE_OVERRIDES_FILE, data)
        st.success(f"Saved {role_value} for {role_player}.")

    st.markdown('<div class="section-title-pro">Player Profile Override CSV</div>', unsafe_allow_html=True)
    st.caption("Optional fallback for a player missing from public stats. Accepted fields: Player, Team, Maps, Rounds, KPR, DPR, ADR, Rating, KD, HS Pct.")
    profile_file = st.file_uploader("Upload player profile override CSV", type=["csv"], key="cs2_profile_upload")
    if profile_file is not None:
        try:
            pdf = pd.read_csv(profile_file)
            st.dataframe(pdf, use_container_width=True, hide_index=True)
            if st.button("SAVE PLAYER PROFILE OVERRIDES", use_container_width=True):
                current = load_json(PLAYER_OVERRIDES_FILE, {})
                cmap = {normalize_name(c): c for c in pdf.columns}
                pcol = cmap.get("player") or cmap.get("player name")
                for _, rr in pdf.iterrows():
                    player = str(rr.get(pcol, "")).strip() if pcol else ""
                    if not player:
                        continue
                    current[normalize_name(player)] = {
                        "player": player,
                        "team": rr.get(cmap.get("team", ""), ""),
                        "maps": safe_int(rr.get(cmap.get("maps", "")), 0),
                        "rounds": safe_int(rr.get(cmap.get("rounds", "")), 0),
                        "kpr": safe_float(rr.get(cmap.get("kpr", "")), None),
                        "dpr": safe_float(rr.get(cmap.get("dpr", "")), None),
                        "adr": safe_float(rr.get(cmap.get("adr", "")), None),
                        "rating": safe_float(rr.get(cmap.get("rating", "")), None),
                        "kd": safe_float(rr.get(cmap.get("kd", "")), None),
                        "hs_pct": safe_float(rr.get(cmap.get("hs pct", "")), None),
                        "saved_at": now_iso(),
                    }
                save_json(PLAYER_OVERRIDES_FILE, current)
                st.success("Player profile overrides saved.")
        except Exception as exc:
            st.error(f"Profile CSV error: {exc}")

    st.markdown('<div class="section-title-pro">Optional Free PandaScore Connection</div>', unsafe_allow_html=True)
    panda_rows, panda_status = fetch_pandascore_upcoming()
    if panda_status.get("configured"):
        st.success(f"PandaScore configured. Upcoming rows: {len(panda_rows)}")
        if panda_rows:
            preview = []
            for x in panda_rows[:50]:
                preview.append({
                    "id": x.get("id"), "name": x.get("name"), "begin_at": x.get("begin_at"),
                    "status": x.get("status"), "tournament": (x.get("tournament") or {}).get("name")
                })
            st.dataframe(pd.DataFrame(preview), use_container_width=True, hide_index=True)
    else:
        st.info("No PandaScore token configured. The core app still runs through no-key public sources. Add PANDASCORE_TOKEN as a Railway variable for an optional free schedule/roster fallback.")

with tab_debug:
    st.markdown('<div class="section-title-pro">Source Status</div>', unsafe_allow_html=True)
    st.write("Line source status")
    st.json(st.session_state.get("cs2_line_source_status") or {}, expanded=False)
    st.write("Projection source status")
    st.json(st.session_state.get("cs2_board_status") or {}, expanded=False)
    if board:
        debug_rows = []
        for row in board:
            debug_rows.append({
                "Player": row.get("player"), "Source": row.get("source"), "Profile Source": row.get("profile_source"),
                "Profile Maps": row.get("profile_maps"), "Match URL": bool(row.get("match_url")),
                "Format": row.get("match_format"), "Maps": " / ".join(row.get("likely_maps") or []),
                "Map Conf": row.get("map_confidence"), "Role": row.get("role"), "Data Score": row.get("data_score"),
                "Flags": " | ".join(row.get("flags") or []), "Error": row.get("error", ""),
            })
        st.dataframe(pd.DataFrame(debug_rows), use_container_width=True, hide_index=True)

    st.markdown('<div class="section-title-pro">Recent Requests / Errors</div>', unsafe_allow_html=True)
    requests_log = load_json(REQUEST_LOG_FILE, [])
    if requests_log:
        st.dataframe(pd.DataFrame(requests_log[-150:]), use_container_width=True, hide_index=True)
    else:
        st.info("No request log entries yet.")

    st.markdown('<div class="section-title-pro">Security + Deployment Variables</div>', unsafe_allow_html=True)
    variables = [
        {"Variable": "CS2_DATA_DIR", "Required": "Recommended", "Purpose": "Railway volume path; use /data/cs2_engine"},
        {"Variable": "UNDERDOG_URL_OVERRIDE", "Required": "No", "Purpose": "Replacement public board endpoint if Underdog changes versions"},
        {"Variable": "PANDASCORE_TOKEN", "Required": "No", "Purpose": "Optional free schedule/roster fallback"},
        {"Variable": "GITHUB_TOKEN", "Required": "No", "Purpose": "Optional persistent backup"},
        {"Variable": "GITHUB_REPO", "Required": "No", "Purpose": "owner/repository for backup"},
        {"Variable": "GITHUB_BRANCH", "Required": "No", "Purpose": "Defaults to main"},
        {"Variable": "GITHUB_AUTO_BACKUP", "Required": "No", "Purpose": "true to back up after save/grade"},
    ]
    st.dataframe(pd.DataFrame(variables), use_container_width=True, hide_index=True)
    st.caption("No live credential is hardcoded in this file.")

    st.markdown('<div class="section-title-pro">Backup + Maintenance</div>', unsafe_allow_html=True)
    backup_files = [PICK_LOG, RESULT_LOG, LEARNING_FILE, LINE_HISTORY_FILE, MANUAL_ODDS_FILE, ROLE_OVERRIDES_FILE, PLAYER_OVERRIDES_FILE]
    if st.button("☁️ BACK UP ALL CS2 DATA TO GITHUB", use_container_width=True):
        outcomes = [github_backup_file(path) for path in backup_files if os.path.exists(path)]
        st.write(outcomes)
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("Clear request log"):
            save_json(REQUEST_LOG_FILE, [])
            st.success("Request log cleared.")
    with c2:
        if st.button("Rebuild learning profiles"):
            out = build_learning_profiles()
            st.success(f"Learning rebuilt. Global samples: {out.get('global', {}).get('samples', 0)}")
    with c3:
        confirm_clear = st.checkbox("Confirm clear all CS2 logs", value=False)
        if st.button("Clear ALL CS2 logs", disabled=not confirm_clear):
            for path in backup_files + [REQUEST_LOG_FILE]:
                save_json(path, {} if path in [LEARNING_FILE, LINE_HISTORY_FILE, MANUAL_ODDS_FILE, ROLE_OVERRIDES_FILE, PLAYER_OVERRIDES_FILE] else [], force=True)
            st.error("All CS2 logs cleared.")

st.caption(
    "Model note: projections are estimates, not guarantees. Public sources can be delayed, incomplete, or blocked. "
    "The app passes uncertain rows instead of inventing data or lines."
)
