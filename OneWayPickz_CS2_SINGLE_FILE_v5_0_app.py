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
import sqlite3
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from statistics import NormalDist
from urllib.parse import quote, urljoin, urlencode

import numpy as np
import pandas as pd
import requests
import streamlit as st

# ============================================================
# APP / MODEL CONFIGURATION
# ============================================================

APP_NAME = "ONE WAY PICKZ — CS2"
APP_VERSION = "CS2 v5.0 — DIRECT BO3 PROFILE CACHE + PROGRESSIVE BOARD RECOVERY"
MODEL_VERSION = "OWP_CS2_KILLS_M12_4.9"
SEED_VERSION = "CS2_ACCURACY_SEED_2026_07_15_V49"

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
MAX_LEARNING_PROJECTION_SHIFT = 0.40
MAX_LEARNING_PROB_SHIFT = 0.045
SIMULATIONS = 30000
DEEP_PULL_MATCH_LIMIT = int(max(6, min(50, float(os.getenv("CS2_DEEP_MATCH_LIMIT", "30") or 30))))
DEEP_PULL_MAPSTATS_LIMIT = int(max(6, min(50, float(os.getenv("CS2_DEEP_MAPSTATS_LIMIT", "30") or 30))))
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
MIN_M12_KILL_LINE = 12.0
MAX_M12_KILL_LINE = 55.0
DATABASE_SCHEMA_VERSION = 8
MIN_CALIBRATION_PRELIMINARY = 100
MIN_CALIBRATION_USABLE = 300
MIN_CALIBRATION_STRONG = 500
MIN_MARKET_VALUE_EDGE = 0.02
MIN_PLAYER_LEARNING_SAMPLES = 40
MIN_CONTEXT_LEARNING_SAMPLES = 80
MIN_ROLE_LEARNING_SAMPLES = 100
MIN_BLEND_TRAINING_SAMPLES = 30
MIN_ROUND_KILL_TRAINING_ROUNDS = 250
SOURCE_MAX_STALE_SECONDS = {
    "underdog": 0,
    "prizepicks": 0,
    "match": 300,
    "lineup": 300,
    "veto": 120,
    "roster": 4 * 3600,
    "player_form": 24 * 3600,
    "team_maps": 24 * 3600,
    "completed_history": 14 * 24 * 3600,
}
CURRENT_ACTIVE_MAPS = ["Ancient", "Anubis", "Cache", "Dust2", "Inferno", "Mirage", "Nuke"]
AUTO_HARVEST_HISTORY = os.getenv("CS2_AUTO_HARVEST_HISTORY", "true").strip().lower() not in {"0","false","no","off"}

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
PLAYER_DATABASE_FILE = os.path.join(STORAGE_DIR, "player_database.json")
TEAM_DATABASE_FILE = os.path.join(STORAGE_DIR, "team_database.json")
MATCH_DATABASE_FILE = os.path.join(STORAGE_DIR, "match_database.json")
MAP_DATABASE_FILE = os.path.join(STORAGE_DIR, "map_database.json")
VETO_DATABASE_FILE = os.path.join(STORAGE_DIR, "veto_database.json")
ROSTER_DATABASE_FILE = os.path.join(STORAGE_DIR, "roster_database.json")
DATABASE_META_FILE = os.path.join(STORAGE_DIR, "database_meta.json")
PLAYER_ALIAS_FILE = os.path.join(STORAGE_DIR, "player_aliases.json")
HISTORICAL_ASOF_FILE = os.path.join(STORAGE_DIR, "cs2_asof_projection_history.jsonl")
CALIBRATION_FILE = os.path.join(STORAGE_DIR, "cs2_probability_calibration.json")
PATCH_ERAS_FILE = os.path.join(STORAGE_DIR, "cs2_patch_map_pool_eras.json")
ROLE_TIMELINE_FILE = os.path.join(STORAGE_DIR, "cs2_role_timeline.json")
DEMO_DATABASE_FILE = os.path.join(STORAGE_DIR, "cs2_demo_telemetry.json")
BOOK_ODDS_HISTORY_FILE = os.path.join(STORAGE_DIR, "cs2_book_odds_history.json")
SLIP_HISTORY_FILE = os.path.join(STORAGE_DIR, "cs2_slip_history.json")
LIVE_STATE_FILE = os.path.join(STORAGE_DIR, "cs2_live_watch_history.json")
CORE_DB_FILE = os.path.join(STORAGE_DIR, "cs2_core_v42.sqlite3")
DEMO_DROP_DIR = os.path.join(STORAGE_DIR, "incoming_demos")
os.makedirs(DEMO_DROP_DIR, exist_ok=True)

_JSON_LOCK = threading.RLock()

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
        with _JSON_LOCK:
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
        with _JSON_LOCK:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            name = os.path.basename(path)
            if (not force) and name in PROTECTED_FILES and os.path.exists(path):
                old = load_json(path, None)
                old_n, new_n = _payload_len(old), _payload_len(payload)
                if old_n >= 30 and (new_n == 0 or new_n < int(old_n * 0.85)):
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
    except Exception:
        return False



def _sqlite_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(CORE_DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_core_database() -> None:
    with _sqlite_connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS projections (
          snapshot_id TEXT PRIMARY KEY, prop_id TEXT, player TEXT, player_key TEXT,
          match_url TEXT, start_time TEXT, line REAL, lean TEXT,
          raw_probability REAL, probability REAL, status TEXT, veto_state TEXT,
          event_tier TEXT, payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
          graded_result TEXT, actual_kills REAL, graded_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_projection_pending ON projections(graded_result,start_time);
        CREATE INDEX IF NOT EXISTS idx_projection_player ON projections(player_key,start_time);
        CREATE TABLE IF NOT EXISTS demo_events (
          event_hash TEXT PRIMARY KEY, player TEXT, player_key TEXT, map_name TEXT,
          match_id TEXT, round_num INTEGER, match_rounds INTEGER, team TEXT,
          opponent TEXT, opponent_rank REAL, event_tier TEXT, side TEXT, weapon TEXT,
          is_headshot INTEGER, is_opening INTEGER, is_trade INTEGER, event_time TEXT,
          source TEXT, ingested_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_demo_player_map ON demo_events(player_key,map_name,event_time);
        CREATE TABLE IF NOT EXISTS demo_rounds (
          round_hash TEXT PRIMARY KEY, match_id TEXT, map_name TEXT, round_num INTEGER,
          ct_team TEXT, t_team TEXT, winner_team TEXT, winner_side TEXT,
          event_time TEXT, source TEXT, ingested_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_demo_round_team_map ON demo_rounds(map_name,ct_team,t_team,event_time);
        CREATE TABLE IF NOT EXISTS roster_events (
          event_id TEXT PRIMARY KEY, player_key TEXT, player TEXT, team_key TEXT,
          team TEXT, event_type TEXT, effective_at TEXT, confidence REAL,
          source TEXT, payload_json TEXT, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_roster_player ON roster_events(player_key,effective_at);
        CREATE TABLE IF NOT EXISTS model_parameters (
          name TEXT PRIMARY KEY, payload_json TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS entity_snapshots (
          snapshot_key TEXT PRIMARY KEY, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
          as_of TEXT NOT NULL, source TEXT, source_age_seconds REAL, payload_json TEXT NOT NULL,
          checksum TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_entity_snapshots_lookup ON entity_snapshots(entity_type,entity_id,as_of);
        CREATE TABLE IF NOT EXISTS market_ticks (
          tick_id TEXT PRIMARY KEY, prop_id TEXT, player_key TEXT, market TEXT, line REAL,
          source TEXT, observed_at TEXT, start_time TEXT, payload_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_market_ticks_prop ON market_ticks(prop_id,observed_at);
        CREATE TABLE IF NOT EXISTS grading_audit (
          audit_id TEXT PRIMARY KEY, snapshot_id TEXT, match_id TEXT, player_key TEXT,
          map1_id TEXT, map2_id TEXT, map1_name TEXT, map2_name TEXT,
          map1_kills INTEGER, map2_kills INTEGER, total_kills INTEGER,
          void_reason TEXT, confidence REAL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_grading_audit_snapshot ON grading_audit(snapshot_id);
        CREATE TABLE IF NOT EXISTS model_fit_history (
          fit_id TEXT PRIMARY KEY, fit_type TEXT, scope_key TEXT, sample_size INTEGER,
          payload_json TEXT NOT NULL, fitted_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS team_map_observations (
          observation_id TEXT PRIMARY KEY, team_id TEXT NOT NULL, team_name TEXT, match_id TEXT NOT NULL,
          match_url TEXT, map_name TEXT NOT NULL, played_at TEXT, team_score INTEGER, opponent_score INTEGER,
          rounds INTEGER, margin INTEGER, overtime INTEGER, opponent_rank REAL, same_core INTEGER,
          environment TEXT, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_team_map_obs_lookup ON team_map_observations(team_id,map_name,played_at);
        """)


def sqlite_store_entity_snapshot(entity_type: str, entity_id: str, payload: Dict[str, Any], source: str = "", source_age_seconds: Optional[float] = None, as_of: Any = None) -> bool:
    if not entity_type or not entity_id or not isinstance(payload, dict) or not payload:
        return False
    observed = (_parse_iso_datetime(as_of) or datetime.now(timezone.utc)).isoformat()
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    checksum = hashlib.sha256(body.encode("utf-8")).hexdigest()
    raw = f"{entity_type}|{entity_id}|{observed[:16]}|{checksum}"
    key = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    try:
        with _sqlite_connect() as conn:
            conn.execute("""INSERT OR IGNORE INTO entity_snapshots(
                snapshot_key,entity_type,entity_id,as_of,source,source_age_seconds,payload_json,checksum,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (key,entity_type,str(entity_id),observed,source,safe_float(source_age_seconds,None),body,checksum,now_iso()))
        return True
    except Exception:
        return False


def sqlite_latest_entity_snapshot(entity_type: str, entity_id: str, max_age_seconds: Optional[int] = None, as_of: Any = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    cutoff = _parse_iso_datetime(as_of) or datetime.now(timezone.utc)
    try:
        with _sqlite_connect() as conn:
            rec = conn.execute("""SELECT payload_json,as_of,source,source_age_seconds FROM entity_snapshots
                                WHERE entity_type=? AND entity_id=? AND as_of<=? ORDER BY as_of DESC LIMIT 1""",
                               (entity_type,str(entity_id),cutoff.isoformat())).fetchone()
        if not rec:
            return {}, {"ok":False,"warning":"no SQLite snapshot"}
        observed = _parse_iso_datetime(rec["as_of"])
        age = (cutoff-observed).total_seconds() if observed else None
        if max_age_seconds is not None and (age is None or age>max_age_seconds):
            return {}, {"ok":False,"warning":"SQLite snapshot too old","age_seconds":age}
        return json.loads(rec["payload_json"]), {"ok":True,"source":rec["source"] or "SQLite historical snapshot","age_seconds":age,"cache":"sqlite"}
    except Exception as exc:
        return {}, {"ok":False,"warning":str(exc)}


def sqlite_store_market_ticks(props: Sequence[Dict[str, Any]]) -> int:
    added=0
    try:
        with _sqlite_connect() as conn:
            for prop in props:
                line=safe_float(prop.get("line"),None)
                if line is None: continue
                observed=str(prop.get("source_pulled_at") or now_iso())
                raw=f"{prop.get('prop_id')}|{normalize_name(prop.get('player'))}|{prop.get('market')}|{line}|{observed[:19]}|{prop.get('source')}"
                tick_id=hashlib.sha256(raw.encode()).hexdigest()
                cur=conn.execute("""INSERT OR IGNORE INTO market_ticks(tick_id,prop_id,player_key,market,line,source,observed_at,start_time,payload_json)
                                  VALUES(?,?,?,?,?,?,?,?,?)""",
                                 (tick_id,str(prop.get("prop_id") or ""),normalize_name(prop.get("player")),str(prop.get("market") or ""),float(line),str(prop.get("source") or ""),observed,str(prop.get("start_time") or ""),json.dumps(dict(prop),default=str)))
                added += int(cur.rowcount or 0)
    except Exception:
        pass
    return added


def sqlite_store_grading_audit(snapshot_id: str, player: str, meta: Dict[str, Any]) -> None:
    details=list(meta.get("details") or [])
    maps=list(meta.get("map_results") or [])
    map1=details[0] if len(details)>0 else {}; map2=details[1] if len(details)>1 else {}
    result1=maps[0] if len(maps)>0 else {}; result2=maps[1] if len(maps)>1 else {}
    payload=json.dumps(meta,ensure_ascii=False,default=str)
    raw=f"{snapshot_id}|{player}|{meta.get('team_total_kills')}|{meta.get('void_reason')}"
    audit_id=hashlib.sha256(raw.encode()).hexdigest()
    try:
        with _sqlite_connect() as conn:
            conn.execute("""INSERT OR REPLACE INTO grading_audit(audit_id,snapshot_id,match_id,player_key,map1_id,map2_id,map1_name,map2_name,map1_kills,map2_kills,total_kills,void_reason,confidence,payload_json,created_at)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (audit_id,snapshot_id,str(meta.get("match_id") or ""),normalize_name(player),str(map1.get("map_id") or ""),str(map2.get("map_id") or ""),str(result1.get("map") or result1.get("name") or ""),str(result2.get("map") or result2.get("name") or ""),safe_int(map1.get("kills"),None),safe_int(map2.get("kills"),None),safe_int(meta.get("total_kills"),None),str(meta.get("void_reason") or ""),safe_float(meta.get("confidence"),0.0),payload,now_iso()))
    except Exception:
        pass


def save_model_fit(name: str, payload: Dict[str, Any]) -> None:
    try:
        with _sqlite_connect() as conn:
            conn.execute("INSERT OR REPLACE INTO model_parameters(name,payload_json,updated_at) VALUES(?,?,?)",(name,json.dumps(payload,default=str),now_iso()))
            fit_id=hashlib.sha256(f"{name}|{now_iso()}|{payload.get('sample',payload.get('sample_size',0))}".encode()).hexdigest()
            conn.execute("INSERT OR REPLACE INTO model_fit_history(fit_id,fit_type,scope_key,sample_size,payload_json,fitted_at) VALUES(?,?,?,?,?,?)",(fit_id,str(payload.get('fit_type') or name.split(':')[0]),name,safe_int(payload.get('sample',payload.get('sample_size',0)),0),json.dumps(payload,default=str),now_iso()))
    except Exception:
        pass


def load_model_fit(name: str) -> Dict[str, Any]:
    try:
        with _sqlite_connect() as conn:
            rec=conn.execute("SELECT payload_json,updated_at FROM model_parameters WHERE name=?",(name,)).fetchone()
        if rec:
            out=json.loads(rec["payload_json"]); out["updated_at"]=rec["updated_at"]; return out
    except Exception:
        pass
    return {}


def invalidate_model_fits(prefixes: Sequence[str]) -> int:
    deleted=0
    try:
        with _sqlite_connect() as conn:
            for prefix in prefixes:
                cur=conn.execute("DELETE FROM model_parameters WHERE name LIKE ?",(f"{prefix}%",))
                deleted += int(cur.rowcount or 0)
    except Exception:
        pass
    return deleted


def sqlite_store_projection(row: Dict[str, Any]) -> bool:
    if safe_float(row.get("projection"), None) is None or not row.get("match_url"):
        return False
    sid = snapshot_key(row)
    payload = dict(row)
    payload["snapshot_id"] = sid
    created = str(row.get("projection_time") or now_iso())
    try:
        with _sqlite_connect() as conn:
            conn.execute("""INSERT INTO projections(
              snapshot_id,prop_id,player,player_key,match_url,start_time,line,lean,
              raw_probability,probability,status,veto_state,event_tier,payload_json,created_at,
              graded_result,actual_kills,graded_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'PENDING',NULL,NULL)
              ON CONFLICT(snapshot_id) DO UPDATE SET
                payload_json=excluded.payload_json, probability=excluded.probability,
                raw_probability=excluded.raw_probability, status=excluded.status
            """, (sid,str(row.get("prop_id") or ""),str(row.get("player") or ""),normalize_name(row.get("player")),
                   str(row.get("match_url") or ""),str(row.get("start_time") or ""),safe_float(row.get("line"),None),
                   str(row.get("lean") or ""),safe_float(row.get("raw_probability"),None),safe_float(row.get("probability"),None),
                   str(row.get("status") or ""),str(row.get("veto_state") or ""),str(row.get("event_tier") or ""),
                   json.dumps(payload,ensure_ascii=False,default=str),created))
        return True
    except Exception:
        return False


def sqlite_graded_projection_rows() -> List[Dict[str, Any]]:
    rows=[]
    try:
        with _sqlite_connect() as conn:
            for rec in conn.execute("SELECT payload_json,graded_result,actual_kills,graded_at FROM projections WHERE graded_result IN ('WIN','LOSS','PUSH') ORDER BY created_at"):
                payload=json.loads(rec["payload_json"])
                payload.update({"graded_result":rec["graded_result"],"actual_kills":rec["actual_kills"],"graded_at":rec["graded_at"]})
                rows.append(payload)
    except Exception:
        pass
    return rows


def sqlite_pending_projection_rows(limit: int = 180) -> List[Dict[str, Any]]:
    rows=[]
    try:
        with _sqlite_connect() as conn:
            for rec in conn.execute("SELECT snapshot_id,payload_json FROM projections WHERE graded_result IS NULL OR graded_result='PENDING' ORDER BY start_time LIMIT ?",(limit,)):
                payload=json.loads(rec["payload_json"]); payload["snapshot_id"]=rec["snapshot_id"]
                start=_parse_iso_datetime(payload.get("start_time"))
                if start is None or start <= datetime.now(timezone.utc)-timedelta(hours=1): rows.append(payload)
    except Exception:
        pass
    return rows


def sqlite_mark_projection_graded(snapshot_id: str, result: str, actual: float, grade_meta: Optional[Dict[str, Any]] = None) -> None:
    with _sqlite_connect() as conn:
        rec=conn.execute("SELECT payload_json FROM projections WHERE snapshot_id=?",(snapshot_id,)).fetchone()
        payload=json.loads(rec["payload_json"]) if rec else {}
        if grade_meta:
            payload["grade_meta"]=grade_meta
            payload["team_total_kills"]=grade_meta.get("team_total_kills")
            payload["observed_player_share"]=grade_meta.get("observed_player_share")
        conn.execute("UPDATE projections SET graded_result=?,actual_kills=?,graded_at=?,payload_json=? WHERE snapshot_id=?",(result,float(actual),now_iso(),json.dumps(payload,default=str),snapshot_id))


init_core_database()


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


def cache_age_seconds(key: str) -> Optional[float]:
    path = cache_path(key)
    try:
        return max(0.0, time.time() - os.path.getmtime(path)) if os.path.exists(path) else None
    except Exception:
        return None


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


def _source_stale_limit(source: str, fallback: int = 0) -> int:
    text=normalize_name(source)
    if "underdog" in text: return SOURCE_MAX_STALE_SECONDS["underdog"]
    if "prize" in text: return SOURCE_MAX_STALE_SECONDS["prizepicks"]
    if "grade" in text or "recent team match" in text or "deep mapstats" in text: return SOURCE_MAX_STALE_SECONDS["completed_history"]
    if "match" in text: return SOURCE_MAX_STALE_SECONDS["match"]
    if "roster" in text: return SOURCE_MAX_STALE_SECONDS["roster"]
    if "player" in text: return SOURCE_MAX_STALE_SECONDS["player_form"]
    if "team maps" in text: return SOURCE_MAX_STALE_SECONDS["team_maps"]
    return fallback


def source_freshness_ok(status: Dict[str, Any], max_age_seconds: int) -> bool:
    if not status or not status.get("ok"): return False
    age=safe_float(status.get("age_seconds"),None)
    return age is not None and age<=max_age_seconds and status.get("cache") != "stale"


def http_get_text(
    url: str,
    source: str,
    ttl: int = 900,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 18,
    allow_stale: bool = True,
    stale_ttl: Optional[int] = None,
) -> Tuple[Optional[str], Dict[str, Any]]:
    full_key = url + "?" + json.dumps(params or {}, sort_keys=True)
    cached = read_cache(full_key, ttl)
    if cached is not None:
        return cached, {"ok": True, "source": source, "cache": "fresh", "status": 200, "url": url, "age_seconds": cache_age_seconds(full_key)}
    merged = dict(DEFAULT_HEADERS)
    if headers:
        merged.update(headers)
    try:
        response = requests.get(url, params=params, headers=merged, timeout=timeout)
        log_request(source, response.url, response.status_code, response.reason)
        if response.ok and response.text:
            write_cache(full_key, response.text)
            return response.text, {"ok": True, "source": source, "cache": "live", "status": response.status_code, "url": response.url, "age_seconds": 0.0, "fetched_at": now_iso()}
        message = f"HTTP {response.status_code}"
    except Exception as exc:
        message = str(exc)
        log_request(source, url, 0, message)
    if allow_stale:
        limit = _source_stale_limit(source, 0) if stale_ttl is None else max(0,int(stale_ttl))
        stale = read_cache(full_key, limit) if limit>0 else None
        if stale is not None:
            return stale, {"ok": True, "source": source, "cache": "stale", "status": 200, "url": url, "warning": message, "age_seconds": cache_age_seconds(full_key), "stale_limit_seconds":limit}
    return None, {"ok": False, "source": source, "status": 0, "url": url, "warning": message}


def http_get_json(
    url: str,
    source: str,
    ttl: int = 300,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 18,
    allow_stale: bool = True,
    stale_ttl: Optional[int] = None,
) -> Tuple[Optional[Any], Dict[str, Any]]:
    text, status = http_get_text(url, source, ttl, params, headers, timeout, allow_stale, stale_ttl)
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
        dt=datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
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
        # Maps 1-2 kill lines are normally far above single-map lines. Reject
        # obvious Map 1 / specialty markets even if Underdog's relationship text
        # contains a nearby "Maps 1-2" label.
        if not (MIN_M12_KILL_LINE <= float(line) <= MAX_M12_KILL_LINE):
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
            "market_scope": "maps_1_2",
            "market_scope_verified": True,
            "market_identity_method": "Underdog relationship + exact Maps 1-2 kill label",
            "source_line_id": str(line_obj.get("id") or ""),
            "appearance_id": str(appearance_id or ""),
            "game_id": str(game_id or ""),
            "sport_id": str(sport_id or ""),
            "stat_name": str(stat_name or ""),
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
            if not (MIN_M12_KILL_LINE <= float(line) <= MAX_M12_KILL_LINE):
                rejected += 1
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

# ============================================================
# PERSISTENT CS2 DATABASE — REAL MATCHED DATA ONLY
# ============================================================

def _load_json_dict(path: str) -> Dict[str, Any]:
    try:
        value = load_json(path, {})
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _save_json_dict(path: str, value: Dict[str, Any]) -> None:
    save_json(path, value)


def sqlite_store_team_map_observation(team_id: str, team_name: str, match_url: str, row: Dict[str, Any], opponent_rank: Optional[float], same_core: bool, environment: str) -> bool:
    map_name=canonical_map_name(row.get("map")); match_id=_match_id_from_url(match_url) or hashlib.md5(str(match_url).encode()).hexdigest()[:16]
    if not team_id or not map_name or not match_id:
        return False
    played=str(row.get("played_at") or row.get("match_datetime") or "")
    team_score=safe_int(row.get("rounds_won"),None); opp_score=safe_int(row.get("rounds_lost"),None)
    rounds=safe_int(row.get("rounds"),None)
    if rounds is None and team_score is not None and opp_score is not None: rounds=team_score+opp_score
    margin=abs((team_score or 0)-(opp_score or 0)) if team_score is not None and opp_score is not None else None
    overtime=bool(row.get("overtime")) or bool(rounds and rounds>24)
    obs_id=hashlib.sha256(f"{team_id}|{match_id}|{map_name}".encode()).hexdigest()
    payload={**row,"team_id":team_id,"team_name":team_name,"match_id":match_id,"match_url":match_url,"opponent_rank":opponent_rank,"same_core":bool(same_core),"environment":environment}
    try:
        with _sqlite_connect() as conn:
            conn.execute("""INSERT OR REPLACE INTO team_map_observations(observation_id,team_id,team_name,match_id,match_url,map_name,played_at,team_score,opponent_score,rounds,margin,overtime,opponent_rank,same_core,environment,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (obs_id,str(team_id),team_name,match_id,match_url,map_name,played,team_score,opp_score,rounds,margin,int(overtime),safe_float(opponent_rank,None),int(bool(same_core)),environment,json.dumps(payload,default=str),now_iso()))
        return True
    except Exception:
        return False


def sqlite_team_map_observations(team_id: str, days: int = 365, limit: int = 300) -> List[Dict[str, Any]]:
    if not team_id: return []
    cutoff=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
    rows=[]
    try:
        with _sqlite_connect() as conn:
            records=conn.execute("""SELECT * FROM team_map_observations WHERE team_id=? AND (played_at='' OR played_at IS NULL OR played_at>=?) ORDER BY played_at DESC,created_at DESC LIMIT ?""",(str(team_id),cutoff,int(limit))).fetchall()
        for rec in records:
            payload=json.loads(rec["payload_json"] or "{}")
            payload.update({"map":rec["map_name"],"rounds_won":rec["team_score"],"rounds_lost":rec["opponent_score"],"rounds":rec["rounds"],"margin":rec["margin"],"overtime":bool(rec["overtime"]),"opponent_rank":rec["opponent_rank"],"same_core":bool(rec["same_core"]),"match_id":rec["match_id"],"match_url":rec["match_url"],"match_datetime":rec["played_at"]})
            rows.append(payload)
    except Exception:
        pass
    return rows


def database_status() -> Dict[str, Any]:
    files = {
        "players": PLAYER_DATABASE_FILE,
        "teams": TEAM_DATABASE_FILE,
        "matches": MATCH_DATABASE_FILE,
        "maps": MAP_DATABASE_FILE,
        "vetoes": VETO_DATABASE_FILE,
        "rosters": ROSTER_DATABASE_FILE,
    }
    out: Dict[str, Any] = {"schema_version": DATABASE_SCHEMA_VERSION}
    for key, path in files.items():
        data = _load_json_dict(path)
        out[key] = len(data)
        out[f"{key}_file"] = path
    out["meta"] = _load_json_dict(DATABASE_META_FILE)
    out["team_map_observations"]=_sqlite_count("team_map_observations")
    out["sqlite_projections"]=_sqlite_count("projections")
    out["demo_events"]=_sqlite_count("demo_events")
    out["demo_rounds"]=_sqlite_count("demo_rounds")
    return out


def _sqlite_count(table: str, where: str = "", params: Sequence[Any] = ()) -> int:
    allowed={"projections","demo_events","demo_rounds","roster_events","entity_snapshots","market_ticks","grading_audit","model_fit_history","team_map_observations"}
    if table not in allowed:
        return 0
    try:
        with _sqlite_connect() as conn:
            row=conn.execute(f"SELECT COUNT(*) AS n FROM {table}" + (f" WHERE {where}" if where else ""),tuple(params)).fetchone()
        return int(row["n"] if row else 0)
    except Exception:
        return 0


def model_health_report(board: Optional[List[Dict[str, Any]]] = None, line_status: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    board=list(board or [])
    valid=[r for r in board if safe_float(r.get("projection"),None) is not None]
    exact=[r for r in valid if r.get("market_scope_verified") and safe_float(r.get("market_identity_confidence"),0)>=0.90]
    ids=[r for r in valid if ((r.get("identity_ids") or {}).get("player_id") and (r.get("identity_ids") or {}).get("match_id") and (r.get("identity_ids") or {}).get("team_id") and (r.get("identity_ids") or {}).get("opponent_id"))]
    lineups=[r for r in valid if bool(r.get("player_in_lineup"))]
    fresh=[r for r in valid if not any("STALE" in str(f).upper() for f in (r.get("flags") or []))]
    confirmed=[r for r in valid if r.get("veto_state")=="CONFIRMED"]
    graded=_sqlite_count("projections","graded_result IN ('WIN','LOSS')")
    demos=_sqlite_count("demo_events"); demo_rounds=_sqlite_count("demo_rounds")
    snapshots=_sqlite_count("entity_snapshots"); ticks=_sqlite_count("market_ticks"); audits=_sqlite_count("grading_audit"); team_obs=_sqlite_count("team_map_observations")
    cal=calibration_metrics()
    round_fit=load_model_fit("round_kill_env:global")
    def ratio(rows): return len(rows)/len(valid) if valid else 0.0
    integrity=25*(.30*ratio(exact)+.25*ratio(ids)+.25*ratio(lineups)+.20*ratio(fresh))
    depth=25*min(1.0,(snapshots/1000)*.20+(demos/5000)*.20+(demo_rounds/5000)*.20+(ticks/1000)*.20+(team_obs/1000)*.20)
    calibration=25*min(1.0,graded/max(MIN_CALIBRATION_STRONG,1))
    simulation=25*(.35*min(1.0,demo_rounds/max(MIN_ROUND_KILL_TRAINING_ROUNDS,1))+.25*min(1.0,safe_int(round_fit.get("sample"),0)/max(MIN_ROUND_KILL_TRAINING_ROUNDS,1))+.20*ratio(confirmed)+.20*min(1.0,audits/max(graded,1)))
    score=round(clamp(integrity+depth+calibration+simulation,0,100),1)
    grade="A" if score>=85 else "B" if score>=72 else "C" if score>=58 else "D" if score>=42 else "BUILDING"
    return {
        "score":score,"grade":grade,"board_rows":len(board),"valid_projections":len(valid),
        "exact_market_ratio":ratio(exact),"verified_id_ratio":ratio(ids),"confirmed_lineup_ratio":ratio(lineups),
        "fresh_source_ratio":ratio(fresh),"confirmed_veto_ratio":ratio(confirmed),
        "graded_binary":graded,"calibration_tier":("UNREADY" if graded<MIN_CALIBRATION_PRELIMINARY else "PRELIMINARY" if graded<MIN_CALIBRATION_USABLE else "USABLE" if graded<MIN_CALIBRATION_STRONG else "STRONG"),
        "brier":cal.get("brier"),"log_loss":cal.get("log_loss"),"demo_events":demos,"demo_rounds":demo_rounds,
        "entity_snapshots":snapshots,"market_ticks":ticks,"grading_audits":audits,"team_map_observations":team_obs,
        "round_environment_sample":safe_int(round_fit.get("sample"),0),
        "components":{"integrity":round(integrity,1),"data_depth":round(depth,1),"calibration":round(calibration,1),"simulation_training":round(simulation,1)},
        "line_status":line_status or {},
        "note":"Health score measures data readiness and validation depth; it is not a promised win rate."
    }


def upsert_database_record(path: str, key: str, record: Dict[str, Any]) -> None:
    if not key:
        return
    data = _load_json_dict(path)
    old = data.get(key, {}) if isinstance(data.get(key), dict) else {}
    merged = {**old, **record, "updated_at": now_iso(), "schema_version": DATABASE_SCHEMA_VERSION}
    data[key] = merged
    _save_json_dict(path, data)


def save_projection_entities(row: Dict[str, Any]) -> None:
    """Persist only verified entities. Never turn fallback defaults into database facts."""
    player = str(row.get("player") or "").strip()
    team = str(row.get("team") or "").strip()
    opponent = str(row.get("opponent") or "").strip()
    if player and int(row.get("profile_maps") or 0) > 0 and row.get("profile_href"):
        upsert_database_record(PLAYER_DATABASE_FILE, normalize_name(player), {
            k: row.get(k) for k in [
                "player", "team", "profile_href", "profile_maps", "profile_rounds", "base_kpr",
                "dpr", "adr", "rating", "kd", "kast_pct", "impact", "opening_kpr",
                "opening_deaths_pr", "multi_kill_rate", "awp_kill_share", "ct_kpr", "t_kpr",
                "role", "role_confidence", "profile_source", "player_map_profiles"
            ]
        })
    if team and row.get("match_url"):
        upsert_database_record(TEAM_DATABASE_FILE, normalize_name(team), {
            "team": team, "world_ranks": row.get("world_ranks"), "recent_maps": row.get("team_recent_maps"),
            "current_roster_maps": row.get("current_roster_maps"), "roster_stability": row.get("roster_stability")
        })
    if opponent and row.get("opponent_mapstats_samples", 0):
        upsert_database_record(TEAM_DATABASE_FILE, normalize_name(opponent), {
            "team": opponent, "deaths_allowed_per_round": row.get("opponent_deaths_allowed_pr"),
            "mapstats_samples": row.get("opponent_mapstats_samples")
        })
    if row.get("match_url") and team and opponent:
        upsert_database_record(MATCH_DATABASE_FILE, str(row.get("match_url")), {
            k: row.get(k) for k in ["match_url", "team", "opponent", "start_time", "event", "stage", "event_tier", "environment", "match_format", "likely_maps", "map_scenarios"]
        })
    for map_name, profile in (row.get("player_map_profiles") or {}).items():
        if isinstance(profile, dict) and int(profile.get("maps") or 0) > 0:
            upsert_database_record(MAP_DATABASE_FILE, f"{normalize_name(player)}|{normalize_name(map_name)}", {
                "player": player, "map": map_name, **profile
            })
    # Also retain immutable as-of snapshots in SQLite for chronological backtests.
    as_of=row.get("projection_time") or now_iso()
    if player and int(row.get("profile_maps") or 0)>0:
        sqlite_store_entity_snapshot("player",str((row.get("identity_ids") or {}).get("player_id") or normalize_name(player)),{k:row.get(k) for k in ["player","team","profile_maps","profile_rounds","base_kpr","dpr","adr","rating","kd","role","role_confidence","player_map_profiles","kpr_source"]},row.get("profile_source") or "HLTV",None,as_of)
    if team:
        sqlite_store_entity_snapshot("team",str((row.get("identity_ids") or {}).get("team_id") or normalize_team(team)),{"team":team,"world_ranks":row.get("world_ranks"),"recent_maps":row.get("team_recent_maps"),"current_roster_maps":row.get("current_roster_maps"),"roster_stability":row.get("roster_stability")},"HLTV team history",None,as_of)
    if opponent:
        sqlite_store_entity_snapshot("opponent",str((row.get("identity_ids") or {}).get("opponent_id") or normalize_team(opponent)),{"team":opponent,"deaths_allowed_per_round":row.get("opponent_deaths_allowed_pr"),"mapstats_samples":row.get("opponent_mapstats_samples")},"HLTV opponent history",None,as_of)
    if row.get("match_url"):
        sqlite_store_entity_snapshot("match",str((row.get("identity_ids") or {}).get("match_id") or row.get("match_url")),{k:row.get(k) for k in ["match_url","team","opponent","start_time","event","stage","event_tier","environment","match_format","likely_maps","map_scenarios","veto_state"]},"HLTV match page",safe_float((row.get("source_freshness") or {}).get("match_age_seconds"),None),as_of)
    meta = _load_json_dict(DATABASE_META_FILE)
    meta.update({"last_updated": now_iso(), "schema_version": DATABASE_SCHEMA_VERSION})
    _save_json_dict(DATABASE_META_FILE, meta)


def lookup_database_player(player: str) -> Dict[str, Any]:
    aliases = _load_json_dict(PLAYER_ALIAS_FILE)
    key = normalize_name(player)
    alias = normalize_name(str(aliases.get(key) or key))
    players = _load_json_dict(PLAYER_DATABASE_FILE)
    if alias in players:
        return players[alias]
    candidates = [(name_similarity(player, rec.get("player", k)), rec) for k, rec in players.items() if isinstance(rec, dict)]
    if candidates:
        score, rec = max(candidates, key=lambda x: x[0])
        if score >= 0.91:
            return rec
    return {}


def projection_integrity_errors(row: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    line = safe_float(row.get("line"), None)
    if line is None or not (MIN_M12_KILL_LINE <= line <= MAX_M12_KILL_LINE):
        errors.append("INVALID MAPS 1-2 LINE")
    if int(row.get("profile_maps") or 0) <= 0:
        errors.append("NO REAL PLAYER PROFILE")
    if not row.get("team"):
        errors.append("TEAM UNMATCHED")
    if not row.get("opponent"):
        errors.append("OPPONENT UNMATCHED")
    if not row.get("match_url"):
        errors.append("MATCH UNMATCHED")
    if str(row.get("likely_maps") or "").lower() in {"", "unconfirmed"} and float(row.get("map_confidence") or 0) < 50:
        errors.append("MAPS UNVERIFIED")
    return errors

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
    kpr_source: str = "unknown"

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
    page, status = http_get_text(url, "HLTV player table", ttl=60 * 60, params=params, timeout=20, allow_stale=True, stale_ttl=SOURCE_MAX_STALE_SECONDS["player_form"])
    rows = _extract_hltv_player_rows(page or "")
    for row in rows.values():
        row["_source_age_seconds"]=safe_float(status.get("age_seconds"),None)
        row["_source_cache"]=status.get("cache")
        row["_source_fresh"]=source_freshness_ok(status,SOURCE_MAX_STALE_SECONDS["player_form"])
    status.update({"rows": len(rows), "period_days": days})
    if rows:
        sqlite_store_entity_snapshot("player_table",f"{days}d",{"rows":len(rows),"players":rows},"HLTV player table",status.get("age_seconds"),now_iso())
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
    page, status = http_get_text(url, "HLTV player overview", ttl=60 * 60 * 4, params=params, timeout=20, allow_stale=True, stale_ttl=SOURCE_MAX_STALE_SECONDS["player_form"])
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
    page, status = http_get_text(url, "HLTV filtered individual", ttl=60 * 60 * 4, params=params, timeout=20, allow_stale=True, stale_ttl=SOURCE_MAX_STALE_SECONDS["player_form"])
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



def era_recency_weight(observed_at: Any, target_at: Any = None, era: Optional[Dict[str, Any]] = None) -> float:
    target = _parse_iso_datetime(target_at) if target_at else datetime.now(timezone.utc)
    observed = _parse_iso_datetime(observed_at)
    if not target or not observed:
        return 0.55
    half_life = max(safe_float((era or {}).get("decay_half_life_days"), 120) or 120, 20)
    age = max(0.0, (target-observed).total_seconds()/86400.0)
    weight = 0.5 ** (age/half_life)
    era_start = _parse_iso_datetime((era or {}).get("effective_from"))
    if era_start and observed < era_start:
        weight *= 0.30
    return clamp(weight, 0.03, 1.0)


def build_player_map_profiles(profile: PlayerStats, likely_maps: Sequence[str], target_time: Any = None, patch_era: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    statuses: Dict[str, Any] = {}
    if not profile.player_id or not likely_maps:
        return out, {"ok": False, "message": "player id or likely maps unavailable"}
    era = patch_era or patch_era_for_time(target_time)
    target_dt = _parse_iso_datetime(target_time) or datetime.now(timezone.utc)
    era_start = _parse_iso_datetime(era.get("effective_from"))
    days_in_era = max(1.0, (target_dt-(era_start or target_dt-timedelta(days=180))).total_seconds()/86400.0)
    long_era_factor = clamp(days_in_era/180.0, 0.20, 1.0)
    for map_name in list(dict.fromkeys(likely_maps))[:3]:
        long_row, long_status = fetch_hltv_filtered_player_profile(profile.player_id, profile.slug, 180, map_name)
        recent_row, recent_status = fetch_hltv_filtered_player_profile(profile.player_id, profile.slug, 60, map_name)
        demo = demo_profile_for(profile.player, map_name)
        long_maps = safe_int(long_row.get("maps"), 0) or 0
        recent_maps = safe_int(recent_row.get("maps"), 0) or 0
        long_kpr = safe_float(long_row.get("kpr"), None)
        recent_kpr = safe_float(recent_row.get("kpr"), None)
        demo_kpr = safe_float(demo.get("kpr"), None) if demo.get("denominator_verified") else None
        if long_kpr is None and demo_kpr is None:
            statuses[map_name] = {"long": long_status, "recent": recent_status, "demo": demo, "usable": False}
            continue
        center = long_kpr if long_kpr is not None else profile.kpr
        sample_weight = clamp(long_maps/(long_maps+12.0),0.0,0.88) * long_era_factor
        recent_weight = clamp(recent_maps/(recent_maps+8.0),0.0,0.48) if recent_kpr is not None else 0.0
        shrunk = profile.kpr*(1-sample_weight)+center*sample_weight
        blended = shrunk*(1-recent_weight)+(recent_kpr if recent_kpr is not None else shrunk)*recent_weight
        demo_rounds=safe_int(demo.get("rounds"),0) or 0
        demo_weight=0.0
        if demo_kpr is not None:
            demo_weight=clamp(demo_rounds/(demo_rounds+180.0),0.0,0.58)*era_recency_weight(demo.get("latest_event_time"),target_time,era)
            blended=blended*(1-demo_weight)+demo_kpr*demo_weight
        sources=[]
        if long_kpr is not None: sources.append("HLTV map KPR")
        if recent_kpr is not None: sources.append("HLTV recent map KPR")
        if demo_kpr is not None: sources.append("demo full-round KPR")
        out[map_name]={
            "map":map_name,"maps":long_maps,"rounds":safe_int(long_row.get("rounds"),0) or 0,
            "kpr":round(float(center),4),"recent_maps":recent_maps,
            "recent_kpr":round(float(recent_kpr),4) if recent_kpr is not None else None,
            "demo_rounds":demo_rounds,"demo_kpr":round(float(demo_kpr),4) if demo_kpr is not None else None,
            "demo_weight":round(demo_weight,4),"blended_kpr":round(clamp(float(blended),0.44,0.98),4),
            "dpr":safe_float(long_row.get("dpr"),None),"adr":safe_float(long_row.get("adr"),None),
            "rating":safe_float(long_row.get("rating"),None),
            "opening_kpr":safe_float(demo.get("opening_kpr"),None) or ((safe_float(long_row.get("opening_kills"),0) or 0)/max(safe_float(long_row.get("rounds"),0) or 0,1)),
            "hs_pct":safe_float(demo.get("hs_pct"),None),"awp_kill_share":safe_float(demo.get("awp_kill_share"),None),
            "source":" + ".join(sources),"core_map_kpr_verified":long_kpr is not None or demo_kpr is not None,
            "patch_era_weight":round(long_era_factor,4),
        }
        statuses[map_name]={"long":long_status,"recent":recent_status,"demo":demo,"usable":True}
    saved=load_json(DEEP_PLAYER_MAP_FILE,{})
    if not isinstance(saved,dict): saved={}
    if out:
        saved[normalize_name(profile.player)]={"updated_at":now_iso(),"maps":out}; save_json(DEEP_PLAYER_MAP_FILE,saved)
    return out,{"ok":bool(out),"maps":list(out),"statuses":statuses,"patch_era":era.get("name")}


@st.cache_data(ttl=60 * 60 * 4, show_spinner=False)
def fetch_hltv_individual_profile(player_id: str, slug: str, days: int = 180) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not player_id:
        return {}, {"ok": False, "warning": "no player id"}
    start, end = _period_dates(days)
    url = f"{HLTV_BASE}/stats/players/individual/{player_id}/{slug}"
    page, status = http_get_text(url, "HLTV individual", ttl=60 * 60 * 4, params={"startDate": start, "endDate": end}, timeout=20, allow_stale=True, stale_ttl=SOURCE_MAX_STALE_SECONDS["player_form"])
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
    recent30_table: Dict[str, Dict[str, Any]],
    recent15_table: Dict[str, Dict[str, Any]],
) -> Tuple[PlayerStats, Dict[str, Any]]:
    long_row, match_score = fuzzy_lookup_player(player_name, long_table)
    medium_row, medium_score = fuzzy_lookup_player(player_name, medium_table)
    recent30_row, recent30_score = fuzzy_lookup_player(player_name, recent30_table)
    recent15_row, recent15_score = fuzzy_lookup_player(player_name, recent15_table)
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
            "matched": False, "match_score": match_score, "recent30_score": recent30_score, "recent15_score": recent15_score,
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
    kpr_source = "hltv_reported_kpr" if kpr is not None else "unknown"
    dpr = safe_float(individual.get("dpr"), None)
    adr = safe_float(individual.get("adr"), None)

    if kpr is None and rounds > 0 and kills > 0:
        kpr = kills / rounds
        kpr_source = "real_kills_div_rounds"
    if dpr is None and rounds > 0 and deaths > 0:
        dpr = deaths / rounds
    if kpr is None:
        # Data-driven fallback from the real player's K/D and rating.
        kpr = LEAGUE_KPR * clamp(0.50 * kd + 0.50 * rating, 0.78, 1.28)
        warnings.append("KPR estimated from player K/D and rating")
        kpr_source = "estimated_from_kd_rating"
    if dpr is None:
        dpr = clamp(kpr / max(kd, 0.65), 0.52, 0.86)
        warnings.append("DPR estimated")
    if adr is None:
        adr = LEAGUE_ADR * clamp(0.55 * rating + 0.45 * (kpr / LEAGUE_KPR), 0.78, 1.30)
        warnings.append("ADR estimated")

    # Current form uses the requested 15/30-day windows, shrunk toward 60/180-day baselines.
    # This reduces overreaction to one hot series while still catching genuine role/form changes.
    long_rating = safe_float(long_row.get("rating"), rating) or rating
    medium_rating = safe_float((medium_row or {}).get("rating"), long_rating) or long_rating
    r30_rating = safe_float((recent30_row or {}).get("rating"), medium_rating) or medium_rating
    r15_rating = safe_float((recent15_row or {}).get("rating"), r30_rating) or r30_rating
    medium_maps = safe_int((medium_row or {}).get("maps"), 0) or 0
    r30_maps = safe_int((recent30_row or {}).get("maps"), 0) or 0
    r15_maps = safe_int((recent15_row or {}).get("maps"), 0) or 0
    w60 = clamp(medium_maps / 35.0, 0.0, 1.0)
    w30 = clamp(r30_maps / 22.0, 0.0, 1.0)
    w15 = clamp(r15_maps / 12.0, 0.0, 1.0)
    adj60 = w60 * medium_rating + (1-w60) * long_rating
    adj30 = w30 * r30_rating + (1-w30) * adj60
    adj15 = w15 * r15_rating + (1-w15) * adj30
    form_rating = 0.50*long_rating + 0.20*adj60 + 0.18*adj30 + 0.12*adj15
    form_factor = clamp(form_rating / max(long_rating, 0.75), 0.91, 1.09)
    kpr = clamp(kpr * form_factor, 0.47, 0.92)

    # Manual profile fields override only the supplied metric, never the line.
    if override:
        if safe_float(override.get("kpr"), None) is not None:
            kpr_source = "manual_override"
        kpr = safe_float(override.get("kpr"), kpr) or kpr
        dpr = safe_float(override.get("dpr"), dpr) or dpr
        adr = safe_float(override.get("adr"), adr) or adr
        rating = safe_float(override.get("rating"), rating) or rating
        maps = safe_int(override.get("maps"), maps) or maps
        rounds = safe_int(override.get("rounds"), rounds) or rounds

    table_fresh=bool(long_row.get("_source_fresh",True))
    advanced_statuses=[individual_status,overview_status]
    advanced_fresh=any(source_freshness_ok(x,SOURCE_MAX_STALE_SECONDS["player_form"]) for x in advanced_statuses if x)
    source_fresh=bool(table_fresh and advanced_fresh)
    if not source_fresh:
        warnings.append("PLAYER FORM SOURCE STALE OR UNVERIFIED")

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
        kpr_source=kpr_source,
    )
    return profile, {
        "matched": True,
        "match_score": round(match_score, 3),
        "medium_score": round(medium_score, 3),
        "recent30_score": round(recent30_score, 3),
        "recent15_score": round(recent15_score, 3),
        "long_row": long_row,
        "medium_row": medium_row,
        "recent30_row": recent30_row,
        "recent15_row": recent15_row,
        "form_windows": {"180d_maps":safe_int(long_row.get("maps"),0) or 0,"60d_maps":medium_maps,"30d_maps":r30_maps,"15d_maps":r15_maps},
        "individual_status": individual_status,
        "overview_status": overview_status,
        "overview": overview,
        "form_factor": round(form_factor, 4),
        "kpr_source": kpr_source,
        "source_fresh":source_fresh,
        "source_ages": {"table":long_row.get("_source_age_seconds"),"individual":individual_status.get("age_seconds"),"overview":overview_status.get("age_seconds")},
        "core_kpr_verified": kpr_source in {"hltv_reported_kpr", "real_kills_div_rounds"},
    }

# ============================================================
# MATCH DISCOVERY / MAP POOL / FORMAT / ROSTER
# ============================================================

@st.cache_data(ttl=10 * 60, show_spinner=False)
def fetch_hltv_matches_page() -> Tuple[str, Dict[str, Any]]:
    page, status = http_get_text(f"{HLTV_BASE}/matches", "HLTV matches", ttl=2 * 60, timeout=20, allow_stale=False)
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


@st.cache_data(ttl=2 * 60, show_spinner=False)
def fetch_match_context(match_url: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not match_url:
        return {}, {"ok": False, "warning": "no match URL"}
    page, status = http_get_text(match_url, "HLTV match", ttl=2 * 60, timeout=20, allow_stale=False)
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
    page, status = http_get_text(url, "HLTV team maps", ttl=60 * 60 * 4, params={"startDate": start, "endDate": end}, timeout=20, allow_stale=True, stale_ttl=SOURCE_MAX_STALE_SECONDS["team_maps"])
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
    page, status = http_get_text(url, "HLTV team roster", ttl=60 * 60, timeout=20, allow_stale=True, stale_ttl=SOURCE_MAX_STALE_SECONDS["roster"])
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
        page, status = http_get_text(url, "HLTV team recent matches", ttl=60 * 60 * 6, stale_ttl=SOURCE_MAX_STALE_SECONDS["completed_history"],
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
        page, status = http_get_text(match_url, "HLTV recent team match", ttl=60 * 60 * 6, timeout=20, allow_stale=True, stale_ttl=SOURCE_MAX_STALE_SECONDS["completed_history"])
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
            "world_ranks": _extract_world_ranks(page),
        })
    return summaries, {"ok": bool(summaries), "rows": len(summaries), "link_status": link_status, "statuses": statuses}


def _team_name_matches(target: str, candidate: str) -> bool:
    if not target or not candidate:
        return False
    a, b = normalize_team(target), normalize_team(candidate)
    return a == b or (len(a)>=5 and len(b)>=5 and (a in b or b in a)) or name_similarity(a, b) >= 0.86


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
    page, status = http_get_text(map_url, "HLTV deep mapstats", ttl=60 * 60 * 12, timeout=20, allow_stale=True, stale_ttl=SOURCE_MAX_STALE_SECONDS["completed_history"])
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
    historical_opponent_ranks: List[float] = []

    roster_norm = {normalize_name(x) for x in roster[:5]}
    roster_fingerprint=hashlib.sha256("|".join(sorted(roster_norm)).encode()).hexdigest()[:16] if roster_norm else ""
    for summary in summaries:
        environment_counts[summary.get("environment") or "UNKNOWN"] += 1
        summary_opp_rank=None
        dt = _parse_iso_datetime(summary.get("match_datetime"))
        if dt:
            match_dates.append(dt)
        sranks=summary.get("world_ranks") or []; steams=summary.get("teams") or []
        if len(sranks)>=2 and len(steams)>=2:
            t_idx=next((i for i,t in enumerate(steams[:2]) if _team_name_matches(team_name,t.get("name",""))),None)
            if t_idx in {0,1}:
                opp_rank=safe_float(sranks[1-int(t_idx)],None)
                if opp_rank and opp_rank>0:
                    summary_opp_rank=float(opp_rank); historical_opponent_ranks.append(float(opp_rank))
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
            row = {**result, "rounds_won": team_score, "rounds_lost": opp_score, "match_datetime": summary.get("match_datetime"), "played_at": summary.get("match_datetime"), "opponent_rank": summary_opp_rank, "same_core": same_core, "roster_fingerprint":roster_fingerprint}
            map_rows[result.get("map", "Unknown")].append(row)
            sqlite_store_team_map_observation(team_id,team_name,str(summary.get("match_url") or ""),row,summary_opp_rank,same_core,str(summary.get("environment") or "UNKNOWN"))
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
            # Never infer team table identity from page ordering.
            if target_idx not in {0, 1}:
                continue
            opponent_idx = 1 - int(target_idx)
            kills_for_num += float(tables[int(target_idx)].get("total_kills", 0))
            deaths_allowed_num += float(tables[opponent_idx].get("total_kills", 0))
            mapstats_rounds += float(rounds)
            mapstats_used += 1

    cumulative=sqlite_team_map_observations(team_id,180,300)
    if cumulative:
        map_rows=defaultdict(list)
        for obs in cumulative:
            map_name=canonical_map_name(obs.get("map"))
            if map_name: map_rows[map_name].append(obs)
        historical_opponent_ranks=[float(x.get("opponent_rank")) for x in cumulative if safe_float(x.get("opponent_rank"),None)]
        current_roster_maps=sum(1 for x in cumulative if x.get("same_core") and roster_fingerprint and str(x.get("roster_fingerprint") or "")==roster_fingerprint)

    map_profiles: Dict[str, Dict[str, Any]] = {}
    all_maps = set(pool) | set(map_rows)
    for map_name in all_maps:
        observed = _summarize_map_observations(map_rows.get(map_name, []))
        base = pool.get(map_name, {})
        demo_side=demo_team_side_profile(team_name,map_name)
        map_profiles[map_name] = {
            **base,
            **{k: v for k, v in observed.items() if v is not None},
            **({k:v for k,v in demo_side.items() if v is not None} if safe_int(demo_side.get("rounds"),0)>=24 else {}),
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
        "cumulative_map_observations":len(cumulative),
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
        "historical_opponent_rank_avg":round(float(np.mean(historical_opponent_ranks)),2) if historical_opponent_ranks else None,
        "historical_opponent_rank_samples":len(historical_opponent_ranks),
        "updated_at": now_iso(),
    }
    status = {
        "ok": bool(pool or summaries),
        "pool": pool_status,
        "roster": roster_status,
        "summaries": summaries_status,
        "mapstats_samples": mapstats_used,
        "roster_fresh":source_freshness_ok(roster_status,SOURCE_MAX_STALE_SECONDS["roster"]),
        "pool_fresh":source_freshness_ok(pool_status,SOURCE_MAX_STALE_SECONDS["team_maps"]),
    }
    if profile.get("team_id"):
        sqlite_store_entity_snapshot("team_deep",str(profile["team_id"]),profile,"HLTV team/map/roster history",max([safe_float(pool_status.get("age_seconds"),0) or 0,safe_float(roster_status.get("age_seconds"),0) or 0]),now_iso())
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
    health={
        "roster_fresh":all(bool(x.get("roster_fresh")) for x in statuses),
        "team_map_fresh":all(bool(x.get("pool_fresh")) for x in statuses),
        "history_maps":sum(safe_int(p.get("recent_maps"),0) or 0 for p in profiles),
        "mapstats_samples":sum(safe_int(p.get("mapstats_samples"),0) or 0 for p in profiles),
    }
    enriched["deep_source_health"]=health
    return enriched, {"ok": all(bool(x) for x in profiles), "statuses": statuses, "health":health}


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
    active=set((patch_era_for_time().get("active_maps") or CURRENT_ACTIVE_MAPS))
    candidate_maps = [m for m in KNOWN_MAPS if m in active and any((safe_int((p.get("map_profiles") or {}).get(m,{}).get("maps"),0) or 0)>0 or (safe_int((p.get("pick_counts") or {}).get(m),0) or 0)>0 for p in profiles)]
    # Use the official configured seven-map pool; missing team samples are represented as uncertainty, not replaced by inactive maps.
    for m in active:
        if m not in candidate_maps: candidate_maps.append(m)
    candidate_maps=candidate_maps[:7]
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
    # Never infer team identity from page ordering. Incorrect side assignment is
    # worse than missing matchup data and blocks Official status downstream.
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



MAP_CT_BASE = {"Nuke":0.535,"Ancient":0.515,"Anubis":0.495,"Dust2":0.505,"Inferno":0.505,"Mirage":0.505,"Overpass":0.525,"Train":0.535,"Vertigo":0.505,"Cache":0.505}

def _mr12_round_sample(rng: np.random.Generator, n: int, p_ct: float, p_t: float) -> np.ndarray:
    n=max(int(n),1); p_ct=clamp(p_ct,.25,.75); p_t=clamp(p_t,.25,.75)
    starts_ct=rng.random(n)<.5
    a=np.zeros(n,dtype=int); b=np.zeros(n,dtype=int)
    for r in range(12):
        p=np.where(starts_ct,p_ct,p_t); win=rng.random(n)<p; a+=win; b+=~win
    active=(a<13)&(b<13)
    for r in range(12):
        if not active.any(): break
        p=np.where(starts_ct,p_t,p_ct); win=(rng.random(n)<p)&active; lose=(~win)&active
        a+=win; b+=lose; active=(a<13)&(b<13)&((a+b)<24)
    tied=(a==12)&(b==12)
    ot_blocks=0
    while tied.any() and ot_blocks<5:
        idx=np.where(tied)[0]; wins=np.zeros(len(idx),dtype=int)
        for rr in range(6):
            p=np.where(starts_ct[idx], p_ct if rr<3 else p_t, p_t if rr<3 else p_ct)
            wins += rng.random(len(idx))<p
        a[idx]+=wins; b[idx]+=6-wins
        tied=np.zeros(n,dtype=bool); tied[idx]=(wins==3); ot_blocks+=1
    # Rare unresolved cap: award one deciding round without changing regulation distribution materially.
    if tied.any():
        idx=np.where(tied)[0]; win=rng.random(len(idx))<.5; a[idx]+=win; b[idx]+=~win
    return a+b


def learned_round_kill_environment(map_name: str = "") -> Dict[str, Any]:
    """Fit team kills in won/lost rounds from uploaded demos; zero-kill rounds are retained."""
    map_key=canonical_map_name(map_name); key=f"round_kill_env:{map_key or 'GLOBAL'}"
    cached=load_model_fit(key); updated=_parse_iso_datetime(cached.get("updated_at")) if cached else None
    if cached and updated and (datetime.now(timezone.utc)-updated).total_seconds()<24*3600: return cached
    won=[]; lost=[]
    try:
        with _sqlite_connect() as conn:
            rounds=conn.execute("SELECT match_id,round_num,ct_team,t_team,winner_team FROM demo_rounds WHERE (?='' OR map_name=?)",(map_key,map_key)).fetchall()
            counts=conn.execute("SELECT match_id,round_num,team,COUNT(*) kills FROM demo_events WHERE (?='' OR map_name=?) AND team<>'' GROUP BY match_id,round_num,team",(map_key,map_key)).fetchall()
        count_map={(r["match_id"],r["round_num"],normalize_team(r["team"])):int(r["kills"]) for r in counts}
        for r in rounds:
            winner=normalize_team(r["winner_team"]); teams=[normalize_team(r["ct_team"]),normalize_team(r["t_team"])]
            if not winner or len([x for x in teams if x])<2: continue
            for t in teams:
                k=count_map.get((r["match_id"],r["round_num"],t),0)
                (won if t==winner else lost).append(k)
    except Exception:
        won=[]; lost=[]
    sample=len(won)+len(lost)
    if len(won)>=MIN_ROUND_KILL_TRAINING_ROUNDS//2 and len(lost)>=MIN_ROUND_KILL_TRAINING_ROUNDS//2:
        out={"winner_mean":clamp(float(np.mean(won)),3.2,4.8),"loser_mean":clamp(float(np.mean(lost)),0.5,3.4),"winner_sd":float(np.std(won)),"loser_sd":float(np.std(lost)),"sample":sample,"trained":True,"source":"uploaded demo round outcomes","fit_type":"round_kill_environment"}
    else:
        out={"winner_mean":4.10,"loser_mean":2.05,"winner_sd":1.05,"loser_sd":1.25,"sample":sample,"trained":False,"source":"conservative CS2 round prior","fit_type":"round_kill_environment"}
    save_model_fit(key,out); return out

def _simulate_mr12_round_environment(rng: np.random.Generator, n: int, p_ct: float, p_t: float, map_name: str = "") -> Dict[str, Any]:
    n=max(int(n),1); p_ct=clamp(p_ct,.25,.75); p_t=clamp(p_t,.25,.75)
    starts_ct=rng.random(n)<.5; a=np.zeros(n,dtype=int); b=np.zeros(n,dtype=int)
    ak=np.zeros(n,dtype=int); bk=np.zeros(n,dtype=int); env=learned_round_kill_environment(map_name)
    win_p=clamp(env["winner_mean"]/5.0,.50,.96); lose_p=clamp(env["loser_mean"]/4.0,.08,.82)
    def outcome(mask: np.ndarray, probs: np.ndarray) -> Tuple[np.ndarray,np.ndarray]:
        win=(rng.random(n)<probs)&mask; lose=(~win)&mask
        wk=rng.binomial(5,win_p,size=n); lk=rng.binomial(4,lose_p,size=n)
        ak[:] += np.where(win,wk,np.where(lose,lk,0)); bk[:] += np.where(win,lk,np.where(lose,wk,0))
        return win,lose
    active=np.ones(n,dtype=bool)
    for _ in range(12):
        win,lose=outcome(active,np.where(starts_ct,p_ct,p_t)); a+=win; b+=lose
    active=(a<13)&(b<13)
    for _ in range(12):
        if not active.any(): break
        win,lose=outcome(active,np.where(starts_ct,p_t,p_ct)); a+=win; b+=lose
        active=(a<13)&(b<13)&((a+b)<24)
    tied=(a==12)&(b==12); blocks=0
    while tied.any() and blocks<5:
        ba=np.zeros(n,dtype=int); bb=np.zeros(n,dtype=int); block_active=tied.copy()
        for rr in range(6):
            if not block_active.any(): break
            probs=np.where(starts_ct,p_ct if rr<3 else p_t,p_t if rr<3 else p_ct)
            win,lose=outcome(block_active,probs); a+=win; b+=lose; ba+=win; bb+=lose
            block_active=tied&(ba<4)&(bb<4)&((ba+bb)<6)
        tied=tied&(ba==3)&(bb==3); blocks+=1
    if tied.any():
        win,lose=outcome(tied,np.full(n,.5)); a+=win; b+=lose
    return {"rounds":a+b,"team_score":a,"opponent_score":b,"team_kills":ak,"opponent_kills":bk,"round_kill_environment":env}

def learned_simulation_blend(player: str, role: str) -> Dict[str, Any]:
    rows=sqlite_graded_projection_rows(); scopes=[("player",lambda r:normalize_name(r.get("player"))==normalize_name(player),MIN_BLEND_TRAINING_SAMPLES),("role",lambda r:str(r.get("role") or "")==str(role),80),("global",lambda r:True,150)]
    for scope,pred,min_n in scopes:
        pairs=[]
        for r in rows:
            comp=r.get("model_components") or {}; actual=safe_float(r.get("actual_kills"),None); d=safe_float(comp.get("direct_projection"),None); sh=safe_float(comp.get("share_projection"),None)
            if pred(r) and None not in (actual,d,sh): pairs.append((abs(actual-d),abs(actual-sh)))
        if len(pairs)>=min_n:
            d_mae=float(np.mean([x[0] for x in pairs])); s_mae=float(np.mean([x[1] for x in pairs])); weight=clamp(d_mae/max(d_mae+s_mae,1e-6),.20,.80)
            out={"share_weight":weight,"direct_weight":1-weight,"sample":len(pairs),"scope":scope,"direct_mae":d_mae,"share_mae":s_mae,"trained":True,"fit_type":"simulation_blend"}; save_model_fit(f"simulation_blend:{scope}:{normalize_name(player) if scope=='player' else role if scope=='role' else 'all'}",out); return out
    return {"share_weight":.50,"direct_weight":.50,"sample":0,"scope":"prior","trained":False,"source":"neutral untrained blend"}


def _map_side_probabilities(context: Dict[str, Any], map_name: str, team: str, opponent: str) -> Tuple[float,float,Dict[str,Any]]:
    tp,op=_resolve_team_profiles(context,team,opponent)
    tr_row=(tp.get("map_profiles") or {}).get(map_name,{}); orow=(op.get("map_profiles") or {}).get(map_name,{})
    tr=safe_float(tr_row.get("round_win_pct"),50) or 50; OR=safe_float(orow.get("round_win_pct"),50) or 50
    team_ct=safe_float(tr_row.get("ct_round_win_pct"),None); team_t=safe_float(tr_row.get("t_round_win_pct"),None)
    opp_ct=safe_float(orow.get("ct_round_win_pct"),None); opp_t=safe_float(orow.get("t_round_win_pct"),None)
    diff=clamp((tr-OR)/100.0,-.22,.22); ct=MAP_CT_BASE.get(map_name,.505)
    baseline_ct=clamp(.5+diff*.75+(ct-.5)*.65,.31,.69); baseline_t=clamp(.5+diff*.75-(ct-.5)*.65,.31,.69)
    if team_ct is not None and opp_t is not None: p_ct=clamp(.5*((team_ct/100)+(1-opp_t/100)),.28,.72)
    else: p_ct=baseline_ct
    if team_t is not None and opp_ct is not None: p_t=clamp(.5*((team_t/100)+(1-opp_ct/100)),.28,.72)
    else: p_t=baseline_t
    return p_ct,p_t,{"team_map_round_win_pct":tr,"opponent_map_round_win_pct":OR,"team_ct":team_ct,"team_t":team_t,"opponent_ct":opp_ct,"opponent_t":opp_t,"map_ct_base":ct,"side_data_verified":all(x is not None for x in [team_ct,team_t,opp_ct,opp_t])}


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
    p_ct,p_t,side_meta=_map_side_probabilities(context,map_name,team,opponent)
    rng=np.random.default_rng(stable_seed("mr12",map_name,team,opponent,p_ct,p_t,MODEL_VERSION))
    mr12=_simulate_mr12_round_environment(rng,5000,p_ct,p_t,map_name)["rounds"]
    mr12_mean=float(np.mean(mr12)); mr12_sd=float(np.std(mr12))
    # Keep a small empirical blend while enforcing an actual MR12 score process.
    mean=clamp(.82*mr12_mean+.18*clamp(mean,17.0,25.8),17.0,30.0)
    sd_values=[safe_float(x.get("rounds_sd"),None) for x in rows]
    sd_values=[float(x) for x in sd_values if x is not None and 1.5<=x<=8]
    empirical_sd=float(np.mean(sd_values)) if sd_values else mr12_sd
    sd=clamp(.82*mr12_sd+.18*empirical_sd,2.5,7.5)
    return {
        "map": map_name,
        "mean_rounds": round(mean, 4),
        "rounds_sd": round(sd, 4),
        "team_ct_round_win_prob":round(p_ct,5),"team_t_round_win_prob":round(p_t,5),"side_meta":side_meta,
        "round_model":"MR12 regulation + MR3 overtime",
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
    return clamp(mean, 26.0, 64.0), clamp(sd, 2.8, 10.5), {
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
    if results is not None:
        rows=results
    else:
        rows=sqlite_graded_projection_rows()
        legacy=load_json(RESULT_LOG,[])
        if isinstance(legacy,list):
            seen={str(x.get("snapshot_id") or "") for x in rows}
            rows += [x for x in legacy if str(x.get("snapshot_id") or "") not in seen]
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
    if safe_int(global_row.get("samples"), 0) >= 100:
        pieces.append((safe_float(global_row.get("bias"), 0.0) or 0.0, 0.40, "global"))
    player_row = profiles.get("players", {}).get(normalize_name(player), {}) if isinstance(profiles, dict) else {}
    if safe_int(player_row.get("samples"), 0) >= MIN_PLAYER_LEARNING_SAMPLES:
        pieces.append((safe_float(player_row.get("bias"), 0.0) or 0.0, 0.25, "player"))
    role_row = profiles.get("roles", {}).get(role, {}) if isinstance(profiles, dict) else {}
    if safe_int(role_row.get("samples"), 0) >= MIN_ROLE_LEARNING_SAMPLES:
        pieces.append((safe_float(role_row.get("bias"), 0.0) or 0.0, 0.15, "role"))
    bucket = f"{int(line // 5) * 5}-{int(line // 5) * 5 + 4.5}"
    bucket_row = profiles.get("line_buckets", {}).get(bucket, {}) if isinstance(profiles, dict) else {}
    if safe_int(bucket_row.get("samples"), 0) >= MIN_CONTEXT_LEARNING_SAMPLES:
        pieces.append((safe_float(bucket_row.get("bias"), 0.0) or 0.0, 0.10, "line bucket"))
    map_rows = []
    for map_name in likely_maps[:2]:
        row = profiles.get("maps", {}).get(str(map_name), {}) if isinstance(profiles, dict) else {}
        if safe_int(row.get("samples"), 0) >= MIN_CONTEXT_LEARNING_SAMPLES:
            map_rows.append(safe_float(row.get("bias"), 0.0) or 0.0)
    if map_rows:
        pieces.append((float(np.mean(map_rows)), 0.10, "map context"))
    tier_row = profiles.get("event_tiers", {}).get(str(event_tier), {}) if isinstance(profiles, dict) else {}
    if safe_int(tier_row.get("samples"), 0) >= MIN_CONTEXT_LEARNING_SAMPLES:
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
    sqlite_store_market_ticks(props)
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
    total_direct = np.zeros(n, dtype=int)
    total_share = np.zeros(n, dtype=int)
    blend = learned_simulation_blend(player, role)
    map_expected: Dict[str, Dict[str, float]] = defaultdict(lambda: {"rounds": 0.0, "kills": 0.0, "weight": 0.0})

    profile_uncertainty = 0.025 + 0.050 / math.sqrt(max(profile_maps, 1) / 10.0 + 1.0)
    if "Unknown" in role:
        profile_uncertainty += 0.012
    if "Entry" in role:
        profile_uncertainty += 0.008
    profile_uncertainty *= context_variance_multiplier
    common_form = rng.normal(0.0, profile_uncertainty, size=n)
    map_factors = opponent_meta.get("map_factors") or {}
    # V4 team-total consistency: player kills are partly generated as a share
    # of simulated team kills, preventing five independent player models from
    # implying impossible team totals.
    pseudo_profile = PlayerStats(player=player, maps=profile_maps, kpr=base_kpr)
    share_params = team_kill_share_parameters(pseudo_profile, role, opponent_meta)

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
            p_ct=safe_float(model.get("team_ct_round_win_prob"),None)
            p_t=safe_float(model.get("team_t_round_win_prob"),None)
            round_env=None
            if p_ct is not None and p_t is not None:
                round_env=_simulate_mr12_round_environment(rng,count,p_ct,p_t,map_name)
                map_rounds=round_env["rounds"]
            else:
                map_rounds=np.rint(rng.normal(mean_rounds,round_sd,size=count)).astype(int)
                map_rounds=np.clip(map_rounds,13,42)
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
            direct_kills = rng.poisson(gamma_rate)
            team_kills = round_env["team_kills"] if round_env is not None else rng.poisson(np.clip(map_rounds*share_params["team_kpr"],4.0,120.0))
            alpha = max(share_params["player_share"] * share_params["concentration"], 0.5)
            beta = max((1-share_params["player_share"]) * share_params["concentration"], 0.5)
            latent_share = rng.beta(alpha, beta, size=count)
            share_kills = rng.binomial(team_kills, np.clip(latent_share, 0.05, 0.40))
            use_share = rng.random(count) < blend["share_weight"]
            kills = np.where(use_share, share_kills, direct_kills)
            scenario_rounds += map_rounds
            scenario_kills += kills
            total_direct[mask] += direct_kills
            total_share[mask] += share_kills
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
        "team_kill_share_model": {**share_params,"blend":blend},
        "model_components": {"direct_projection":float(np.mean(total_direct)),"share_projection":float(np.mean(total_share)),"share_weight":blend.get("share_weight"),"blend_sample":blend.get("sample",0),"blend_scope":blend.get("scope")},
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



def _match_id_from_url(url: str) -> str:
    m=re.search(r"/matches/(\d+)/",str(url or "")); return m.group(1) if m else ""



def sqlite_record_roster_event(player: str, team: str, event_type: str, effective_at: str, confidence: float, payload: Dict[str, Any]) -> None:
    raw=f"{normalize_name(player)}|{normalize_team(team)}|{event_type}|{str(effective_at)[:16]}"
    event_id=hashlib.sha256(raw.encode()).hexdigest()
    try:
        with _sqlite_connect() as conn:
            conn.execute("""INSERT OR REPLACE INTO roster_events(event_id,player_key,player,team_key,team,event_type,effective_at,confidence,source,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (event_id,normalize_name(player),player,normalize_team(team),team,event_type,effective_at,float(confidence),"verified match page + current roster",json.dumps(payload,default=str),now_iso()))
    except Exception:
        pass


def roster_transaction_risk(player: str, team: str, as_of: Any = None) -> Dict[str, Any]:
    cutoff=_parse_iso_datetime(as_of) or datetime.now(timezone.utc); rows=[]
    try:
        with _sqlite_connect() as conn:
            rows=conn.execute("SELECT * FROM roster_events WHERE player_key=? ORDER BY effective_at DESC LIMIT 20",(normalize_name(player),)).fetchall()
    except Exception:
        rows=[]
    changes=[]
    for r in rows:
        dt=_parse_iso_datetime(r["effective_at"])
        if dt and 0 <= (cutoff-dt).total_seconds()/86400 <= 45: changes.append(dict(r))
    teams={normalize_team(x.get("team")) for x in changes}; latest=changes[0] if changes else {}
    risky=latest.get("event_type") in {"OUTSIDE_CURRENT_ROSTER","LINEUP_MISMATCH","POSSIBLE_STANDIN"} or len(teams)>1
    return {"recent_events":len(changes),"recent_team_count":len(teams),"risky":risky,"latest_event_type":latest.get("event_type"),"events":changes[:8]}


def roster_identity_assessment(player: str, team: str, opponent: str, profile: PlayerStats, context: Dict[str, Any], prop: Dict[str, Any], context_status: Dict[str, Any], map_meta: Dict[str, Any]) -> Dict[str, Any]:
    teams=context.get("teams") or []; team_matches=[x for x in teams if _team_name_matches(team,x.get("name",""))]; opp_matches=[x for x in teams if _team_name_matches(opponent,x.get("name",""))]
    match_id=_match_id_from_url(context.get("match_url") or prop.get("match_url") or "")
    lineup=context.get("lineup_names") or []; lineup_score=max([name_similarity(player,x) for x in lineup] or [0]); lineup_verified=bool(lineup and lineup_score>=.84)
    profiles=context.get("team_deep_profiles") or []; tp=next((p for p in profiles if _team_name_matches(team,p.get("team",""))),{})
    current=tp.get("current_roster") or []; roster_score=max([name_similarity(player,x) for x in current] or [0]); current_roster_verified=bool(current and roster_score>=.84)
    flags=[]; hard_pass=False
    if not profile.player_id: flags.append("VERIFIED PLAYER ID MISSING")
    if not match_id: flags.append("VERIFIED MATCH ID MISSING")
    if not team_matches or not opp_matches: flags.append("TEAM/OPPONENT ID MATCH FAILED"); hard_pass=True
    if lineup and not lineup_verified: flags.append("PLAYER NOT IN ANNOUNCED LINEUP"); hard_pass=True
    elif not lineup: flags.append("LINEUP UNCONFIRMED")
    if current and not current_roster_verified: flags.append("PLAYER OUTSIDE CURRENT TEAM ROSTER"); hard_pass=True
    overlap=len({normalize_name(x) for x in lineup}&{normalize_name(x) for x in current}) if lineup and current else 0
    if lineup and current and overlap<4: flags.append("ROSTER CORE MISMATCH / POSSIBLE STAND-IN"); hard_pass=True
    line_age=max(0.0,(datetime.now(timezone.utc)-(_parse_iso_datetime(prop.get("source_pulled_at")) or datetime.now(timezone.utc))).total_seconds())
    match_age=safe_float(context_status.get("age_seconds"),None)
    if match_age is None: match_age=999999
    veto_state=veto_state_from_meta(map_meta); veto_fresh=(match_age<=120 if veto_state=="CONFIRMED" else match_age<=300)
    if line_age>180: flags.append("UNDERDOG LINE STALE")
    if match_age>300: flags.append("MATCH/LINEUP SOURCE STALE")
    if veto_state=="CONFIRMED" and not veto_fresh: flags.append("CONFIRMED VETO STALE")
    event_type="VERIFIED_CURRENT_ROSTER" if lineup_verified and current_roster_verified and overlap>=4 else "POSSIBLE_STANDIN" if hard_pass else "LINEUP_UNCONFIRMED"
    sqlite_record_roster_event(player,team,event_type,str(prop.get("start_time") or now_iso()),min(lineup_score,roster_score) if current else lineup_score,{"lineup":lineup,"current_roster":current,"overlap":overlap,"match_id":match_id})
    transaction_risk=roster_transaction_risk(player,team,prop.get("start_time"))
    if transaction_risk.get("risky"):
        flags.append("RECENT STRUCTURED ROSTER TRANSACTION RISK"); hard_pass=True
    official_ready=bool(profile.player_id and match_id and team_matches and opp_matches and lineup_verified and current_roster_verified and overlap>=4 and line_age<=180 and veto_fresh and not transaction_risk.get("risky"))
    return {"hard_pass":hard_pass,"official_ready":official_ready,"lineup_verified":lineup_verified,"current_roster_verified":current_roster_verified,"roster_overlap":overlap,"transaction_risk":transaction_risk,
            "match_id":match_id,"team_id":team_matches[0].get("team_id") if team_matches else "","opponent_id":opp_matches[0].get("team_id") if opp_matches else "",
            "player_id":profile.player_id,"line_age_seconds":line_age,"match_age_seconds":match_age,"veto_fresh":veto_fresh,"flags":flags}


def build_projection_for_prop(
    prop: Dict[str, Any],
    long_table: Dict[str, Dict[str, Any]],
    medium_table: Dict[str, Dict[str, Any]],
    recent30_table: Dict[str, Dict[str, Any]],
    recent15_table: Dict[str, Dict[str, Any]],
    deep_enabled: bool = True,
) -> Dict[str, Any]:
    player = str(prop.get("player", "")).strip()
    line = safe_float(prop.get("line"), None)
    if not player or line is None:
        return {**prop, "error": "Missing player or real line", "status": "PASS", "status_label": "🚫 PASS"}
    market_ok, market_identity_confidence, market_identity_method, market_identity_flags = market_identity_validation(prop)
    if not market_ok:
        return {
            **prop, "projection": None, "lean": "PASS", "probability": 0.0, "raw_probability": 0.0,
            "edge": None, "status": "PASS", "status_label": "🚫 PASS", "data_score": 0,
            "market_scope_verified": False, "market_identity_confidence": market_identity_confidence,
            "market_identity_method": market_identity_method, "flags": market_identity_flags,
            "error": "Exact CS2 Maps 1-2 kill market identity was not verified"
        }
    if not (MIN_M12_KILL_LINE <= float(line) <= MAX_M12_KILL_LINE):
        return {
            **prop, "projection": None, "lean": "PASS", "probability": 0.0, "edge": None,
            "status": "PASS", "status_label": "🚫 PASS", "data_score": 0,
            "flags": ["INVALID MAPS 1-2 LINE", "LIKELY SINGLE-MAP MARKET"],
            "error": f"Line {line} is outside valid Maps 1-2 kill range"
        }

    profile, profile_meta = build_player_profile(player, long_table, medium_table, recent30_table, recent15_table)
    team = str(prop.get("team") or profile.team or "")
    opponent = str(prop.get("opponent") or "")
    # Never manufacture a projection from league-average KPR when the player did
    # not match a real profile. Database fallback is allowed only when it contains
    # an actual historical sample.
    if int(profile.maps or 0) <= 0:
        dbp = lookup_database_player(player)
        if not dbp or int(dbp.get("profile_maps") or 0) <= 0:
            return {
                **prop, "team": team, "opponent": opponent, "projection": None,
                "lean": "PASS", "probability": 0.0, "edge": None, "expected_rounds": None,
                "adjusted_kpr": None, "base_kpr": None, "profile_maps": 0,
                "likely_maps": "Unconfirmed", "map_confidence": 0.0,
                "status": "PASS", "status_label": "🚫 PASS", "data_score": 0,
                "flags": ["NO REAL PLAYER PROFILE", "PROJECTION BLOCKED — NO DEFAULT KPR"],
                "error": "Player could not be matched to verified historical CS2 data"
            }
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
    veto_state = veto_state_from_meta(map_meta)
    patch_era = patch_era_for_time(prop.get("start_time"))
    active_maps = set(patch_era.get("active_maps") or KNOWN_MAPS)
    inactive_likely_maps = [m for m in likely_maps if m not in active_maps]
    expected_rounds, rounds_sd, rounds_meta = project_expected_rounds(context, likely_maps, map_meta, team, opponent)
    role, role_confidence, role_method = get_player_role(player, profile)
    player_map_profiles, player_map_status = build_player_map_profiles(profile, likely_maps, prop.get("start_time"), patch_era) if deep_enabled else ({}, {"ok": False, "message": "deep data disabled"})
    matchup_factor, matchup_meta = opponent_kpr_factor(profile, context, team, opponent, likely_maps, role)
    sos_factor, sos_meta = strength_of_schedule_adjustment(context, profile, team, opponent)
    matchup_factor = clamp(matchup_factor * sos_factor, 0.90, 1.10)
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
    raw_probability = sim["over_prob"] if lean == "OVER" else sim["under_prob"]
    calibration = calibrate_probability(raw_probability, {
        "lean": lean, "veto_state": veto_state, "event_tier": context.get("event_tier", "")
    })
    probability = calibration["calibrated"]
    saved_odds = get_saved_odds(player, float(line))
    over_nv, under_nv = no_vig_probs(safe_float(saved_odds.get("over_odds"), None), safe_float(saved_odds.get("under_odds"), None))
    market_prob = over_nv if lean == "OVER" else under_nv
    consensus_line = safe_float(prop.get("consensus_line"), None)
    market_source_count = safe_int(prop.get("market_source_count"), 0) or 0
    consensus_edge = (sim["projection"] - consensus_line) if consensus_line is not None else None
    market_direction_agrees = None
    if market_prob is not None:
        market_direction_agrees = market_prob >= 0.50
        market_edge = probability - market_prob
        market_agreement = bool(market_direction_agrees and market_edge >= MIN_MARKET_VALUE_EDGE)
        market_method = "sportsbook no-vig positive-value test"
    elif consensus_line is not None and market_source_count >= 2:
        market_agreement = (consensus_edge >= 0 and lean == "OVER") or (consensus_edge <= 0 and lean == "UNDER")
        market_edge = None
        market_method = "multi-board line consensus"
    else:
        market_agreement = None
        market_edge = None
        market_method = "single-board/no odds"

    identity = roster_identity_assessment(player,team,opponent,profile,context,prop,context_status,map_meta)
    core_kpr_verified = bool(profile_meta.get("core_kpr_verified"))
    data_score, data_notes, data_components = calculate_data_score_deep(
        profile, profile_meta, context, map_confidence, role_confidence, match_url,
        player_map_profiles, map_meta, matchup_meta, context_meta, market_source_count,
    )
    status, status_label, flags = classify_play_deep(
        lean, probability, edge, data_score, profile, context, map_confidence,
        market_agreement, player_map_profiles, matchup_meta, context_meta,
    )
    flags.extend(identity.get("flags") or [])
    if identity.get("hard_pass"):
        status,status_label="PASS","🚫 PASS — ID/ROSTER FAILURE"
    elif not core_kpr_verified:
        if status in {"OFFICIAL","PLAYABLE"}: status,status_label="TRACK","⚠️ TRACK — CORE KPR ESTIMATED"
        flags.append("CORE KPR NOT VERIFIED FROM REAL KILLS/ROUNDS")
    elif not profile_meta.get("source_fresh"):
        if status in {"OFFICIAL","PLAYABLE"}: status,status_label="TRACK","⚠️ TRACK — PLAYER DATA STALE"
        flags.append("PLAYER FORM DATA EXCEEDS 24-HOUR FRESHNESS POLICY")
    elif not (context.get("deep_source_health") or {}).get("roster_fresh",False):
        if status in {"OFFICIAL","PLAYABLE"}: status,status_label="TRACK","⚠️ TRACK — ROSTER SOURCE STALE"
        flags.append("CURRENT ROSTER DATA EXCEEDS 4-HOUR FRESHNESS POLICY")
    elif not (context.get("deep_source_health") or {}).get("team_map_fresh",False) and status=="OFFICIAL":
        status,status_label="PLAYABLE","✅ PLAYABLE — TEAM MAP DATA STALE"
        flags.append("TEAM MAP PROFILE EXCEEDS 24-HOUR FRESHNESS POLICY")
    elif not identity.get("official_ready") and status=="OFFICIAL":
        status,status_label="TRACK","⚠️ TRACK — LINEUP/ID/FRESHNESS UNVERIFIED"
    if inactive_likely_maps:
        status, status_label = "PASS", "🚫 PASS"
        flags.append("MAP OUTSIDE CONFIGURED PATCH ERA")
    if status == "OFFICIAL" and veto_state == "PRE_VETO" and (probability < 0.645 or abs(edge) < 2.50):
        status, status_label = "PLAYABLE", "✅ PLAYABLE — PRE-VETO"
        flags.append("PRE-VETO REQUIRES LARGER EDGE")
    if status == "OFFICIAL" and market_agreement is None and (probability < 0.655 or abs(edge) < 2.75):
        status, status_label = "PLAYABLE", "✅ PLAYABLE — NO MARKET CONFIRMATION"
        flags.append("NO EXTERNAL MARKET CONFIRMATION")
    if status == "OFFICIAL" and not calibration.get("ready"):
        status, status_label = "TRACK", "⚠️ TRACK — CALIBRATION NOT USABLE"
        flags.append("FEWER THAN 300 COMPARABLE GRADED PROJECTIONS")
    share_model=sim.get("team_kill_share_model") or {}; blend_meta=(share_model.get("blend") or {})
    if status=="OFFICIAL" and (share_model.get("team_kpr_source") != "recent team scoreboard" or safe_int(share_model.get("trained_sample"),0)<MIN_BLEND_TRAINING_SAMPLES or safe_int(blend_meta.get("sample"),0)<MIN_BLEND_TRAINING_SAMPLES):
        status,status_label="PLAYABLE","✅ PLAYABLE — SIMULATION PRIORS NOT TRAINED"
        flags.append("TEAM KILL-SHARE / DIRECT-BLEND PARAMETERS NOT YET TRAINED")
    movement = line_movement(player, prop.get("market", "Maps 1-2 Kills"), prop.get("start_time", ""), float(line))
    lineup_names = context.get("lineup_names") or []
    lineup_verified = bool(lineup_names and max([name_similarity(player, x) for x in lineup_names] or [0]) >= 0.80)
    opponent_profile = matchup_meta.get("opponent_profile") or {}
    team_profile = matchup_meta.get("team_profile") or {}
    role_timeline_update(player, team, role, role_confidence, str(prop.get("start_time") or now_iso()), safe_int(context_meta.get("current_roster_maps"), 0) or 0)
    role_history = role_timeline_risk(player, team, role)

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
        "raw_probability": round(raw_probability, 4),
        "calibration_sample": calibration.get("sample", 0),
        "calibration_ready": calibration.get("ready", False),
        "calibration_method": calibration.get("method"),
        "calibration_empirical_rate": calibration.get("empirical_rate"),
        "calibration_tier": calibration.get("tier"),
        "core_kpr_verified": core_kpr_verified,
        "player_source_fresh":bool(profile_meta.get("source_fresh")),
        "player_source_ages":profile_meta.get("source_ages") or {},
        "kpr_source": profile.kpr_source,
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
        "role_history": role_history,
        "likely_maps": likely_maps,
        "map_confidence": round(map_confidence, 1),
        "map_scenarios": (map_meta.get("scenarios") or [])[:8],
        "veto_state": veto_state,
        "patch_era": patch_era.get("name"),
        "patch_era_note": patch_era.get("note"),
        "inactive_likely_maps": inactive_likely_maps,
        "match_format": context.get("format", "UNKNOWN"),
        "environment": context.get("environment", "UNKNOWN"),
        "stage": context.get("stage", ""),
        "event": context.get("event", ""),
        "event_tier": context.get("event_tier", "LOW/UNKNOWN"),
        "world_ranks": context.get("world_ranks", []),
        "lineup_verified": identity.get("lineup_verified",lineup_verified),
        "confirmed_lineup_names": list(lineup_names),
        "confirmed_lineup_groups": context.get("lineup_groups") or [],
        "confirmed_starting_side_hints": context.get("starting_side_hints") or {},
        "current_roster_names": list((team_profile.get("current_roster") or [])),
        "current_roster_verified": identity.get("current_roster_verified"),
        "roster_overlap": identity.get("roster_overlap"),
        "roster_transaction_risk": identity.get("transaction_risk"),
        "identity_official_ready": identity.get("official_ready"),
        "identity_ids": {k:identity.get(k) for k in ["player_id","match_id","team_id","opponent_id"]},
        "source_freshness": {**{k:identity.get(k) for k in ["line_age_seconds","match_age_seconds","veto_fresh"]},**(context.get("deep_source_health") or {})},
        "standin_warning": bool(context.get("standin_warning")),
        "current_roster_maps": safe_int(context_meta.get("current_roster_maps"), 0) or 0,
        "roster_stability": round(safe_float(context_meta.get("roster_stability"), 0) or 0, 3),
        "rest_days": context_meta.get("rest_days"),
        "opponent_deaths_allowed_pr": opponent_profile.get("deaths_allowed_per_round"),
        "opponent_mapstats_samples": safe_int(opponent_profile.get("mapstats_samples"), 0) or 0,
        "team_recent_maps": safe_int(team_profile.get("recent_maps"), 0) or 0,
        "strength_of_schedule_factor": round(sos_factor, 4),
        "strength_of_schedule_meta": sos_meta,
        "team_kill_share_model": sim.get("team_kill_share_model") or {},
        "model_components": sim.get("model_components") or {},
        "market_scope_verified": True,
        "market_identity_confidence": round(market_identity_confidence, 3),
        "market_identity_method": market_identity_method,
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
        "market_direction_agrees": market_direction_agrees,
        "market_positive_value": bool(market_edge is not None and market_edge >= MIN_MARKET_VALUE_EDGE),
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
            "strength_of_schedule": sos_meta,
            "patch_era": patch_era,
            "calibration": calibration,
            "context_adjustment": context_meta,
            "learning": learning_meta,
        },
    }


def build_full_board(props: List[Dict[str, Any]], deep_enabled: bool = True) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if not props:
        return [], {"profiles": {}, "status": "No real lines"}
    valve_map_pool_status=refresh_active_map_pool_from_valve()
    demo_drop_status=auto_ingest_demo_dropbox() if AUTO_HARVEST_HISTORY else {"ok":False,"disabled":True}
    long_table, long_status = fetch_hltv_player_table(180)
    medium_table, medium_status = fetch_hltv_player_table(60)
    recent30_table, recent30_status = fetch_hltv_player_table(30)
    recent15_table, recent15_status = fetch_hltv_player_table(15)
    results: List[Dict[str, Any]] = []
    # Keep concurrency low to be respectful and reduce blocking. Cached global
    # tables make most work local; individual profiles are cached for four hours.
    max_workers = min(5, max(1, len(props)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(build_projection_for_prop, prop, long_table, medium_table, recent30_table, recent15_table, deep_enabled): prop
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
        "long": long_status, "medium": medium_status, "recent30": recent30_status, "recent15": recent15_status,
        "deep_data_enabled": deep_enabled, "valve_map_pool":valve_map_pool_status, "demo_dropbox":demo_drop_status,
        "long_rows": len(long_table), "medium_rows": len(medium_table), "recent30_rows": len(recent30_table), "recent15_rows": len(recent15_table)
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
    links=[]
    for href in re.findall(r'href=["\']([^"\']*mapstatsid/(\d+)/[^"\']+)["\']',match_page,flags=re.I):
        full=urljoin(HLTV_BASE,href[0])
        if full not in links: links.append(full)
    return links


def _mapstats_id(url: str) -> str:
    m=re.search(r"mapstatsid/(\d+)/",str(url or "")); return m.group(1) if m else ""


def parse_mapstats_map_name(page: str) -> str:
    if not page: return ""
    head=strip_tags(page[:25000])
    hits=[]
    for m in KNOWN_MAPS:
        pos=re.search(rf"\b{re.escape(m.replace('2',' II'))}\b|\b{re.escape(m)}\b",head,re.I)
        if pos: hits.append((pos.start(),m))
    return min(hits)[1] if hits else ""


def parse_map_player_kills(page: str, player: str, player_id: str = "") -> Tuple[Optional[int], Dict[str, Any]]:
    if not page:
        return None,{"matched":False}
    best=(0.0,None,"","")
    for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>",page,flags=re.I|re.S):
        anchor=re.search(r'href=["\']/stats/players?/(\d+)/[^"\']+["\'][^>]*>(.*?)</a>',tr,flags=re.I|re.S)
        if not anchor: anchor=re.search(r'href=["\']/player/(\d+)/[^"\']+["\'][^>]*>(.*?)</a>',tr,flags=re.I|re.S)
        if not anchor: continue
        pid=anchor.group(1); candidate=strip_tags(anchor.group(2)).replace("\n"," ").strip()
        score=1.0 if player_id and pid==str(player_id) else name_similarity(player,candidate)
        if player_id and pid!=str(player_id): continue
        if score<.84: continue
        text=strip_tags(tr).replace("\n"," "); kd=re.search(r"\b(\d{1,2})\s*[-–]\s*(\d{1,2})\b",text)
        kills=safe_int(kd.group(1),None) if kd else None
        if kills is None:
            nums=[safe_int(x) for x in re.findall(r"\b\d{1,2}\b",text)]; kills=nums[0] if nums else None
        if score>best[0] and kills is not None: best=(score,kills,candidate,pid)
    return best[1],{"matched":best[1] is not None,"score":round(best[0],3),"name":best[2],"player_id":best[3],"exact_id":bool(player_id and best[3]==str(player_id))}


def parse_player_team_total_kills(page: str, player: str) -> Tuple[Optional[int], Dict[str, Any]]:
    tables=parse_mapstats_team_tables(page or "",())
    best=None; best_score=0.0
    for table in tables:
        score=max([name_similarity(player,x.get("player","")) for x in table.get("players",[])] or [0])
        if score>best_score:
            best_score=score; best=table
    if best is None or best_score<.78:
        return None,{"matched":False,"score":best_score}
    return safe_int(best.get("total_kills"),None),{"matched":True,"score":best_score,"team":best.get("team"),"players":len(best.get("players",[]))}


def fetch_actual_maps12_kills(match_url: str, player: str, player_id: str = "") -> Tuple[Optional[int], Dict[str, Any]]:
    match_page,match_status=http_get_text(match_url,"HLTV grade match",ttl=4*60,timeout=20,allow_stale=False)
    if not match_page: return None,{"ok":False,"match_status":match_status}
    lower=strip_tags(match_page).lower()
    void_terms=[x for x in ["walkover","forfeit","technical win","match cancelled","match postponed"] if x in lower]
    if void_terms: return None,{"ok":False,"void_reason":", ".join(void_terms),"message":"Match requires manual/void review"}
    map_results=_extract_map_results(match_page); links=_mapstats_links(match_page)
    if len(map_results)<2: return None,{"ok":False,"message":"Two chronological completed maps not confirmed","map_results":map_results}
    if len(links)<2: return None,{"ok":False,"message":"Fewer than two completed map-stat links","links":links}
    fetched=[]
    for url in links[:5]:
        page,status=http_get_text(url,"HLTV grade map",ttl=4*60,timeout=20,allow_stale=False)
        fetched.append({"url":url,"map_id":_mapstats_id(url),"map":parse_mapstats_map_name(page or ""),"page":page or "","status":status})
    ordered=[]; used=set()
    for result in map_results[:2]:
        target=result.get("map"); candidates=[x for x in fetched if x["map_id"] not in used and x.get("map")==target]
        if len(candidates)!=1:
            return None,{"ok":False,"message":f"Could not uniquely match chronological {target} map-stat page","map_results":map_results[:2],"fetched_maps":[{k:x.get(k) for k in ["url","map_id","map"]} for x in fetched]}
        ordered.append(candidates[0]); used.add(candidates[0]["map_id"])
    kills_total=0; team_total=0; team_verified=True; details=[]; scores=[]
    for rec in ordered:
        kills,meta=parse_map_player_kills(rec["page"],player,player_id); tk,tm=parse_player_team_total_kills(rec["page"],player)
        scores.append(safe_float(meta.get("score"),0) or 0)
        if tk is None: team_verified=False
        else: team_total+=int(tk)
        detail={"url":rec["url"],"map_id":rec["map_id"],"map":rec["map"],"kills":kills,"meta":meta,"team_total_kills":tk,"team_meta":tm,"status":rec["status"]}; details.append(detail)
        if kills is None: return None,{"ok":False,"message":"Player not matched on one of first two chronological maps","details":details}
        kills_total+=int(kills)
    confidence=min(scores) if scores else 0.0
    if player_id and not all((x.get("meta") or {}).get("exact_id") for x in details): confidence=min(confidence,.70)
    meta={"ok":confidence>=.84,"confidence":confidence,"details":details,"map_links":[x["url"] for x in ordered],"map_results":map_results[:2],"team_total_kills":team_total if team_verified else None,"observed_player_share":kills_total/team_total if team_verified and team_total>0 else None,"total_kills":kills_total,"match_id":_match_id_from_url(match_url)}
    if confidence<.84: return None,{**meta,"message":"Grading identity confidence below 0.84"}
    return kills_total,meta

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
        actual, meta = fetch_actual_maps12_kills(match_url, str(row.get("player", "")), str((row.get("identity_ids") or {}).get("player_id") or ""))
        sqlite_store_grading_audit(str(row.get("snapshot_id") or snapshot_key(row)),str(row.get("player") or ""),meta)
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
            "team_total_kills": meta.get("team_total_kills"),
            "observed_player_share": meta.get("observed_player_share"),
        })
        results.append(result_row)
        result_ids.add(row.get("snapshot_id"))
        row["actual_kills"] = int(actual)
        row["graded_result"] = graded_label
        row["graded_at"] = now_iso()
        graded += 1
    save_json(PICK_LOG, picks)
    save_json(RESULT_LOG, results, github_backup=bool(get_secret("GITHUB_AUTO_BACKUP", "").lower() in {"1", "true", "yes"}))
    # Grade every valid board projection saved in SQLite, not only selected picks.
    for row in sqlite_pending_projection_rows():
        sid=row.get("snapshot_id")
        if not sid or not row.get("match_url"): continue
        player_id=str((row.get("identity_ids") or {}).get("player_id") or "")
        actual,meta=fetch_actual_maps12_kills(str(row.get("match_url")),str(row.get("player","")),player_id)
        sqlite_store_grading_audit(str(sid),str(row.get("player") or ""),meta)
        if actual is None: continue
        label=grade_result(str(row.get("lean")),float(row.get("line")),float(actual))
        sqlite_mark_projection_graded(str(sid),label,float(actual),meta); graded+=1
    if graded:
        build_learning_profiles()
        save_calibration_state()
    return {"graded": graded, "pending": pending, "errors": errors, "diagnostics": diagnostics[:100], "all_board_calibration_rows": len(sqlite_graded_projection_rows())}


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
        build_learning_profiles()
        save_calibration_state()
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
        "raw_probability", "calibration_sample", "veto_state", "patch_era", "strength_of_schedule_factor",
        "full_lineup_model", "multi_book_market",
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
        row["likely_maps"] = format_likely_maps(x.get("likely_maps"))
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
    map_text = format_likely_maps(row.get("likely_maps"))
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
    <div class="metric-box"><div class="metric-label">Accuracy Health</div><div class="metric-value">{_esc((row.get('accuracy_health') or {}).get('score'))}/100</div><div class="small-muted">Disagreement {_esc(row.get('model_disagreement'))}</div></div>
    <div class="metric-box"><div class="metric-label">Calibrated Range</div><div class="metric-value" style="font-size:17px;">{_fmt_pct(row.get('calibration_lower90'))}–{_fmt_pct(row.get('calibration_upper90'))}</div><div class="small-muted">Local n={_esc(row.get('calibration_local_sample'))}</div></div>
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
                "Calibrated 90% range": [row.get("calibration_lower90"), row.get("calibration_upper90")],
                "Local calibration sample": row.get("calibration_local_sample"),
                "Direct/share model disagreement": row.get("model_disagreement"),
                "Accuracy health": row.get("accuracy_health"),
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
# V4 — HISTORICAL / CALIBRATION / DEMO / CORRELATION SYSTEMS
# ============================================================

def canonical_map_name(value: Any) -> str:
    raw = str(value or "").strip()
    norm = normalize_name(raw).replace("de ", "")
    for known in KNOWN_MAPS:
        kn = normalize_name(known)
        if norm == kn or kn in norm or norm in kn:
            return known
    return raw.title() if raw else ""


def american_to_decimal(odds: float) -> Optional[float]:
    try:
        value = float(odds)
    except Exception:
        return None
    if value == 0:
        return None
    return 1.0 + (100.0 / abs(value) if value < 0 else value / 100.0)

def _append_jsonl(path: str, row: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def _read_jsonl(path: str, limit: int = 10000) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    value = json.loads(line)
                    if isinstance(value, dict):
                        rows.append(value)
                except Exception:
                    continue
        return rows[-limit:]
    except Exception:
        return []


def market_identity_validation(prop: Dict[str, Any]) -> Tuple[bool, float, str, List[str]]:
    """Require sport + stat + map scope, never line size alone."""
    source = str(prop.get("source") or "")
    scope = normalize_name(prop.get("market_scope") or "")
    evidence = " | ".join(str(prop.get(k) or "") for k in [
        "market", "stat_name", "evidence", "market_identity_method", "sport_id"
    ])
    explicit = _is_cs2_m12_kills(evidence)
    verified_flag = bool(prop.get("market_scope_verified"))
    line = safe_float(prop.get("line"), None)
    flags: List[str] = []
    if line is None or not (MIN_M12_KILL_LINE <= line <= MAX_M12_KILL_LINE):
        flags.append("INVALID MAPS 1-2 LINE RANGE")
    if scope not in {"maps 1 2", "maps_1_2", "m1 m2"} and not explicit:
        flags.append("MAP SCOPE NOT VERIFIED")
    if not explicit:
        flags.append("EXACT MARKET LABEL NOT VERIFIED")
    if source == "Underdog" and not verified_flag:
        flags.append("UNDERDOG RELATIONSHIP SCOPE UNVERIFIED")
    if source == "Underdog" and not str(prop.get("source_line_id") or prop.get("prop_id") or ""):
        flags.append("UNDERDOG LINE ID MISSING")
    confidence = 0.0
    if explicit:
        confidence += 0.55
    if verified_flag:
        confidence += 0.25
    if prop.get("appearance_id") or prop.get("game_id"):
        confidence += 0.10
    if line is not None and MIN_M12_KILL_LINE <= line <= MAX_M12_KILL_LINE:
        confidence += 0.10
    confidence = clamp(confidence, 0.0, 1.0)
    return not flags, confidence, "exact relationship/label validation" if not flags else "market identity incomplete", flags


def _graded_binary_rows() -> List[Dict[str, Any]]:
    combined=[]; seen=set()
    raw=load_json(RESULT_LOG,[]); raw=raw if isinstance(raw,list) else []
    for row in raw+sqlite_graded_projection_rows():
        result=str(row.get("graded_result") or "").upper(); p=safe_float(row.get("raw_probability"),None) or safe_float(row.get("probability"),None)
        if result not in {"WIN","LOSS"} or p is None: continue
        key=str(row.get("snapshot_id") or snapshot_key(row))
        if key in seen: continue
        seen.add(key); item=dict(row); item["_p"]=clamp(float(p),.001,.999); item["_y"]=1 if result=="WIN" else 0
        item["_time"]=str(row.get("projection_time") or row.get("saved_at") or row.get("graded_at") or ""); combined.append(item)
    combined.sort(key=lambda x:x.get("_time","")); return combined


def _calibration_subset(rows: List[Dict[str, Any]], context: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    if not context:
        return rows
    lean = str(context.get("lean") or "")
    veto = str(context.get("veto_state") or "")
    tier = str(context.get("event_tier") or "")
    scoped = [x for x in rows if (not lean or str(x.get("lean") or "") == lean)]
    if len(scoped) >= 35 and veto:
        narrower = [x for x in scoped if str(x.get("veto_state") or "") == veto]
        if len(narrower) >= 25:
            scoped = narrower
    if len(scoped) >= 60 and tier:
        narrower = [x for x in scoped if str(x.get("event_tier") or "") == tier]
        if len(narrower) >= 25:
            scoped = narrower
    return scoped



def _isotonic_fit_predict(rows: List[Dict[str, Any]], raw: float) -> Optional[float]:
    if len(rows)<40: return None
    pairs=sorted((float(x["_p"]),float(x["_y"])) for x in rows)
    blocks=[]
    for p,y in pairs:
        blocks.append([p,p,y,1])
        while len(blocks)>=2 and blocks[-2][2]/blocks[-2][3] > blocks[-1][2]/blocks[-1][3]:
            b=blocks.pop(); a=blocks.pop(); blocks.append([a[0],b[1],a[2]+b[2],a[3]+b[3]])
    points=[((a+b)/2,s/n) for a,b,s,n in blocks]
    if raw<=points[0][0]: return points[0][1]
    if raw>=points[-1][0]: return points[-1][1]
    for (x1,y1),(x2,y2) in zip(points,points[1:]):
        if x1<=raw<=x2:
            t=(raw-x1)/max(x2-x1,1e-9); return y1+t*(y2-y1)
    return None


def calibrate_probability(raw_probability: float, context: Optional[Dict[str, Any]] = None, prior_rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    raw=clamp(float(raw_probability),.001,.999); rows=_calibration_subset(prior_rows if prior_rows is not None else _graded_binary_rows(),context)
    n=len(rows); iso=_isotonic_fit_predict(rows,raw)
    if iso is None:
        near=[x for x in rows if abs(x["_p"]-raw)<=.075] or rows; wins=sum(x["_y"] for x in near); nn=len(near); prior=30.0
        empirical=(wins+raw*prior)/(nn+prior) if nn else raw; reliability=nn/(nn+80.0); calibrated=raw*(1-reliability)+empirical*reliability; method="beta-bin shrinkage"
        sample=nn; empirical_rate=wins/nn if nn else None
    else:
        reliability=n/(n+180.0); calibrated=raw*(1-reliability)+iso*reliability; method="walk-forward isotonic + shrinkage"; sample=n; empirical_rate=iso
    ceiling=.70 if sample<MIN_CALIBRATION_PRELIMINARY else .76 if sample<MIN_CALIBRATION_USABLE else .82
    calibrated=clamp(calibrated,.50,ceiling)
    tier="UNREADY" if sample<MIN_CALIBRATION_PRELIMINARY else "PRELIMINARY" if sample<MIN_CALIBRATION_USABLE else "USABLE" if sample<MIN_CALIBRATION_STRONG else "STRONG"
    return {"raw":raw,"calibrated":calibrated,"sample":sample,"empirical_rate":empirical_rate,"method":method,"ready":sample>=MIN_CALIBRATION_USABLE,"tier":tier}


def calibration_metrics(rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    rows = rows if rows is not None else _graded_binary_rows()
    if not rows:
        return {"n": 0, "brier": None, "log_loss": None, "ece": None, "bins": []}
    ps = np.array([x["_p"] for x in rows], dtype=float)
    ys = np.array([x["_y"] for x in rows], dtype=float)
    brier = float(np.mean((ps - ys) ** 2))
    logloss = float(-np.mean(ys * np.log(ps) + (1 - ys) * np.log(1 - ps)))
    bins = []
    ece = 0.0
    for low in np.arange(0.50, 0.76, 0.025):
        high = low + 0.025
        mask = (ps >= low) & (ps < high if high < 0.775 else ps <= high)
        n = int(mask.sum())
        if n:
            pred = float(ps[mask].mean())
            actual = float(ys[mask].mean())
            ece += n / len(rows) * abs(pred - actual)
            bins.append({"bucket": f"{low:.3f}-{high:.3f}", "n": n, "predicted": pred, "actual": actual, "gap": actual - pred})
    return {"n": len(rows), "brier": brier, "log_loss": logloss, "ece": float(ece), "bins": bins}


def walk_forward_calibration_report() -> Dict[str, Any]:
    rows = _graded_binary_rows()
    preds, ys = [], []
    records = []
    for i, row in enumerate(rows):
        if i < 20:
            continue
        cal = calibrate_probability(row["_p"], row, prior_rows=rows[:i])
        preds.append(cal["calibrated"])
        ys.append(row["_y"])
        records.append({
            "time": row.get("_time"), "player": row.get("player"), "raw": row["_p"],
            "calibrated": cal["calibrated"], "actual": row["_y"], "training_sample": cal["sample"],
        })
    if not preds:
        return {"n": 0, "message": "At least 21 chronological graded rows are required.", "records": []}
    p = np.clip(np.array(preds), 0.001, 0.999)
    y = np.array(ys)
    return {
        "n": len(preds),
        "brier": float(np.mean((p-y)**2)),
        "log_loss": float(-np.mean(y*np.log(p)+(1-y)*np.log(1-p))),
        "hit_rate": float(np.mean(y)),
        "records": records,
    }


def save_calibration_state() -> Dict[str, Any]:
    state = {"updated_at": now_iso(), "in_sample": calibration_metrics(), "walk_forward": walk_forward_calibration_report()}
    save_json(CALIBRATION_FILE, state)
    return state


def seed_patch_eras() -> List[Dict[str, Any]]:
    existing = load_json(PATCH_ERAS_FILE, [])
    if isinstance(existing, list) and existing:
        return existing
    # Versioned defaults. Valve added Cache and removed Overpass on 2026-07-08.
    eras = [
        {
            "name":"CS2_PRE_CACHE_2026","effective_from":"2026-01-01","effective_to":"2026-07-07",
            "active_maps":["Ancient","Anubis","Dust2","Inferno","Mirage","Nuke","Overpass"],
            "decay_half_life_days":90,"same_era_multiplier":1.0,"old_era_multiplier":0.35,
            "note":"Pre-July 8 2026 Active Duty era."
        },
        {
            "name":"CS2_CACHE_ERA_2026","effective_from":"2026-07-08","effective_to":None,
            "active_maps":CURRENT_ACTIVE_MAPS,"decay_half_life_days":75,"same_era_multiplier":1.0,"old_era_multiplier":0.25,
            "note":"Cache added and Overpass removed from Active Duty on July 8, 2026."
        }
    ]
    save_json(PATCH_ERAS_FILE, eras)
    return eras


def refresh_active_map_pool_from_valve(force: bool = False) -> Dict[str, Any]:
    """Best-effort official map-pool refresh. It never invents a pool if parsing fails."""
    marker=os.path.join(STORAGE_DIR,"valve_map_pool_refresh.json")
    old=load_json(marker,{})
    last=_parse_iso_datetime(old.get("checked_at")) if isinstance(old,dict) else None
    if not force and last and (datetime.now(timezone.utc)-last).total_seconds()<24*3600:
        return old
    url="https://www.counter-strike.net/news/updates"
    page,status=http_get_text(url,"Valve official updates",ttl=6*3600,timeout=20,allow_stale=True,stale_ttl=24*3600)
    result={"checked_at":now_iso(),"ok":False,"status":status}
    if page:
        text=strip_tags(page)
        added=re.search(r"Added\s+([A-Za-z0-9 II]+?)\s+to the Active Duty Map Pool",text,re.I)
        removed=re.search(r"Removed\s+([A-Za-z0-9 II]+?)\s+from the Active Duty Map Pool",text,re.I)
        if added and removed:
            add=canonical_map_name(added.group(1).strip()); rem=canonical_map_name(removed.group(1).strip())
            eras=seed_patch_eras(); current=dict(eras[-1]); maps=list(current.get("active_maps") or CURRENT_ACTIVE_MAPS)
            maps=[m for m in maps if m!=rem]
            if add and add not in maps: maps.append(add)
            current["active_maps"]=sorted(set(maps),key=lambda x:KNOWN_MAPS.index(x) if x in KNOWN_MAPS else 999)
            current["official_refresh_at"]=now_iso(); current["official_added"]=add; current["official_removed"]=rem
            eras[-1]=current; save_json(PATCH_ERAS_FILE,eras)
            result.update({"ok":True,"added":add,"removed":rem,"active_maps":current["active_maps"]})
    save_json(marker,result)
    return result

def patch_era_for_time(value: Any = None) -> Dict[str, Any]:
    eras = seed_patch_eras()
    dt = _parse_iso_datetime(value) if value else datetime.now(timezone.utc)
    if dt is None:
        dt = datetime.now(timezone.utc)
    day = dt.date().isoformat()
    candidates = []
    for era in eras:
        start = str(era.get("effective_from") or "0000-01-01")
        end = str(era.get("effective_to") or "9999-12-31")
        if start <= day <= end:
            candidates.append(era)
    return candidates[-1] if candidates else eras[-1]


def veto_state_from_meta(map_meta: Dict[str, Any]) -> str:
    method = str(map_meta.get("method") or "").lower()
    if "confirmed veto" in method or "confirmed match" in method:
        return "CONFIRMED"
    if map_meta.get("veto_actions"):
        return "PARTIAL"
    return "PRE_VETO"


def strength_of_schedule_adjustment(context: Dict[str, Any], profile: PlayerStats, team: str, opponent: str) -> Tuple[float, Dict[str, Any]]:
    ranks=[safe_float(x,None) for x in (context.get("world_ranks") or [])]; ranks=[x for x in ranks if x and x>0]
    upcoming=None; teams=context.get("teams") or []
    if len(teams)>=2 and len(ranks)>=2:
        for i,t in enumerate(teams[:2]):
            if opponent and _team_name_matches(opponent,t.get("name","")): upcoming=ranks[i]
    tp,_=_resolve_team_profiles(context,team,opponent)
    hist=safe_float(tp.get("historical_opponent_rank_avg"),None); hist_n=safe_int(tp.get("historical_opponent_rank_samples"),0) or 0
    if upcoming is None:
        return 1.0,{"factor":1.0,"upcoming_opponent_rank":None,"historical_average_rank":hist,"method":"no upcoming rank evidence"}
    current_strength=clamp((40.0-upcoming)/39.0,0.0,1.0)
    base=1.022-0.042*current_strength
    schedule_delta=0.0
    if hist is not None and hist_n>=5:
        # A player/team built its baseline against weaker opposition (larger rank number): tax it against a stronger upcoming team.
        schedule_delta=clamp((upcoming-hist)*0.00065,-0.025,0.025)
    factor=clamp(base+schedule_delta,.955,1.035)
    return factor,{"factor":factor,"upcoming_opponent_rank":upcoming,"historical_average_rank":hist,"historical_samples":hist_n,"schedule_delta":schedule_delta,"method":"upcoming + historical opponent-strength normalization"}

def role_timeline_update(player: str, team: str, role: str, confidence: float, start_time: str, roster_maps: int) -> None:
    data = load_json(ROLE_TIMELINE_FILE, {})
    if not isinstance(data, dict):
        data = {}
    key = normalize_name(player)
    rows = data.get(key, []) if isinstance(data.get(key), list) else []
    current = {"time": start_time or now_iso(), "team": team, "role": role, "confidence": confidence, "roster_maps": roster_maps}
    if not rows or any(str(rows[-1].get(k) or "") != str(current.get(k) or "") for k in ["team", "role"]):
        rows.append(current)
    data[key] = rows[-100:]
    save_json(ROLE_TIMELINE_FILE, data)


def role_timeline_risk(player: str, team: str, role: str) -> Dict[str, Any]:
    data = load_json(ROLE_TIMELINE_FILE, {})
    rows = data.get(normalize_name(player), []) if isinstance(data, dict) else []
    changes = 0
    recent = rows[-5:] if isinstance(rows, list) else []
    for a, b in zip(recent, recent[1:]):
        if normalize_name(a.get("team")) != normalize_name(b.get("team")) or a.get("role") != b.get("role"):
            changes += 1
    return {"entries": len(rows), "recent_changes": changes, "stable": changes == 0, "current_team": team, "current_role": role}


def save_asof_projection_history(board: List[Dict[str, Any]], source_status: Dict[str, Any]) -> int:
    count = 0
    for row in board:
        compact_keys = [
            "prop_id", "player", "team", "opponent", "start_time", "source", "source_line_id", "market",
            "line", "opening_line", "projection", "projection_before_learning", "raw_probability", "probability",
            "lean", "status", "data_score", "expected_rounds", "adjusted_kpr", "likely_maps", "veto_state",
            "map_confidence", "role", "profile_maps", "current_roster_maps", "roster_stability", "event", "event_tier",
            "patch_era", "market_scope_verified", "market_identity_confidence", "model_version", "feature_fingerprint",
            "joint_lineup_model", "neutral_projection_correction"
        ]
        snap = {k: row.get(k) for k in compact_keys}
        snap.update({"as_of": now_iso(), "snapshot_id": snapshot_key(row), "source_status": source_status})
        _append_jsonl(HISTORICAL_ASOF_FILE, snap)
        sqlite_store_projection(row)
        count += 1
    return count



def ingest_demo_rounds_dataframe(df: pd.DataFrame, source_name: str, match_id: str = "", map_name: str = "", event_time: str = "") -> Dict[str, Any]:
    if df is None or df.empty: return {"added":0}
    cols={normalize_name(c):c for c in df.columns}
    def col(*names): return next((cols[normalize_name(n)] for n in names if normalize_name(n) in cols),None)
    round_c=col("round","round_num","round_number"); ct_c=col("ct_team","ct_team_name"); t_c=col("t_team","t_team_name")
    winner_c=col("winner","winner_team","winning_team"); side_c=col("winner_side","winning_side")
    map_c=col("map","map_name"); match_c=col("match_id","match","demo"); time_c=col("event_time","match_date","date")
    added=0
    with _sqlite_connect() as conn:
        for idx,r in df.iterrows():
            rn=safe_int(r.get(round_c),idx+1) if round_c else idx+1
            mid=str(r.get(match_c) or match_id or source_name) if match_c else (match_id or source_name)
            mn=canonical_map_name(r.get(map_c) if map_c else map_name)
            ct=str(r.get(ct_c) or "") if ct_c else ""; tt=str(r.get(t_c) or "") if t_c else ""
            winner=str(r.get(winner_c) or "") if winner_c else ""; side=str(r.get(side_c) or "").upper() if side_c else ""
            et=str(r.get(time_c) or event_time or now_iso()) if time_c else (event_time or now_iso())
            if not mn or (not ct and not tt): continue
            rh=hashlib.sha256(f"{mid}|{mn}|{rn}|{ct}|{tt}|{winner}|{side}".encode()).hexdigest()
            cur=conn.execute("INSERT OR IGNORE INTO demo_rounds(round_hash,match_id,map_name,round_num,ct_team,t_team,winner_team,winner_side,event_time,source,ingested_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (rh,mid,mn,rn,ct,tt,winner,side,et,source_name,now_iso()))
            added += int(cur.rowcount or 0)
    return {"added":added}


def demo_team_side_profile(team: str, map_name: str) -> Dict[str, Any]:
    team_key=normalize_team(team); map_name=canonical_map_name(map_name); rows=[]
    try:
        with _sqlite_connect() as conn: rows=conn.execute("SELECT * FROM demo_rounds WHERE map_name=?",(map_name,)).fetchall()
    except Exception: rows=[]
    ct_played=ct_won=t_played=t_won=0
    for r in rows:
        ct_match=_team_name_matches(team_key,r["ct_team"]); t_match=_team_name_matches(team_key,r["t_team"])
        winner_side=str(r["winner_side"] or "").upper(); winner=str(r["winner_team"] or "")
        if ct_match:
            ct_played+=1; ct_won+=int(winner_side=="CT" or _team_name_matches(team_key,winner))
        elif t_match:
            t_played+=1; t_won+=int(winner_side in {"T","TERRORIST"} or _team_name_matches(team_key,winner))
    total=ct_played+t_played
    return {"rounds":total,"ct_rounds":ct_played,"t_rounds":t_played,"ct_round_win_pct":100*ct_won/ct_played if ct_played else None,"t_round_win_pct":100*t_won/t_played if t_played else None,"source":"uploaded demo round table"}


def ingest_demo_dataframe(df: pd.DataFrame, source_name: str = "uploaded export") -> Dict[str, Any]:
    if df is None or df.empty:
        return {"ok":False,"message":"No rows supplied","added":0}
    cols={normalize_name(c):c for c in df.columns}
    def col(*names):
        return next((cols[normalize_name(n)] for n in names if normalize_name(n) in cols),None)
    player_c=col("player","attacker_name","name"); map_c=col("map","map_name")
    if not player_c or not map_c:
        return {"ok":False,"message":"Required columns: player and map","added":0}
    round_c=col("round","round_num","round_number"); match_c=col("match_id","match","demo")
    match_rounds_c=col("match_rounds","map_total_rounds","total_rounds")
    head_c=col("headshot","is_headshot"); side_c=col("side","attacker_side"); weapon_c=col("weapon","weapon_name")
    opening_c=col("opening","is_opening","first_kill"); trade_c=col("trade","is_trade")
    team_c=col("team","attacker_team_name"); opp_c=col("opponent","opponent_team")
    rank_c=col("opponent_rank","opp_rank"); tier_c=col("event_tier","tier"); time_c=col("event_time","match_date","date")
    inserted=duplicates=0
    with _sqlite_connect() as conn:
        for idx,r in df.iterrows():
            player=str(r.get(player_c) or "").strip(); map_name=canonical_map_name(r.get(map_c))
            if not player or not map_name: continue
            match_id=str(r.get(match_c) or source_name).strip() if match_c else source_name
            round_num=safe_int(r.get(round_c),None) if round_c else None
            match_rounds=safe_int(r.get(match_rounds_c),None) if match_rounds_c else None
            side=str(r.get(side_c) or "").upper() if side_c else ""; weapon=str(r.get(weapon_c) or "") if weapon_c else ""
            event_time=str(r.get(time_c) or now_iso()) if time_c else now_iso()
            values=[normalize_name(player),map_name,match_id,round_num,idx,weapon,side,source_name]
            event_hash=hashlib.sha256("|".join(map(str,values)).encode()).hexdigest()
            try:
                cur=conn.execute("""INSERT OR IGNORE INTO demo_events(event_hash,player,player_key,map_name,match_id,round_num,match_rounds,team,opponent,opponent_rank,event_tier,side,weapon,is_headshot,is_opening,is_trade,event_time,source,ingested_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (event_hash,player,normalize_name(player),map_name,match_id,round_num,match_rounds,
                     str(r.get(team_c) or "") if team_c else "",str(r.get(opp_c) or "") if opp_c else "",
                     safe_float(r.get(rank_c),None) if rank_c else None,str(r.get(tier_c) or "") if tier_c else "",side,weapon,
                     int(str(r.get(head_c)).lower() in {"1","true","yes"}) if head_c else 0,
                     int(str(r.get(opening_c)).lower() in {"1","true","yes"}) if opening_c else 0,
                     int(str(r.get(trade_c)).lower() in {"1","true","yes"}) if trade_c else 0,event_time,source_name,now_iso()))
                if cur.rowcount: inserted+=1
                else: duplicates+=1
            except Exception:
                continue
    # Keep compact JSON summary for backwards compatibility/UI export.
    store=load_json(DEMO_DATABASE_FILE,{})
    if not isinstance(store,dict): store={}
    for player_key,map_name in {(normalize_name(str(r.get(player_c) or "")),canonical_map_name(r.get(map_c))) for _,r in df.iterrows()}:
        if player_key and map_name:
            prof=demo_profile_for(player_key,map_name,normalized=True)
            if prof: store[f"{player_key}|{map_name}"]=prof
    save_json(DEMO_DATABASE_FILE,store)
    round_rows=df.attrs.get("rounds_df") if hasattr(df,"attrs") else None
    round_result=ingest_demo_rounds_dataframe(pd.DataFrame(round_rows),source_name,str(df.attrs.get("match_id") or ""),str(df.attrs.get("map_name") or ""),str(df.attrs.get("event_time") or now_iso())) if round_rows else {"added":0}
    if inserted or round_result.get("added",0):
        invalidate_model_fits(["round_kill_env:","simulation_blend:","team_kill_share:"])
    return {"ok":True,"added":inserted,"duplicates":duplicates,"rounds_added":round_result.get("added",0),"profiles":len(store),"database":CORE_DB_FILE}


def parse_awpy_demo_file(uploaded_file: Any) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    try:
        from awpy import Demo
    except Exception as exc:
        return pd.DataFrame(),{"ok":False,"message":f"Awpy unavailable: {exc}"}
    import tempfile
    path=""
    try:
        with tempfile.NamedTemporaryFile(delete=False,suffix=".dem") as tmp:
            tmp.write(uploaded_file.getbuffer()); path=tmp.name
        demo=Demo(path); demo.parse()
        kills=getattr(demo,"kills",None); rounds=getattr(demo,"rounds",None)
        if kills is None: return pd.DataFrame(),{"ok":False,"message":"Parsed demo contained no kills table"}
        df=kills.copy() if isinstance(kills,pd.DataFrame) else pd.DataFrame(kills)
        round_df=rounds.copy() if isinstance(rounds,pd.DataFrame) else pd.DataFrame(rounds or [])
        match_id=hashlib.sha256(uploaded_file.getvalue()).hexdigest()[:20]
        total_rounds=len(round_df) if not round_df.empty else None
        event_time=now_iso(); df["match_id"]=match_id; df["match_rounds"]=total_rounds; df["event_time"]=event_time
        header=getattr(demo,"header",{}) or {}; header_map=header.get("map_name") if isinstance(header,dict) else getattr(header,"map_name",None)
        map_name=getattr(demo,"map_name",None) or header_map
        if "map_name" not in df.columns and "map" not in df.columns and map_name: df["map_name"]=map_name
        df.attrs["rounds_df"]=round_df.to_dict("records") if not round_df.empty else []
        df.attrs["match_id"]=match_id; df.attrs["map_name"]=map_name or ""; df.attrs["event_time"]=event_time
        return df,{"ok":True,"rows":len(df),"rounds":total_rounds,"match_id":match_id,"columns":list(df.columns),"denominator_verified":bool(total_rounds),"round_rows":len(round_df)}
    except Exception as exc:
        return pd.DataFrame(),{"ok":False,"message":str(exc)}
    finally:
        try:
            if path: os.unlink(path)
        except Exception: pass


class _LocalUpload:
    def __init__(self,path: str):
        self.path=path; self.name=os.path.basename(path); self._data=Path(path).read_bytes()
    def getbuffer(self): return memoryview(self._data)
    def getvalue(self): return self._data


def ingest_demo_path(path: str) -> Dict[str, Any]:
    path=str(path); suffix=Path(path).suffix.lower(); digest=hashlib.sha256(Path(path).read_bytes()).hexdigest()
    done=load_model_fit(f"demo_file:{digest}")
    if done: return {"ok":True,"skipped":True,"path":path,"reason":"already ingested"}
    try:
        if suffix==".dem":
            df,meta=parse_awpy_demo_file(_LocalUpload(path))
            result=ingest_demo_dataframe(df,os.path.basename(path)) if meta.get("ok") else meta
        elif suffix==".csv":
            result=ingest_demo_dataframe(pd.read_csv(path),os.path.basename(path))
        elif suffix in {".json",".jsonl"}:
            try: df=pd.read_json(path,lines=suffix==".jsonl")
            except Exception: df=pd.DataFrame(json.loads(Path(path).read_text()))
            result=ingest_demo_dataframe(df,os.path.basename(path))
        elif suffix==".zip":
            import zipfile,tempfile
            children=[]
            with tempfile.TemporaryDirectory() as td:
                with zipfile.ZipFile(path) as z: z.extractall(td)
                for child in Path(td).rglob("*"):
                    if child.is_file() and child.suffix.lower() in {".dem",".csv",".json",".jsonl"}: children.append(ingest_demo_path(str(child)))
            result={"ok":any(x.get("ok") for x in children),"children":children,"archive":path}
        else: return {"ok":False,"message":"unsupported demo file","path":path}
        if result.get("ok"): save_model_fit(f"demo_file:{digest}",{"path":path,"sample":safe_int(result.get("added"),0) or 0,"ingested_at":now_iso(),"fit_type":"demo_file"})
        return result
    except Exception as exc:
        return {"ok":False,"message":str(exc),"path":path}


def ingest_uploaded_demo_file(uploaded_file: Any) -> Dict[str, Any]:
    import tempfile
    suffix=Path(str(getattr(uploaded_file,"name","upload"))).suffix.lower() or ".bin"
    path=""
    try:
        with tempfile.NamedTemporaryFile(delete=False,suffix=suffix) as tmp:
            data=uploaded_file.getvalue() if hasattr(uploaded_file,"getvalue") else bytes(uploaded_file.getbuffer())
            tmp.write(data); path=tmp.name
        result=ingest_demo_path(path)
        result["uploaded_name"]=str(getattr(uploaded_file,"name","upload"))
        return result
    finally:
        try:
            if path: os.unlink(path)
        except Exception:
            pass


def auto_ingest_demo_dropbox() -> Dict[str, Any]:
    results=[]
    try:
        for child in sorted(Path(DEMO_DROP_DIR).glob("*")):
            if child.is_file() and child.suffix.lower() in {".dem",".csv",".json",".jsonl",".zip"}: results.append(ingest_demo_path(str(child)))
    except Exception as exc:
        return {"ok":False,"message":str(exc),"directory":DEMO_DROP_DIR}
    return {"ok":True,"directory":DEMO_DROP_DIR,"files":len(results),"results":results}


def demo_profile_for(player: str, map_name: str, normalized: bool = False) -> Dict[str, Any]:
    player_key=player if normalized else normalize_name(player); map_name=canonical_map_name(map_name)
    try:
        with _sqlite_connect() as conn:
            rows=conn.execute("SELECT * FROM demo_events WHERE player_key=? AND map_name=?",(player_key,map_name)).fetchall()
    except Exception:
        rows=[]
    if not rows: return {}
    matches={}
    for r in rows:
        mid=r["match_id"] or "unknown"
        if r["match_rounds"] and r["match_rounds"]>0: matches[mid]=max(matches.get(mid,0),int(r["match_rounds"]))
    rounds=sum(matches.values()); kills=len(rows); hs=sum(int(r["is_headshot"] or 0) for r in rows)
    openings=sum(int(r["is_opening"] or 0) for r in rows); trades=sum(int(r["is_trade"] or 0) for r in rows)
    weapons=Counter(str(r["weapon"] or "Unknown") for r in rows); sides=Counter(str(r["side"] or "UNKNOWN") for r in rows)
    sniper=sum(v for k,v in weapons.items() if any(x in normalize_name(k) for x in ["awp","ssg","scar","g3sg1"]))
    ranks=[float(r["opponent_rank"]) for r in rows if r["opponent_rank"] is not None and r["opponent_rank"]>0]
    avg_rank=float(np.mean(ranks)) if ranks else None
    sos_factor=clamp(1.0+(35-(avg_rank or 35))*0.0015,.96,1.05) if avg_rank else 1.0
    raw_kpr=kills/rounds if rounds>0 else None
    normalized_kpr=raw_kpr*sos_factor if raw_kpr is not None else None
    latest=max((str(r["event_time"] or "") for r in rows),default="")
    return {"player":rows[0]["player"],"map":map_name,"kills":kills,"rounds":rounds,"matches":len(matches),
            "kpr":normalized_kpr,"raw_kpr":raw_kpr,"denominator_verified":rounds>0,"hs_pct":hs/kills*100 if kills else None,
            "opening_kpr":openings/rounds if rounds else None,"trade_rate":trades/kills if kills else None,
            "awp_kill_share":sniper/kills if kills else None,"weapons":dict(weapons),"sides":dict(sides),
            "average_opponent_rank":avg_rank,"historical_sos_factor":sos_factor,"latest_event_time":latest,"source":"SQLite demo telemetry"}


def learned_team_kill_share_parameters(player: str, role: str) -> Dict[str, Any]:
    rows=sqlite_graded_projection_rows()
    scopes=[("player",lambda r:normalize_name(r.get("player"))==normalize_name(player),MIN_BLEND_TRAINING_SAMPLES),("role",lambda r:str(r.get("role") or "")==str(role),100),("global",lambda r:True,200)]
    for scope,pred,min_n in scopes:
        samples=[]
        for row in rows:
            observed=safe_float(row.get("observed_player_share"),None)
            if observed is None: observed=safe_float((row.get("team_kill_share_model") or {}).get("observed_player_share"),None)
            if pred(row) and observed is not None and .04<=observed<=.50: samples.append(float(observed))
        if len(samples)>=min_n:
            mean=float(np.mean(samples)); var=float(np.var(samples)); concentration=clamp(mean*(1-mean)/max(var,1e-4)-1,12,160)
            return {"player_share":clamp(mean,.08,.36),"concentration":concentration,"sample":len(samples),"source":f"historical {scope} team-kill share","scope":scope}
    return {"sample":0,"scope":"prior"}

def team_kill_share_parameters(profile: PlayerStats, role: str, opponent_meta: Dict[str, Any]) -> Dict[str, Any]:
    team_profile=opponent_meta.get("team_profile") or {}; team_kpr=safe_float(team_profile.get("kills_for_per_round"),None) or safe_float(team_profile.get("kills_per_round"),None)
    team_source="recent team scoreboard"
    if team_kpr is None or not (2.4<=team_kpr<=4.5):
        team_kpr=3.35; team_source="league baseline — Track cap"
    learned=learned_team_kill_share_parameters(profile.player,role)
    if learned.get("sample",0)>=MIN_BLEND_TRAINING_SAMPLES:
        share=learned["player_share"]; concentration=learned["concentration"]; source=learned["source"]
    else:
        share=clamp(profile.kpr/team_kpr,.11,.29)
        if "AWPer" in role or "Star" in role: share*=1.05
        elif "Support" in role or "IGL" in role: share*=.92
        elif "Entry" in role: share*=1.01
        share=clamp(share,.10,.31); concentration=48.0 if profile.maps>=30 else 28.0; source="untrained role prior"
    return {"team_kpr":team_kpr,"player_share":share,"concentration":concentration,"source":source,"team_kpr_source":team_source,"trained_sample":learned.get("sample",0)}


def headshot_prop_projection(row: Dict[str, Any]) -> Dict[str, Any]:
    kills = safe_float(row.get("projection"), None)
    hs = safe_float(row.get("hs_pct"), None)
    awp = safe_float(row.get("awp_kill_share"), 0) or 0
    if kills is None or hs is None:
        return {"projection": None, "ready": False, "reason": "headshot rate unavailable"}
    # AWP-heavy roles are lower-HS and more volatile; use observed HS% with mild shrinkage.
    observed = clamp(hs / 100.0, 0.15, 0.80)
    shrunk = observed * 0.75 + (0.46 - 0.18 * awp) * 0.25
    projection = kills * shrunk
    return {"projection": projection, "hs_rate": shrunk, "ready": safe_int(row.get("profile_maps"), 0) >= 20, "reason": "kills × shrunk role/weapon headshot rate"}


def maps_over_25_probability(row: Dict[str, Any]) -> Dict[str, Any]:
    if str(row.get("match_format")) != "BO3":
        return {"probability": None, "ready": False, "reason": "BO3 required"}
    ranks = row.get("world_ranks") or []
    comp = 0.55
    if len(ranks) >= 2 and all(safe_float(x, 0) > 0 for x in ranks[:2]):
        gap = abs(math.log((float(ranks[0])+4)/(float(ranks[1])+4)))
        comp = clamp(math.exp(-0.95*gap), 0.12, 1.0)
    veto = str(row.get("veto_state") or "PRE_VETO")
    depth = clamp((safe_float(row.get("map_confidence"), 50) or 50)/100.0, 0.35, 0.98)
    p = clamp(0.31 + 0.34*comp + 0.08*depth + (0.03 if veto == "CONFIRMED" else 0), 0.24, 0.76)
    return {"probability": p, "ready": safe_int(row.get("team_recent_maps"), 0) >= 6, "reason": "competitiveness + veto depth model"}


def correlation_between(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    same_match = bool(a.get("match_url") and a.get("match_url") == b.get("match_url")) or (
        normalize_name(a.get("team")) in {normalize_name(b.get("team")), normalize_name(b.get("opponent"))} and
        normalize_name(a.get("opponent")) in {normalize_name(b.get("team")), normalize_name(b.get("opponent"))}
    )
    if not same_match:
        return 0.0
    same_team = normalize_name(a.get("team")) == normalize_name(b.get("team"))
    same_lean = a.get("lean") == b.get("lean")
    if same_team and same_lean == True and a.get("lean") == "OVER":
        return 0.08
    if same_team and not same_lean:
        return -0.18
    if same_lean and a.get("lean") == "OVER":
        return 0.24
    if same_lean and a.get("lean") == "UNDER":
        return 0.18
    return -0.08


def simulate_joint_slip(rows: List[Dict[str, Any]], simulations: int = 60000) -> Dict[str, Any]:
    rows = [x for x in rows if safe_float(x.get("probability"), None) is not None]
    n = len(rows)
    if n < 2:
        return {"ok": False, "message": "Select at least two projections."}
    probs = np.array([clamp(float(x["probability"]), 0.001, 0.999) for x in rows])
    corr = np.eye(n)
    for i in range(n):
        for j in range(i+1, n):
            corr[i,j] = corr[j,i] = correlation_between(rows[i], rows[j])
    vals, vecs = np.linalg.eigh(corr)
    vals = np.clip(vals, 1e-5, None)
    corr = vecs @ np.diag(vals) @ vecs.T
    d = np.sqrt(np.diag(corr))
    corr = corr / np.outer(d, d)
    rng = np.random.default_rng(stable_seed("slip", *[x.get("prop_id") for x in rows], MODEL_VERSION))
    z = rng.multivariate_normal(np.zeros(n), corr, size=simulations)
    thresholds = np.array([NormalDist().inv_cdf(float(p)) for p in probs])
    hits = z <= thresholds
    joint = float(np.mean(np.all(hits, axis=1)))
    independent = float(np.prod(probs))
    return {
        "ok": True, "legs": n, "joint_probability": joint, "independent_probability": independent,
        "correlation_adjustment": joint-independent, "correlation_matrix": corr.tolist(),
        "leg_probabilities": probs.tolist(), "simulations": simulations,
    }


def bankroll_recommendation(bankroll: float, probability: float, american_odds: Optional[float], risk_pct: float = 1.5) -> Dict[str, Any]:
    bankroll = max(float(bankroll), 0.0)
    p = clamp(float(probability), 0.001, 0.999)
    flat = bankroll * clamp(risk_pct, 1.0, 3.0) / 100.0
    if american_odds is None:
        return {"flat_stake": flat, "kelly": None, "quarter_kelly": None, "ev_per_dollar": None}
    dec = american_to_decimal(float(american_odds))
    if dec is None or dec <= 1:
        return {"flat_stake": flat, "kelly": None, "quarter_kelly": None, "ev_per_dollar": None}
    b = dec - 1
    q = 1-p
    k = max((b*p-q)/b, 0.0)
    return {"flat_stake": flat, "kelly": bankroll*k, "quarter_kelly": bankroll*k*0.25, "ev_per_dollar": p*b-q, "decimal_odds": dec}


def save_book_odds_history(df: pd.DataFrame) -> int:
    if df is None or df.empty:
        return 0
    history = load_json(BOOK_ODDS_HISTORY_FILE, [])
    if not isinstance(history, list):
        history = []
    count = 0
    for _, row in df.iterrows():
        item = {str(k): (None if pd.isna(v) else v) for k, v in row.to_dict().items()}
        item["captured_at"] = now_iso()
        history.append(item)
        count += 1
    save_json(BOOK_ODDS_HISTORY_FILE, history[-10000:])
    return count


def closing_line_value(row: Dict[str, Any]) -> Optional[float]:
    opening = safe_float(row.get("opening_line"), None)
    closing = safe_float(row.get("closing_line"), None)
    if opening is None or closing is None:
        return None
    # Positive means the selected direction beat the closing line.
    return (closing-opening) if row.get("lean") == "OVER" else (opening-closing)


def live_watch_projection(player: str, current_kills: int, rounds_played: int, current_score_a: int, current_score_b: int, prematch_row: Dict[str, Any]) -> Dict[str, Any]:
    base_kpr = safe_float(prematch_row.get("adjusted_kpr"), None)
    expected_total = safe_float(prematch_row.get("expected_rounds"), None)
    if base_kpr is None or expected_total is None or rounds_played <= 0:
        return {"ok": False, "message": "Prematch projection and rounds played are required."}
    live_kpr = current_kills / rounds_played
    # Do not overreact to a few rounds; gradually blend live information.
    live_weight = clamp(rounds_played / 30.0, 0.08, 0.58)
    blended = base_kpr*(1-live_weight)+live_kpr*live_weight
    remaining = max(expected_total-rounds_played, 0)
    projected = current_kills+remaining*blended
    economy_volatility = 1.0 + (0.08 if abs(current_score_a-current_score_b) <= 2 else -0.03)
    return {"ok": True, "player": player, "live_kpr": live_kpr, "blended_kpr": blended, "remaining_rounds": remaining, "live_projection": projected, "volatility_multiplier": economy_volatility, "note": "Informational live pace only; verify economy, lineup and official market before betting."}

# ============================================================
# V4.3 ACCURACY LAYER — DEMO + ELO + VETO + ECONOMY + CALIBRATION
# ============================================================

V43_MIN_CURRENT_ROSTER_MAPS = 8
V43_MIN_MAP_SAMPLE_OFFICIAL = 8
V43_MIN_LOCAL_CALIBRATION = 40
V43_MAX_MODEL_DISAGREEMENT = 3.25
V43_ELO_K = 24.0
V43_RATING_CACHE_SECONDS = 3600


def init_v43_accuracy_schema() -> None:
    """Idempotent schema extension. Keeps all v4.2 data on an existing Railway volume."""
    with _sqlite_connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS veto_observations (
          observation_id TEXT PRIMARY KEY, team_id TEXT, team_name TEXT, opponent_name TEXT,
          match_id TEXT, played_at TEXT, sequence_num INTEGER, action TEXT, map_name TEXT,
          same_core INTEGER, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_veto_team_time ON veto_observations(team_id,played_at);
        CREATE TABLE IF NOT EXISTS source_health_events (
          event_id TEXT PRIMARY KEY, source TEXT, grade TEXT, observed_at TEXT,
          payload_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_source_health_time ON source_health_events(observed_at);
        """)
        existing={str(r[1]) for r in conn.execute("PRAGMA table_info(demo_rounds)").fetchall()}
        additions={
            "ct_equipment_value":"REAL", "t_equipment_value":"REAL",
            "ct_buy_type":"TEXT", "t_buy_type":"TEXT", "bomb_planted":"INTEGER",
            "ct_survivors":"INTEGER", "t_survivors":"INTEGER"
        }
        for name,kind in additions.items():
            if name not in existing:
                try: conn.execute(f"ALTER TABLE demo_rounds ADD COLUMN {name} {kind}")
                except Exception: pass


init_v43_accuracy_schema()


def _buy_type(value: Any, equipment: Any = None) -> str:
    text=normalize_name(value)
    if "pistol" in text: return "PISTOL"
    if "eco" in text or "save" in text: return "ECO"
    if "force" in text or "half" in text: return "FORCE"
    if "full" in text or "gun" in text: return "FULL"
    eq=safe_float(equipment,None)
    if eq is None: return "UNKNOWN"
    if eq < 8000: return "ECO"
    if eq < 17000: return "FORCE"
    return "FULL"


def ingest_demo_rounds_dataframe(df: pd.DataFrame, source_name: str, match_id: str = "", map_name: str = "", event_time: str = "") -> Dict[str, Any]:
    """V4.3 round ingestion includes economy/survivor fields when Awpy exposes them."""
    if df is None or df.empty: return {"added":0}
    cols={normalize_name(c):c for c in df.columns}
    def col(*names): return next((cols[normalize_name(n)] for n in names if normalize_name(n) in cols),None)
    round_c=col("round","round_num","round_number"); ct_c=col("ct_team","ct_team_name"); t_c=col("t_team","t_team_name")
    winner_c=col("winner","winner_team","winning_team"); side_c=col("winner_side","winning_side")
    map_c=col("map","map_name"); match_c=col("match_id","match","demo"); time_c=col("event_time","match_date","date")
    ct_eq_c=col("ct_equipment_value","ct_equip_value","ct_equipment","ct_start_equipment_value")
    t_eq_c=col("t_equipment_value","t_equip_value","t_equipment","t_start_equipment_value")
    ct_buy_c=col("ct_buy_type","ct_buy","ct_buy_class"); t_buy_c=col("t_buy_type","t_buy","t_buy_class")
    bomb_c=col("bomb_planted","bomb_plant","is_bomb_planted")
    ct_surv_c=col("ct_survivors","ct_alive_end","ct_end_alive"); t_surv_c=col("t_survivors","t_alive_end","t_end_alive")
    added=0
    with _sqlite_connect() as conn:
        for idx,r in df.iterrows():
            rn=safe_int(r.get(round_c),idx+1) if round_c else idx+1
            mid=str(r.get(match_c) or match_id or source_name) if match_c else (match_id or source_name)
            mn=canonical_map_name(r.get(map_c) if map_c else map_name)
            ct=str(r.get(ct_c) or "") if ct_c else ""; tt=str(r.get(t_c) or "") if t_c else ""
            winner=str(r.get(winner_c) or "") if winner_c else ""; side=str(r.get(side_c) or "").upper() if side_c else ""
            et=str(r.get(time_c) or event_time or now_iso()) if time_c else (event_time or now_iso())
            if not mn or (not ct and not tt): continue
            ct_eq=safe_float(r.get(ct_eq_c),None) if ct_eq_c else None; t_eq=safe_float(r.get(t_eq_c),None) if t_eq_c else None
            ct_buy=_buy_type(r.get(ct_buy_c) if ct_buy_c else "",ct_eq); t_buy=_buy_type(r.get(t_buy_c) if t_buy_c else "",t_eq)
            bomb=int(str(r.get(bomb_c)).lower() in {"1","true","yes"}) if bomb_c else 0
            ct_surv=safe_int(r.get(ct_surv_c),None) if ct_surv_c else None; t_surv=safe_int(r.get(t_surv_c),None) if t_surv_c else None
            rh=hashlib.sha256(f"{mid}|{mn}|{rn}|{ct}|{tt}|{winner}|{side}".encode()).hexdigest()
            cur=conn.execute("""INSERT OR REPLACE INTO demo_rounds(
                round_hash,match_id,map_name,round_num,ct_team,t_team,winner_team,winner_side,event_time,source,ingested_at,
                ct_equipment_value,t_equipment_value,ct_buy_type,t_buy_type,bomb_planted,ct_survivors,t_survivors
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (rh,mid,mn,rn,ct,tt,winner,side,et,source_name,now_iso(),ct_eq,t_eq,ct_buy,t_buy,bomb,ct_surv,t_surv))
            added += int(cur.rowcount or 0)
    if added: invalidate_model_fits(["economy_kill_env:","round_kill_env:","simulation_blend:","map_elo:"])
    return {"added":added,"economy_columns":bool(ct_eq_c or t_eq_c or ct_buy_c or t_buy_c),"source":source_name}


def demo_player_side_profile(player: str, map_name: str) -> Dict[str, Any]:
    player_key=normalize_name(player); map_name=canonical_map_name(map_name)
    try:
        with _sqlite_connect() as conn:
            events=conn.execute("SELECT * FROM demo_events WHERE player_key=? AND map_name=?",(player_key,map_name)).fetchall()
            rounds=conn.execute("SELECT * FROM demo_rounds WHERE map_name=?",(map_name,)).fetchall()
    except Exception:
        return {}
    if not events: return {}
    by_match=defaultdict(list)
    for e in events: by_match[str(e["match_id"] or "")].append(e)
    round_lookup=defaultdict(list)
    for r in rounds: round_lookup[str(r["match_id"] or "")].append(r)
    ct_rounds=t_rounds=ct_kills=t_kills=0
    econ_rounds=Counter(); econ_kills=Counter()
    for mid,evs in by_match.items():
        team_candidates=[normalize_team(e["team"]) for e in evs if str(e["team"] or "").strip()]
        team=Counter(team_candidates).most_common(1)[0][0] if team_candidates else ""
        rrows=round_lookup.get(mid,[])
        for r in rrows:
            side=""
            if team and _team_name_matches(team,r["ct_team"]): side="CT"
            elif team and _team_name_matches(team,r["t_team"]): side="T"
            if side=="CT":
                ct_rounds+=1; econ_rounds[str(r["ct_buy_type"] or "UNKNOWN")]+=1
            elif side=="T":
                t_rounds+=1; econ_rounds[str(r["t_buy_type"] or "UNKNOWN")]+=1
        rmap={safe_int(r["round_num"],-1):r for r in rrows}
        for e in evs:
            side=str(e["side"] or "").upper()
            if side not in {"CT","T"}:
                rr=rmap.get(safe_int(e["round_num"],-1))
                if rr is not None and team:
                    side="CT" if _team_name_matches(team,rr["ct_team"]) else "T" if _team_name_matches(team,rr["t_team"]) else ""
            if side=="CT": ct_kills+=1
            elif side=="T": t_kills+=1
            rr=rmap.get(safe_int(e["round_num"],-1))
            if rr is not None:
                buy=str(rr["ct_buy_type"] if side=="CT" else rr["t_buy_type"] if side=="T" else "UNKNOWN")
                econ_kills[buy]+=1
    return {
        "ct_rounds":ct_rounds,"t_rounds":t_rounds,"ct_kills":ct_kills,"t_kills":t_kills,
        "ct_kpr":ct_kills/ct_rounds if ct_rounds else None,"t_kpr":t_kills/t_rounds if t_rounds else None,
        "economy_kpr":{k:econ_kills[k]/v for k,v in econ_rounds.items() if v>0},
        "economy_rounds":dict(econ_rounds),"side_denominator_verified":bool(ct_rounds+t_rounds),
        "source":"uploaded demos with full round table"
    }


_v42_demo_profile_for = demo_profile_for

def demo_profile_for(player: str, map_name: str, normalized: bool = False) -> Dict[str, Any]:
    base=_v42_demo_profile_for(player,map_name,normalized)
    if not base: return base
    side=demo_player_side_profile(base.get("player") or player,map_name)
    return {**base,**side}


def build_player_map_profiles(profile: PlayerStats, likely_maps: Sequence[str], target_time: Any = None, patch_era: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """Map true talent blends HLTV long/recent data with verified demo side/economy samples."""
    out={}; statuses={}
    if not profile.player_id or not likely_maps:
        return out,{"ok":False,"message":"player id or likely maps unavailable"}
    era=patch_era or patch_era_for_time(target_time); target_dt=_parse_iso_datetime(target_time) or datetime.now(timezone.utc)
    era_start=_parse_iso_datetime(era.get("effective_from")); days_in_era=max(1.0,(target_dt-(era_start or target_dt-timedelta(days=180))).total_seconds()/86400.0)
    era_factor=clamp(days_in_era/180.0,.20,1.0)
    for map_name in list(dict.fromkeys(likely_maps))[:3]:
        long_row,long_status=fetch_hltv_filtered_player_profile(profile.player_id,profile.slug,180,map_name)
        recent_row,recent_status=fetch_hltv_filtered_player_profile(profile.player_id,profile.slug,60,map_name)
        demo=demo_profile_for(profile.player,map_name)
        long_maps=safe_int(long_row.get("maps"),0) or 0; recent_maps=safe_int(recent_row.get("maps"),0) or 0
        long_kpr=safe_float(long_row.get("kpr"),None); recent_kpr=safe_float(recent_row.get("kpr"),None)
        demo_kpr=safe_float(demo.get("kpr"),None) if demo.get("denominator_verified") else None
        if long_kpr is None and demo_kpr is None:
            statuses[map_name]={"long":long_status,"recent":recent_status,"demo":demo,"usable":False}; continue
        center=long_kpr if long_kpr is not None else profile.kpr
        w_long=clamp(long_maps/(long_maps+16.0),0,.86)*era_factor
        w_recent=clamp(recent_maps/(recent_maps+10.0),0,.42) if recent_kpr is not None else 0
        blended=profile.kpr*(1-w_long)+center*w_long
        blended=blended*(1-w_recent)+(recent_kpr if recent_kpr is not None else blended)*w_recent
        demo_rounds=safe_int(demo.get("rounds"),0) or 0; w_demo=0.0
        if demo_kpr is not None:
            w_demo=clamp(demo_rounds/(demo_rounds+260.0),0,.52)*era_recency_weight(demo.get("latest_event_time"),target_time,era)
            blended=blended*(1-w_demo)+demo_kpr*w_demo
        ct_base=safe_float(long_row.get("ct_kpr"),None) or safe_float(profile.ct_kpr,None) or blended
        t_base=safe_float(long_row.get("t_kpr"),None) or safe_float(profile.t_kpr,None) or blended
        dct=safe_float(demo.get("ct_kpr"),None); dt=safe_float(demo.get("t_kpr"),None)
        ct_rounds=safe_int(demo.get("ct_rounds"),0) or 0; t_rounds=safe_int(demo.get("t_rounds"),0) or 0
        wct=clamp(ct_rounds/(ct_rounds+160.0),0,.55) if dct is not None else 0
        wt=clamp(t_rounds/(t_rounds+160.0),0,.55) if dt is not None else 0
        ct_kpr=ct_base*(1-wct)+(dct if dct is not None else ct_base)*wct
        t_kpr=t_base*(1-wt)+(dt if dt is not None else t_base)*wt
        effective_maps=long_maps+0.65*recent_maps+demo_rounds/20.0
        confidence=clamp(25+effective_maps*2.2+min(demo_rounds,500)/20.0,25,98)
        sources=[]
        if long_kpr is not None:sources.append("HLTV map KPR")
        if recent_kpr is not None:sources.append("HLTV 60-day map KPR")
        if demo_kpr is not None:sources.append("demo full-round KPR")
        if dct is not None or dt is not None:sources.append("demo CT/T KPR")
        out[map_name]={
            "map":map_name,"maps":long_maps,"rounds":safe_int(long_row.get("rounds"),0) or 0,
            "kpr":round(float(center),4),"recent_maps":recent_maps,"recent_kpr":round(float(recent_kpr),4) if recent_kpr is not None else None,
            "demo_rounds":demo_rounds,"demo_kpr":round(float(demo_kpr),4) if demo_kpr is not None else None,"demo_weight":round(w_demo,4),
            "blended_kpr":round(clamp(float(blended),.42,1.02),4),"ct_kpr":round(clamp(float(ct_kpr),.38,1.08),4),"t_kpr":round(clamp(float(t_kpr),.38,1.08),4),
            "ct_demo_rounds":ct_rounds,"t_demo_rounds":t_rounds,"economy_kpr":demo.get("economy_kpr") or {},"economy_rounds":demo.get("economy_rounds") or {},
            "dpr":safe_float(long_row.get("dpr"),None),"adr":safe_float(long_row.get("adr"),None),"rating":safe_float(long_row.get("rating"),None),
            "opening_kpr":safe_float(demo.get("opening_kpr"),None) or ((safe_float(long_row.get("opening_kills"),0) or 0)/max(safe_float(long_row.get("rounds"),0) or 0,1)),
            "hs_pct":safe_float(demo.get("hs_pct"),None),"awp_kill_share":safe_float(demo.get("awp_kill_share"),None),
            "source":" + ".join(sources),"core_map_kpr_verified":long_kpr is not None or demo_kpr is not None,"patch_era_weight":round(era_factor,4),
            "map_model_confidence":round(confidence,1),"effective_map_sample":round(effective_maps,1)
        }
        statuses[map_name]={"long":long_status,"recent":recent_status,"demo":demo,"usable":True}
    saved=load_json(DEEP_PLAYER_MAP_FILE,{})
    if not isinstance(saved,dict):saved={}
    if out:
        saved[normalize_name(profile.player)]={"updated_at":now_iso(),"maps":out}; save_json(DEEP_PLAYER_MAP_FILE,saved)
    return out,{"ok":bool(out),"maps":list(out),"statuses":statuses,"patch_era":era.get("name"),"side_specific_demo_connected":True}


def sqlite_store_veto_observation(team_id: str, team_name: str, opponent_name: str, match_url: str, played_at: str, action: Dict[str, Any], sequence_num: int, same_core: bool) -> bool:
    map_name=canonical_map_name(action.get("map")); act=str(action.get("action") or "").lower(); match_id=_match_id_from_url(match_url) or hashlib.md5(str(match_url).encode()).hexdigest()[:16]
    if not team_id or map_name not in KNOWN_MAPS or act not in {"picked","removed","left"}:return False
    oid=hashlib.sha256(f"{team_id}|{match_id}|{sequence_num}|{act}|{map_name}".encode()).hexdigest()
    payload={**action,"team_id":team_id,"team_name":team_name,"opponent_name":opponent_name,"match_url":match_url,"same_core":same_core}
    try:
        with _sqlite_connect() as conn:
            conn.execute("""INSERT OR REPLACE INTO veto_observations(observation_id,team_id,team_name,opponent_name,match_id,played_at,sequence_num,action,map_name,same_core,payload_json,created_at)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",(oid,team_id,team_name,opponent_name,match_id,played_at,sequence_num,act,map_name,int(bool(same_core)),json.dumps(payload,default=str),now_iso()))
        return True
    except Exception:return False


def empirical_veto_profile(team_id: str, team_name: str, opponent_name: str = "", days: int = 365) -> Dict[str, Any]:
    cutoff=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat(); rows=[]
    try:
        with _sqlite_connect() as conn: rows=conn.execute("SELECT * FROM veto_observations WHERE team_id=? AND (played_at='' OR played_at>=?) ORDER BY played_at DESC",(str(team_id),cutoff)).fetchall()
    except Exception:rows=[]
    picks=Counter(); bans=Counter(); left=Counter(); weighted=0.0; opponent_specific=0
    now=datetime.now(timezone.utc)
    for r in rows:
        dt=_parse_iso_datetime(r["played_at"]); age=max(0,(now-dt).total_seconds()/86400) if dt else 180
        w=0.5**(age/90.0); w*=1.0 if bool(r["same_core"]) else .55
        if opponent_name and _team_name_matches(opponent_name,r["opponent_name"]): w*=1.30; opponent_specific+=1
        target=picks if r["action"]=="picked" else bans if r["action"]=="removed" else left
        target[canonical_map_name(r["map_name"])]+=w; weighted+=w
    return {"pick_counts":dict(picks),"ban_counts":dict(bans),"left_counts":dict(left),"observations":len(rows),"weighted_sample":round(weighted,2),"opponent_specific":opponent_specific,"source":"cumulative chronological veto database"}


_v42_build_team_deep_profile = build_team_deep_profile

def build_team_deep_profile(team_id: str, slug: str, team_name: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    profile,status=_v42_build_team_deep_profile(team_id,slug,team_name)
    try:
        summaries,_=fetch_recent_team_match_summaries(team_id,slug,DEEP_PULL_MATCH_LIMIT)
        roster_norm={normalize_name(x) for x in profile.get("current_roster") or []}
        for summary in summaries:
            teams=summary.get("teams") or []; opponent_name=next((x.get("name","") for x in teams if not _team_name_matches(team_name,x.get("name",""))),"")
            lineup={normalize_name(x) for x in summary.get("lineup_names") or []}; same_core=bool(roster_norm and len(roster_norm&lineup)>=4)
            for seq,action in enumerate(summary.get("veto_actions") or []):
                if _team_name_matches(team_name,action.get("team","")) or action.get("action")=="left":
                    sqlite_store_veto_observation(team_id,team_name,opponent_name,str(summary.get("match_url") or ""),str(summary.get("match_datetime") or ""),action,seq,same_core)
    except Exception:pass
    veto=empirical_veto_profile(team_id,team_name)
    p=Counter(profile.get("pick_counts") or {}); b=Counter(profile.get("ban_counts") or {})
    for k,v in (veto.get("pick_counts") or {}).items():p[k]+=float(v)
    for k,v in (veto.get("ban_counts") or {}).items():b[k]+=float(v)
    profile["pick_counts"]=dict(p); profile["ban_counts"]=dict(b); profile["empirical_veto_profile"]=veto
    return profile,{**status,"veto_history_observations":veto.get("observations",0),"veto_weighted_sample":veto.get("weighted_sample",0)}


def chronological_map_elo(force: bool = False) -> Dict[str, Any]:
    cached=load_model_fit("map_elo:global")
    updated=_parse_iso_datetime(cached.get("updated_at")) if cached else None
    if cached and not force and updated and (datetime.now(timezone.utc)-updated).total_seconds()<V43_RATING_CACHE_SECONDS:return cached
    try:
        with _sqlite_connect() as conn: recs=conn.execute("SELECT * FROM team_map_observations ORDER BY played_at ASC,created_at ASC").fetchall()
    except Exception:recs=[]
    global_r=defaultdict(lambda:1500.0); map_r=defaultdict(lambda:1500.0); samples=Counter(); seen=set()
    for rec in recs:
        try:payload=json.loads(rec["payload_json"] or "{}")
        except Exception:payload={}
        team=normalize_team(rec["team_name"]); team1=normalize_team(payload.get("team1")); team2=normalize_team(payload.get("team2"))
        opponent=team2 if _team_name_matches(team,team1) else team1 if _team_name_matches(team,team2) else normalize_team(payload.get("opponent"))
        if not team or not opponent or team==opponent:continue
        key=(str(rec["match_id"]),canonical_map_name(rec["map_name"]));
        if key in seen:continue
        seen.add(key); m=canonical_map_name(rec["map_name"]); ts=safe_float(rec["team_score"],None); oscore=safe_float(rec["opponent_score"],None)
        if ts is None or oscore is None or ts==oscore:continue
        actual=1.0 if ts>oscore else 0.0; margin=abs(ts-oscore); env_mult=1.06 if str(rec["environment"] or "").upper()=="LAN" else 1.0
        for ratings,scope in [(global_r,"G"),(map_r,"M")]:
            a=ratings[(scope,team,m if scope=="M" else "")]; b=ratings[(scope,opponent,m if scope=="M" else "")]
            expected=1/(1+10**((b-a)/400)); k=V43_ELO_K*env_mult*(1+min(margin,10)/25.0)
            ratings[(scope,team,m if scope=="M" else "")]=a+k*(actual-expected); ratings[(scope,opponent,m if scope=="M" else "")]=b-k*(actual-expected)
        samples[(team,m)]+=1; samples[(opponent,m)]+=1
    out={"global":{k[1]:v for k,v in global_r.items()},"map":{f"{k[1]}|{k[2]}":v for k,v in map_r.items()},"samples":{f"{k[0]}|{k[1]}":v for k,v in samples.items()},"matches":len(seen),"sample":len(seen),"fit_type":"chronological_map_elo"}
    save_model_fit("map_elo:global",out); return out


def map_elo_matchup(team: str, opponent: str, map_name: str = "") -> Dict[str, Any]:
    model=chronological_map_elo(); t=normalize_team(team); o=normalize_team(opponent); m=canonical_map_name(map_name)
    tg=safe_float((model.get("global") or {}).get(t),1500) or 1500; og=safe_float((model.get("global") or {}).get(o),1500) or 1500
    tm=safe_float((model.get("map") or {}).get(f"{t}|{m}"),tg) or tg; om=safe_float((model.get("map") or {}).get(f"{o}|{m}"),og) or og
    ts=safe_int((model.get("samples") or {}).get(f"{t}|{m}"),0) or 0; osamp=safe_int((model.get("samples") or {}).get(f"{o}|{m}"),0) or 0
    wt=clamp(min(ts,osamp)/30.0,0,1); tr=(1-wt)*tg+wt*tm; orating=(1-wt)*og+wt*om
    winp=1/(1+10**((orating-tr)/400)); return {"team_rating":round(tr,1),"opponent_rating":round(orating,1),"win_probability":winp,"team_map_samples":ts,"opponent_map_samples":osamp,"trained_matches":model.get("matches",0),"source":"chronological cumulative Elo"}


_v42_map_side_probabilities = _map_side_probabilities

def _map_side_probabilities(context: Dict[str, Any], map_name: str, team: str, opponent: str) -> Tuple[float,float,Dict[str,Any]]:
    p_ct,p_t,meta=_v42_map_side_probabilities(context,map_name,team,opponent)
    elo=map_elo_matchup(team,opponent,map_name); strength=(safe_float(elo.get("win_probability"),.5) or .5)-.5
    reliability=clamp(min(safe_int(elo.get("team_map_samples"),0),safe_int(elo.get("opponent_map_samples"),0))/20.0,0,1)
    shift=strength*.12*reliability
    return clamp(p_ct+shift,.24,.76),clamp(p_t+shift,.24,.76),{**meta,"elo":elo,"elo_side_shift":round(shift,5)}


_v42_opponent_kpr_factor = opponent_kpr_factor

def opponent_kpr_factor(profile: PlayerStats, context: Dict[str, Any], team: str, opponent: str, likely_maps: Sequence[str] = (), role: str = "") -> Tuple[float, Dict[str, Any]]:
    factor,meta=_v42_opponent_kpr_factor(profile,context,team,opponent,likely_maps,role)
    ratings=[map_elo_matchup(team,opponent,m) for m in likely_maps[:2]] or [map_elo_matchup(team,opponent,"")]
    probs=[safe_float(x.get("win_probability"),.5) or .5 for x in ratings]; samples=[min(safe_int(x.get("team_map_samples"),0),safe_int(x.get("opponent_map_samples"),0)) for x in ratings]
    reliability=clamp(float(np.mean(samples))/25.0,0,1) if samples else 0
    elo_factor=clamp(1+(float(np.mean(probs))-.5)*.10*reliability,.965,1.035)
    return clamp(factor*elo_factor,.88,1.12),{**meta,"chronological_elo":ratings,"elo_kpr_factor":round(elo_factor,4),"elo_reliability":round(reliability,3)}


def learned_economy_kill_environment(map_name: str = "") -> Dict[str, Any]:
    m=canonical_map_name(map_name); key=f"economy_kill_env:{m or 'GLOBAL'}"; cached=load_model_fit(key)
    updated=_parse_iso_datetime(cached.get("updated_at")) if cached else None
    if cached and updated and (datetime.now(timezone.utc)-updated).total_seconds()<24*3600:return cached
    samples=defaultdict(list)
    try:
        with _sqlite_connect() as conn:
            rounds=conn.execute("SELECT * FROM demo_rounds WHERE (?='' OR map_name=?)",(m,m)).fetchall()
            counts=conn.execute("SELECT match_id,round_num,team,COUNT(*) kills FROM demo_events WHERE (?='' OR map_name=?) AND team<>'' GROUP BY match_id,round_num,team",(m,m)).fetchall()
        cmap={(str(r["match_id"]),safe_int(r["round_num"],-1),normalize_team(r["team"])):int(r["kills"]) for r in counts}
        for r in rounds:
            teams=[("CT",normalize_team(r["ct_team"]),str(r["ct_buy_type"] or "UNKNOWN")),("T",normalize_team(r["t_team"]),str(r["t_buy_type"] or "UNKNOWN"))]
            winner=normalize_team(r["winner_team"])
            for side,team,buy in teams:
                if not team:continue
                outcome="WIN" if _team_name_matches(team,winner) or str(r["winner_side"] or "").upper()==side else "LOSS"
                samples[(buy if buy in {"FULL","FORCE","ECO","PISTOL"} else "UNKNOWN",outcome)].append(cmap.get((str(r["match_id"]),safe_int(r["round_num"],-1),team),0))
    except Exception:pass
    priors={("FULL","WIN"):4.05,("FULL","LOSS"):2.15,("FORCE","WIN"):4.15,("FORCE","LOSS"):1.75,("ECO","WIN"):4.45,("ECO","LOSS"):1.05,("PISTOL","WIN"):4.10,("PISTOL","LOSS"):1.85,("UNKNOWN","WIN"):4.10,("UNKNOWN","LOSS"):2.05}
    means={}; ns={}
    for k,prior in priors.items():
        vals=samples.get(k,[]); n=len(vals); means[f"{k[0]}_{k[1]}"]=(sum(vals)+prior*80)/(n+80); ns[f"{k[0]}_{k[1]}"]=n
    total=sum(ns.values()); out={"means":means,"samples":ns,"sample":total,"trained":total>=500,"source":"demo economy-round outcomes" if total else "economy priors","fit_type":"economy_kill_environment"}; save_model_fit(key,out); return out


def _economy_state(rng: np.random.Generator, loss_streak: np.ndarray, previous_win: np.ndarray, pistol: bool) -> np.ndarray:
    n=len(loss_streak)
    if pistol:return np.full(n,3,dtype=int) # PISTOL
    u=rng.random(n); state=np.zeros(n,dtype=int) # FULL=0 FORCE=1 ECO=2
    state[previous_win]=np.where(u[previous_win]<.82,0,1)
    mask=~previous_win
    ls=loss_streak[mask]; uu=u[mask]
    vals=np.where(ls<=1,np.where(uu<.20,0,np.where(uu<.78,1,2)),np.where(ls==2,np.where(uu<.18,0,np.where(uu<.55,1,2)),np.where(uu<.58,0,np.where(uu<.90,1,2))))
    state[mask]=vals; return state


def _simulate_mr12_round_environment(rng: np.random.Generator, n: int, p_ct: float, p_t: float, map_name: str = "") -> Dict[str, Any]:
    """Score-, side-, and economy-coupled MR12/MR3 simulation."""
    n=max(int(n),1); p_ct=clamp(p_ct,.22,.78); p_t=clamp(p_t,.22,.78); starts_ct=rng.random(n)<.5
    a=np.zeros(n,dtype=int); b=np.zeros(n,dtype=int); ak=np.zeros(n,dtype=int); bk=np.zeros(n,dtype=int)
    act=np.zeros(n,dtype=int); at=np.zeros(n,dtype=int); bct=np.zeros(n,dtype=int); bt=np.zeros(n,dtype=int)
    loss_a=np.zeros(n,dtype=int); loss_b=np.zeros(n,dtype=int); prev_a=np.zeros(n,dtype=bool); prev_b=np.zeros(n,dtype=bool)
    env=learned_economy_kill_environment(map_name); names=np.array(["FULL","FORCE","ECO","PISTOL"],dtype=object); econ_counts=Counter()
    def play(mask: np.ndarray, probs: np.ndarray, team_a_ct_round: np.ndarray, pistol: bool=False):
        nonlocal a,b,ak,bk,loss_a,loss_b,prev_a,prev_b,act,at,bct,bt
        if not mask.any():return
        sa=_economy_state(rng,loss_a,prev_a,pistol); sb=_economy_state(rng,loss_b,prev_b,pistol)
        strength=np.array([1.0,.90,.70,.92]); adj=np.clip(probs+(strength[sa]-strength[sb])*.18,.12,.88)
        awin=(rng.random(n)<adj)&mask; bwin=(~awin)&mask
        for i,label in enumerate(names):econ_counts[str(label)]+=int(((sa==i)&mask).sum())+int(((sb==i)&mask).sum())
        def draw(states: np.ndarray,outcome: str,max_k:int):
            means=np.array([safe_float(env["means"].get(f"{names[s]}_{outcome}"),4.1 if outcome=="WIN" else 2.0) or 2.0 for s in states])
            return np.minimum(rng.binomial(max_k,np.clip(means/max_k,.02,.98)),max_k)
        aw=draw(sa,"WIN",5); al=draw(sa,"LOSS",4); bw=draw(sb,"WIN",5); bl=draw(sb,"LOSS",4)
        ka=np.where(awin,aw,np.where(bwin,al,0)); kb=np.where(bwin,bw,np.where(awin,bl,0)); excess=np.maximum(ka+kb-9,0); kb=np.maximum(kb-excess,0)
        ak+=ka; bk+=kb; a+=awin; b+=bwin
        team_a_ct=np.asarray(team_a_ct_round,dtype=bool)
        act+=mask&team_a_ct; at+=mask&(~team_a_ct); bct+=mask&(~team_a_ct); bt+=mask&team_a_ct
        loss_a=np.where(awin,0,np.where(bwin,loss_a+1,loss_a)); loss_b=np.where(bwin,0,np.where(awin,loss_b+1,loss_b)); prev_a=awin; prev_b=bwin
    active=np.ones(n,dtype=bool)
    for r in range(12):play(active,np.where(starts_ct,p_ct,p_t),starts_ct,pistol=(r==0))
    active=(a<13)&(b<13)
    for r in range(12):
        if not active.any():break
        play(active,np.where(starts_ct,p_t,p_ct),~starts_ct,pistol=(r==0)); active=(a<13)&(b<13)&((a+b)<24)
    tied=(a==12)&(b==12); blocks=0
    while tied.any() and blocks<5:
        ba=np.zeros(n,dtype=int); bb=np.zeros(n,dtype=int); block=tied.copy()
        for rr in range(6):
            if not block.any():break
            before_a=a.copy();before_b=b.copy();play(block,np.where(starts_ct,p_ct if rr<3 else p_t,p_t if rr<3 else p_ct),starts_ct if rr<3 else ~starts_ct,False)
            ba+=a-before_a;bb+=b-before_b;block=tied&(ba<4)&(bb<4)&((ba+bb)<6)
        tied=tied&(ba==3)&(bb==3);blocks+=1
    if tied.any():play(tied,np.full(n,.5),starts_ct,False)
    return {"rounds":a+b,"team_score":a,"opponent_score":b,"team_kills":ak,"opponent_kills":bk,"team_ct_rounds":act,"team_t_rounds":at,"opponent_ct_rounds":bct,"opponent_t_rounds":bt,"economy_counts":dict(econ_counts),"economy_environment":env,"round_kill_environment":env}


def simulation_projection_deep(player: str,line: float,base_kpr: float,player_map_profiles: Dict[str, Dict[str, Any]],rounds_meta: Dict[str, Any],opponent_factor: float,opponent_meta: Dict[str, Any],context_mean_factor: float,context_variance_multiplier: float,profile_maps: int,role: str,learning_shift: float) -> Dict[str, Any]:
    scenarios=list(rounds_meta.get("scenarios") or []) or [{"maps":["Unknown","Unknown"],"probability":1.0,"map_models":[{"map":"Unknown","mean_rounds":21.4,"rounds_sd":3.8},{"map":"Unknown","mean_rounds":21.4,"rounds_sd":3.8}]}]
    probs=np.array([max(safe_float(x.get("probability"),0) or 0,0) for x in scenarios],dtype=float);probs=probs/probs.sum() if probs.sum()>0 else np.ones(len(scenarios))/len(scenarios)
    rng=np.random.default_rng(stable_seed(player,line,base_kpr,profile_maps,role,learning_shift,MODEL_VERSION));n=SIMULATIONS;idx=rng.choice(len(scenarios),size=n,p=probs)
    total=np.zeros(n,dtype=int);trounds=np.zeros(n,dtype=int);direct_all=np.zeros(n,dtype=int);share_all=np.zeros(n,dtype=int);blend=learned_simulation_blend(player,role)
    unc=(.025+.05/math.sqrt(max(profile_maps,1)/10+1)+(0.012 if "Unknown" in role else 0)+(0.008 if "Entry" in role else 0))*context_variance_multiplier
    common=rng.normal(0,unc,size=n);map_factors=opponent_meta.get("map_factors") or {};pseudo=PlayerStats(player=player,maps=profile_maps,kpr=base_kpr);sharep=team_kill_share_parameters(pseudo,role,opponent_meta);breakdown={}
    for sn,scenario in enumerate(scenarios):
        mask=idx==sn;count=int(mask.sum())
        if not count:continue
        maps=list(scenario.get("maps") or [])[:2];models=list(scenario.get("map_models") or [])[:2]
        while len(maps)<2:maps.append("Unknown")
        while len(models)<2:models.append({"map":maps[len(models)],"mean_rounds":21.4,"rounds_sd":3.8})
        sk=np.zeros(count,dtype=int);sr=np.zeros(count,dtype=int);cform=common[mask]
        for map_name,model in zip(maps,models):
            pct=safe_float(model.get("team_ct_round_win_prob"),None);pt=safe_float(model.get("team_t_round_win_prob"),None)
            if pct is not None and pt is not None:env=_simulate_mr12_round_environment(rng,count,pct,pt,map_name);mr=env["rounds"]
            else:
                mr=np.clip(np.rint(rng.normal(safe_float(model.get("mean_rounds"),21.4) or 21.4,safe_float(model.get("rounds_sd"),3.8) or 3.8,size=count)).astype(int),13,42)
                env={"team_kills":rng.poisson(np.clip(mr*sharep["team_kpr"],4,120)),"team_ct_rounds":mr//2,"team_t_rounds":mr-mr//2}
            row=player_map_profiles.get(map_name,{}) ; mk=safe_float(row.get("blended_kpr"),base_kpr) or base_kpr; ctk=safe_float(row.get("ct_kpr"),mk) or mk; tk=safe_float(row.get("t_kpr"),mk) or mk
            local=safe_float(map_factors.get(map_name),1) or 1;factor=opponent_factor*local*context_mean_factor
            ctk=clamp(ctk*factor,.36,1.12);tk=clamp(tk*factor,.36,1.12);noise=rng.normal(0,.018*context_variance_multiplier,size=count)
            ct_rounds=np.asarray(env.get("team_ct_rounds"));t_rounds=np.asarray(env.get("team_t_rounds"));lam=np.clip(ct_rounds*np.clip(ctk+cform+noise,.34,1.15)+t_rounds*np.clip(tk+cform+noise,.34,1.15),.5,52)
            disp=18 if "Entry" in role else 24;direct=rng.poisson(rng.gamma(shape=disp,scale=lam/disp))
            teamkills=np.asarray(env["team_kills"]);alpha=max(sharep["player_share"]*sharep["concentration"],.5);beta=max((1-sharep["player_share"])*sharep["concentration"],.5);latent=rng.beta(alpha,beta,size=count);share=rng.binomial(teamkills,np.clip(latent,.04,.42))
            w=clamp(safe_float(blend.get("share_weight"),.5) or .5,.15,.85);mix=(1-w)*direct+w*share;frac=mix-np.floor(mix);kills=np.floor(mix).astype(int)+(rng.random(count)<frac)
            disagreement=np.abs(direct-share);extra=np.clip((disagreement-3)/8,0,.45);kills+=rng.binomial(1,extra);kills=np.maximum(kills,0)
            sk+=kills;sr+=mr;direct_all[mask]+=direct;share_all[mask]+=share
            b=breakdown.setdefault(map_name,{"rounds":0.0,"kills":0.0,"weight":0.0});pw=probs[sn];b["rounds"]+=pw*float(np.mean(mr));b["kills"]+=pw*float(np.mean(kills));b["weight"]+=pw
        total[mask]=sk;trounds[mask]=sr
    shift=clamp(float(learning_shift or 0),-MAX_LEARNING_PROJECTION_SHIFT,MAX_LEARNING_PROJECTION_SHIFT)
    if shift>0:total+=rng.binomial(1,min(shift,1),size=n)
    elif shift<0:total=np.maximum(total-rng.binomial(1,min(abs(shift),1),size=n),0)
    proj=float(np.mean(total));direct_mean=float(np.mean(direct_all));share_mean=float(np.mean(share_all));disagreement=abs(direct_mean-share_mean)
    out_break={k:{"expected_rounds":round(v["rounds"]/max(v["weight"],1e-9),3),"expected_kills":round(v["kills"]/max(v["weight"],1e-9),3),"scenario_weight":round(v["weight"],4),"player_map_kpr":player_map_profiles.get(k,{}).get("blended_kpr"),"ct_kpr":player_map_profiles.get(k,{}).get("ct_kpr"),"t_kpr":player_map_profiles.get(k,{}).get("t_kpr")} for k,v in breakdown.items()}
    return {"projection":proj,"median":float(np.median(total)),"over_prob":float(np.mean(total>line)),"under_prob":float(np.mean(total<line)),"push_prob":float(np.mean(total==line)) if float(line).is_integer() else 0.0,"floor_20":float(np.percentile(total,20)),"ceiling_80":float(np.percentile(total,80)),"p10":float(np.percentile(total,10)),"p90":float(np.percentile(total,90)),"sim_sd":float(np.std(total)),"expected_rounds":float(np.mean(trounds)),"rounds_sd":float(np.std(trounds)),"effective_kpr":proj/max(float(np.mean(trounds)),1),"map_breakdown":out_break,"team_kill_share_model":{**sharep,"blend":blend},"model_components":{"direct_projection":direct_mean,"share_projection":share_mean,"share_weight":blend.get("share_weight"),"blend_sample":blend.get("sample",0),"blend_scope":blend.get("scope"),"model_disagreement":disagreement,"economy_aware":True}}


def _beta_interval(wins: float, losses: float, prior_mean: float, prior_strength: float=36.0) -> Tuple[float,float,float]:
    a=wins+prior_mean*prior_strength;b=losses+(1-prior_mean)*prior_strength;mean=a/(a+b);var=a*b/(((a+b)**2)*(a+b+1));sd=math.sqrt(max(var,1e-9));return mean,clamp(mean-1.645*sd,.001,.999),clamp(mean+1.645*sd,.001,.999)


def calibrate_probability(raw_probability: float, context: Optional[Dict[str, Any]] = None, prior_rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    raw=clamp(float(raw_probability),.001,.999);rows=_calibration_subset(prior_rows if prior_rows is not None else _graded_binary_rows(),context);n=len(rows)
    near=[x for x in rows if abs(x["_p"]-raw)<=.06];wins=sum(x["_y"] for x in near);local=len(near);post,low,high=_beta_interval(wins,local-wins,raw)
    iso=_isotonic_fit_predict(rows,raw) if n>=120 else None
    target=(.55*iso+.45*post) if iso is not None else post;reliability=min(n/(n+220.0),local/(local+70.0));cal=raw*(1-reliability)+target*reliability
    # Model disagreement and data-quality uncertainty can only shrink confidence, never inflate it.
    disagreement=safe_float((context or {}).get("model_disagreement"),0) or safe_float(((context or {}).get("model_components") or {}).get("model_disagreement"),0) or 0
    if disagreement>2:cal=.5+(cal-.5)*clamp(1-(disagreement-2)*.08,.65,1)
    ceiling=.69 if n<MIN_CALIBRATION_PRELIMINARY else .75 if n<MIN_CALIBRATION_USABLE else .82;cal=clamp(cal,.50,ceiling)
    tier="UNREADY" if n<MIN_CALIBRATION_PRELIMINARY else "PRELIMINARY" if n<MIN_CALIBRATION_USABLE else "USABLE" if n<MIN_CALIBRATION_STRONG else "STRONG"
    ready=bool(n>=MIN_CALIBRATION_USABLE and local>=V43_MIN_LOCAL_CALIBRATION)
    return {"raw":raw,"calibrated":cal,"sample":n,"local_sample":local,"empirical_rate":wins/local if local else None,"posterior_mean":post,"lower90":low,"upper90":high,"method":"walk-forward isotonic + local beta uncertainty" if iso is not None else "local beta-binomial shrinkage","ready":ready,"tier":tier}


def row_accuracy_health(row: Dict[str, Any]) -> Dict[str, Any]:
    flags=[];score=100.0
    if not row.get("market_scope_verified"):flags.append("MARKET SCOPE");score-=35
    if not row.get("core_kpr_verified"):flags.append("CORE KPR");score-=30
    if not row.get("identity_official_ready"):flags.append("IDENTITY/LINEUP");score-=25
    if not row.get("player_source_fresh"):flags.append("PLAYER SOURCE STALE");score-=15
    if safe_int(row.get("current_roster_maps"),0)<V43_MIN_CURRENT_ROSTER_MAPS:flags.append("THIN CURRENT ROSTER SAMPLE");score-=12
    map_profiles=row.get("player_map_profiles") or {};usable=[x for x in map_profiles.values() if safe_float(x.get("blended_kpr"),None) is not None]
    if len(usable)<2:flags.append("INCOMPLETE MAP MODEL");score-=15
    if any((safe_float(x.get("map_model_confidence"),0) or 0)<55 for x in usable):flags.append("LOW MAP CONFIDENCE");score-=8
    er=safe_float(row.get("expected_rounds"),None)
    if er is None or not 26<=er<=68:flags.append("ROUND MODEL RANGE");score-=30
    disagreement=safe_float(((row.get("model_components") or {}).get("model_disagreement")),0) or 0
    if disagreement>V43_MAX_MODEL_DISAGREEMENT:flags.append("DIRECT/SHARE DISAGREEMENT");score-=min(18,(disagreement-V43_MAX_MODEL_DISAGREEMENT)*5)
    return {"score":round(clamp(score,0,100),1),"flags":flags,"model_disagreement":round(disagreement,3),"ready":score>=82 and not flags}


def global_source_circuit(board: List[Dict[str, Any]], status: Dict[str, Any]) -> Dict[str, Any]:
    n=max(len(board),1);profiles=sum(bool(x.get("core_kpr_verified")) for x in board)/n;ids=sum(bool(x.get("identity_ids",{}).get("match_id")) for x in board)/n;fresh=sum(bool(x.get("player_source_fresh")) for x in board)/n;valid=sum(not x.get("error") for x in board)/n
    line_rows=safe_int((status.get("long") or {}).get("rows"),0) or 0
    score=100*(.30*profiles+.25*ids+.20*fresh+.15*valid+.10*min(line_rows/40,1));grade="HEALTHY" if score>=78 else "CAUTION" if score>=58 else "DEGRADED"
    payload={"score":round(score,1),"grade":grade,"profile_rate":round(profiles,3),"match_id_rate":round(ids,3),"fresh_rate":round(fresh,3),"valid_rate":round(valid,3),"player_table_rows":line_rows,"official_enabled":grade=="HEALTHY"}
    eid=hashlib.sha256(f"{now_iso()[:16]}|{grade}|{round(score)}".encode()).hexdigest()
    try:
        with _sqlite_connect() as conn:conn.execute("INSERT OR REPLACE INTO source_health_events(event_id,source,grade,observed_at,payload_json,created_at) VALUES(?,?,?,?,?,?)",(eid,"combined projection sources",grade,now_iso(),json.dumps(payload),now_iso()))
    except Exception:pass
    return payload


_v42_build_projection_for_prop = build_projection_for_prop

def build_projection_for_prop(prop: Dict[str, Any],long_table: Dict[str, Dict[str, Any]],medium_table: Dict[str, Dict[str, Any]],recent30_table: Dict[str, Dict[str, Any]],recent15_table: Dict[str, Dict[str, Any]],deep_enabled: bool=True) -> Dict[str, Any]:
    row=_v42_build_projection_for_prop(prop,long_table,medium_table,recent30_table,recent15_table,deep_enabled)
    if row.get("projection") is None:return row
    health=row_accuracy_health(row);row["accuracy_health"]=health;row["model_disagreement"]=health.get("model_disagreement")
    # Recalibrate using the completed model components, including disagreement uncertainty.
    raw=safe_float(row.get("raw_probability"),None)
    if raw is not None:
        cal=calibrate_probability(raw,{**row,"model_disagreement":health.get("model_disagreement")});row["probability"]=round(cal["calibrated"],4);row["calibration_sample"]=cal["sample"];row["calibration_local_sample"]=cal["local_sample"];row["calibration_lower90"]=round(cal["lower90"],4);row["calibration_upper90"]=round(cal["upper90"],4);row["calibration_ready"]=cal["ready"];row["calibration_method"]=cal["method"];row["calibration_tier"]=cal["tier"]
    flags=list(row.get("flags") or [])
    if row.get("status")=="OFFICIAL" and not health.get("ready"):
        row["status"]="PLAYABLE" if health.get("score",0)>=68 else "TRACK";row["status_label"]="✅ PLAYABLE — ACCURACY GATES" if row["status"]=="PLAYABLE" else "⚠️ TRACK — ACCURACY GATES";flags.extend(health.get("flags") or [])
    if row.get("status")=="OFFICIAL" and safe_float(row.get("calibration_lower90"),0)<.54:
        row["status"]="TRACK";row["status_label"]="⚠️ TRACK — PROBABILITY UNCERTAINTY";flags.append("CALIBRATION LOWER BOUND BELOW OFFICIAL STANDARD")
    if row.get("status")=="OFFICIAL" and safe_int(row.get("current_roster_maps"),0)<V43_MIN_CURRENT_ROSTER_MAPS:
        row["status"]="TRACK";row["status_label"]="⚠️ TRACK — CURRENT ROSTER SAMPLE";flags.append("FEWER THAN 8 MAPS WITH CURRENT CORE")
    row["flags"]=list(dict.fromkeys(flags));row["model_version"]=MODEL_VERSION;return row


_v42_build_full_board = build_full_board

def build_full_board(props: List[Dict[str, Any]], deep_enabled: bool=True) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    board,status=_v42_build_full_board(props,deep_enabled);circuit=global_source_circuit(board,status) if board else {"grade":"DEGRADED","official_enabled":False,"score":0}
    if not circuit.get("official_enabled"):
        for row in board:
            if row.get("status")=="OFFICIAL":row["status"]="TRACK";row["status_label"]="⚠️ TRACK — SOURCE CIRCUIT BREAKER";row["flags"]=list(dict.fromkeys(list(row.get("flags") or [])+["GLOBAL SOURCE HEALTH NOT STRONG ENOUGH FOR OFFICIAL"]));row["source_circuit_breaker"]=circuit
    board.sort(key=lambda x:({"OFFICIAL":0,"PLAYABLE":1,"TRACK":2,"PASS":3}.get(x.get("status"),9),-safe_float(x.get("probability"),0),-safe_float(x.get("abs_edge"),0)))
    return board,{**status,"source_circuit_breaker":circuit,"v43_accuracy_layer":True,"elo_matches":chronological_map_elo().get("matches",0)}


# V4.3.1 roster/opponent and opponent-specific veto refinements.
_v43_enrich_match_context_base = enrich_match_context

def enrich_match_context(context: Dict[str, Any], deep_enabled: bool = True) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    enriched,status=_v43_enrich_match_context_base(context,deep_enabled)
    profiles=enriched.get("team_deep_profiles") or []
    if len(profiles)>=2:
        for i,p in enumerate(profiles[:2]):
            other=profiles[1-i]
            emp=empirical_veto_profile(str(p.get("team_id") or ""),str(p.get("team") or ""),str(other.get("team") or ""))
            picks=Counter(p.get("pick_counts") or {});bans=Counter(p.get("ban_counts") or {})
            for k,v in (emp.get("pick_counts") or {}).items():picks[k]+=float(v)
            for k,v in (emp.get("ban_counts") or {}).items():bans[k]+=float(v)
            p["pick_counts"]=dict(picks);p["ban_counts"]=dict(bans);p["opponent_specific_veto"]=emp
        enriched["team_deep_profiles"]=profiles
        status={**status,"opponent_specific_veto_samples":[safe_int((p.get("opponent_specific_veto") or {}).get("opponent_specific"),0) for p in profiles[:2]]}
    return enriched,status


_v43_match_context_adjustment_base = match_context_adjustment

def match_context_adjustment(profile: PlayerStats, context: Dict[str, Any], team: str, opponent: str) -> Tuple[float,float,Dict[str,Any]]:
    mean,var,meta=_v43_match_context_adjustment_base(profile,context,team,opponent)
    tp,op=_resolve_team_profiles(context,team,opponent)
    team_maps=safe_int(tp.get("current_roster_maps"),0) or 0;opp_maps=safe_int(op.get("current_roster_maps"),0) or 0
    team_stab=safe_float(tp.get("roster_stability"),None);opp_stab=safe_float(op.get("roster_stability"),None)
    notes=list(meta.get("notes") or [])
    if team_maps<V43_MIN_CURRENT_ROSTER_MAPS:var*=1.07;notes.append("thin player-team current-core sample")
    if opp_maps<V43_MIN_CURRENT_ROSTER_MAPS:var*=1.06;notes.append("thin opponent current-core sample")
    if opp_stab is not None and opp_stab<.45:var*=1.05;notes.append("opponent role/roster uncertainty")
    return clamp(mean,.965,1.025),clamp(var,1.0,1.30),{**meta,"current_roster_maps":team_maps,"opponent_current_roster_maps":opp_maps,"opponent_roster_stability":opp_stab,"team_roster_stability":team_stab,"notes":notes}


# Redefine row health after the opponent-aware context fields are available.
_v43_row_accuracy_health_base = row_accuracy_health

def row_accuracy_health(row: Dict[str, Any]) -> Dict[str, Any]:
    out=_v43_row_accuracy_health_base(row);score=float(out.get("score",0));flags=list(out.get("flags") or [])
    context_meta=((row.get("source_meta") or {}).get("context_adjustment") or {})
    opp_maps=safe_int(context_meta.get("opponent_current_roster_maps"),0) or 0
    opp_stab=safe_float(context_meta.get("opponent_roster_stability"),None)
    if opp_maps<V43_MIN_CURRENT_ROSTER_MAPS:score-=8;flags.append("THIN OPPONENT CURRENT-ROSTER SAMPLE")
    if opp_stab is not None and opp_stab<.40:score-=6;flags.append("LOW OPPONENT ROSTER CONTINUITY")
    veto_state=str(row.get("veto_state") or "")
    if veto_state=="PRE_VETO" and safe_float(row.get("map_confidence"),0)<70:score-=8;flags.append("LOW PRE-VETO MAP CERTAINTY")
    return {**out,"score":round(clamp(score,0,100),1),"flags":list(dict.fromkeys(flags)),"opponent_current_roster_maps":opp_maps,"opponent_roster_stability":opp_stab,"ready":score>=82 and not flags}



# ============================================================
# V4.4 FINAL RELIABILITY LAYER — DIRECTION NEUTRALITY / JOINT LINEUPS
# ============================================================

MODEL_SCHEMA_FINGERPRINT = "v44_joint5_side_veto_glicko_dualgrade_neutral_schema1"
V44_MIN_JOINT_GROUP = 2
V44_DIRECTION_ALERT_RATIO = 0.72
V44_DIRECTION_ALERT_MIN_ROWS = 10
V44_NEUTRAL_EDGE_ZONE = 0.65
V44_MIN_DUAL_GRADE_WEIGHT = 0.50
V44_CALIBRATION_HALF_LIFE_DAYS = 180.0
V44_GLICKO_CACHE_SECONDS = 3600


def init_v44_schema() -> None:
    with _sqlite_connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS model_registry (
          model_version TEXT PRIMARY KEY, feature_fingerprint TEXT NOT NULL,
          status TEXT NOT NULL, promoted_at TEXT, payload_json TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS side_choice_observations (
          observation_id TEXT PRIMARY KEY, team_key TEXT NOT NULL, opponent_key TEXT,
          match_id TEXT, map_name TEXT, started_side TEXT, observed_at TEXT,
          source TEXT, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_side_choice_team_map ON side_choice_observations(team_key,map_name,observed_at);
        CREATE TABLE IF NOT EXISTS model_comparisons (
          comparison_id TEXT PRIMARY KEY, model_version TEXT, feature_fingerprint TEXT,
          champion_name TEXT, challenger_name TEXT, sample_size INTEGER,
          champion_mae REAL, challenger_mae REAL, champion_bias REAL, challenger_bias REAL,
          decision TEXT, payload_json TEXT NOT NULL, evaluated_at TEXT NOT NULL
        );
        """)
        payload={"model_version":MODEL_VERSION,"feature_fingerprint":MODEL_SCHEMA_FINGERPRINT,"market":"CS2 Maps 1-2 Kills"}
        conn.execute("""INSERT INTO model_registry(model_version,feature_fingerprint,status,promoted_at,payload_json,created_at,updated_at)
                      VALUES(?,?,?,'',?,?,?)
                      ON CONFLICT(model_version) DO UPDATE SET feature_fingerprint=excluded.feature_fingerprint,
                      payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                     (MODEL_VERSION,MODEL_SCHEMA_FINGERPRINT,"ACTIVE",json.dumps(payload),now_iso(),now_iso()))


init_v44_schema()


def snapshot_key(row: Dict[str, Any]) -> str:
    # Model version and schema are part of the identity so a v4.4 projection
    # can never overwrite or inherit the grade of an older engine.
    raw=f"{MODEL_VERSION}|{MODEL_SCHEMA_FINGERPRINT}|{normalize_name(row.get('player'))}|{row.get('line')}|{row.get('lean')}|{str(row.get('start_time',''))[:16]}|{row.get('source')}"
    return hashlib.md5(raw.encode()).hexdigest()


def _row_model_compatible(row: Dict[str, Any]) -> bool:
    return str(row.get("model_version") or "") == MODEL_VERSION and str(row.get("feature_fingerprint") or MODEL_SCHEMA_FINGERPRINT) == MODEL_SCHEMA_FINGERPRINT


def _grade_weight(row: Dict[str, Any]) -> float:
    meta=row.get("grade_meta") or {}
    if str(row.get("grade_source") or "").lower().startswith("manual"):
        return 1.0
    if meta.get("dual_source_confirmed"):
        return 1.0
    if meta.get("training_eligible"):
        return clamp(safe_float(meta.get("confirmation_weight"),0.70) or 0.70,V44_MIN_DUAL_GRADE_WEIGHT,1.0)
    return clamp(safe_float(meta.get("confirmation_weight"),0.50) or 0.50,0.25,0.65)


def _graded_binary_rows() -> List[Dict[str, Any]]:
    """Only the exact current feature schema can train v4.4 probabilities."""
    combined=[];seen=set();raw=load_json(RESULT_LOG,[]);raw=raw if isinstance(raw,list) else []
    for row in raw+sqlite_graded_projection_rows():
        if not _row_model_compatible(row):
            continue
        result=str(row.get("graded_result") or "").upper()
        p=safe_float(row.get("raw_probability"),None)
        if p is None: p=safe_float(row.get("probability"),None)
        if result not in {"WIN","LOSS"} or p is None: continue
        key=str(row.get("snapshot_id") or snapshot_key(row))
        if key in seen: continue
        seen.add(key);item=dict(row);item["_p"]=clamp(float(p),.001,.999);item["_y"]=1 if result=="WIN" else 0
        item["_time"]=str(row.get("projection_time") or row.get("saved_at") or row.get("graded_at") or "")
        dt=_parse_iso_datetime(item["_time"]);age=max(0.0,(datetime.now(timezone.utc)-dt).total_seconds()/86400.0) if dt else V44_CALIBRATION_HALF_LIFE_DAYS
        item["_w"]=_grade_weight(row)*(0.5**(age/V44_CALIBRATION_HALF_LIFE_DAYS))
        combined.append(item)
    combined.sort(key=lambda x:x.get("_time",""));return combined


def _calibration_subset(rows: List[Dict[str, Any]], context: Optional[Dict[str, Any]]=None) -> List[Dict[str, Any]]:
    context=context or {};lean=str(context.get("lean") or "");veto=str(context.get("veto_state") or "");tier=str(context.get("event_tier") or "");era=str(context.get("patch_era") or "")
    scoped=[x for x in rows if _row_model_compatible(x) and (not lean or str(x.get("lean") or "")==lean)]
    for field,value,min_base,min_n in [("veto_state",veto,35,25),("patch_era",era,60,30),("event_tier",tier,80,35)]:
        if value and len(scoped)>=min_base:
            narrower=[x for x in scoped if str(x.get(field) or "")==value]
            if len(narrower)>=min_n: scoped=narrower
    return scoped


def _weighted_isotonic_predict(rows: List[Dict[str, Any]], raw: float) -> Optional[float]:
    if len(rows)<60:return None
    pairs=sorted((float(x["_p"]),float(x["_y"]),max(float(x.get("_w",1)),.01)) for x in rows)
    blocks=[]
    for p,y,w in pairs:
        blocks.append([p,p,y*w,w])
        while len(blocks)>=2 and blocks[-2][2]/blocks[-2][3] > blocks[-1][2]/blocks[-1][3]:
            b=blocks.pop();a=blocks.pop();blocks.append([a[0],b[1],a[2]+b[2],a[3]+b[3]])
    pts=[((a+b)/2,sy/sw) for a,b,sy,sw in blocks]
    if raw<=pts[0][0]:return pts[0][1]
    if raw>=pts[-1][0]:return pts[-1][1]
    for (x1,y1),(x2,y2) in zip(pts,pts[1:]):
        if x1<=raw<=x2:
            t=(raw-x1)/max(x2-x1,1e-9);return y1+t*(y2-y1)
    return None


def _platt_predict(rows: List[Dict[str, Any]], raw: float) -> Optional[float]:
    if len(rows)<60:return None
    p=np.clip(np.array([x["_p"] for x in rows],dtype=float),.005,.995);y=np.array([x["_y"] for x in rows],dtype=float);w=np.array([max(float(x.get("_w",1)),.01) for x in rows])
    x=np.log(p/(1-p));X=np.column_stack([np.ones(len(x)),x]);beta=np.array([0.0,1.0])
    for _ in range(30):
        z=np.clip(X@beta,-20,20);q=1/(1+np.exp(-z));g=X.T@(w*(y-q))-np.array([.02*beta[0],.05*(beta[1]-1)])
        h=-(X.T@((w*q*(1-q))[:,None]*X))-np.diag([.02,.05])
        try:step=np.linalg.solve(h,g)
        except Exception:break
        beta-=step
        if float(np.max(np.abs(step)))<1e-7:break
    xr=math.log(clamp(raw,.005,.995)/(1-clamp(raw,.005,.995)));return float(1/(1+math.exp(-clamp(beta[0]+beta[1]*xr,-20,20))))


def _weighted_brier(rows: List[Dict[str, Any]], predictor) -> float:
    vals=[];weights=[]
    for r in rows:
        pred=predictor(r["_p"])
        if pred is None:continue
        vals.append((pred-r["_y"])**2);weights.append(max(float(r.get("_w",1)),.01))
    return float(np.average(vals,weights=weights)) if vals else 99.0


def calibrate_probability(raw_probability: float, context: Optional[Dict[str, Any]]=None, prior_rows: Optional[List[Dict[str, Any]]]=None) -> Dict[str, Any]:
    raw=clamp(float(raw_probability),.001,.999);rows=_calibration_subset(prior_rows if prior_rows is not None else _graded_binary_rows(),context);n=len(rows);effective=float(sum(x.get("_w",1) for x in rows))
    near=[x for x in rows if abs(x["_p"]-raw)<=.06];wins=sum(x["_y"]*x.get("_w",1) for x in near);losses=sum((1-x["_y"])*x.get("_w",1) for x in near);local=float(sum(x.get("_w",1) for x in near))
    post,low,high=_beta_interval(wins,losses,raw,prior_strength=48.0)
    iso=_weighted_isotonic_predict(rows,raw);platt=_platt_predict(rows,raw)
    method="weighted local beta-binomial";target=post
    if n>=120:
        split=max(60,int(n*.80));train=rows[:split];hold=rows[split:]
        iso_fn=lambda p:_weighted_isotonic_predict(train,p);pl_fn=lambda p:_platt_predict(train,p)
        bi=_weighted_brier(hold,iso_fn);bp=_weighted_brier(hold,pl_fn);br=_weighted_brier(hold,lambda p:p)
        if min(bi,bp)<br-.002:
            if bi<=bp and iso is not None:target=.65*iso+.35*post;method="versioned weighted isotonic + beta shrinkage"
            elif platt is not None:target=.65*platt+.35*post;method="versioned Platt + beta shrinkage"
    reliability=min(effective/(effective+220.0),local/(local+75.0));cal=raw*(1-reliability)+target*reliability
    disagreement=safe_float((context or {}).get("model_disagreement"),0) or safe_float(((context or {}).get("model_components") or {}).get("model_disagreement"),0) or 0
    if disagreement>2:cal=.5+(cal-.5)*clamp(1-(disagreement-2)*.08,.60,1)
    ceiling=.68 if effective<MIN_CALIBRATION_PRELIMINARY else .74 if effective<MIN_CALIBRATION_USABLE else .82
    cal=clamp(cal,.50,ceiling);tier="UNREADY" if effective<MIN_CALIBRATION_PRELIMINARY else "PRELIMINARY" if effective<MIN_CALIBRATION_USABLE else "USABLE" if effective<MIN_CALIBRATION_STRONG else "STRONG"
    ready=bool(effective>=MIN_CALIBRATION_USABLE and local>=V43_MIN_LOCAL_CALIBRATION)
    return {"raw":raw,"calibrated":cal,"sample":n,"effective_sample":round(effective,2),"local_sample":round(local,2),"empirical_rate":wins/local if local else None,"posterior_mean":post,"lower90":low,"upper90":high,"method":method,"ready":ready,"tier":tier,"model_version":MODEL_VERSION,"feature_fingerprint":MODEL_SCHEMA_FINGERPRINT}


def build_learning_profiles(results: Optional[List[Dict[str, Any]]]=None) -> Dict[str, Any]:
    rows=results if results is not None else sqlite_graded_projection_rows()+((load_json(RESULT_LOG,[]) if isinstance(load_json(RESULT_LOG,[]),list) else []))
    graded=[x for x in rows if _row_model_compatible(x) and x.get("graded_result") in {"WIN","LOSS","PUSH"} and safe_float(x.get("actual_kills"),None) is not None]
    profiles={"model_version":MODEL_VERSION,"feature_fingerprint":MODEL_SCHEMA_FINGERPRINT,"global":{},"players":{},"roles":{},"line_buckets":{},"maps":{},"event_tiers":{},"directions":{},"updated_at":now_iso()}
    def summarize(group):
        vals=[];ws=[]
        for x in group:
            base=safe_float(x.get("projection_before_learning"),None)
            if base is None:base=safe_float(x.get("projection"),None)
            if base is None:continue
            vals.append(float(x["actual_kills"])-base);ws.append(_grade_weight(x))
        n=len(vals)
        if not n:return {"samples":0,"bias":0.0,"mae":None}
        raw=float(np.average(vals,weights=ws));shrunk=raw*n/(n+35)
        return {"samples":n,"effective_samples":round(sum(ws),2),"bias":round(clamp(shrunk,-.30,.30),4),"raw_bias":round(raw,4),"mae":round(float(np.average(np.abs(vals),weights=ws)),4)}
    profiles["global"]=summarize(graded)
    sections={k:defaultdict(list) for k in ["players","roles","line_buckets","maps","event_tiers","directions"]}
    for r in graded:
        sections["players"][normalize_name(r.get("player"))].append(r);sections["roles"][str(r.get("role") or "Unknown")].append(r);sections["directions"][str(r.get("lean") or "")].append(r)
        line=safe_float(r.get("line"),0) or 0;sections["line_buckets"][f"{int(line//5)*5}-{int(line//5)*5+4.5}"].append(r)
        for m in r.get("likely_maps") or []:sections["maps"][str(m)].append(r)
        sections["event_tiers"][str(r.get("event_tier") or "LOW/UNKNOWN")].append(r)
    for key,groups in sections.items():profiles[key]={k:summarize(v) for k,v in groups.items()}
    save_json(LEARNING_FILE,profiles);return profiles


def learning_adjustment(player: str,role: str,line: float,likely_maps: Sequence[str]=(),event_tier: str="",data_score_hint: int=0) -> Tuple[float,Dict[str,Any]]:
    profiles=load_json(LEARNING_FILE,{})
    if not isinstance(profiles,dict) or profiles.get("model_version")!=MODEL_VERSION or profiles.get("feature_fingerprint")!=MODEL_SCHEMA_FINGERPRINT:profiles=build_learning_profiles()
    pieces=[]
    def add(row,min_n,w,label):
        if safe_int(row.get("samples"),0)>=min_n:pieces.append((safe_float(row.get("bias"),0) or 0,w,label))
    add(profiles.get("global",{}),150,.35,"global")
    add((profiles.get("players") or {}).get(normalize_name(player),{}),50,.25,"player")
    add((profiles.get("roles") or {}).get(role,{}),120,.15,"role")
    bucket=f"{int(line//5)*5}-{int(line//5)*5+4.5}";add((profiles.get("line_buckets") or {}).get(bucket,{}),100,.10,"line bucket")
    map_bias=[]
    for m in likely_maps[:2]:
        rr=(profiles.get("maps") or {}).get(str(m),{})
        if safe_int(rr.get("samples"),0)>=100:map_bias.append(safe_float(rr.get("bias"),0) or 0)
    if map_bias:pieces.append((float(np.mean(map_bias)),.10,"map context"))
    add((profiles.get("event_tiers") or {}).get(str(event_tier),{}),100,.05,"event tier")
    if not pieces:return 0.0,{"applied":False,"pieces":[],"model_version":MODEL_VERSION}
    total=sum(w for _,w,_ in pieces);adj=clamp(sum(v*w for v,w,_ in pieces)/max(total,.01),-.30,.30)
    return adj,{"applied":True,"pieces":pieces,"adjustment":round(adj,4),"model_version":MODEL_VERSION}


def learned_team_kill_share_parameters(player: str,role: str) -> Dict[str,Any]:
    rows=[r for r in sqlite_graded_projection_rows() if _row_model_compatible(r)]
    scopes=[("player",lambda r:normalize_name(r.get("player"))==normalize_name(player),MIN_BLEND_TRAINING_SAMPLES),("role",lambda r:str(r.get("role") or "")==str(role),100),("global",lambda r:True,200)]
    for scope,pred,min_n in scopes:
        samples=[]
        for row in rows:
            observed=safe_float(row.get("observed_player_share"),None)
            if observed is None:observed=safe_float((row.get("team_kill_share_model") or {}).get("observed_player_share"),None)
            if pred(row) and observed is not None and .04<=observed<=.50:samples.append(float(observed))
        if len(samples)>=min_n:
            mean=float(np.mean(samples));var=float(np.var(samples));conc=clamp(mean*(1-mean)/max(var,1e-4)-1,12,160)
            return {"player_share":clamp(mean,.08,.36),"concentration":conc,"sample":len(samples),"source":f"v4.4 historical {scope} team-kill share","scope":scope}
    return {"sample":0,"scope":"prior"}


def learned_simulation_blend(player: str,role: str) -> Dict[str,Any]:
    rows=[r for r in sqlite_graded_projection_rows() if _row_model_compatible(r)]
    scopes=[("player",lambda r:normalize_name(r.get("player"))==normalize_name(player),MIN_BLEND_TRAINING_SAMPLES),("role",lambda r:str(r.get("role") or "")==str(role),80),("global",lambda r:True,150)]
    for scope,pred,min_n in scopes:
        pairs=[]
        for r in rows:
            comp=r.get("model_components") or {};actual=safe_float(r.get("actual_kills"),None);d=safe_float(comp.get("direct_projection"),None);sh=safe_float(comp.get("share_projection"),None)
            if pred(r) and None not in (actual,d,sh):pairs.append((abs(actual-d),abs(actual-sh)))
        if len(pairs)>=min_n:
            dm=float(np.mean([x[0] for x in pairs]));sm=float(np.mean([x[1] for x in pairs]));w=clamp(dm/max(dm+sm,1e-6),.20,.80)
            return {"share_weight":w,"direct_weight":1-w,"sample":len(pairs),"scope":scope,"direct_mae":dm,"share_mae":sm,"trained":True,"source":"v4.4 isolated history"}
    return {"share_weight":.50,"direct_weight":.50,"sample":0,"scope":"prior","trained":False,"source":"neutral untrained blend"}


def _parse_starting_side_hints(page: str,teams: Sequence[Dict[str,Any]],maps: Sequence[str]) -> Dict[str,Dict[str,float]]:
    text=" ".join(strip_tags(page or "").split());out={}
    for m in maps or KNOWN_MAPS:
        for team in teams[:2]:
            name=str(team.get("name") or "")
            if not name:continue
            patterns=[rf"{re.escape(name)}.{{0,80}}(?:start|starting).{{0,25}}\b(CT|T)\b.{{0,80}}{re.escape(m)}",rf"{re.escape(m)}.{{0,80}}{re.escape(name)}.{{0,80}}(?:start|starting).{{0,25}}\b(CT|T)\b"]
            for pat in patterns:
                hit=re.search(pat,text,re.I)
                if hit:out.setdefault(m,{})[normalize_team(name)]=1.0 if hit.group(1).upper()=="CT" else 0.0;break
    return out


def empirical_start_ct_probability(team: str,map_name: str,days: int=365) -> Dict[str,Any]:
    cutoff=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat();seen=set();ct=0;total=0
    try:
        with _sqlite_connect() as conn:rows=conn.execute("SELECT * FROM demo_rounds WHERE map_name=? AND event_time>=? ORDER BY match_id,round_num",(canonical_map_name(map_name),cutoff)).fetchall()
        for r in rows:
            mid=str(r["match_id"] or "")
            if mid in seen:continue
            seen.add(mid);total+=1
            if _team_name_matches(team,r["ct_team"]):ct+=1
            elif not _team_name_matches(team,r["t_team"]):total-=1
    except Exception:pass
    p=(ct+4)/(total+8) if total else .5
    return {"probability":p,"sample":total,"source":"demo first-round side history" if total else "50/50 unknown side prior"}


_v43_fetch_match_context=fetch_match_context

def fetch_match_context(match_url: str) -> Tuple[Dict[str,Any],Dict[str,Any]]:
    context,status=_v43_fetch_match_context(match_url)
    if context:
        page,_=http_get_text(match_url,"HLTV match side hints",ttl=2*60,timeout=20,allow_stale=False)
        context["starting_side_hints"]=_parse_starting_side_hints(page or "",context.get("teams") or [],context.get("confirmed_maps") or KNOWN_MAPS)
    return context,status


_v43_map_round_model=_map_round_model

def _map_round_model(context: Dict[str,Any],map_name: str,team: str="",opponent: str="") -> Dict[str,Any]:
    out=_v43_map_round_model(context,map_name,team,opponent);hints=(context.get("starting_side_hints") or {}).get(map_name,{})
    exact=hints.get(normalize_team(team));emp=empirical_start_ct_probability(team,map_name)
    start_p=float(exact) if exact is not None else .5+(safe_float(emp.get("probability"),.5)-.5)*clamp(safe_int(emp.get("sample"),0)/30,0,1)
    out["team_start_ct_probability"]=clamp(start_p,0,1);out["starting_side_source"]="confirmed match page" if exact is not None else emp.get("source");out["starting_side_sample"]=emp.get("sample",0)
    return out


def _simulate_mr12_round_environment(rng: np.random.Generator,n: int,p_ct: float,p_t: float,map_name: str="",start_ct_probability: float=.5) -> Dict[str,Any]:
    n=max(int(n),1);p_ct=clamp(p_ct,.22,.78);p_t=clamp(p_t,.22,.78);start_ct_probability=clamp(start_ct_probability,0,1);starts_ct=rng.random(n)<start_ct_probability
    a=np.zeros(n,dtype=int);b=np.zeros(n,dtype=int);ak=np.zeros(n,dtype=int);bk=np.zeros(n,dtype=int);act=np.zeros(n,dtype=int);at=np.zeros(n,dtype=int);bct=np.zeros(n,dtype=int);bt=np.zeros(n,dtype=int)
    loss_a=np.zeros(n,dtype=int);loss_b=np.zeros(n,dtype=int);prev_a=np.zeros(n,dtype=bool);prev_b=np.zeros(n,dtype=bool);env=learned_economy_kill_environment(map_name);names=np.array(["FULL","FORCE","ECO","PISTOL"],dtype=object);econ_counts=Counter()
    def play(mask,probs,team_a_ct_round,pistol=False):
        nonlocal a,b,ak,bk,loss_a,loss_b,prev_a,prev_b,act,at,bct,bt
        if not mask.any():return
        sa=_economy_state(rng,loss_a,prev_a,pistol);sb=_economy_state(rng,loss_b,prev_b,pistol);strength=np.array([1.0,.90,.70,.92]);adj=np.clip(probs+(strength[sa]-strength[sb])*.18,.12,.88);awin=(rng.random(n)<adj)&mask;bwin=(~awin)&mask
        for i,label in enumerate(names):econ_counts[str(label)]+=int(((sa==i)&mask).sum())+int(((sb==i)&mask).sum())
        def draw(states,outcome,max_k):
            means=np.array([safe_float(env["means"].get(f"{names[s]}_{outcome}"),4.1 if outcome=="WIN" else 2.0) or 2.0 for s in states]);return np.minimum(rng.binomial(max_k,np.clip(means/max_k,.02,.98)),max_k)
        aw=draw(sa,"WIN",5);al=draw(sa,"LOSS",4);bw=draw(sb,"WIN",5);bl=draw(sb,"LOSS",4);ka=np.where(awin,aw,np.where(bwin,al,0));kb=np.where(bwin,bw,np.where(awin,bl,0));excess=np.maximum(ka+kb-9,0);kb=np.maximum(kb-excess,0)
        ak+=ka;bk+=kb;a+=awin;b+=bwin;team_a_ct=np.asarray(team_a_ct_round,dtype=bool);act+=mask&team_a_ct;at+=mask&(~team_a_ct);bct+=mask&(~team_a_ct);bt+=mask&team_a_ct
        loss_a=np.where(awin,0,np.where(bwin,loss_a+1,loss_a));loss_b=np.where(bwin,0,np.where(awin,loss_b+1,loss_b));prev_a=awin;prev_b=bwin
    active=np.ones(n,dtype=bool)
    for r in range(12):play(active,np.where(starts_ct,p_ct,p_t),starts_ct,pistol=(r==0))
    active=(a<13)&(b<13)
    for r in range(12):
        if not active.any():break
        play(active,np.where(starts_ct,p_t,p_ct),~starts_ct,pistol=(r==0));active=(a<13)&(b<13)&((a+b)<24)
    tied=(a==12)&(b==12);blocks=0
    while tied.any() and blocks<5:
        ba=np.zeros(n,dtype=int);bb=np.zeros(n,dtype=int);block=tied.copy()
        for rr in range(6):
            if not block.any():break
            before_a=a.copy();before_b=b.copy();play(block,np.where(starts_ct,p_ct if rr<3 else p_t,p_t if rr<3 else p_ct),starts_ct if rr<3 else ~starts_ct,False);ba+=a-before_a;bb+=b-before_b;block=tied&(ba<4)&(bb<4)&((ba+bb)<6)
        tied=tied&(ba==3)&(bb==3);blocks+=1
    if tied.any():play(tied,np.full(n,.5),starts_ct,False)
    return {"rounds":a+b,"team_score":a,"opponent_score":b,"team_kills":ak,"opponent_kills":bk,"team_ct_rounds":act,"team_t_rounds":at,"opponent_ct_rounds":bct,"opponent_t_rounds":bt,"starts_ct":starts_ct,"start_ct_probability":start_ct_probability,"economy_counts":dict(econ_counts),"economy_environment":env,"round_kill_environment":env}


def simulate_veto_scenarios(team_profiles: Sequence[Dict[str,Any]],simulations: int=8000) -> List[Dict[str,Any]]:
    if len(team_profiles)<2:return []
    profiles=list(team_profiles[:2]);active=set(patch_era_for_time().get("active_maps") or CURRENT_ACTIVE_MAPS);maps=[m for m in KNOWN_MAPS if m in active]
    if len(maps)<4:return []
    rng=np.random.default_rng(stable_seed("veto-full-mass",*(p.get("team") for p in profiles),MODEL_VERSION));counts=Counter()
    for _ in range(simulations):
        available=list(maps);picked=[]
        for ti in [0,1]:
            p=profiles[ti];weights=[]
            for m in available:
                row=(p.get("map_profiles") or {}).get(m,{});play=safe_float(row.get("maps"),0) or 0;win=safe_float(row.get("win_pct"),50) or 50;bans=safe_float((p.get("ban_counts") or {}).get(m),0) or 0
                weights.append(1+bans*2.4+max(0,6-play)*.32+max(0,48-win)*.045)
            ban=_sample_weighted(rng,available,weights)
            if ban in available:available.remove(ban)
        for ti in [0,1]:
            p=profiles[ti];opp=profiles[1-ti];weights=[]
            for m in available:
                row=(p.get("map_profiles") or {}).get(m,{});orow=(opp.get("map_profiles") or {}).get(m,{});play=safe_float(row.get("maps"),0) or 0;win=safe_float(row.get("win_pct"),50) or 50;rw=safe_float(row.get("round_win_pct"),50) or 50;pc=safe_float((p.get("pick_counts") or {}).get(m),0) or 0;ob=safe_float((opp.get("ban_counts") or {}).get(m),0) or 0;ow=safe_float(orow.get("win_pct"),50) or 50
                weights.append(1+pc*3.1+play*.42+max(0,win-45)*.12+max(0,rw-47)*.10+max(0,52-ow)*.05-ob*.10)
            choice=_sample_weighted(rng,available,weights)
            if choice:picked.append(choice);available.remove(choice)
        if len(picked)==2:counts[tuple(picked)]+=1
    total=sum(counts.values())
    if not total:return []
    return [{"maps":list(pair),"probability":count/total,"simulation_count":count,"probability_mass_retained":1.0} for pair,count in counts.most_common()]


def project_expected_rounds(context: Dict[str,Any],likely_maps: Sequence[str],map_meta: Optional[Dict[str,Any]]=None,team: str="",opponent: str="") -> Tuple[float,float,Dict[str,Any]]:
    scenarios=list((map_meta or {}).get("scenarios") or [])
    if not scenarios and len(likely_maps)>=2:scenarios=[{"maps":list(likely_maps[:2]),"probability":1.0}]
    if not scenarios:scenarios=[{"maps":[likely_maps[0] if likely_maps else "Unknown",likely_maps[1] if len(likely_maps)>1 else "Unknown"],"probability":1.0}]
    total=sum(max(safe_float(x.get("probability"),0) or 0,0) for x in scenarios) or 1.0;normalized=[]
    # A full seven-map veto creates up to 42 ordered pairs, but only seven unique
    # map models. Cache each map once so preserving the probability tail does not
    # multiply the expensive MR12 side simulation by every pair.
    model_cache={}
    for scenario in scenarios:
        maps=list(scenario.get("maps") or [])[:2]
        while len(maps)<2:maps.append("Unknown")
        models=[]
        for m in maps:
            if m not in model_cache:
                model_cache[m]=_map_round_model(context,m,team,opponent) if m in KNOWN_MAPS else {"map":m,"mean_rounds":21.1,"rounds_sd":3.8,"team_start_ct_probability":.5}
            models.append(model_cache[m])
        prob=max(safe_float(scenario.get("probability"),0) or 0,0)/total;mean=sum(float(x["mean_rounds"]) for x in models);var=sum(float(x["rounds_sd"])**2 for x in models)
        normalized.append({"maps":maps,"probability":prob,"map_models":models,"mean_rounds":mean,"rounds_sd":math.sqrt(var)})
    mean=sum(x["probability"]*x["mean_rounds"] for x in normalized);second=sum(x["probability"]*(x["rounds_sd"]**2+x["mean_rounds"]**2) for x in normalized);sd=math.sqrt(max(second-mean**2,.01));mass=sum(x["probability"] for x in normalized)
    return clamp(mean,26,64),clamp(sd,2.8,10.5),{"invalid_format":context.get("format")=="BO1","format":context.get("format","UNKNOWN"),"scenarios":normalized,"probability_mass_retained":round(mass,6),"scenario_count":len(normalized),"full_veto_mass":True}


_v43_sqlite_store_team_map_observation = sqlite_store_team_map_observation

def sqlite_store_team_map_observation(team_id: str,team_name: str,match_url: str,row: Dict[str,Any],opponent_rank: Optional[float],same_core: bool,environment: str) -> bool:
    ok=_v43_sqlite_store_team_map_observation(team_id,team_name,match_url,row,opponent_rank,same_core,environment)
    if ok:invalidate_model_fits(["roster_glicko:","map_elo:"])
    return ok


def chronological_roster_glicko(force: bool=False) -> Dict[str,Any]:
    cached=load_model_fit("roster_glicko:v44")
    updated=_parse_iso_datetime(cached.get("updated_at")) if cached else None
    if cached and not force and updated and (datetime.now(timezone.utc)-updated).total_seconds()<V44_GLICKO_CACHE_SECONDS:return cached
    try:
        with _sqlite_connect() as conn:recs=conn.execute("SELECT * FROM team_map_observations ORDER BY played_at ASC,created_at ASC").fetchall()
    except Exception:recs=[]
    ratings=defaultdict(lambda:1500.0);rds=defaultdict(lambda:350.0);last={};latest_fp={};samples=Counter();seen=set()
    for rec in recs:
        try:p=json.loads(rec["payload_json"] or "{}")
        except Exception:p={}
        team=normalize_team(rec["team_name"]);t1=normalize_team(p.get("team1"));t2=normalize_team(p.get("team2"));opp=t2 if _team_name_matches(team,t1) else t1 if _team_name_matches(team,t2) else normalize_team(p.get("opponent"))
        if not team or not opp or team==opp:continue
        key=(str(rec["match_id"]),canonical_map_name(rec["map_name"]),team)
        if key in seen:continue
        seen.add(key);m=canonical_map_name(rec["map_name"]);fp=str(p.get("roster_fingerprint") or "ORG");latest_fp[team]=fp;opp_fp=latest_fp.get(opp,"ORG")
        ts=safe_float(rec["team_score"],None);oscore=safe_float(rec["opponent_score"],None)
        if ts is None or oscore is None or ts==oscore:continue
        actual=1.0 if ts>oscore else 0.0;dt=_parse_iso_datetime(rec["played_at"]);keys=[f"G|{team}|{fp}",f"M|{team}|{fp}|{m}"];okeys=[f"G|{opp}|{opp_fp}",f"M|{opp}|{opp_fp}|{m}"]
        for ka,kb in zip(keys,okeys):
            if dt:
                for kk in [ka,kb]:
                    age=max(0,(dt-last[kk]).total_seconds()/86400) if kk in last else 60;rds[kk]=min(350,math.sqrt(rds[kk]**2+(age*3.0)**2));last[kk]=dt
            ra,rb=ratings[ka],ratings[kb];rda,rdb=rds[ka],rds[kb];expected=1/(1+10**((rb-ra)/400));unc=clamp((rda+rdb)/700,.25,1);k=30*unc*(1+min(abs(ts-oscore),10)/30)
            ratings[ka]=ra+k*(actual-expected);ratings[kb]=rb-k*(actual-expected);rds[ka]=max(55,rda*.965);rds[kb]=max(55,rdb*.965);samples[ka]+=1;samples[kb]+=1
    out={"ratings":dict(ratings),"rds":dict(rds),"samples":dict(samples),"latest_fingerprint":latest_fp,"matches":len(seen),"fit_type":"roster-era Glicko-style","feature_fingerprint":MODEL_SCHEMA_FINGERPRINT};save_model_fit("roster_glicko:v44",out);return out


def map_elo_matchup(team: str,opponent: str,map_name: str="") -> Dict[str,Any]:
    model=chronological_roster_glicko();t=normalize_team(team);o=normalize_team(opponent);m=canonical_map_name(map_name);tf=(model.get("latest_fingerprint") or {}).get(t,"ORG");of=(model.get("latest_fingerprint") or {}).get(o,"ORG")
    tg=f"G|{t}|{tf}";og=f"G|{o}|{of}";tm=f"M|{t}|{tf}|{m}";om=f"M|{o}|{of}|{m}";ratings=model.get("ratings") or {};rds=model.get("rds") or {};samples=model.get("samples") or {}
    tr=safe_float(ratings.get(tm),safe_float(ratings.get(tg),1500)) or 1500;orr=safe_float(ratings.get(om),safe_float(ratings.get(og),1500)) or 1500;rdt=safe_float(rds.get(tm),safe_float(rds.get(tg),350)) or 350;rdo=safe_float(rds.get(om),safe_float(rds.get(og),350)) or 350
    reliability=clamp(1-(rdt+rdo)/700,0,1);winp=1/(1+10**((orr-tr)/400));winp=.5+(winp-.5)*(.35+.65*reliability)
    return {"team_rating":round(tr,1),"opponent_rating":round(orr,1),"team_rd":round(rdt,1),"opponent_rd":round(rdo,1),"win_probability":winp,"team_map_samples":safe_int(samples.get(tm),0) or 0,"opponent_map_samples":safe_int(samples.get(om),0) or 0,"team_roster_fingerprint":tf,"opponent_roster_fingerprint":of,"reliability":round(reliability,3),"trained_matches":model.get("matches",0),"source":"chronological roster-era Glicko-style"}


_V44_ECONOMY_ENV_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_v43_learned_economy_kill_environment = learned_economy_kill_environment

def learned_economy_kill_environment(map_name: str = "") -> Dict[str, Any]:
    key=canonical_map_name(map_name) or "GLOBAL";now=time.time();cached=_V44_ECONOMY_ENV_CACHE.get(key)
    if cached and now-cached[0]<900:return dict(cached[1])
    out=_v43_learned_economy_kill_environment(map_name);_V44_ECONOMY_ENV_CACHE[key]=(now,dict(out));return out


def simulation_projection_deep(player: str,line: float,base_kpr: float,player_map_profiles: Dict[str,Dict[str,Any]],rounds_meta: Dict[str,Any],opponent_factor: float,opponent_meta: Dict[str,Any],context_mean_factor: float,context_variance_multiplier: float,profile_maps: int,role: str,learning_shift: float) -> Dict[str,Any]:
    scenarios=list(rounds_meta.get("scenarios") or []) or [{"maps":["Unknown","Unknown"],"probability":1.0,"map_models":[{"map":"Unknown","mean_rounds":21.4,"rounds_sd":3.8,"team_start_ct_probability":.5},{"map":"Unknown","mean_rounds":21.4,"rounds_sd":3.8,"team_start_ct_probability":.5}]}]
    probs=np.array([max(safe_float(x.get("probability"),0) or 0,0) for x in scenarios]);probs=probs/probs.sum() if probs.sum()>0 else np.ones(len(scenarios))/len(scenarios);rng=np.random.default_rng(stable_seed(player,line,base_kpr,profile_maps,role,learning_shift,MODEL_VERSION));n=SIMULATIONS;idx=rng.choice(len(scenarios),size=n,p=probs)
    total=np.zeros(n,dtype=int);trounds=np.zeros(n,dtype=int);direct_all=np.zeros(n,dtype=int);share_all=np.zeros(n,dtype=int);blend=learned_simulation_blend(player,role);unc=(.025+.05/math.sqrt(max(profile_maps,1)/10+1)+(0.012 if "Unknown" in role else 0)+(0.008 if "Entry" in role else 0))*context_variance_multiplier;common=rng.normal(0,unc,n);map_factors=opponent_meta.get("map_factors") or {};sharep=team_kill_share_parameters(PlayerStats(player=player,maps=profile_maps,kpr=base_kpr),role,opponent_meta);breakdown={}
    for sn,scenario in enumerate(scenarios):
        mask=idx==sn;count=int(mask.sum())
        if not count:continue
        maps=list(scenario.get("maps") or [])[:2];models=list(scenario.get("map_models") or [])[:2]
        while len(maps)<2:maps.append("Unknown")
        while len(models)<2:models.append({"map":maps[len(models)],"mean_rounds":21.4,"rounds_sd":3.8,"team_start_ct_probability":.5})
        sk=np.zeros(count,dtype=int);sr=np.zeros(count,dtype=int);cform=common[mask]
        for m,model in zip(maps,models):
            pct=safe_float(model.get("team_ct_round_win_prob"),None);pt=safe_float(model.get("team_t_round_win_prob"),None);sp=safe_float(model.get("team_start_ct_probability"),.5) or .5
            if pct is not None and pt is not None:env=_simulate_mr12_round_environment(rng,count,pct,pt,m,sp);mr=env["rounds"]
            else:mr=np.clip(np.rint(rng.normal(safe_float(model.get("mean_rounds"),21.4) or 21.4,safe_float(model.get("rounds_sd"),3.8) or 3.8,count)).astype(int),13,42);env={"team_kills":rng.poisson(np.clip(mr*sharep["team_kpr"],4,120)),"team_ct_rounds":mr//2,"team_t_rounds":mr-mr//2,"start_ct_probability":sp}
            row=player_map_profiles.get(m,{});mk=safe_float(row.get("blended_kpr"),base_kpr) or base_kpr;ctk=safe_float(row.get("ct_kpr"),mk) or mk;tk=safe_float(row.get("t_kpr"),mk) or mk;local=safe_float(map_factors.get(m),1) or 1;factor=opponent_factor*local*context_mean_factor;ctk=clamp(ctk*factor,.36,1.12);tk=clamp(tk*factor,.36,1.12);noise=rng.normal(0,.018*context_variance_multiplier,count)
            cr=np.asarray(env["team_ct_rounds"]);tr=np.asarray(env["team_t_rounds"]);lam=np.clip(cr*np.clip(ctk+cform+noise,.34,1.15)+tr*np.clip(tk+cform+noise,.34,1.15),.5,52);disp=18 if "Entry" in role else 24;direct=rng.poisson(rng.gamma(disp,lam/disp));teamkills=np.asarray(env["team_kills"]);alpha=max(sharep["player_share"]*sharep["concentration"],.5);beta=max((1-sharep["player_share"])*sharep["concentration"],.5);share=rng.binomial(teamkills,np.clip(rng.beta(alpha,beta,count),.04,.42));w=clamp(safe_float(blend.get("share_weight"),.5) or .5,.15,.85);mix=(1-w)*direct+w*share;frac=mix-np.floor(mix);kills=np.floor(mix).astype(int)+(rng.random(count)<frac);sk+=kills;sr+=mr;direct_all[mask]+=direct;share_all[mask]+=share
            b=breakdown.setdefault(m,{"rounds":0.,"kills":0.,"weight":0.,"start_ct_probability":0.});pw=probs[sn];b["rounds"]+=pw*float(np.mean(mr));b["kills"]+=pw*float(np.mean(kills));b["weight"]+=pw;b["start_ct_probability"]+=pw*sp
        total[mask]=sk;trounds[mask]=sr
    shift=clamp(float(learning_shift or 0),-.30,.30)
    if shift>0:total+=rng.binomial(1,shift,n)
    elif shift<0:total=np.maximum(total-rng.binomial(1,abs(shift),n),0)
    proj=float(np.mean(total));dm=float(np.mean(direct_all));sm=float(np.mean(share_all));dis=abs(dm-sm);out={k:{"expected_rounds":round(v["rounds"]/max(v["weight"],1e-9),3),"expected_kills":round(v["kills"]/max(v["weight"],1e-9),3),"scenario_weight":round(v["weight"],4),"start_ct_probability":round(v["start_ct_probability"]/max(v["weight"],1e-9),3),"player_map_kpr":player_map_profiles.get(k,{}).get("blended_kpr")} for k,v in breakdown.items()}
    return {"projection":proj,"median":float(np.median(total)),"over_prob":float(np.mean(total>line)),"under_prob":float(np.mean(total<line)),"push_prob":float(np.mean(total==line)) if float(line).is_integer() else 0.,"floor_20":float(np.percentile(total,20)),"ceiling_80":float(np.percentile(total,80)),"p10":float(np.percentile(total,10)),"p90":float(np.percentile(total,90)),"sim_sd":float(np.std(total)),"expected_rounds":float(np.mean(trounds)),"rounds_sd":float(np.std(trounds)),"effective_kpr":proj/max(float(np.mean(trounds)),1),"map_breakdown":out,"team_kill_share_model":{**sharep,"blend":blend},"model_components":{"direct_projection":dm,"share_projection":sm,"individual_projection":proj,"share_weight":blend.get("share_weight"),"blend_sample":blend.get("sample",0),"model_disagreement":dis,"economy_aware":True,"starting_side_modeled":True}}


def _count_samples(mean: float,sd: float,n: int,rng: np.random.Generator) -> np.ndarray:
    mean=max(float(mean),.01);var=max(float(sd)**2,mean)
    if var<=mean*1.05:return rng.poisson(mean,n)
    shape=mean**2/max(var-mean,1e-6);scale=max(var-mean,1e-6)/mean;return rng.poisson(rng.gamma(shape,scale,n))


def model_comparison_state() -> Dict[str,Any]:
    rows=[r for r in sqlite_graded_projection_rows() if _row_model_compatible(r)];pairs=[]
    for r in rows:
        c=r.get("model_components") or {};a=safe_float(r.get("actual_kills"),None);champ=safe_float(c.get("individual_projection"),None);chall=safe_float(c.get("joint_projection"),None)
        if None not in (a,champ,chall):pairs.append((a,champ,chall))
    n=len(pairs);cm=float(np.mean([abs(a-c) for a,c,h in pairs])) if pairs else None;hm=float(np.mean([abs(a-h) for a,c,h in pairs])) if pairs else None;cb=float(np.mean([a-c for a,c,h in pairs])) if pairs else None;hb=float(np.mean([a-h for a,c,h in pairs])) if pairs else None
    decision="JOINT_GUARDED";bonus=0.0
    if n>=100 and hm is not None and cm is not None:
        if hm<=cm-.05 and abs(hb or 0)<=abs(cb or 0)+.15:decision="JOINT_PROMOTED";bonus=.15
        elif hm>=cm+.08:decision="JOINT_REDUCED";bonus=-.15
    payload={"sample":n,"champion_mae":cm,"challenger_mae":hm,"champion_bias":cb,"challenger_bias":hb,"decision":decision,"joint_weight_bonus":bonus,"model_version":MODEL_VERSION}
    cid=hashlib.sha256(f"{MODEL_VERSION}|{n}|{decision}|{round(cm or 0,3)}|{round(hm or 0,3)}".encode()).hexdigest()
    try:
        with _sqlite_connect() as conn:conn.execute("INSERT OR REPLACE INTO model_comparisons VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(cid,MODEL_VERSION,MODEL_SCHEMA_FINGERPRINT,"individual score-coupled","joint lineup Dirichlet",n,cm,hm,cb,hb,decision,json.dumps(payload),now_iso()))
    except Exception:pass
    return payload


def _neutral_projection_correction(row: Dict[str,Any]) -> Tuple[float,Dict[str,Any]]:
    rows=[r for r in sqlite_graded_projection_rows() if _row_model_compatible(r) and safe_float(r.get("actual_kills"),None) is not None and safe_float(r.get("projection"),None) is not None]
    if len(rows)<100:return 0.0,{"sample":len(rows),"applied":False}
    bucket=int((safe_float(row.get("line"),0) or 0)//5)*5;subset=[r for r in rows if int((safe_float(r.get("line"),0) or 0)//5)*5==bucket]
    use=subset if len(subset)>=50 else rows;res=[float(r["actual_kills"])-float(r["projection"]) for r in use];raw=float(np.mean(res));adj=clamp(raw*len(res)/(len(res)+60),-.25,.25)
    return adj,{"sample":len(use),"raw_residual":round(raw,4),"adjustment":round(adj,4),"applied":True,"scope":"line bucket" if use is subset else "global"}


def _joint_lineup_reconcile(board: List[Dict[str,Any]]) -> Dict[str,Any]:
    groups=defaultdict(list)
    for row in board:
        if row.get("projection") is None or row.get("status")=="PASS":continue
        ids=row.get("identity_ids") or {};key=(str(ids.get("match_id") or row.get("match_url") or ""),str(ids.get("team_id") or normalize_team(row.get("team"))))
        if all(key):groups[key].append(row)
    comparison=model_comparison_state();updated=0
    for key,rows in groups.items():
        if len(rows)<V44_MIN_JOINT_GROUP:continue
        n=SIMULATIONS;rng=np.random.default_rng(stable_seed("joint-lineup",key,MODEL_VERSION));team_means=[]
        for r in rows:
            tm=r.get("team_kill_share_model") or {};tkpr=safe_float(tm.get("team_kpr"),None);er=safe_float(r.get("expected_rounds"),None)
            if tkpr and er:team_means.append(tkpr*er)
        team_mean=float(np.median(team_means)) if team_means else max(sum(safe_float(r.get("projection"),0) or 0 for r in rows)/.82,20)
        team_sd=max(5.0,math.sqrt(team_mean)*1.35);team_totals=_count_samples(team_mean,team_sd,n,rng)
        raw_shares=[]
        for r in rows:
            s=safe_float((r.get("team_kill_share_model") or {}).get("player_share"),None)
            if s is None:s=(safe_float(r.get("projection"),0) or 0)/max(team_mean,1)
            raw_shares.append(clamp(s,.06,.36))
        reserve=clamp(1-sum(raw_shares),.10,.55);scale=(1-reserve)/max(sum(raw_shares),1e-9);means=[x*scale for x in raw_shares]+[reserve];conc=70 if len(rows)>=5 else 48;alpha=np.maximum(np.array(means)*conc,.4);g=rng.gamma(alpha,1,(n,len(alpha)));probs=g/g.sum(axis=1,keepdims=True);remaining=team_totals.copy();joint=[];remain_prob=np.ones(n)
        for j in range(len(rows)):
            cond=np.clip(probs[:,j]/np.maximum(remain_prob,1e-9),0,1);draw=rng.binomial(remaining,cond);joint.append(draw);remaining-=draw;remain_prob-=probs[:,j]
        base_weight=0.55 if len(rows)>=5 else .45 if len(rows)>=3 else .32;weight=clamp(base_weight+safe_float(comparison.get("joint_weight_bonus"),0),.15,.75)
        for r,js in zip(rows,joint):
            ind=_count_samples(safe_float(r.get("projection"),0) or 0,safe_float(r.get("sim_sd"),5) or 5,n,rng);mix=(1-weight)*ind+weight*js;frac=mix-np.floor(mix);samples=np.floor(mix).astype(int)+(rng.random(n)<frac)
            correction,cmeta=_neutral_projection_correction(r)
            if correction>0:samples+=rng.binomial(1,correction,n)
            elif correction<0:samples=np.maximum(samples-rng.binomial(1,abs(correction),n),0)
            oldlean=str(r.get("lean") or "");line=float(r.get("line"));projection=float(np.mean(samples));edge=projection-line;lean="OVER" if edge>0 else "UNDER";rawp=float(np.mean(samples>line)) if lean=="OVER" else float(np.mean(samples<line));cal=calibrate_probability(rawp,{**r,"lean":lean,"model_components":{**(r.get("model_components") or {}),"joint_projection":float(np.mean(js))}})
            r.update({"projection":round(projection,2),"edge":round(edge,2),"abs_edge":round(abs(edge),2),"lean":lean,"raw_probability":round(rawp,4),"probability":round(cal["calibrated"],4),"over_probability":round(float(np.mean(samples>line)),4),"under_probability":round(float(np.mean(samples<line)),4),"median":round(float(np.median(samples)),1),"p10":round(float(np.percentile(samples,10)),1),"p90":round(float(np.percentile(samples,90)),1),"floor_20":round(float(np.percentile(samples,20)),1),"ceiling_80":round(float(np.percentile(samples,80)),1),"sim_sd":round(float(np.std(samples)),2),"calibration_sample":cal["sample"],"calibration_effective_sample":cal["effective_sample"],"calibration_local_sample":cal["local_sample"],"calibration_lower90":round(cal["lower90"],4),"calibration_upper90":round(cal["upper90"],4),"calibration_ready":cal["ready"],"calibration_method":cal["method"],"calibration_tier":cal["tier"],"neutral_projection_correction":cmeta,"joint_lineup_model":{"group_size":len(rows),"team_kills_mean":round(team_mean,3),"player_share_mean":round(float(np.mean(js/np.maximum(team_totals,1))),4),"joint_weight":round(weight,3),"reserve_share":round(reserve,3),"champion_challenger":comparison}})
            comp=dict(r.get("model_components") or {});comp.update({"individual_projection":safe_float(comp.get("individual_projection"),safe_float(r.get("projection_before_learning"),projection)),"joint_projection":float(np.mean(js)),"final_joint_blend_projection":projection,"joint_weight":weight,"joint_group_size":len(rows)});r["model_components"]=comp
            if oldlean and oldlean!=lean:r["direction_flip_after_joint"]=True;r["flags"]=list(dict.fromkeys(list(r.get("flags") or [])+["DIRECTION FLIPPED AFTER JOINT LINEUP RECONCILIATION"]))
            updated+=1
    return {"groups":len(groups),"rows_updated":updated,"comparison":comparison}


def _v44_reclassify(row: Dict[str,Any]) -> None:
    if row.get("projection") is None or row.get("error") or not row.get("market_scope_verified"):
        row["status"]="PASS";row["status_label"]="🚫 PASS";return
    if (row.get("identity_ids") or {}).get("match_id") in {None,""} or not row.get("identity_official_ready"):
        if row.get("status")=="OFFICIAL":row["status"]="TRACK";row["status_label"]="⚠️ TRACK — ID/LINEUP NOT COMPLETE"
    prob=safe_float(row.get("probability"),0) or 0;edge=abs(safe_float(row.get("edge"),0) or 0);data=safe_int(row.get("data_score"),0) or 0;lower=safe_float(row.get("calibration_lower90"),0) or 0;health=row_accuracy_health(row);flags=list(row.get("flags") or [])
    if edge<V44_NEUTRAL_EDGE_ZONE or max(safe_float(row.get("over_probability"),.5) or .5,safe_float(row.get("under_probability"),.5) or .5)<MIN_TRACK_PROB:
        row["status"]="PASS";row["status_label"]="🚫 PASS — NEUTRAL ZONE";flags.append("PROJECTION TOO CLOSE TO LINE")
    elif row.get("direction_flip_after_joint"):
        row["status"]="TRACK";row["status_label"]="⚠️ TRACK — MODEL DIRECTION DISAGREEMENT"
    elif prob>=MIN_OFFICIAL_PROB and edge>=MIN_OFFICIAL_EDGE and data>=MIN_OFFICIAL_DATA_SCORE and lower>=.55 and row.get("calibration_ready") and health.get("ready") and row.get("identity_official_ready"):
        if str(row.get("veto_state"))=="PRE_VETO" and (prob<.65 or edge<2.5):row["status"]="PLAYABLE";row["status_label"]="✅ PLAYABLE — PRE-VETO"
        elif row.get("market_agreement") is False:row["status"]="TRACK";row["status_label"]="⚠️ TRACK — MARKET DISAGREES"
        else:row["status"]="OFFICIAL";row["status_label"]="🔥 OFFICIAL PLAY"
    elif prob>=MIN_PLAYABLE_PROB and edge>=MIN_PLAYABLE_EDGE and data>=MIN_PLAYABLE_DATA_SCORE:row["status"]="PLAYABLE";row["status_label"]="✅ PLAYABLE"
    elif prob>=MIN_TRACK_PROB and edge>=V44_NEUTRAL_EDGE_ZONE:row["status"]="TRACK";row["status_label"]="⚠️ TRACK ONLY"
    else:row["status"]="PASS";row["status_label"]="🚫 PASS"
    row["accuracy_health"]=health;row["flags"]=list(dict.fromkeys(flags));row["feature_fingerprint"]=MODEL_SCHEMA_FINGERPRINT;row["model_version"]=MODEL_VERSION


def direction_balance_circuit(board: List[Dict[str,Any]]) -> Dict[str,Any]:
    valid=[r for r in board if r.get("projection") is not None and r.get("status")!="PASS"]
    counts=Counter(str(r.get("lean") or "") for r in valid);n=len(valid);direction=counts.most_common(1)[0][0] if counts else "";ratio=counts[direction]/n if n else 0;triggered=n>=V44_DIRECTION_ALERT_MIN_ROWS and ratio>=V44_DIRECTION_ALERT_RATIO
    if triggered:
        hist=[r for r in _graded_binary_rows() if str(r.get("lean") or "")==direction];support=len(hist)>=100 and (sum(x["_y"]*x.get("_w",1) for x in hist)/max(sum(x.get("_w",1) for x in hist),1))>=.55
        if not support:
            for r in valid:
                if r.get("lean")==direction and r.get("status")=="OFFICIAL":r["status"]="PLAYABLE";r["status_label"]="✅ PLAYABLE — SLATE DIRECTION CIRCUIT";r["flags"]=list(dict.fromkeys(list(r.get("flags") or [])+[f"{direction} CONCENTRATION {ratio:.0%} WITHOUT VERSIONED HISTORICAL SUPPORT"]))
    return {"valid_rows":n,"over_rows":counts.get("OVER",0),"under_rows":counts.get("UNDER",0),"dominant_direction":direction,"dominant_ratio":round(ratio,3),"triggered":triggered}


_v43_build_full_board_final=build_full_board

def build_full_board(props: List[Dict[str,Any]],deep_enabled: bool=True) -> Tuple[List[Dict[str,Any]],Dict[str,Any]]:
    board,status=_v43_build_full_board_final(props,deep_enabled);joint=_joint_lineup_reconcile(board)
    for row in board:_v44_reclassify(row)
    balance=direction_balance_circuit(board);board.sort(key=lambda x:({"OFFICIAL":0,"PLAYABLE":1,"TRACK":2,"PASS":3}.get(x.get("status"),9),-safe_float(x.get("probability"),0),-safe_float(x.get("abs_edge"),0)))
    return board,{**status,"v44_reliability_layer":True,"joint_lineup_reconciliation":joint,"direction_balance":balance,"model_version":MODEL_VERSION,"feature_fingerprint":MODEL_SCHEMA_FINGERPRINT}


_v43_fetch_actual_maps12_kills=fetch_actual_maps12_kills

def _demo_maps12_confirmation(match_id: str,player: str,map_names: Sequence[str]) -> Dict[str,Any]:
    if not match_id or len(map_names)<2:return {"available":False}
    try:
        with _sqlite_connect() as conn:
            rows=conn.execute("SELECT map_name,COUNT(*) kills FROM demo_events WHERE match_id=? AND player_key=? GROUP BY map_name",(str(match_id),normalize_name(player))).fetchall()
        counts={canonical_map_name(r["map_name"]):int(r["kills"]) for r in rows};vals=[counts.get(canonical_map_name(m)) for m in map_names[:2]]
        if all(v is not None for v in vals):return {"available":True,"kills":sum(vals),"map_kills":vals,"source":"local parsed demo telemetry"}
    except Exception:pass
    return {"available":False}


def fetch_actual_maps12_kills(match_url: str,player: str,player_id: str="") -> Tuple[Optional[int],Dict[str,Any]]:
    actual,meta=_v43_fetch_actual_maps12_kills(match_url,player,player_id)
    if actual is None:return actual,meta
    map_names=[x.get("map") for x in meta.get("map_results",[])[:2]];demo=_demo_maps12_confirmation(meta.get("match_id") or _match_id_from_url(match_url),player,map_names);dual=bool(demo.get("available") and int(demo.get("kills"))==int(actual))
    if demo.get("available") and not dual:return None,{**meta,"ok":False,"message":"HLTV and local demo totals disagree — manual review required","demo_confirmation":demo,"training_eligible":False,"confirmation_weight":0.0}
    exact_ids=all((d.get("meta") or {}).get("exact_id") for d in meta.get("details",[])) if player_id else False;internal_dual=bool(len(meta.get("details",[]))==2 and len(meta.get("map_results",[]))>=2 and exact_ids)
    weight=1.0 if dual else .70 if internal_dual else .50
    return actual,{**meta,"demo_confirmation":demo,"dual_source_confirmed":dual,"provider_internal_dual_structure":internal_dual,"confirmation_sources":["HLTV chronological result","HLTV player mapstats"]+(["local parsed demo"] if dual else []),"training_eligible":bool(dual or internal_dual),"confirmation_weight":weight}



# ============================================================
# V4.5 FINAL DATA / REVIEW / FULL-LINEUP RELIABILITY LAYER
# ============================================================

MODEL_SCHEMA_FINGERPRINT = "v45_full5_demo_backfill_exactids_reviewedgrades_sidechoice_parserhealth_multibook_schema1"
V45_MIN_EXACT_STARTERS = 5
V45_PARSER_SHUTDOWN_SCORE = 74.0
V45_PARSER_WARNING_SCORE = 86.0
V45_BOOK_MAX_AGE_HOURS = 48


def init_v45_schema() -> None:
    init_v44_schema()
    with _sqlite_connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS manual_grade_reviews (
          review_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL, player TEXT NOT NULL,
          line REAL, actual_kills REAL NOT NULL, review_status TEXT NOT NULL,
          reviewer_note TEXT, payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
          reviewed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_manual_grade_review_status ON manual_grade_reviews(review_status,created_at);
        CREATE TABLE IF NOT EXISTS backfill_imports (
          import_id TEXT PRIMARY KEY, source_name TEXT, row_type TEXT,
          rows_seen INTEGER, rows_added INTEGER, payload_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS parser_health_runs (
          run_id TEXT PRIMARY KEY, model_version TEXT, score REAL, grade TEXT,
          official_enabled INTEGER, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS book_odds_v45 (
          odds_id TEXT PRIMARY KEY, player_key TEXT NOT NULL, player TEXT,
          match_id TEXT, line REAL NOT NULL, over_odds REAL, under_odds REAL,
          book TEXT NOT NULL, observed_at TEXT NOT NULL, payload_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_book_odds_v45_lookup ON book_odds_v45(player_key,line,observed_at);
        CREATE TABLE IF NOT EXISTS lineup_model_snapshots (
          lineup_id TEXT PRIMARY KEY, match_id TEXT, team_id TEXT, team TEXT,
          starters_json TEXT NOT NULL, exact_starters INTEGER NOT NULL,
          source TEXT, observed_at TEXT NOT NULL, payload_json TEXT NOT NULL
        );
        """)
        payload={"model_version":MODEL_VERSION,"feature_fingerprint":MODEL_SCHEMA_FINGERPRINT,"market":"CS2 Maps 1-2 Kills","release":"v4.5"}
        conn.execute("""INSERT INTO model_registry(model_version,feature_fingerprint,status,promoted_at,payload_json,created_at,updated_at)
                      VALUES(?,?,?,'',?,?,?)
                      ON CONFLICT(model_version) DO UPDATE SET feature_fingerprint=excluded.feature_fingerprint,
                      payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                     (MODEL_VERSION,MODEL_SCHEMA_FINGERPRINT,"ACTIVE",json.dumps(payload),now_iso(),now_iso()))


init_v45_schema()


def snapshot_key(row: Dict[str, Any]) -> str:
    """Exact identity; model versions and source IDs can never collide."""
    ids=row.get("identity_ids") or {}
    exact=[
        MODEL_VERSION, MODEL_SCHEMA_FINGERPRINT,
        str(row.get("prop_id") or row.get("line_id") or ""),
        str(row.get("appearance_id") or ""), str(row.get("game_id") or ""),
        str(ids.get("match_id") or _match_id_from_url(str(row.get("match_url") or "")) or ""),
        str(ids.get("player_id") or ""), str(ids.get("team_id") or ""), str(ids.get("opponent_id") or ""),
        normalize_name(row.get("player")), str(row.get("line")), str(row.get("lean")),
        str(row.get("start_time") or "")[:19], str(row.get("source") or ""),
    ]
    return hashlib.sha256("|".join(exact).encode("utf-8")).hexdigest()


def _grade_weight(row: Dict[str, Any]) -> float:
    meta=row.get("grade_meta") or {}
    source=str(row.get("grade_source") or "").lower()
    if source.startswith("manual"):
        return 0.90 if meta.get("manual_reviewed") and meta.get("training_eligible") else 0.0
    if meta.get("dual_source_confirmed"): return 1.0
    if meta.get("training_eligible"):
        return clamp(safe_float(meta.get("confirmation_weight"),0.70) or 0.70,V44_MIN_DUAL_GRADE_WEIGHT,1.0)
    return clamp(safe_float(meta.get("confirmation_weight"),0.50) or 0.50,0.25,0.65)


def _manual_review_rows(status: str="PENDING") -> List[Dict[str,Any]]:
    try:
        with _sqlite_connect() as conn:
            rows=conn.execute("SELECT * FROM manual_grade_reviews WHERE review_status=? ORDER BY created_at",(status,)).fetchall()
        out=[]
        for r in rows:
            d=dict(r)
            try:d["payload"]=json.loads(d.pop("payload_json"))
            except Exception:d["payload"]={}
            out.append(d)
        return out
    except Exception:return []


def grade_from_manual_dataframe(df: pd.DataFrame, overwrite: bool=False) -> Dict[str,Any]:
    """Stage manual grades for explicit review; they do not train immediately."""
    if df is None or df.empty:return {"staged":0,"unmatched":0,"message":"No rows"}
    cmap={normalize_name(c):c for c in df.columns};pcol=cmap.get("player") or cmap.get("player name");acol=cmap.get("actual kills") or cmap.get("kills") or cmap.get("actual")
    if not pcol or not acol:return {"staged":0,"unmatched":len(df),"message":"Need Player and Actual Kills columns"}
    picks=load_json(PICK_LOG,[]);picks=picks if isinstance(picks,list) else [];staged=unmatched=0
    with _sqlite_connect() as conn:
        for _,raw in df.iterrows():
            player=str(raw.get(pcol,"")).strip();actual=safe_float(raw.get(acol),None);line_filter=safe_float(raw.get(cmap.get("line","")),None) if cmap.get("line") else None
            if not player or actual is None:continue
            candidates=[]
            for row in picks:
                score=name_similarity(player,row.get("player"))
                if line_filter is not None and abs((safe_float(row.get("line"),0) or 0)-line_filter)>.01:score-=.30
                if score>=.82:candidates.append((score,row))
            if not candidates:unmatched+=1;continue
            score,row=max(candidates,key=lambda x:x[0]);sid=str(row.get("snapshot_id") or snapshot_key(row));rid=hashlib.sha256(f"{sid}|{actual}|{MODEL_VERSION}".encode()).hexdigest()
            payload={"snapshot":row,"actual_kills":float(actual),"match_score":score,"overwrite":bool(overwrite),"submitted_row":{str(k):raw.get(k) for k in df.columns}}
            conn.execute("""INSERT OR REPLACE INTO manual_grade_reviews(review_id,snapshot_id,player,line,actual_kills,review_status,reviewer_note,payload_json,created_at,reviewed_at)
                          VALUES(?,?,?,?,?,'PENDING','',?,?,NULL)""",(rid,sid,str(row.get("player") or player),safe_float(row.get("line"),None),float(actual),json.dumps(payload,default=str),now_iso()))
            staged+=1
    return {"staged":staged,"unmatched":unmatched,"message":"Manual results staged. Review and approve them before training."}


def approve_manual_grade_reviews(review_ids: Sequence[str], reviewer_note: str="") -> Dict[str,int]:
    wanted={str(x) for x in review_ids if x};picks=load_json(PICK_LOG,[]);picks=picks if isinstance(picks,list) else [];results=load_json(RESULT_LOG,[]);results=results if isinstance(results,list) else []
    result_ids={str(x.get("snapshot_id")) for x in results};approved=skipped=0
    with _sqlite_connect() as conn:
        rows=conn.execute("SELECT * FROM manual_grade_reviews WHERE review_status='PENDING'").fetchall()
        for rec in rows:
            if wanted and rec["review_id"] not in wanted:continue
            try:payload=json.loads(rec["payload_json"])
            except Exception:payload={}
            row=dict(payload.get("snapshot") or {});sid=str(rec["snapshot_id"]);actual=float(rec["actual_kills"])
            if sid in result_ids and not payload.get("overwrite"):skipped+=1;continue
            label=grade_result(str(row.get("lean")),float(row.get("line")),actual)
            grade_meta={"manual_reviewed":True,"training_eligible":True,"confirmation_weight":.90,"review_id":rec["review_id"],"reviewer_note":reviewer_note}
            out={**row,"snapshot_id":sid,"actual_kills":actual,"graded_result":label,"graded_at":now_iso(),"grade_source":"manual reviewed","grade_meta":grade_meta}
            results=[x for x in results if str(x.get("snapshot_id"))!=sid];results.append(out);result_ids.add(sid)
            for p in picks:
                if str(p.get("snapshot_id"))==sid:p.update({"actual_kills":actual,"graded_result":label,"graded_at":now_iso(),"grade_source":"manual reviewed","grade_meta":grade_meta})
            sqlite_mark_projection_graded(sid,label,actual,grade_meta)
            conn.execute("UPDATE manual_grade_reviews SET review_status='APPROVED',reviewer_note=?,reviewed_at=? WHERE review_id=?",(reviewer_note,now_iso(),rec["review_id"]));approved+=1
    save_json(PICK_LOG,picks);save_json(RESULT_LOG,results,github_backup=bool(get_secret("GITHUB_AUTO_BACKUP","").lower() in {"1","true","yes"}))
    if approved:build_learning_profiles();save_calibration_state()
    return {"approved":approved,"skipped":skipped}


def reject_manual_grade_reviews(review_ids: Sequence[str], reviewer_note: str="") -> int:
    ids=[str(x) for x in review_ids if x]
    if not ids:return 0
    with _sqlite_connect() as conn:
        for rid in ids:conn.execute("UPDATE manual_grade_reviews SET review_status='REJECTED',reviewer_note=?,reviewed_at=? WHERE review_id=?",(reviewer_note,now_iso(),rid))
    return len(ids)


def save_multibook_odds_dataframe(df: pd.DataFrame) -> Dict[str,int]:
    if df is None or df.empty:return {"added":0,"skipped":0}
    cmap={normalize_name(c):c for c in df.columns};pcol=cmap.get("player") or cmap.get("player name");lcol=cmap.get("line");bcol=cmap.get("book") or cmap.get("sportsbook");ocol=cmap.get("over odds") or cmap.get("over");ucol=cmap.get("under odds") or cmap.get("under");mcol=cmap.get("match id")
    if not pcol or not lcol or not bcol:return {"added":0,"skipped":len(df)}
    added=skipped=0
    with _sqlite_connect() as conn:
        for _,r in df.iterrows():
            player=str(r.get(pcol,"")).strip();line=safe_float(r.get(lcol),None);book=str(r.get(bcol,"")).strip();oo=safe_float(r.get(ocol),None) if ocol else None;uo=safe_float(r.get(ucol),None) if ucol else None
            if not player or line is None or not book or (oo is None and uo is None):skipped+=1;continue
            observed=now_iso();mid=str(r.get(mcol,"")).strip() if mcol else "";raw=f"{normalize_name(player)}|{line}|{book}|{oo}|{uo}|{observed[:16]}|{mid}";oid=hashlib.sha256(raw.encode()).hexdigest();payload={str(c):r.get(c) for c in df.columns}
            cur=conn.execute("INSERT OR IGNORE INTO book_odds_v45 VALUES(?,?,?,?,?,?,?,?,?,?)",(oid,normalize_name(player),player,mid,float(line),oo,uo,book,observed,json.dumps(payload,default=str)));added+=int(cur.rowcount or 0)
    return {"added":added,"skipped":skipped}


def multibook_consensus(player: str,line: float,match_id: str="") -> Dict[str,Any]:
    cutoff=(datetime.now(timezone.utc)-timedelta(hours=V45_BOOK_MAX_AGE_HOURS)).isoformat();rows=[]
    try:
        with _sqlite_connect() as conn:
            rows=conn.execute("SELECT * FROM book_odds_v45 WHERE player_key=? AND ABS(line-?)<0.011 AND observed_at>=? ORDER BY observed_at DESC",(normalize_name(player),float(line),cutoff)).fetchall()
    except Exception:rows=[]
    latest={}
    for r in rows:
        if match_id and r["match_id"] and str(r["match_id"])!=str(match_id):continue
        latest.setdefault(str(r["book"]),dict(r))
    ovs=[];uns=[];details=[]
    for book,r in latest.items():
        ov,un=no_vig_probs(safe_float(r.get("over_odds"),None),safe_float(r.get("under_odds"),None))
        if ov is not None and un is not None:ovs.append(ov);uns.append(un);details.append({"book":book,"over_probability":ov,"under_probability":un,"over_odds":r.get("over_odds"),"under_odds":r.get("under_odds")})
    return {"books":len(details),"over_probability":float(np.median(ovs)) if ovs else None,"under_probability":float(np.median(uns)) if uns else None,"details":details,"method":"median no-vig across books" if details else "unavailable"}


def record_side_choice_observations(row: Dict[str,Any]) -> int:
    hints=row.get("confirmed_starting_side_hints") or {};ids=row.get("identity_ids") or {};team=str(row.get("team") or "");opp=str(row.get("opponent") or "");match_id=str(ids.get("match_id") or _match_id_from_url(str(row.get("match_url") or "")) or "")
    added=0
    if not team or not match_id:return 0
    with _sqlite_connect() as conn:
        for map_name,teams in hints.items():
            val=(teams or {}).get(normalize_team(team))
            if val is None:continue
            side="CT" if float(val)>=.5 else "T";raw=f"{match_id}|{normalize_team(team)}|{canonical_map_name(map_name)}|{side}";oid=hashlib.sha256(raw.encode()).hexdigest();payload={"row_prop_id":row.get("prop_id"),"hints":teams}
            cur=conn.execute("INSERT OR IGNORE INTO side_choice_observations VALUES(?,?,?,?,?,?,?,?,?,?)",(oid,normalize_team(team),normalize_team(opp),match_id,canonical_map_name(map_name),side,now_iso(),"confirmed match page",json.dumps(payload,default=str),now_iso()));added+=int(cur.rowcount or 0)
    return added


_v44_empirical_start_ct_probability = empirical_start_ct_probability

def empirical_start_ct_probability(team: str,map_name: str,days: int=365) -> Dict[str,Any]:
    cutoff=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat();ct=total=0
    try:
        with _sqlite_connect() as conn:
            rows=conn.execute("SELECT started_side FROM side_choice_observations WHERE team_key=? AND map_name=? AND observed_at>=?",(normalize_team(team),canonical_map_name(map_name),cutoff)).fetchall()
        for r in rows:total+=1;ct+=1 if str(r["started_side"]).upper()=="CT" else 0
    except Exception:pass
    demo=_v44_empirical_start_ct_probability(team,map_name,days) if '_v44_empirical_start_ct_probability' in globals() else {"probability":.5,"sample":0}
    d_n=safe_int(demo.get("sample"),0) or 0;d_p=safe_float(demo.get("probability"),.5) or .5
    combined=total+d_n;p=((ct+d_p*d_n)+4)/(combined+8) if combined else .5
    return {"probability":p,"sample":combined,"confirmed_side_samples":total,"demo_samples":d_n,"source":"confirmed side choice + demo history" if combined else "50/50 unknown side prior"}


def _starter_profile_kpr(name: str,maps: Sequence[str],known_rows: Dict[str,Dict[str,Any]],fallback: float) -> Dict[str,Any]:
    key=normalize_name(name)
    if key in known_rows:
        r=known_rows[key];return {"player":name,"kpr":safe_float(r.get("adjusted_kpr"),fallback) or fallback,"source":"listed prop projection","verified":bool(r.get("core_kpr_verified")),"role":r.get("role")}
    db=lookup_database_player(name);vals=[];rounds=0
    for m in maps[:2]:
        mp=(db.get("player_map_profiles") or {}).get(m) or {};v=safe_float(mp.get("blended_kpr"),None) or safe_float(mp.get("kpr"),None)
        if v is not None:vals.append(v);rounds+=safe_int(mp.get("rounds"),0) or 0
        demo=demo_profile_for(name,m)
        if demo.get("denominator_verified") and safe_float(demo.get("kpr"),None) is not None:vals.append(float(demo["kpr"]));rounds+=safe_int(demo.get("rounds"),0) or 0
    if not vals:
        v=safe_float(db.get("base_kpr"),None) or safe_float(db.get("kpr"),None)
        if v is not None:vals=[v]
    return {"player":name,"kpr":clamp(float(np.mean(vals)) if vals else fallback,.45,.98),"source":"database/demo starter profile" if vals else "team-median starter prior","verified":bool(vals and (safe_int(db.get("profile_maps"),0) or rounds)>=10),"role":db.get("role") or "Unknown"}


def _select_team_lineup(row: Dict[str,Any]) -> List[str]:
    groups=row.get("confirmed_lineup_groups") or [];team=str(row.get("team") or "");tid=str((row.get("identity_ids") or {}).get("team_id") or "")
    best=[];score=0.0
    for g in groups:
        s=1.0 if tid and str(g.get("team_id") or "")==tid else name_similarity(team,g.get("team"))
        if s>score and len(g.get("players") or [])>=3:score=s;best=list(g.get("players") or [])
    if len(best)<5:
        roster=list(row.get("current_roster_names") or [])
        if len(roster)>=5:best=roster
    return list(dict.fromkeys(str(x).strip() for x in best if str(x).strip()))[:5]


def _full_lineup_joint_reconcile(board: List[Dict[str,Any]]) -> Dict[str,Any]:
    groups=defaultdict(list)
    for row in board:
        if row.get("projection") is None or row.get("status")=="PASS":continue
        ids=row.get("identity_ids") or {};key=(str(ids.get("match_id") or row.get("match_url") or ""),str(ids.get("team_id") or normalize_team(row.get("team"))))
        if all(key):groups[key].append(row)
    updated=exact_groups=partial_groups=0;comparison=model_comparison_state()
    for key,rows in groups.items():
        lineup=_select_team_lineup(rows[0]);known={normalize_name(r.get("player")):r for r in rows};maps=list(rows[0].get("likely_maps") or [])
        if len(lineup)<5:
            partial_groups+=1
            for r in rows:r["flags"]=list(dict.fromkeys(list(r.get("flags") or [])+["FULL FIVE-PLAYER LINEUP NOT AVAILABLE"]));r["full_lineup_model"]={"exact_starters":False,"starters":lineup}
            continue
        exact_groups+=1;fallback=float(np.median([safe_float(r.get("adjusted_kpr"),LEAGUE_KPR) or LEAGUE_KPR for r in rows]))
        starter_profiles=[_starter_profile_kpr(x,maps,known,fallback) for x in lineup];kprs=np.array([x["kpr"] for x in starter_profiles],dtype=float);means=np.clip(kprs/kprs.sum(),.06,.35);means=means/means.sum()
        team_means=[]
        for r in rows:
            tm=r.get("team_kill_share_model") or {};tkpr=safe_float(tm.get("team_kpr"),None);er=safe_float(r.get("expected_rounds"),None)
            if tkpr and er:team_means.append(tkpr*er)
        team_mean=float(np.median(team_means)) if team_means else max(sum(safe_float(r.get("projection"),0) or 0 for r in rows)/max(sum(means[[lineup.index(next(x for x in lineup if normalize_name(x)==normalize_name(r.get('player')))) for r in rows if any(normalize_name(x)==normalize_name(r.get('player')) for x in lineup)]]) if rows else .5,.25),20)
        n=SIMULATIONS;rng=np.random.default_rng(stable_seed("v45-full-lineup",key,MODEL_VERSION));team_sd=max(4.5,math.sqrt(team_mean)*1.25);team_totals=_count_samples(team_mean,team_sd,n,rng)
        verified=sum(1 for x in starter_profiles if x["verified"]);conc=80 if verified>=4 else 52;alpha=np.maximum(means*conc,.45);g=rng.gamma(alpha,1,(n,5));probs=g/g.sum(axis=1,keepdims=True);alloc=np.zeros((n,5),dtype=int);remaining=team_totals.copy();remain_prob=np.ones(n)
        for j in range(4):
            cond=np.clip(probs[:,j]/np.maximum(remain_prob,1e-9),0,1);draw=rng.binomial(remaining,cond);alloc[:,j]=draw;remaining-=draw;remain_prob-=probs[:,j]
        alloc[:,4]=remaining
        weight=clamp((.62 if verified>=4 else .45)+safe_float(comparison.get("joint_weight_bonus"),0),.25,.80)
        for r in rows:
            idx=next((i for i,x in enumerate(lineup) if normalize_name(x)==normalize_name(r.get("player"))),None)
            if idx is None:continue
            js=alloc[:,idx];ind=_count_samples(safe_float(r.get("projection"),0) or 0,safe_float(r.get("sim_sd"),5) or 5,n,rng);mix=(1-weight)*ind+weight*js;frac=mix-np.floor(mix);samples=np.floor(mix).astype(int)+(rng.random(n)<frac)
            correction,cmeta=_neutral_projection_correction(r)
            if correction>0:samples+=rng.binomial(1,correction,n)
            elif correction<0:samples=np.maximum(samples-rng.binomial(1,abs(correction),n),0)
            line=float(r.get("line"));projection=float(np.mean(samples));edge=projection-line;lean="OVER" if edge>0 else "UNDER";rawp=float(np.mean(samples>line)) if lean=="OVER" else float(np.mean(samples<line));cal=calibrate_probability(rawp,{**r,"lean":lean,"model_components":{**(r.get("model_components") or {}),"full_lineup_projection":float(np.mean(js))}})
            r.update({"projection":round(projection,2),"edge":round(edge,2),"abs_edge":round(abs(edge),2),"lean":lean,"raw_probability":round(rawp,4),"probability":round(cal["calibrated"],4),"over_probability":round(float(np.mean(samples>line)),4),"under_probability":round(float(np.mean(samples<line)),4),"median":round(float(np.median(samples)),1),"p10":round(float(np.percentile(samples,10)),1),"p90":round(float(np.percentile(samples,90)),1),"sim_sd":round(float(np.std(samples)),2),"calibration_sample":cal["sample"],"calibration_ready":cal["ready"],"calibration_tier":cal["tier"],"full_lineup_model":{"exact_starters":True,"starters":starter_profiles,"verified_starters":verified,"team_kills_mean":round(team_mean,3),"joint_weight":round(weight,3),"player_share_mean":round(float(np.mean(js/np.maximum(team_totals,1))),4),"all_shares_sum_to_one":True,"champion_challenger":comparison},"neutral_projection_correction":cmeta})
            comp=dict(r.get("model_components") or {});comp.update({"full_lineup_projection":float(np.mean(js)),"full_lineup_weight":weight,"full_lineup_verified_starters":verified});r["model_components"]=comp;updated+=1
        ids=rows[0].get("identity_ids") or {};lid=hashlib.sha256(f"{key}|{lineup}|{MODEL_VERSION}".encode()).hexdigest()
        try:
            with _sqlite_connect() as conn:conn.execute("INSERT OR REPLACE INTO lineup_model_snapshots VALUES(?,?,?,?,?,?,?,?,?)",(lid,str(ids.get("match_id") or ""),str(ids.get("team_id") or ""),str(rows[0].get("team") or ""),json.dumps(starter_profiles,default=str),1,"confirmed lineup / database / demo",now_iso(),json.dumps({"rows":len(rows),"maps":maps},default=str)))
        except Exception:pass
    return {"groups":len(groups),"exact_groups":exact_groups,"partial_groups":partial_groups,"rows_updated":updated,"comparison":comparison}


def parser_consistency_report(board: Sequence[Dict[str,Any]],source_status: Optional[Dict[str,Any]]=None) -> Dict[str,Any]:
    source_status=source_status or {};checks=[];valid=[r for r in board if r.get("projection") is not None]
    def add(name,passed,total,critical=False,note=""):
        ratio=passed/max(total,1);checks.append({"name":name,"passed":passed,"total":total,"ratio":ratio,"critical":critical,"note":note})
    add("valid Maps 1-2 line",sum(MIN_M12_KILL_LINE<=float(r.get("line"))<=MAX_M12_KILL_LINE for r in valid),len(valid),True)
    add("probability sums",sum(abs((safe_float(r.get("over_probability"),0) or 0)+(safe_float(r.get("under_probability"),0) or 0)+(safe_float(r.get("push_probability"),0) or 0)-1)<.035 for r in valid),len(valid),True)
    add("realistic KPR",sum(.42<=float(r.get("adjusted_kpr"))<=1.02 for r in valid if r.get("adjusted_kpr") is not None),len(valid),True)
    add("realistic rounds",sum(26<=float(r.get("expected_rounds"))<=65 for r in valid if r.get("expected_rounds") is not None),len(valid),True)
    add("exact match/player IDs",sum(bool((r.get("identity_ids") or {}).get("match_id") and (r.get("identity_ids") or {}).get("player_id")) for r in valid),len(valid),True)
    add("five-player lineup available",sum(len(_select_team_lineup(r))==5 for r in valid),len(valid),False)
    add("player belongs to lineup",sum(any(normalize_name(x)==normalize_name(r.get("player")) for x in _select_team_lineup(r)) for r in valid),len(valid),True)
    add("active maps only",sum(all(canonical_map_name(m) in CURRENT_ACTIVE_MAPS for m in (r.get("likely_maps") or [])) for r in valid),len(valid),True)
    score=100.0
    for c in checks:score-=((1-c["ratio"])*(22 if c["critical"] else 8))
    score=clamp(score,0,100);critical_fail=any(c["critical"] and c["ratio"]<.80 for c in checks);official=bool(score>=V45_PARSER_WARNING_SCORE and not critical_fail);grade="HEALTHY" if official else "WARNING" if score>=V45_PARSER_SHUTDOWN_SCORE else "DEGRADED"
    out={"score":round(score,1),"grade":grade,"official_enabled":official,"critical_failure":critical_fail,"checks":checks,"valid_rows":len(valid),"model_version":MODEL_VERSION,"created_at":now_iso()}
    rid=hashlib.sha256(f"{MODEL_VERSION}|{out['created_at'][:16]}|{score}|{len(valid)}".encode()).hexdigest()
    try:
        with _sqlite_connect() as conn:conn.execute("INSERT OR REPLACE INTO parser_health_runs VALUES(?,?,?,?,?,?,?)",(rid,MODEL_VERSION,float(score),grade,1 if official else 0,json.dumps(out,default=str),out["created_at"]))
    except Exception:pass
    return out


def ingest_historical_backfill(df: pd.DataFrame,source_name: str="manual backfill") -> Dict[str,Any]:
    if df is None or df.empty:return {"rows_seen":0,"rows_added":0,"types":{}}
    cmap={normalize_name(c):c for c in df.columns};types=Counter();added=0
    def col(*names):
        for n in names:
            if normalize_name(n) in cmap:return cmap[normalize_name(n)]
        return None
    team_c=col("team","team name");opp_c=col("opponent","opponent team");map_c=col("map","map name");tw_c=col("team score","rounds won","team rounds");ol_c=col("opponent score","rounds lost","opponent rounds");action_c=col("action","veto action");side_c=col("started side","starting side");player_c=col("player","player name");event_c=col("event type","roster event");match_c=col("match id","match_id");date_c=col("date","played at","event time","observed at")
    for idx,r in df.iterrows():
        team=str(r.get(team_c,"")).strip() if team_c else "";opp=str(r.get(opp_c,"")).strip() if opp_c else "";map_name=canonical_map_name(r.get(map_c)) if map_c else "";match_id=str(r.get(match_c,"") or f"backfill-{idx}");when=str(r.get(date_c,"") or now_iso())
        if team and map_name and tw_c and ol_c and safe_int(r.get(tw_c),None) is not None and safe_int(r.get(ol_c),None) is not None:
            obs={"map":map_name,"rounds_won":safe_int(r.get(tw_c),0),"rounds_lost":safe_int(r.get(ol_c),0),"rounds":safe_int(r.get(tw_c),0)+safe_int(r.get(ol_c),0),"played_at":when,"team1":team,"team2":opp}
            if sqlite_store_team_map_observation(normalize_team(team),team,f"backfill://{match_id}",obs,safe_float(r.get(col('opponent rank') or ''),None),False,str(r.get(col('environment') or '') or 'UNKNOWN')):added+=1;types["team_map"]+=1
        if team and map_name and action_c and str(r.get(action_c,"")).lower() in {"picked","removed","banned","left"}:
            action=str(r.get(action_c)).lower().replace("banned","removed");sqlite_store_veto_observation(normalize_team(team),team,opp,f"backfill://{match_id}",when,{"action":action,"map":map_name,"team":team},idx,False);added+=1;types["veto"]+=1
        if team and map_name and side_c and str(r.get(side_c,"")).upper() in {"CT","T"}:
            side=str(r.get(side_c)).upper();oid=hashlib.sha256(f"backfill|{team}|{map_name}|{match_id}|{side}".encode()).hexdigest()
            with _sqlite_connect() as conn:conn.execute("INSERT OR IGNORE INTO side_choice_observations VALUES(?,?,?,?,?,?,?,?,?,?)",(oid,normalize_team(team),normalize_team(opp),match_id,map_name,side,when,source_name,json.dumps({"row":idx}),now_iso()));added+=1;types["side_choice"]+=1
        if player_c and event_c and str(r.get(player_c,"")).strip():
            player=str(r.get(player_c)).strip();etype=str(r.get(event_c)).strip();sqlite_record_roster_event(player,team,etype,when,safe_float(r.get(col('confidence') or ''),.8) or .8,{"opponent":opp,"source":source_name});added+=1;types["roster"]+=1
    meta={"rows_seen":len(df),"rows_added":added,"types":dict(types),"source":source_name};iid=hashlib.sha256(f"{source_name}|{now_iso()}|{len(df)}|{added}".encode()).hexdigest()
    with _sqlite_connect() as conn:conn.execute("INSERT OR REPLACE INTO backfill_imports VALUES(?,?,?,?,?,?,?)",(iid,source_name,"mixed",len(df),added,json.dumps(meta,default=str),now_iso()))
    chronological_map_elo(True);chronological_roster_glicko(True);return meta


def direction_bias_report() -> Dict[str,Any]:
    rows=[r for r in sqlite_graded_projection_rows() if _row_model_compatible(r) and safe_float(r.get("actual_kills"),None) is not None and safe_float(r.get("projection"),None) is not None]
    def summarize(items):
        if not items:return {"n":0,"wins":0,"losses":0,"win_rate":None,"mae":None,"bias":None,"brier":None}
        err=np.array([float(x["actual_kills"])-float(x["projection"]) for x in items]);wins=sum(str(x.get("graded_result"))=="WIN" for x in items);losses=sum(str(x.get("graded_result"))=="LOSS" for x in items);b=[]
        for x in items:
            p=safe_float(x.get("probability"),None);y=1 if x.get("graded_result")=="WIN" else 0 if x.get("graded_result")=="LOSS" else None
            if p is not None and y is not None:b.append((p-y)**2)
        return {"n":len(items),"wins":wins,"losses":losses,"win_rate":wins/max(wins+losses,1),"mae":float(np.mean(np.abs(err))),"bias":float(np.mean(err)),"brier":float(np.mean(b)) if b else None}
    groups={"overall":summarize(rows),"direction":{},"status":{},"veto_state":{},"event_tier":{},"role":{},"maps":{}}
    for field in ["lean","status","veto_state","event_tier","role"]:
        target="direction" if field=="lean" else field
        for k in sorted({str(x.get(field) or "UNKNOWN") for x in rows}):groups[target][k]=summarize([x for x in rows if str(x.get(field) or "UNKNOWN")==k])
    for m in KNOWN_MAPS:
        items=[x for x in rows if m in (x.get("likely_maps") or [])]
        if items:groups["maps"][m]=summarize(items)
    over=groups["direction"].get("OVER",{});under=groups["direction"].get("UNDER",{});groups["neutrality"]={"over_bias":over.get("bias"),"under_bias":under.get("bias"),"bias_gap":abs((over.get("bias") or 0)-(under.get("bias") or 0)),"balanced":bool(over.get("n",0)>=50 and under.get("n",0)>=50 and abs((over.get("bias") or 0)-(under.get("bias") or 0))<=.65)}
    return groups


def _apply_multibook_to_board(board: List[Dict[str,Any]]) -> Dict[str,Any]:
    applied=0;disagreed=0
    for r in board:
        if r.get("projection") is None:continue
        mid=str((r.get("identity_ids") or {}).get("match_id") or "");cons=multibook_consensus(str(r.get("player") or ""),float(r.get("line")),mid);r["multi_book_market"]=cons
        p=cons.get("over_probability") if r.get("lean")=="OVER" else cons.get("under_probability")
        if p is None:continue
        applied+=1;edge=(safe_float(r.get("probability"),0) or 0)-float(p);r["market_probability"]=round(float(p),4);r["market_edge"]=round(edge,4);r["market_source_count"]=max(safe_int(r.get("market_source_count"),0) or 0,safe_int(cons.get("books"),0) or 0);r["market_method"]="v4.5 multi-book median no-vig";r["market_agreement"]=edge>=MIN_MARKET_VALUE_EDGE;r["market_positive_value"]=edge>=MIN_MARKET_VALUE_EDGE
        if edge<0:
            disagreed+=1;r["flags"]=list(dict.fromkeys(list(r.get("flags") or [])+["MULTI-BOOK MARKET PRICES MODEL BELOW FAIR VALUE"]))
            if r.get("status") in {"OFFICIAL","PLAYABLE"}:r["status"]="TRACK";r["status_label"]="⚠️ TRACK — MARKET VALUE DISAGREEMENT"
    return {"rows_applied":applied,"negative_value_rows":disagreed}


_v44_build_full_board_for_v45 = build_full_board

def build_full_board(props: List[Dict[str,Any]],deep_enabled: bool=True) -> Tuple[List[Dict[str,Any]],Dict[str,Any]]:
    board,status=_v44_build_full_board_for_v45(props,deep_enabled)
    # Persist confirmed side choice before the full-lineup reconciliation.
    side_added=sum(record_side_choice_observations(r) for r in board)
    full=_full_lineup_joint_reconcile(board);market=_apply_multibook_to_board(board)
    for r in board:
        r["model_version"]=MODEL_VERSION;r["feature_fingerprint"]=MODEL_SCHEMA_FINGERPRINT;_v44_reclassify(r)
    direction=direction_balance_circuit(board);parser=parser_consistency_report(board,status)
    if not parser.get("official_enabled"):
        for r in board:
            if r.get("status")=="OFFICIAL":r["status"]="TRACK";r["status_label"]="⚠️ TRACK — PARSER HEALTH CIRCUIT";r["flags"]=list(dict.fromkeys(list(r.get("flags") or [])+["PARSER CONSISTENCY HEALTH BELOW OFFICIAL THRESHOLD"]))
    order={"OFFICIAL":0,"PLAYABLE":1,"TRACK":2,"PASS":3};board.sort(key=lambda x:(order.get(x.get("status"),9),-safe_float(x.get("probability"),0),-safe_float(x.get("abs_edge"),0)))
    return board,{**status,"v45_full_lineup":full,"v45_multibook":market,"v45_side_choices_added":side_added,"v45_parser_health":parser,"direction_balance":direction,"model_version":MODEL_VERSION,"feature_fingerprint":MODEL_SCHEMA_FINGERPRINT}


def run_v45_collector_maintenance(board: Sequence[Dict[str,Any]],status: Optional[Dict[str,Any]]=None) -> Dict[str,Any]:
    side=sum(record_side_choice_observations(r) for r in board);parser=parser_consistency_report(board,status or {}) if board else {"score":0,"grade":"NO BOARD","official_enabled":False};return {"side_choices_added":side,"parser_health":parser,"full_lineup_exact":sum(bool((r.get("full_lineup_model") or {}).get("exact_starters")) for r in board)}



# ============================================================
# V4.6 SOURCE RECOVERY / PLAYER MATCHING / DIAGNOSTICS PATCH
# ============================================================

MODEL_SCHEMA_FINGERPRINT = "v46_source_recovery_verified_local_demo_alias_hltvsearch_schema1"
SOURCE_RECOVERY_VERSION = "4.6"


def format_likely_maps(value: Any) -> str:
    """Never split a string into characters when exporting/displaying maps."""
    if value is None:
        return "Unconfirmed"
    if isinstance(value, str):
        text=value.strip()
        return text if text else "Unconfirmed"
    if isinstance(value, (list, tuple, set)):
        items=[str(x).strip() for x in value if str(x).strip()]
        return " / ".join(items) if items else "Unconfirmed"
    return str(value)


def _blocked_public_page(text: Optional[str]) -> Tuple[bool,str]:
    blob=str(text or "").lower()
    tests=[
        ("cloudflare", ["cf-chl-", "cloudflare ray id", "attention required", "just a moment"]),
        ("access denied", ["access denied", "request blocked", "forbidden"]),
        ("captcha", ["captcha", "verify you are human", "checking your browser"]),
        ("empty shell", ["enable javascript and cookies to continue"]),
    ]
    for reason,needles in tests:
        if any(x in blob for x in needles): return True,reason
    return False,""


_v46_http_get_text_base=http_get_text

def http_get_text(url: str, source: str, ttl: int=900, params: Optional[Dict[str,Any]]=None,
                  headers: Optional[Dict[str,str]]=None, timeout: int=18,
                  allow_stale: bool=True, stale_ttl: Optional[int]=None) -> Tuple[Optional[str],Dict[str,Any]]:
    text,status=_v46_http_get_text_base(url,source,ttl,params,headers,timeout,allow_stale,stale_ttl)
    blocked,reason=_blocked_public_page(text)
    if blocked:
        status={**status,"ok":False,"blocked":True,"block_reason":reason,"warning":f"{source} returned {reason} protection page","content_length":len(text or "")}
        return None,status
    if text is not None:
        status={**status,"content_length":len(text),"content_type":"html/text"}
    return text,status


def _alias_record(player: str) -> Dict[str,Any]:
    raw=load_json(PLAYER_ALIAS_FILE,{})
    if not isinstance(raw,dict): return {}
    val=raw.get(normalize_name(player))
    if isinstance(val,dict): return dict(val)
    if isinstance(val,str) and val.strip(): return {"alias":val.strip()}
    return {}


def _deep_first(obj: Any, keys: Sequence[str]) -> Any:
    wanted={normalize_name(x).replace(" ","") for x in keys}
    if isinstance(obj,dict):
        for k,v in obj.items():
            nk=normalize_name(k).replace(" ","")
            if nk in wanted and v not in [None,"",[],{}]:
                if isinstance(v,dict):
                    for sk in ["name","display_name","title","value","id"]:
                        if v.get(sk) not in [None,""]: return v.get(sk)
                elif not isinstance(v,(list,dict)): return v
        for v in obj.values():
            found=_deep_first(v,keys)
            if found not in [None,""]: return found
    elif isinstance(obj,list):
        for v in obj:
            found=_deep_first(v,keys)
            if found not in [None,""]: return found
    return None


def _teams_from_matchup(text: Any) -> Tuple[str,str]:
    raw=html_lib.unescape(str(text or "")).strip()
    raw=re.sub(r"\s+"," ",raw)
    pats=[r"^(.+?)\s+(?:vs\.?|versus|@)\s+(.+?)(?:\s*[-|].*)?$",r"^(.+?)\s+v\s+(.+?)(?:\s*[-|].*)?$"]
    for pat in pats:
        m=re.search(pat,raw,re.I)
        if m:
            a,b=m.group(1).strip(),m.group(2).strip()
            if a and b and len(a)<80 and len(b)<80:return a,b
    return "",""


_v46_ud_top_base=_parse_underdog_top_level

def _parse_underdog_top_level(data: Any) -> List[Dict[str,Any]]:
    rows=_v46_ud_top_base(data)
    if not isinstance(data,dict): return rows
    appearances=_record_map(data.get("appearances") or [])
    players=_record_map(data.get("players") or [])
    games=_record_map(data.get("games") or data.get("matches") or data.get("events") or [])
    teams=_record_map(data.get("teams") or [])
    for row in rows:
        appearance=appearances.get(str(row.get("appearance_id") or ""),{})
        player_obj={}
        pid=_deep_first(appearance,["player_id","playerId"])
        if pid is not None: player_obj=players.get(str(pid),{})
        game=games.get(str(row.get("game_id") or ""),{})
        if not row.get("team"):
            tid=_deep_first(appearance,["team_id","teamId","current_team_id"])
            team_obj=teams.get(str(tid),{}) if tid is not None else {}
            row["team"]=str(_deep_first(team_obj,["name","display_name"]) or _deep_first(player_obj,["team_name","team","organization"]) or _deep_first(appearance,["team_name","team"]) or "").strip()
        if not row.get("matchup"):
            row["matchup"]=str(_deep_first(game,["title","display_title","name","matchup"]) or "").strip()
        a,b=_teams_from_matchup(row.get("matchup") or row.get("evidence"))
        if not row.get("team") and a: row["team"]=a
        if not row.get("opponent"):
            if row.get("team") and a and b:
                row["opponent"]=b if _team_name_matches(row["team"],a) else a if _team_name_matches(row["team"],b) else ""
            elif a and b: row["opponent"]=b
        row["underdog_identity_enriched"]=bool(row.get("team") or row.get("opponent") or row.get("matchup"))
    return rows


def _direct_hltv_player_search(player: str) -> Tuple[Dict[str,Any],Dict[str,Any]]:
    alias=_alias_record(player)
    if alias.get("hltv_player_id") or alias.get("player_id"):
        pid=str(alias.get("hltv_player_id") or alias.get("player_id"));slug=str(alias.get("hltv_slug") or alias.get("slug") or normalize_name(alias.get("alias") or player).replace(" ","-"))
        return {"player":alias.get("hltv_name") or alias.get("alias") or player,"player_id":pid,"slug":slug,"team":alias.get("team") or "","href":f"/stats/players/{pid}/{slug}","maps":safe_int(alias.get("maps"),0) or 0,"rounds":safe_int(alias.get("rounds"),0) or 0,"kd":safe_float(alias.get("kd"),1.0) or 1.0,"rating":safe_float(alias.get("rating"),1.0) or 1.0,"_source_fresh":True,"_source_cache":"manual alias"},{"ok":True,"method":"saved exact player ID"}
    url=f"{HLTV_BASE}/search"
    page,status=http_get_text(url,"HLTV player search",ttl=60*60,params={"query":player},timeout=20,allow_stale=True,stale_ttl=SOURCE_MAX_STALE_SECONDS["player_form"])
    if not page:return {},status
    candidates=[]
    for m in re.finditer(r'href=["\']/(?:stats/players|player)/(\d+)/([^"\'/?#]+)[^"\']*["\'][^>]*>(.*?)</a>',page,re.I|re.S):
        pid,slug,label=m.group(1),m.group(2),strip_tags(m.group(3)).split("\n")[0]
        score=max(name_similarity(player,label),name_similarity(player,slug.replace("-"," ")))
        candidates.append((score,{"player":label or slug.replace("-"," "),"player_id":pid,"slug":slug,"team":"","href":f"/stats/players/{pid}/{slug}","maps":0,"rounds":0,"kd":1.0,"rating":1.0,"_source_fresh":source_freshness_ok(status,SOURCE_MAX_STALE_SECONDS["player_form"]),"_source_cache":status.get("cache")}))
    if not candidates:return {},{**status,"ok":False,"warning":"No player result in HLTV search","search_player":player}
    score,row=max(candidates,key=lambda x:x[0])
    if score<.82:return {},{**status,"ok":False,"warning":"HLTV search result similarity too low","best_score":score}
    return row,{**status,"ok":True,"method":"direct HLTV search","match_score":round(score,3)}


def _demo_aggregate_profile(player: str) -> Dict[str,Any]:
    key=normalize_name(player)
    try:
        with _sqlite_connect() as conn:
            rows=conn.execute("SELECT * FROM demo_events WHERE player_key=?",(key,)).fetchall()
    except Exception: rows=[]
    if not rows:return {}
    denoms={}
    for r in rows:
        mr=safe_int(r["match_rounds"],0) or 0
        if mr>0: denoms[(str(r["match_id"] or ""),str(r["map_name"] or ""))]=max(denoms.get((str(r["match_id"] or ""),str(r["map_name"] or "")),0),mr)
    rounds=sum(denoms.values());kills=len(rows)
    if rounds<=0:return {"player":rows[0]["player"],"kills":kills,"rounds":0,"maps":len(denoms),"denominator_verified":False}
    teams=Counter(str(r["team"] or "") for r in rows if str(r["team"] or "").strip())
    opponents=Counter(str(r["opponent"] or "") for r in rows if str(r["opponent"] or "").strip())
    ct=sum(1 for r in rows if str(r["side"] or "").upper()=="CT");tt=sum(1 for r in rows if str(r["side"] or "").upper() in {"T","TERRORIST"})
    hs=sum(int(r["is_headshot"] or 0) for r in rows);openings=sum(int(r["is_opening"] or 0) for r in rows)
    return {"player":rows[0]["player"],"team":teams.most_common(1)[0][0] if teams else "","opponent":opponents.most_common(1)[0][0] if opponents else "","kills":kills,"rounds":rounds,"maps":len(denoms),"kpr":kills/rounds,"hs_pct":hs/kills*100 if kills else None,"opening_kpr":openings/rounds,"ct_kill_share":ct/kills if kills else None,"t_kill_share":tt/kills if kills else None,"denominator_verified":True,"source":"verified demo telemetry"}


def _playerstats_from_local(player: str) -> Tuple[Optional[PlayerStats],Dict[str,Any]]:
    alias=_alias_record(player);db=lookup_database_player(player);demo=_demo_aggregate_profile(player)
    override=load_json(PLAYER_OVERRIDES_FILE,{})
    ov=override.get(normalize_name(player),{}) if isinstance(override,dict) else {}
    source="";rec={}
    if db and safe_int(db.get("profile_maps"),0)>0 and safe_float(db.get("base_kpr"),None) is not None:
        rec=db;source="persistent verified player database"
    elif demo.get("denominator_verified") and safe_int(demo.get("maps"),0)>0:
        rec=demo;source="verified demo telemetry"
    elif ov and safe_int(ov.get("maps"),0)>0 and safe_float(ov.get("kpr"),None) is not None:
        rec=ov;source="manual verified player profile"
    if not rec:return None,{"matched":False,"sources_checked":["persistent database","demo telemetry","manual profile"]}
    maps=safe_int(rec.get("profile_maps"),safe_int(rec.get("maps"),0)) or 0
    rounds=safe_int(rec.get("profile_rounds"),safe_int(rec.get("rounds"),0)) or 0
    kpr=safe_float(rec.get("base_kpr"),safe_float(rec.get("kpr"),None))
    if maps<=0 or kpr is None:return None,{"matched":False,"warning":"local record lacked real maps/KPR"}
    pid=str(alias.get("hltv_player_id") or alias.get("player_id") or (rec.get("identity_ids") or {}).get("player_id") or "")
    slug=str(alias.get("hltv_slug") or alias.get("slug") or normalize_name(player).replace(" ","-"))
    p=PlayerStats(player=player,player_id=pid,slug=slug,team=str(alias.get("team") or rec.get("team") or demo.get("team") or ""),maps=maps,rounds=rounds,kills=safe_int(rec.get("kills"),0) or 0,deaths=safe_int(rec.get("deaths"),0) or 0,kpr=float(kpr),dpr=safe_float(rec.get("dpr"),.68) or .68,adr=safe_float(rec.get("adr"),LEAGUE_ADR) or LEAGUE_ADR,rating=safe_float(rec.get("rating"),1.0) or 1.0,kd=safe_float(rec.get("kd"),1.0) or 1.0,hs_pct=safe_float(rec.get("hs_pct"),safe_float(demo.get("hs_pct"),None)),opening_kpr=safe_float(rec.get("opening_kpr"),safe_float(demo.get("opening_kpr"),None)),ct_kpr=safe_float(rec.get("ct_kpr"),None),t_kpr=safe_float(rec.get("t_kpr"),None),source=source,href=str(rec.get("profile_href") or alias.get("profile_href") or ""),data_warnings=["LIVE HLTV PROFILE UNAVAILABLE — VERIFIED LOCAL SAMPLE USED"],kpr_source="verified_local_sample")
    return p,{"matched":True,"match_score":1.0,"source_fresh":True,"core_kpr_verified":True,"kpr_source":"verified_local_sample","local_fallback":True,"source":source,"demo":demo}


_v46_build_player_profile_base=build_player_profile

def build_player_profile(player_name: str,long_table: Dict[str,Dict[str,Any]],medium_table: Dict[str,Dict[str,Any]],recent30_table: Dict[str,Dict[str,Any]],recent15_table: Dict[str,Dict[str,Any]]) -> Tuple[PlayerStats,Dict[str,Any]]:
    profile,meta=_v46_build_player_profile_base(player_name,long_table,medium_table,recent30_table,recent15_table)
    if meta.get("matched") and safe_int(profile.maps,0)>0:
        return profile,{**meta,"recovery_path":"HLTV table"}
    direct,dstatus=_direct_hltv_player_search(player_name)
    if direct:
        key=normalize_name(direct.get("player") or player_name)
        merged=dict(long_table);merged[key]=direct
        profile2,meta2=_v46_build_player_profile_base(player_name,merged,medium_table,recent30_table,recent15_table)
        if meta2.get("matched") and safe_int(profile2.maps,0)>0:
            return profile2,{**meta2,"direct_search_status":dstatus,"recovery_path":"direct HLTV search"}
    local,lmeta=_playerstats_from_local(player_name)
    if local is not None:
        return local,{**lmeta,"direct_search_status":dstatus,"recovery_path":"verified local database/demo"}
    return profile,{**meta,"direct_search_status":dstatus,"recovery_path":"failed","source_failure":"No HLTV table/search result and no verified local/demo profile"}


def _enrich_prop_identity_v46(prop: Dict[str,Any]) -> Dict[str,Any]:
    out=dict(prop);alias=_alias_record(str(out.get("player") or ""))
    for field,keys in {"team":["team"],"opponent":["opponent"],"match_url":["match_url","hltv_match_url"],"matchup":["matchup"]}.items():
        if not out.get(field):
            for k in keys:
                if alias.get(k):out[field]=alias[k];break
    a,b=_teams_from_matchup(out.get("matchup") or out.get("evidence"))
    if not out.get("team") and a:out["team"]=a
    if not out.get("opponent") and a and b:
        if out.get("team") and _team_name_matches(out["team"],a):out["opponent"]=b
        elif out.get("team") and _team_name_matches(out["team"],b):out["opponent"]=a
        else:out["opponent"]=b
    return out


_v46_build_full_board_base=build_full_board

def build_full_board(props: List[Dict[str,Any]],deep_enabled: bool=True) -> Tuple[List[Dict[str,Any]],Dict[str,Any]]:
    enriched=[_enrich_prop_identity_v46(p) for p in props]
    board,status=_v46_build_full_board_base(enriched,deep_enabled)
    projections=sum(r.get("projection") is not None for r in board);matched=sum((safe_int(r.get("profile_maps"),0) or 0)>0 for r in board)
    teams=sum(bool(r.get("team")) for r in board);matches=sum(bool(r.get("match_url")) for r in board)
    source_failure=bool(board and projections==0)
    diagnosis={"props":len(board),"projections":projections,"player_profiles":matched,"teams":teams,"matches":matches,"source_failure":source_failure,"message":"Underdog lines loaded, but no verified CS2 player profiles/matches were available. Check Source Status, add a mapping, or upload demos." if source_failure else "Projection pipeline produced verified rows."}
    if source_failure:
        for r in board:
            r["flags"]=list(dict.fromkeys(list(r.get("flags") or [])+["CS2 STATISTICS SOURCE UNAVAILABLE — NO PROJECTION GENERATED"]))
    return board,{**status,"v46_source_recovery":diagnosis,"model_version":MODEL_VERSION,"feature_fingerprint":MODEL_SCHEMA_FINGERPRINT}


_v45_parser_consistency_report_base=parser_consistency_report

def parser_consistency_report(board: Sequence[Dict[str,Any]],status: Optional[Dict[str,Any]]=None) -> Dict[str,Any]:
    base=_v45_parser_consistency_report_base(board,status) if '_v45_parser_consistency_report_base' in globals() else {"score":0,"grade":"UNKNOWN","official_enabled":False,"checks":[]}
    # The active v4.5 parser report may be captured under a different name. Fall back to simple checks.
    if not isinstance(base,dict) or not base.get("checks"):
        n=len(board);proj=sum(r.get("projection") is not None for r in board);profiles=sum((safe_int(r.get("profile_maps"),0) or 0)>0 for r in board)
        ratio=proj/max(n,1);score=100*ratio
        base={"score":score,"grade":"HEALTHY" if score>=86 else "DEGRADED","official_enabled":score>=V45_PARSER_SHUTDOWN_SCORE,"checks":[]}
    n=len(board);proj=sum(r.get("projection") is not None for r in board);profile_count=sum((safe_int(r.get("profile_maps"),0) or 0)>0 for r in board);profile_ratio=profile_count/max(n,1)
    base["checks"]=list(base.get("checks") or [])+[
        {"name":"verified player profile coverage","passed":profile_count,"total":n,"ratio":profile_ratio,"critical":True,"note":"BO3, verified local database, or full-round demo profile"},
        {"name":"projection coverage","passed":proj,"total":n,"ratio":proj/max(n,1),"critical":True,"note":"rows that reached the projection engine"}
    ]
    if n>=10 and (profile_ratio<.20 or proj/max(n,1)<.10):
        base["score"]=min(float(base.get("score",0)),20.0);base["grade"]="SOURCE FAILURE";base["official_enabled"]=False;base["shutdown_reason"]="Player/profile source coverage collapsed"
    return base


# ============================================================
# V4.7 RAILWAY SOURCE REPLACEMENT — BO3 PRIMARY + HLTV CIRCUIT
# ============================================================

MODEL_SCHEMA_FINGERPRINT = "v47_bo3_primary_hltv_circuit_database_first_pandascore_fixture_schema1"
SOURCE_RECOVERY_VERSION = "4.7"
BO3_API_BASE = "https://api.bo3.gg/api/v1"
BO3_WEB_BASE = "https://bo3.gg"
BO3_PROFILE_MAX_AGE = 24 * 3600
BO3_MATCH_MAX_AGE = 5 * 60
SOURCE_CIRCUIT_FILE = os.path.join(STORAGE_DIR, "source_circuits.json")

_BO3_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://bo3.gg",
    "Referer": "https://bo3.gg/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}


def _source_circuits() -> Dict[str, Any]:
    raw = load_json(SOURCE_CIRCUIT_FILE, {})
    return raw if isinstance(raw, dict) else {}


def _source_circuit_state(provider: str) -> Dict[str, Any]:
    return dict(_source_circuits().get(provider, {}) or {})


def _source_circuit_open(provider: str) -> bool:
    row = _source_circuit_state(provider)
    until = _parse_iso_datetime(row.get("open_until"))
    return bool(until and until > datetime.now(timezone.utc))


def _trip_source_circuit(provider: str, reason: str, seconds: int) -> None:
    raw = _source_circuits()
    now = datetime.now(timezone.utc)
    old = dict(raw.get(provider, {}) or {})
    failures = int(old.get("failures", 0) or 0) + 1
    raw[provider] = {
        "provider": provider,
        "state": "OPEN",
        "reason": str(reason)[:400],
        "failures": failures,
        "opened_at": now.isoformat(),
        "open_until": (now + timedelta(seconds=max(60, seconds))).isoformat(),
    }
    save_json(SOURCE_CIRCUIT_FILE, raw, force=True)


def _close_source_circuit(provider: str) -> None:
    raw = _source_circuits()
    row = dict(raw.get(provider, {}) or {})
    row.update({"provider": provider, "state": "CLOSED", "reason": "", "failures": 0,
                "closed_at": now_iso(), "open_until": ""})
    raw[provider] = row
    save_json(SOURCE_CIRCUIT_FILE, raw, force=True)


_v47_http_get_text_base = http_get_text

def http_get_text(url: str, source: str, ttl: int = 900, params: Optional[Dict[str, Any]] = None,
                  headers: Optional[Dict[str, str]] = None, timeout: int = 18,
                  allow_stale: bool = True, stale_ttl: Optional[int] = None) -> Tuple[Optional[str], Dict[str, Any]]:
    provider = "hltv" if "hltv.org" in str(url).lower() else "bo3" if "bo3.gg" in str(url).lower() else ""
    if provider and _source_circuit_open(provider):
        state = _source_circuit_state(provider)
        return None, {"ok": False, "source": source, "provider": provider, "circuit_open": True,
                      "warning": f"{provider.upper()} circuit open: {state.get('reason','recent provider failure')}",
                      "open_until": state.get("open_until"), "url": url, "status": 0}
    text, status = _v47_http_get_text_base(url, source, ttl, params, headers, timeout, allow_stale, stale_ttl)
    warning = str(status.get("warning") or "").lower()
    blocked = bool(status.get("blocked")) or any(x in warning for x in ["http 403", "forbidden", "cloudflare", "captcha", "access denied"])
    rate_limited = "http 429" in warning or "too many requests" in warning
    if provider == "hltv" and blocked:
        _trip_source_circuit("hltv", warning or "HTTP 403 / anti-bot block", 30 * 60)
        return None, {**status, "ok": False, "provider": "hltv", "circuit_open": True,
                      "warning": "HLTV blocked this Railway IP. Requests paused for 30 minutes."}
    if provider == "bo3" and (blocked or rate_limited):
        _trip_source_circuit("bo3", warning or "BO3 provider blocked/rate limited", 5 * 60)
    elif provider and status.get("ok"):
        _close_source_circuit(provider)
    return text, status


def _bo3_get_json(endpoint: str, source: str, params: Optional[Dict[str, Any]] = None,
                  ttl: int = 3600, allow_stale: bool = True) -> Tuple[Optional[Any], Dict[str, Any]]:
    return http_get_json(f"{BO3_API_BASE}{endpoint}", source, ttl=ttl, params=params,
                         headers=_BO3_HEADERS, timeout=22, allow_stale=allow_stale,
                         stale_ttl=BO3_PROFILE_MAX_AGE if allow_stale else 0)


def _bo3_payload_data(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        return [data]
    for key in ["items", "results", "players", "matches", "teams"]:
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    return []


def _bo3_included_index(payload: Any) -> Dict[Tuple[str, str], Dict[str, Any]]:
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    if not isinstance(payload, dict):
        return out
    for item in payload.get("included") or []:
        if not isinstance(item, dict):
            continue
        out[(object_type(item), object_id(item))] = item
        out[("", object_id(item))] = item
    return out


def _bo3_resolve_related(item: Dict[str, Any], rel_names: Sequence[str], payload: Any) -> List[Dict[str, Any]]:
    included = _bo3_included_index(payload)
    rels = item.get("relationships") if isinstance(item, dict) else {}
    if not isinstance(rels, dict):
        return []
    output: List[Dict[str, Any]] = []
    wanted = {normalize_name(x).replace(" ", "") for x in rel_names}
    for key, node in rels.items():
        if normalize_name(key).replace(" ", "") not in wanted:
            continue
        data = node.get("data") if isinstance(node, dict) else node
        entries = data if isinstance(data, list) else [data]
        for ref in entries:
            if not isinstance(ref, dict):
                continue
            found = included.get((object_type(ref), object_id(ref))) or included.get(("", object_id(ref)))
            if found:
                output.append(found)
            else:
                output.append(ref)
    return output


def _bo3_scalar(obj: Any, keys: Sequence[str], default: Any = None) -> Any:
    wanted = {normalize_name(k).replace(" ", "") for k in keys}
    if isinstance(obj, dict):
        a = attrs(obj)
        for k, v in a.items():
            nk = normalize_name(k).replace(" ", "")
            if nk in wanted and v not in [None, "", [], {}]:
                if isinstance(v, dict):
                    for sub in ["value", "name", "title", "slug", "id"]:
                        if v.get(sub) not in [None, ""]:
                            return v.get(sub)
                elif not isinstance(v, (list, dict)):
                    return v
        label = normalize_name(a.get("name") or a.get("title") or a.get("label") or "").replace(" ", "")
        if label in wanted:
            for sub in ["value", "count", "avg", "average", "per_round", "percentage"]:
                if a.get(sub) not in [None, ""]:
                    return a.get(sub)
        for v in obj.values():
            found = _bo3_scalar(v, keys, None)
            if found not in [None, ""]:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _bo3_scalar(v, keys, None)
            if found not in [None, ""]:
                return found
    return default


def _bo3_player_candidate(payload: Any, player: str) -> Dict[str, Any]:
    candidates = _bo3_payload_data(payload)
    scored = []
    for item in candidates:
        a = attrs(item)
        name = str(_bo3_scalar(a, ["nickname", "nick_name", "player_name", "display_name", "name", "slug"], "") or "")
        slug = str(_bo3_scalar(a, ["slug"], "") or "")
        score = max(name_similarity(player, name), name_similarity(player, slug.replace("-", " ")))
        scored.append((score, item))
    if not scored:
        return {}
    score, item = max(scored, key=lambda x: x[0])
    return item if score >= 0.78 else {}


def _bo3_slug_candidates(player: str, alias: Optional[Dict[str, Any]] = None) -> List[str]:
    alias = alias or {}
    raw = [alias.get("bo3_slug"), alias.get("slug"), normalize_name(player).replace(" ", "-"),
           re.sub(r"[^a-z0-9]", "", normalize_name(player))]
    return list(dict.fromkeys(str(x).strip().lower() for x in raw if str(x or "").strip()))


def _bo3_parse_player_html(page: str, player: str, slug: str, url: str) -> Tuple[Optional[PlayerStats], Dict[str, Any]]:
    if not page:
        return None, {"ok": False, "warning": "empty BO3 player page"}
    text = strip_tags(page)
    low = text.lower()
    if "general stats" not in low and "overall statistics" not in low and "maps last" not in low:
        return None, {"ok": False, "warning": "BO3 page did not contain player statistics"}
    maps = rounds = 0
    general = re.search(r"General stats last.*?Maps\s+(\d+).*?Rounds\s+(\d+)", text, re.I | re.S)
    if general:
        maps, rounds = int(general.group(1)), int(general.group(2))
    overall = re.search(r"Overall(?:\s+[A-Za-z0-9_.-]+)?\s+statistics(.*?)(?:Player records|Maps last|Transfers History|General stats)", text, re.I | re.S)
    section = overall.group(1) if overall else text[:12000]
    def metric(label: str, lo: float, hi: float) -> Optional[float]:
        m = re.search(rf"\b{label}\b\s+([0-9]+(?:\.[0-9]+)?)", section, re.I)
        v = safe_float(m.group(1), None) if m else None
        return v if v is not None and lo <= v <= hi else None
    score = metric("Score", 0, 20)
    kpr = metric("Kills", 0.25, 1.25)
    dpr = metric("Death", 0.25, 1.25)
    opening = metric("Open kills", 0.01, 0.40)
    adr = metric("Damage", 25, 140)
    if kpr is None:
        return None, {"ok": False, "warning": "BO3 page lacked realistic reported KPR"}
    map_profiles: Dict[str, Dict[str, Any]] = {}
    for map_name in KNOWN_MAPS:
        variants = [map_name, "Dust II" if map_name == "Dust2" else map_name]
        found = None
        for variant in variants:
            m = re.search(rf"\b{re.escape(variant)}\b\s+([0-9]+(?:\.[0-9]+)?)\s+(\d+)\s+([0-9]+(?:\.[0-9]+)?)\s+([0-9]+(?:\.[0-9]+)?)", text, re.I)
            if m:
                found = m; break
        if found:
            mkpr = safe_float(found.group(3), None)
            if mkpr is not None and 0.25 <= mkpr <= 1.25:
                map_profiles[map_name] = {"maps": int(found.group(2)), "long_kpr": mkpr,
                                          "recent_kpr": mkpr, "blended_kpr": mkpr,
                                          "adr": safe_float(found.group(4), None),
                                          "source": "BO3 public player page"}
    if maps <= 0:
        maps = sum(int(v.get("maps", 0) or 0) for v in map_profiles.values())
    if rounds <= 0 and maps > 0:
        rounds = int(round(maps * 21.2))
    title = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
    title_text = strip_tags(title.group(1)) if title else ""
    team = ""
    tm = re.search(r"CS2 Stats\s*[–—-]\s*(.+)$", title_text, re.I)
    if tm:
        team = tm.group(1).strip()
    headshot = None
    hm = re.search(r"\bHead\b\s+\d+\s+(\d+(?:\.\d+)?)%", text, re.I)
    if hm:
        headshot = safe_float(hm.group(1), None)
    rating = clamp(1.0 + ((score or 6.27) - 6.27) * 0.11, 0.70, 1.35)
    kd = kpr / max(dpr or LEAGUE_DPR, 0.25)
    profile = PlayerStats(player=player, player_id=f"bo3:{slug}", slug=slug, team=team,
                          maps=max(maps, 1), rounds=max(rounds, max(maps, 1) * 13),
                          kpr=float(kpr), dpr=float(dpr or LEAGUE_DPR), adr=float(adr or LEAGUE_ADR),
                          rating=float(rating), kd=float(kd), hs_pct=headshot, opening_kpr=opening,
                          source="BO3 public professional statistics", href=url,
                          data_warnings=["HLTV unavailable — BO3 provider used"],
                          kpr_source="bo3_reported_kpr")
    meta = {"matched": True, "source": "BO3 public professional statistics", "source_fresh": True,
            "source_ages": {"bo3_player": 0}, "core_kpr_verified": True,
            "kpr_source": "bo3_reported_kpr", "match_score": 1.0,
            "player_map_profiles": map_profiles, "bo3_slug": slug,
            "provider": "BO3", "profile_url": url}
    return profile, meta


def _bo3_profile_from_api(player: str, alias: Optional[Dict[str, Any]] = None) -> Tuple[Optional[PlayerStats], Dict[str, Any]]:
    alias = alias or {}
    search, sstatus = _bo3_get_json("/filters/players", "BO3 player search", {
        "page[offset]": "0", "page[limit]": "6", "filter[discipline_id][eq]": "1",
        "with": "country", "search_text": player,
    }, ttl=12 * 3600)
    candidate = _bo3_player_candidate(search, player)
    if not candidate:
        return None, {**sstatus, "ok": False, "warning": "BO3 player search returned no confident match"}
    a = attrs(candidate)
    slug = str(_bo3_scalar(a, ["slug"], "") or "")
    pid = str(object_id(candidate) or _bo3_scalar(a, ["id", "player_id"], "") or "")
    name = str(_bo3_scalar(a, ["nickname", "nick_name", "display_name", "name"], player) or player)
    if not slug:
        slug = normalize_name(name).replace(" ", "-")
    general, gstatus = _bo3_get_json(f"/players/{quote(slug)}/general_stats", "BO3 player general stats", {
        "filter[start_date_to]": local_now().date().isoformat(),
        "filter[start_date_from]": (local_now().date() - timedelta(days=180)).isoformat(),
    }, ttl=6 * 3600)
    maps_payload, mstatus = _bo3_get_json(f"/players/{quote(slug)}/map_stats", "BO3 player map stats", {
        "filter[begin_at_to]": local_now().date().isoformat(),
        "filter[begin_at_from]": (local_now().date() - timedelta(days=180)).isoformat(),
    }, ttl=6 * 3600)
    kpr = safe_float(_bo3_scalar(general, ["kills_per_round", "kpr", "kills"], None), None)
    dpr = safe_float(_bo3_scalar(general, ["deaths_per_round", "dpr", "death", "deaths"], None), None)
    adr = safe_float(_bo3_scalar(general, ["damage_per_round", "adr", "damage"], None), None)
    maps = safe_int(_bo3_scalar(general, ["maps", "maps_count", "map_count"], None), 0) or 0
    rounds = safe_int(_bo3_scalar(general, ["rounds", "rounds_count", "round_count"], None), 0) or 0
    map_profiles: Dict[str, Dict[str, Any]] = {}
    for node in flatten_json(maps_payload):
        map_name = canonical_map_name(_bo3_scalar(node, ["map", "map_name", "name", "title"], ""))
        if not map_name:
            continue
        mkpr = safe_float(_bo3_scalar(node, ["kills_per_round", "kpr", "kills"], None), None)
        count = safe_int(_bo3_scalar(node, ["maps", "count", "maps_count"], None), 0) or 0
        madr = safe_float(_bo3_scalar(node, ["damage_per_round", "adr", "damage"], None), None)
        if mkpr is not None and .25 <= mkpr <= 1.25 and count > 0:
            old = map_profiles.get(map_name)
            if not old or count > int(old.get("maps", 0)):
                map_profiles[map_name] = {"maps": count, "long_kpr": mkpr, "recent_kpr": mkpr,
                                          "blended_kpr": mkpr, "adr": madr,
                                          "source": "BO3 API map stats"}
    if maps <= 0:
        maps = sum(int(v.get("maps", 0) or 0) for v in map_profiles.values())
    if rounds <= 0 and maps > 0:
        rounds = int(round(maps * 21.2))
    if kpr is None or not (.25 <= kpr <= 1.25) or maps <= 0:
        return None, {"ok": False, "provider": "BO3", "search": sstatus, "general": gstatus,
                      "maps": mstatus, "warning": "BO3 API did not return a usable KPR/map sample"}
    team = str(_bo3_scalar(a, ["team_name", "current_team", "team"], "") or "")
    if not team and details:
        team = _v48_team_name_from_payload(details)
    score = safe_float(_bo3_scalar(general, ["score", "rating"], None), None)
    rating = clamp(1.0 + ((score or 6.27) - 6.27) * .11, .70, 1.35)
    profile = PlayerStats(player=player, player_id=f"bo3:{pid or slug}", slug=slug, team=team,
                          maps=maps, rounds=max(rounds, maps * 13), kpr=float(kpr),
                          dpr=float(dpr or LEAGUE_DPR), adr=float(adr or LEAGUE_ADR), rating=rating,
                          kd=float(kpr / max(dpr or LEAGUE_DPR, .25)),
                          source="BO3 professional statistics API", href=f"{BO3_WEB_BASE}/players/{slug}",
                          data_warnings=["HLTV unavailable — BO3 API used"], kpr_source="bo3_reported_kpr")
    return profile, {"matched": True, "source": "BO3 professional statistics API", "provider": "BO3",
                     "source_fresh": True, "source_ages": {"bo3_player": 0},
                     "core_kpr_verified": True, "kpr_source": "bo3_reported_kpr",
                     "match_score": max(name_similarity(player, name), name_similarity(player, slug)),
                     "player_map_profiles": map_profiles, "bo3_slug": slug,
                     "search_status": sstatus, "general_status": gstatus, "map_status": mstatus}


def _bo3_player_profile(player: str) -> Tuple[Optional[PlayerStats], Dict[str, Any]]:
    alias = _alias_record(player)
    # Direct public page is one request and contains overall + map KPR.
    for slug in _bo3_slug_candidates(player, alias):
        url = f"{BO3_WEB_BASE}/players/{quote(slug)}"
        page, status = http_get_text(url, "BO3 player page", ttl=6 * 3600, headers=_BO3_HEADERS,
                                     timeout=22, allow_stale=True, stale_ttl=BO3_PROFILE_MAX_AGE)
        profile, meta = _bo3_parse_player_html(page or "", player, slug, url)
        if profile is not None:
            return profile, {**meta, "http_status": status}
        if status.get("circuit_open"):
            break
    return _bo3_profile_from_api(player, alias)


def _persist_provider_profile(profile: PlayerStats, meta: Dict[str, Any]) -> None:
    key = normalize_name(profile.player)
    maps = meta.get("player_map_profiles") or {}
    record = {"player": profile.player, "team": profile.team, "profile_maps": profile.maps,
              "profile_rounds": profile.rounds, "base_kpr": profile.kpr, "dpr": profile.dpr,
              "adr": profile.adr, "rating": profile.rating, "kd": profile.kd,
              "hs_pct": profile.hs_pct, "opening_kpr": profile.opening_kpr,
              "player_map_profiles": maps, "profile_source": profile.source,
              "profile_href": profile.href, "kpr_source": profile.kpr_source,
              "identity_ids": {"player_id": profile.player_id}, "updated_at": now_iso()}
    upsert_database_record(PLAYER_DATABASE_FILE, key, record)
    sqlite_store_entity_snapshot("player", str(profile.player_id or key), record, profile.source, 0, now_iso())


_v47_build_player_profile_base = build_player_profile

def build_player_profile(player_name: str, long_table: Dict[str, Dict[str, Any]], medium_table: Dict[str, Dict[str, Any]],
                         recent30_table: Dict[str, Dict[str, Any]], recent15_table: Dict[str, Dict[str, Any]]) -> Tuple[PlayerStats, Dict[str, Any]]:
    # Database/demo first prevents a blocked provider from creating hundreds of repeated requests.
    local, lmeta = _playerstats_from_local(player_name)
    if local is not None:
        updated = _parse_iso_datetime((lookup_database_player(player_name) or {}).get("updated_at"))
        age = (datetime.now(timezone.utc) - updated).total_seconds() if updated else 999999
        if age <= BO3_PROFILE_MAX_AGE or "demo" in str(lmeta.get("source", "")).lower():
            return local, {**lmeta, "recovery_path": "verified local cache/demo", "source_fresh": age <= BO3_PROFILE_MAX_AGE,
                           "source_ages": {"local_profile": age}}
    profile, meta = _bo3_player_profile(player_name)
    if profile is not None and int(profile.maps or 0) > 0:
        _persist_provider_profile(profile, meta)
        return profile, {**meta, "recovery_path": "BO3 primary provider"}
    # HLTV is now optional and called only if its circuit is closed.
    if not _source_circuit_open("hltv"):
        profile2, meta2 = _v47_build_player_profile_base(player_name, long_table, medium_table, recent30_table, recent15_table)
        if meta2.get("matched") and int(profile2.maps or 0) > 0:
            return profile2, {**meta2, "recovery_path": "optional HLTV"}
    local, lmeta = _playerstats_from_local(player_name)
    if local is not None:
        return local, {**lmeta, "recovery_path": "verified local stale fallback", "source_fresh": False}
    empty = PlayerStats(player=player_name, maps=0, rounds=0, kpr=LEAGUE_KPR, source="")
    return empty, {"matched": False, "source_fresh": False, "core_kpr_verified": False,
                   "recovery_path": "failed", "bo3_status": meta,
                   "source_failure": "BO3 and verified local/demo profiles unavailable; HLTV optional source blocked/unavailable"}


def _bo3_materialize_matches(payload: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in _bo3_payload_data(payload):
        a = attrs(item)
        mid = str(object_id(item) or _bo3_scalar(a, ["id", "match_id"], "") or "")
        slug = str(_bo3_scalar(a, ["slug"], "") or "")
        status = str(_bo3_scalar(a, ["status"], "") or "")
        start = _bo3_scalar(a, ["start_date", "begin_at", "scheduled_at", "start_time"], "")
        bo = safe_int(_bo3_scalar(a, ["best_of", "number_of_games", "games_count", "format"], None), None)
        teams_raw = _bo3_resolve_related(item, ["teams", "opponents"], payload)
        teams: List[Dict[str, Any]] = []
        for t in teams_raw:
            ta = attrs(t)
            name = str(_bo3_scalar(ta, ["name", "title", "short_name"], "") or "")
            tslug = str(_bo3_scalar(ta, ["slug"], "") or "")
            tid = str(object_id(t) or _bo3_scalar(ta, ["id", "team_id"], "") or "")
            if name:
                teams.append({"name": name, "slug": tslug or normalize_name(name).replace(" ", "-"),
                              "team_id": f"bo3:{tid or tslug or normalize_team(name)}", "provider_id": tid})
        if len(teams) < 2:
            # Fallback for non-JSONAPI payloads.
            seen = set()
            for node in flatten_json(item):
                na = attrs(node)
                typ = object_type(node)
                name = str(_bo3_scalar(na, ["team_name", "name"], "") or "")
                if name and ("team" in typ or na.get("team_id") or na.get("logo_url")) and normalize_team(name) not in seen:
                    seen.add(normalize_team(name)); tid = str(object_id(node) or na.get("team_id") or "")
                    teams.append({"name": name, "slug": str(na.get("slug") or normalize_name(name).replace(" ", "-")),
                                  "team_id": f"bo3:{tid or normalize_team(name)}", "provider_id": tid})
                if len(teams) >= 2: break
        tournament = ""
        rel_t = _bo3_resolve_related(item, ["tournament", "tournament_deep", "league"], payload)
        if rel_t:
            tournament = str(_bo3_scalar(attrs(rel_t[0]), ["name", "title"], "") or "")
        games = _bo3_resolve_related(item, ["games", "match_maps"], payload)
        maps = []
        for g in games:
            mn = canonical_map_name(_bo3_scalar(g, ["map_name", "map", "name"], ""))
            if mn and mn not in maps: maps.append(mn)
        players = _bo3_resolve_related(item, ["players", "lineups", "rosters"], payload)
        lineup_names = []
        for p in players:
            pn = str(_bo3_scalar(p, ["nickname", "nick_name", "display_name", "name"], "") or "")
            if pn and normalize_name(pn) not in {normalize_name(x) for x in lineup_names}: lineup_names.append(pn)
        rows.append({"match_id": f"bo3:{mid or slug}", "provider_match_id": mid, "slug": slug,
                     "match_url": f"bo3://{mid or slug}/{slug}", "status": status, "start_time": start,
                     "format": f"BO{bo}" if bo else "BO3", "teams": teams[:2], "event": tournament,
                     "confirmed_maps": maps[:3], "lineup_names": lineup_names})
    return [r for r in rows if len(r.get("teams") or []) >= 2]


@st.cache_data(ttl=3 * 60, show_spinner=False)
def fetch_bo3_upcoming_matches() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    today = datetime.now(timezone.utc).date()
    params = {"scope": "widget-matches", "page[offset]": "0", "page[limit]": "150",
              "sort": "tier_rank,-start_date", "filter[matches.status][in]": "upcoming,current",
              "filter[matches.start_date][lt]": f"{(today + timedelta(days=4)).isoformat()} 23:59",
              "filter[matches.start_date][gt]": f"{(today - timedelta(days=1)).isoformat()} 00:00",
              "filter[matches.discipline_id][eq]": "1",
              "with": "teams,tournament,ai_predictions,games,streams"}
    payload, status = _bo3_get_json("/matches", "BO3 upcoming matches", params, ttl=3 * 60, allow_stale=False)
    rows = _bo3_materialize_matches(payload)
    return rows, {**status, "provider": "BO3", "rows": len(rows)}


def _bo3_match_score(row: Dict[str, Any], team: str, opponent: str, player: str = "") -> float:
    names = [str(x.get("name") or "") for x in row.get("teams") or []]
    score = 0.0
    if team and names: score += max(name_similarity(team, n) for n in names) * .58
    if opponent and names: score += max(name_similarity(opponent, n) for n in names) * .38
    if not team and not opponent and player and player in (row.get("lineup_names") or []): score += .90
    start = _parse_iso_datetime(row.get("start_time"))
    if start:
        hours = abs((start - datetime.now(timezone.utc)).total_seconds()) / 3600
        score += max(0, .08 - min(hours, 96) / 1200)
    return score


def discover_bo3_match(team: str, opponent: str, player: str = "") -> Tuple[str, Dict[str, Any]]:
    rows, status = fetch_bo3_upcoming_matches()
    if not rows:
        return "", {**status, "ok": False, "warning": "BO3 returned no upcoming/current matches"}
    scored = sorted([(_bo3_match_score(r, team, opponent, player), r) for r in rows], key=lambda x: x[0], reverse=True)
    if not scored or scored[0][0] < (.70 if team and opponent else .50):
        return "", {**status, "ok": False, "warning": "No confident BO3 match identity", "best_score": scored[0][0] if scored else 0}
    score, row = scored[0]
    return str(row.get("match_url") or ""), {**status, "ok": True, "method": "BO3 fixtures/match identity",
                                             "match_score": round(score, 3), "match": row}


_v47_discover_match_base = discover_hltv_match

def discover_hltv_match(team: str, opponent: str, player: str = "") -> Tuple[str, Dict[str, Any]]:
    url, meta = discover_bo3_match(team, opponent, player)
    if url:
        return url, meta
    # Free PandaScore fixture fallback when configured.
    panda_rows, pstatus = fetch_pandascore_upcoming()
    best = (0.0, None)
    for raw in panda_rows:
        opponents = raw.get("opponents") or []
        names = [str(((x.get("opponent") or {}).get("name")) or "") for x in opponents]
        s = (max([name_similarity(team, n) for n in names] or [0]) * .58 +
             max([name_similarity(opponent, n) for n in names] or [0]) * .38)
        if s > best[0]: best = (s, raw)
    if best[1] is not None and best[0] >= .70:
        raw = best[1]; mid = str(raw.get("id") or ""); slug = str(raw.get("slug") or mid)
        return f"pandascore://{mid}/{slug}", {**pstatus, "ok": True, "method": "PandaScore free fixtures", "match": raw}
    if not _source_circuit_open("hltv"):
        return _v47_discover_match_base(team, opponent, player)
    return "", {"ok": False, "method": "all match providers failed", "bo3": meta, "pandascore": pstatus,
                "hltv_circuit": _source_circuit_state("hltv")}


def _bo3_team_roster_from_html(page: str) -> List[str]:
    if not page: return []
    segment = page
    marker = re.search(r"(?:Squad|Roster)", page, re.I)
    if marker:
        segment = page[marker.start():]
    transfer = re.search(r"Transfers History", segment, re.I)
    if transfer:
        segment = segment[:transfer.start()]
    players = []
    for href, anchor in re.findall(r'href=["\'](?:https?://bo3\.gg)?/players/([^"\'/?#]+)[^"\']*["\'][^>]*>(.*?)</a>', segment, re.I | re.S):
        name = strip_tags(anchor).split("\n")[0].strip() or href.replace("-", " ")
        if name and normalize_name(name) not in {normalize_name(x) for x in players}:
            players.append(name)
    return players[:7]


def _bo3_team_profile_from_html(team_id: str, slug: str, team_name: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    url = f"{BO3_WEB_BASE}/teams/{quote(slug or normalize_name(team_name).replace(' ', '-'))}"
    page, status = http_get_text(url, "BO3 team page", ttl=6 * 3600, headers=_BO3_HEADERS,
                                 timeout=22, allow_stale=True, stale_ttl=SOURCE_MAX_STALE_SECONDS["team_maps"])
    if not page:
        return {}, {**status, "ok": False}
    text = strip_tags(page)
    roster = _bo3_team_roster_from_html(page)
    map_profiles: Dict[str, Dict[str, Any]] = {}
    for map_name in KNOWN_MAPS:
        variant = "Dust II" if map_name == "Dust2" else map_name
        # BO3 team table: map, winrate, count, recent, picks, bans, CT%, T%.
        m = re.search(rf"\b{re.escape(variant)}\b\s+(\d+(?:\.\d+)?)%\s+(\d+)\s+.*?\s+(\d+)\s+(\d+)\s+(\d+(?:\.\d+)?)%\s+(\d+(?:\.\d+)?)%", text, re.I)
        if m:
            map_profiles[map_name] = {"win_pct": float(m.group(1)), "maps": int(m.group(2)),
                                      "pick_count": int(m.group(3)), "ban_count": int(m.group(4)),
                                      "ct_round_win_pct": float(m.group(5)), "t_round_win_pct": float(m.group(6)),
                                      "round_win_pct": (float(m.group(5)) + float(m.group(6))) / 2,
                                      "avg_rounds": MAP_ROUND_BASE.get(map_name, 21.2), "source": "BO3 team page"}
    overall = re.search(r"Overall statistics(.*?)(?:Team records|Maps last|Transfers History|General stats)", text, re.I | re.S)
    section = overall.group(1) if overall else text[:10000]
    def metric(label: str, lo: float, hi: float) -> Optional[float]:
        mm = re.search(rf"\b{label}\b\s+([0-9]+(?:\.[0-9]+)?)", section, re.I)
        vv = safe_float(mm.group(1), None) if mm else None
        return vv if vv is not None and lo <= vv <= hi else None
    team_kpr = metric("Kills", 1.5, 5.0)
    deaths = metric("Death", 1.5, 5.0)
    total_maps = sum(int(v.get("maps", 0)) for v in map_profiles.values())
    profile = {"team_id": team_id, "slug": slug, "team": team_name, "current_roster": roster,
               "recent_matches": 0, "recent_maps": total_maps, "cumulative_map_observations": 0,
               "same_core_matches": 0, "current_roster_maps": min(total_maps, 30) if len(roster) >= 5 else 0,
               "roster_stability": 1.0 if len(roster) >= 5 else 0.0,
               "pick_counts": {m: int(v.get("pick_count", 0)) for m, v in map_profiles.items()},
               "ban_counts": {m: int(v.get("ban_count", 0)) for m, v in map_profiles.items()},
               "map_profiles": map_profiles, "kills_for_per_round": team_kpr,
               "deaths_allowed_per_round": deaths, "mapstats_samples": total_maps,
               "environment_counts": {}, "latest_match": "", "rest_days": None,
               "historical_opponent_rank_avg": None, "historical_opponent_rank_samples": 0,
               "updated_at": now_iso(), "provider": "BO3"}
    return profile, {**status, "ok": bool(map_profiles or roster), "roster_fresh": status.get("ok"),
                     "pool_fresh": status.get("ok"), "provider": "BO3"}


_v47_build_team_deep_base = build_team_deep_profile

def build_team_deep_profile(team_id: str, slug: str, team_name: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    is_bo3 = str(team_id).startswith("bo3:") or not re.fullmatch(r"\d+", str(team_id or ""))
    if is_bo3 or _source_circuit_open("hltv"):
        profile, status = _bo3_team_profile_from_html(team_id, slug, team_name)
        if profile:
            sqlite_store_entity_snapshot("team_deep", str(team_id or normalize_team(team_name)), profile,
                                         "BO3 team/map/roster", 0, now_iso())
            return profile, status
        snap, smeta = sqlite_latest_entity_snapshot("team_deep", str(team_id or normalize_team(team_name)),
                                                    SOURCE_MAX_STALE_SECONDS["team_maps"])
        if snap:
            return snap, {**smeta, "ok": True, "roster_fresh": False, "pool_fresh": False,
                          "provider": "local team snapshot"}
    if not _source_circuit_open("hltv"):
        return _v47_build_team_deep_base(team_id, slug, team_name)
    return {}, {"ok": False, "provider": "none", "warning": "BO3 team profile unavailable; HLTV circuit open"}


def _bo3_match_from_cache(match_id: str, slug: str) -> Optional[Dict[str, Any]]:
    rows, _ = fetch_bo3_upcoming_matches()
    clean = str(match_id).replace("bo3:", "")
    return next((r for r in rows if str(r.get("provider_match_id") or "") == clean or str(r.get("slug") or "") == slug), None)


def fetch_bo3_match_context(match_url: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    m = re.match(r"bo3://([^/]+)/?(.*)", str(match_url or ""))
    if not m:
        return {}, {"ok": False, "warning": "invalid BO3 match URL"}
    mid, slug = m.group(1), m.group(2)
    cached = _bo3_match_from_cache(mid, slug)
    payload = None; status = {"ok": True, "provider": "BO3", "cache": "upcoming list", "age_seconds": 0.0}
    if slug:
        payload, status = _bo3_get_json(f"/matches/{quote(slug)}", "BO3 match details",
                                        {"scope": "show-match", "with": "games,streams,teams,tournament_deep,stage,ai_predictions"},
                                        ttl=2 * 60, allow_stale=False)
    details = _bo3_materialize_matches(payload)
    row = details[0] if details else cached
    if not row:
        return {}, {**status, "ok": False, "warning": "BO3 match details unavailable"}
    teams = row.get("teams") or []
    # Pull team pages to recover current rosters. These are not treated as announced lineups.
    roster_groups = []
    for t in teams[:2]:
        tp, _ = _bo3_team_profile_from_html(str(t.get("team_id") or ""), str(t.get("slug") or ""), str(t.get("name") or ""))
        roster_groups.append({"team": t.get("name"), "players": tp.get("current_roster") or []})
    exact_lineup = row.get("lineup_names") or []
    context = {"match_url": match_url, "teams": teams, "format": row.get("format") or "BO3",
               "event": row.get("event") or "", "stage": "", "event_tier": "LOW/UNKNOWN",
               "environment": "UNKNOWN", "world_ranks": [], "confirmed_maps": row.get("confirmed_maps") or [],
               "veto_actions": [], "lineup_names": exact_lineup,
               "lineup_groups": roster_groups, "starting_side_hints": {},
               "provider": "BO3", "provider_match_id": row.get("provider_match_id") or mid,
               "lineup_source": "BO3 exact match payload" if exact_lineup else "BO3 current team rosters only"}
    return context, {**status, "ok": True, "provider": "BO3", "age_seconds": 0.0,
                     "exact_lineup": bool(exact_lineup), "match_id": row.get("provider_match_id") or mid}


def fetch_pandascore_match_context(match_url: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    m = re.match(r"pandascore://([^/]+)/?(.*)", str(match_url or ""))
    if not m: return {}, {"ok": False}
    mid = str(m.group(1)); rows, status = fetch_pandascore_upcoming()
    raw = next((x for x in rows if str(x.get("id")) == mid), None)
    if not raw: return {}, {**status, "ok": False, "warning": "PandaScore match missing"}
    teams=[]
    for x in raw.get("opponents") or []:
        o=x.get("opponent") or {}; name=str(o.get("name") or ""); tid=str(o.get("id") or "")
        if name: teams.append({"name":name,"slug":str(o.get("slug") or normalize_name(name).replace(" ","-")),"team_id":f"panda:{tid}"})
    context={"match_url":match_url,"teams":teams,"format":f"BO{raw.get('number_of_games') or 3}",
             "event":str(((raw.get("tournament") or {}).get("name")) or ""),"stage":"","event_tier":"LOW/UNKNOWN",
             "environment":"UNKNOWN","world_ranks":[],"confirmed_maps":[],"veto_actions":[],"lineup_names":[],
             "lineup_groups":[],"starting_side_hints":{},"provider":"PandaScore","provider_match_id":mid,
             "lineup_source":"PandaScore fixtures (no announced lineup)"}
    return context,{**status,"ok":len(teams)>=2,"provider":"PandaScore","age_seconds":0.0,"match_id":mid}


_v47_fetch_match_context_base = fetch_match_context

def fetch_match_context(match_url: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if str(match_url).startswith("bo3://"):
        return fetch_bo3_match_context(match_url)
    if str(match_url).startswith("pandascore://"):
        return fetch_pandascore_match_context(match_url)
    if _source_circuit_open("hltv"):
        return {}, {"ok": False, "provider": "HLTV", "circuit_open": True,
                    "warning": "HLTV blocked on Railway; BO3/PandaScore/local context required"}
    return _v47_fetch_match_context_base(match_url)


_v47_match_id_base = _match_id_from_url

def _match_id_from_url(url: str) -> str:
    m = re.match(r"(?:bo3|pandascore)://([^/]+)", str(url or ""))
    if m: return str(m.group(1)).replace("bo3:", "")
    return _v47_match_id_base(url)


_v47_build_full_board_base = build_full_board

def build_full_board(props: List[Dict[str, Any]], deep_enabled: bool = True) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    # Trip HLTV only once. All actual projection data is BO3/database/demo first.
    enriched = [_enrich_prop_identity_v46(p) for p in props]
    board, status = _v47_build_full_board_base(enriched, deep_enabled)
    projections = sum(r.get("projection") is not None for r in board)
    profiles = sum((safe_int(r.get("profile_maps"), 0) or 0) > 0 for r in board)
    bo3_profiles = sum("bo3" in str(r.get("profile_source") or "").lower() for r in board)
    local_profiles = sum(any(x in str(r.get("profile_source") or "").lower() for x in ["database", "demo", "manual"]) for r in board)
    matches = sum(bool(r.get("match_url")) for r in board)
    bo3_matches = sum(str(r.get("match_url") or "").startswith("bo3://") for r in board)
    hltv_state = _source_circuit_state("hltv"); bo3_state = _source_circuit_state("bo3")
    provider_status = {"lines_loaded": len(board), "verified_profiles": profiles,
                       "bo3_profiles": bo3_profiles, "local_demo_profiles": local_profiles,
                       "matched_events": matches, "bo3_matches": bo3_matches,
                       "projections_generated": projections,
                       "profile_coverage_pct": round(profiles / max(len(board), 1) * 100, 1),
                       "projection_coverage_pct": round(projections / max(len(board), 1) * 100, 1),
                       "primary_provider": "BO3", "hltv_role": "optional only",
                       "hltv_circuit": hltv_state, "bo3_circuit": bo3_state,
                       "message": ("Underdog lines and verified BO3/local profiles loaded." if projections
                                   else "Underdog lines loaded, but BO3/local/demo profiles were unavailable. Official picks disabled.")}
    for r in board:
        r["model_version"] = MODEL_VERSION; r["feature_fingerprint"] = MODEL_SCHEMA_FINGERPRINT
    return board, {**status, "v47_provider_recovery": provider_status,
                   "v46_source_recovery": provider_status,
                   "model_version": MODEL_VERSION, "feature_fingerprint": MODEL_SCHEMA_FINGERPRINT}


_v47_parser_report_base = parser_consistency_report

def parser_consistency_report(board: Sequence[Dict[str, Any]], status: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    base = _v47_parser_report_base(board, status)
    n = len(board); projections = sum(r.get("projection") is not None for r in board)
    profiles = sum(
        (safe_int(r.get("profile_maps"), 0) or 0) > 0
        or bool(r.get("core_kpr_verified"))
        or bool(str(r.get("profile_source") or "").strip())
        for r in board
    )
    lines_ok = n > 0
    if lines_ok and profiles == 0 and projections == 0:
        base["score"] = max(10.0, min(float(base.get("score", 0) or 0), 25.0))
        base["grade"] = "LINES OK — STATS PROVIDER UNAVAILABLE"
        base["official_enabled"] = False
        base["shutdown_reason"] = "Underdog loaded, but no BO3/local/demo player profiles were verified"
    elif lines_ok and projections == 0:
        base["score"] = max(20.0, min(float(base.get("score", 0) or 0), 40.0))
        base["grade"] = "LINES/PROFILES OK — MATCH CONTEXT INCOMPLETE"
        base["official_enabled"] = False
    base["source_summary"] = {"underdog_lines": n, "verified_profiles": profiles,
                              "projections": projections, "hltv_circuit_open": _source_circuit_open("hltv"),
                              "bo3_circuit_open": _source_circuit_open("bo3")}
    return base


# ============================================================
# V4.8 VERIFIED PROVIDER BRIDGE — RAILWAY-INDEPENDENT DATA RECOVERY
# ============================================================
# This layer deliberately disables HLTV as a required live dependency. It can
# resolve the board through a verified GitHub-hosted provider cache, direct
# BO3 JSON API calls, the persistent database, or full-round demo telemetry.
# No league-average profile is ever promoted into a projection.

import asyncio
from concurrent.futures import ThreadPoolExecutor as _V48ThreadPoolExecutor

MODEL_SCHEMA_FINGERPRINT = "v48_verified_bridge_bo3_async_relationship_identity_schema2"
SOURCE_RECOVERY_VERSION = "4.8"
V48_BRIDGE_SCHEMA = 2
V48_BRIDGE_FILENAME = "cs2_provider_cache.json"
V48_BRIDGE_LOCAL_FILE = os.path.join(STORAGE_DIR, V48_BRIDGE_FILENAME)
V48_BRIDGE_BRANCH = os.getenv("CS2_BRIDGE_BRANCH", "data-cache").strip() or "data-cache"
V48_BRIDGE_REPO = (os.getenv("CS2_BRIDGE_REPO", "").strip() or os.getenv("GITHUB_CODE_REPO", "").strip() or (f"{os.getenv('RAILWAY_GIT_REPO_OWNER','').strip()}/{os.getenv('RAILWAY_GIT_REPO_NAME','').strip()}" if os.getenv("RAILWAY_GIT_REPO_OWNER") and os.getenv("RAILWAY_GIT_REPO_NAME") else ""))
V48_BRIDGE_PROFILE_MAX_AGE = 36 * 3600
V48_BRIDGE_MATCH_MAX_AGE = 90 * 60
V48_DIRECT_BO3_ENABLED = os.getenv("CS2_DIRECT_BO3_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
V48_LEGACY_WEB_ENABLED = os.getenv("CS2_ENABLE_LEGACY_WEB_SOURCES", "false").strip().lower() in {"1", "true", "yes", "on"}
V48_DIRECT_CONCURRENCY = max(1, min(10, int(float(os.getenv("CS2_BO3_CONCURRENCY", "5") or 5))))
V48_DIRECT_TIMEOUT = max(8, min(45, int(float(os.getenv("CS2_BO3_TIMEOUT", "22") or 22))))
V48_DIRECT_MAX_PLAYERS = max(5, min(150, int(float(os.getenv("CS2_DIRECT_MAX_PLAYERS", "45") or 45))))
V48_HTTP_RETRIES = max(1, min(5, int(float(os.getenv("CS2_PROVIDER_RETRIES", "3") or 3))))
V48_FETCH_TEAM_DATA = os.getenv("CS2_BRIDGE_FETCH_TEAMS", "true").strip().lower() not in {"0", "false", "no", "off"}
V48_PROFILE_RETENTION_DAYS = max(7, min(365, int(float(os.getenv("CS2_PROFILE_RETENTION_DAYS", "90") or 90))))
V48_MAX_CACHED_PROFILES = max(100, min(3000, int(float(os.getenv("CS2_MAX_CACHED_PROFILES", "800") or 800))))
V48_PROFILE_MIN_MAPS = 5
V48_PROFILE_MIN_ROUNDS = 80
V48_RUNTIME: Dict[str, Any] = {"bridge": None, "profiles": {}, "matches": [], "teams": {}, "direct_status": {}, "loaded_at": ""}


def _v48_json_age_seconds(payload: Any) -> Optional[float]:
    if not isinstance(payload, dict):
        return None
    dt = _parse_iso_datetime(payload.get("generated_at") or payload.get("updated_at") or payload.get("created_at"))
    if not dt:
        return None
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())


def _v48_valid_bridge(payload: Any) -> bool:
    return bool(
        isinstance(payload, dict)
        and int(payload.get("schema_version", 0) or 0) >= 1
        and isinstance(payload.get("profiles"), dict)
        and isinstance(payload.get("matches"), list)
    )


def _v48_github_headers() -> Dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "OneWayPickz-CS2-v4.8"}
    token = (get_secret("CS2_BRIDGE_TOKEN", "") or os.getenv("CS2_BRIDGE_TOKEN", "")
             or get_secret("GITHUB_TOKEN", "") or os.getenv("GITHUB_TOKEN", ""))
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    return headers


def _v48_fetch_bridge_from_github() -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    repo = V48_BRIDGE_REPO.strip().strip("/")
    if not repo or "/" not in repo:
        return None, {"ok": False, "provider": "GitHub bridge", "warning": "CS2_BRIDGE_REPO is not configured"}
    api_url = f"https://api.github.com/repos/{repo}/contents/{V48_BRIDGE_FILENAME}"
    try:
        resp = requests.get(api_url, params={"ref": V48_BRIDGE_BRANCH}, headers=_v48_github_headers(), timeout=18)
        if resp.status_code == 200:
            node = resp.json()
            content = node.get("content") if isinstance(node, dict) else None
            if content:
                payload = json.loads(base64.b64decode(content).decode("utf-8"))
                if _v48_valid_bridge(payload):
                    save_json(V48_BRIDGE_LOCAL_FILE, payload, force=True)
                    return payload, {"ok": True, "provider": "GitHub data-cache branch", "status": 200,
                                     "repo": repo, "branch": V48_BRIDGE_BRANCH, "age_seconds": _v48_json_age_seconds(payload)}
            # GitHub omits inline base64 for larger files. Request the raw media
            # representation through the authenticated Contents endpoint.
            raw_headers = _v48_github_headers()
            raw_headers["Accept"] = "application/vnd.github.raw+json"
            raw_api = requests.get(api_url, params={"ref": V48_BRIDGE_BRANCH}, headers=raw_headers, timeout=22)
            if raw_api.status_code == 200:
                payload = raw_api.json()
                if _v48_valid_bridge(payload):
                    save_json(V48_BRIDGE_LOCAL_FILE, payload, force=True)
                    return payload, {"ok": True, "provider": "GitHub data-cache raw media", "status": 200,
                                     "repo": repo, "branch": V48_BRIDGE_BRANCH, "age_seconds": _v48_json_age_seconds(payload)}
        # Public repositories can also be read through raw.githubusercontent.com.
        raw_url = f"https://raw.githubusercontent.com/{repo}/{V48_BRIDGE_BRANCH}/{V48_BRIDGE_FILENAME}"
        raw = requests.get(raw_url, headers={"User-Agent": "OneWayPickz-CS2-v4.8"}, timeout=18)
        if raw.status_code == 200:
            payload = raw.json()
            if _v48_valid_bridge(payload):
                save_json(V48_BRIDGE_LOCAL_FILE, payload, force=True)
                return payload, {"ok": True, "provider": "GitHub raw data-cache", "status": 200,
                                 "repo": repo, "branch": V48_BRIDGE_BRANCH, "age_seconds": _v48_json_age_seconds(payload)}
        return None, {"ok": False, "provider": "GitHub bridge", "status": resp.status_code,
                      "warning": f"Provider bridge file unavailable on branch {V48_BRIDGE_BRANCH}"}
    except Exception as exc:
        return None, {"ok": False, "provider": "GitHub bridge", "warning": str(exc)}


def load_provider_bridge(force: bool = False) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    cached = V48_RUNTIME.get("bridge")
    if not force and _v48_valid_bridge(cached):
        return cached, {"ok": True, "provider": "runtime bridge", "age_seconds": _v48_json_age_seconds(cached)}
    local = load_json(V48_BRIDGE_LOCAL_FILE, {})
    local_age = _v48_json_age_seconds(local)
    if not force and _v48_valid_bridge(local) and local_age is not None and local_age <= V48_BRIDGE_PROFILE_MAX_AGE:
        V48_RUNTIME["bridge"] = local
        V48_RUNTIME["profiles"] = dict(local.get("profiles") or {})
        V48_RUNTIME["matches"] = list(local.get("matches") or [])
        V48_RUNTIME["teams"] = dict(local.get("teams") or {})
        return local, {"ok": True, "provider": "Railway volume bridge cache", "age_seconds": local_age}
    remote, status = _v48_fetch_bridge_from_github()
    if _v48_valid_bridge(remote):
        V48_RUNTIME["bridge"] = remote
        V48_RUNTIME["profiles"] = dict(remote.get("profiles") or {})
        V48_RUNTIME["matches"] = list(remote.get("matches") or [])
        V48_RUNTIME["teams"] = dict(remote.get("teams") or {})
        V48_RUNTIME["loaded_at"] = now_iso()
        return remote, status
    if _v48_valid_bridge(local):
        V48_RUNTIME["bridge"] = local
        V48_RUNTIME["profiles"] = dict(local.get("profiles") or {})
        V48_RUNTIME["matches"] = list(local.get("matches") or [])
        V48_RUNTIME["teams"] = dict(local.get("teams") or {})
        return local, {"ok": True, "provider": "stale Railway volume bridge cache", "age_seconds": local_age,
                       "warning": status.get("warning")}
    empty = {"schema_version": V48_BRIDGE_SCHEMA, "generated_at": "", "profiles": {}, "matches": [], "teams": {}, "source_status": {}}
    V48_RUNTIME["bridge"] = empty
    V48_RUNTIME["profiles"] = {}
    V48_RUNTIME["matches"] = []
    V48_RUNTIME["teams"] = {}
    return empty, status


def _v48_bridge_candidates(player: str) -> List[Tuple[float, Dict[str, Any]]]:
    target = normalize_name(player)
    output: List[Tuple[float, Dict[str, Any]]] = []
    for key, row in (V48_RUNTIME.get("profiles") or {}).items():
        if not isinstance(row, dict):
            continue
        aliases = [key, row.get("player"), row.get("nickname"), row.get("slug")]
        aliases.extend(row.get("aliases") or [])
        score = max([name_similarity(target, str(x or "")) for x in aliases] or [0.0])
        if score >= 0.78:
            output.append((score, row))
    output.sort(key=lambda x: x[0], reverse=True)
    return output


def _v48_profile_record_to_playerstats(player: str, record: Dict[str, Any], source_label: str) -> Tuple[Optional[PlayerStats], Dict[str, Any]]:
    maps = safe_int(record.get("profile_maps") or record.get("maps"), 0) or 0
    rounds = safe_int(record.get("profile_rounds") or record.get("rounds"), 0) or 0
    kpr = safe_float(record.get("base_kpr") or record.get("kpr"), None)
    if kpr is None or not (0.25 <= kpr <= 1.25):
        return None, {"matched": False, "warning": "profile cache KPR failed range validation"}
    if maps < V48_PROFILE_MIN_MAPS and rounds < V48_PROFILE_MIN_ROUNDS:
        return None, {"matched": False, "warning": "profile cache sample too small"}
    map_profiles = record.get("player_map_profiles") or record.get("map_profiles") or {}
    profile = PlayerStats(
        player=player,
        player_id=str(((record.get("identity_ids") or {}).get("player_id")) or record.get("player_id") or ""),
        slug=str(record.get("slug") or ""),
        team=str(record.get("team") or ""),
        maps=maps,
        rounds=max(rounds, maps * 13),
        kills=safe_int(record.get("kills"), 0) or 0,
        deaths=safe_int(record.get("deaths"), 0) or 0,
        kpr=float(kpr),
        dpr=float(safe_float(record.get("dpr"), LEAGUE_DPR) or LEAGUE_DPR),
        adr=float(safe_float(record.get("adr"), LEAGUE_ADR) or LEAGUE_ADR),
        rating=float(safe_float(record.get("rating"), LEAGUE_RATING) or LEAGUE_RATING),
        kd=float(safe_float(record.get("kd"), kpr / max(safe_float(record.get("dpr"), LEAGUE_DPR) or LEAGUE_DPR, .25)) or 1.0),
        hs_pct=safe_float(record.get("hs_pct"), None),
        opening_kpr=safe_float(record.get("opening_kpr"), None),
        opening_deaths_pr=safe_float(record.get("opening_deaths_pr"), None),
        kast_pct=safe_float(record.get("kast_pct"), None),
        impact=safe_float(record.get("impact"), None),
        ct_kpr=safe_float(record.get("ct_kpr"), None),
        t_kpr=safe_float(record.get("t_kpr"), None),
        source=source_label,
        href=str(record.get("profile_href") or record.get("href") or ""),
        data_warnings=list(record.get("profile_warnings") or record.get("warnings") or []),
        kpr_source=str(record.get("kpr_source") or "verified_provider_kpr"),
    )
    generated = _parse_iso_datetime(record.get("updated_at") or record.get("generated_at"))
    age = (datetime.now(timezone.utc) - generated).total_seconds() if generated else 999999.0
    return profile, {
        "matched": True, "source": source_label, "provider": record.get("provider") or source_label,
        "source_fresh": age <= V48_BRIDGE_PROFILE_MAX_AGE, "source_ages": {"provider_profile": max(age, 0)},
        "core_kpr_verified": True, "kpr_source": profile.kpr_source,
        "match_score": 1.0, "player_map_profiles": map_profiles,
        "bridge_record": record, "profile_url": profile.href,
    }


def _v48_bridge_profile(player: str, team_hint: str = "") -> Tuple[Optional[PlayerStats], Dict[str, Any]]:
    candidates = _v48_bridge_candidates(player)
    for score, row in candidates:
        row_team = str(row.get("team") or "")
        if team_hint and row_team and not _team_name_matches(team_hint, row_team) and score < .97:
            continue
        if score < .90:
            continue
        profile, meta = _v48_profile_record_to_playerstats(player, row, "GitHub verified provider bridge")
        if profile is not None:
            return profile, {**meta, "match_score": score, "recovery_path": "GitHub provider bridge"}
    return None, {"matched": False, "warning": "no confident bridge profile"}


def _v48_normalize_stat_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize_name(value))


def _v48_metric(payload: Any, names: Sequence[str], lo: Optional[float] = None, hi: Optional[float] = None) -> Optional[float]:
    wanted = {_v48_normalize_stat_key(x) for x in names}
    values: List[float] = []
    def walk(node: Any, label: str = "") -> None:
        if isinstance(node, dict):
            attrs_node = node.get("attributes") if isinstance(node.get("attributes"), dict) else node
            node_label = str(attrs_node.get("name") or attrs_node.get("label") or attrs_node.get("title") or label or "")
            for key, val in attrs_node.items():
                nk = _v48_normalize_stat_key(key)
                nl = _v48_normalize_stat_key(node_label)
                if nk in wanted or nl in wanted:
                    if isinstance(val, dict):
                        for sub in ["value", "avg", "average", "count", "per_round", "percentage"]:
                            v = safe_float(val.get(sub), None)
                            if v is not None: values.append(v)
                    elif not isinstance(val, (dict, list)):
                        v = safe_float(val, None)
                        if v is not None: values.append(v)
            for key, val in node.items():
                if key == "attributes":
                    continue
                walk(val, key)
        elif isinstance(node, list):
            for item in node:
                walk(item, label)
    walk(payload)
    for value in values:
        if (lo is None or value >= lo) and (hi is None or value <= hi):
            return value
    return None


def _v48_profile_record_from_bo3(player: str, candidate: Dict[str, Any], general: Any, maps_payload: Any, accuracy: Any, details: Any = None) -> Optional[Dict[str, Any]]:
    a = attrs(candidate)
    slug = str(_bo3_scalar(a, ["slug"], "") or "")
    pid = str(object_id(candidate) or _bo3_scalar(a, ["id", "player_id"], "") or "")
    nickname = str(_bo3_scalar(a, ["nickname", "nick_name", "display_name", "name"], player) or player)
    kpr = _v48_metric(general, ["kills_per_round", "kpr", "kills", "kill"], .25, 1.25)
    dpr = _v48_metric(general, ["deaths_per_round", "dpr", "deaths", "death"], .25, 1.25)
    adr = _v48_metric(general, ["damage_per_round", "adr", "damage"], 25, 150)
    maps_count = safe_int(_v48_metric(general, ["maps", "maps_count", "map_count"], 1, 10000), 0) or 0
    rounds = safe_int(_v48_metric(general, ["rounds", "rounds_count", "round_count"], 13, 500000), 0) or 0
    rating = _v48_metric(general, ["rating", "score", "bo3_rating"], .5, 10)
    hs_pct = _v48_metric(accuracy, ["headshot_percentage", "headshots_percentage", "headshot", "hs_pct"], 0, 100)
    opening = _v48_metric(general, ["opening_kills_per_round", "open_kills", "opening_kpr"], 0, .5)
    map_profiles: Dict[str, Dict[str, Any]] = {}
    for node in flatten_json(maps_payload):
        map_name = canonical_map_name(_bo3_scalar(node, ["map", "map_name", "name", "title", "slug"], ""))
        if not map_name:
            continue
        mkpr = _v48_metric(node, ["kills_per_round", "kpr", "kills", "kill"], .25, 1.25)
        count = safe_int(_v48_metric(node, ["maps", "maps_count", "count", "games"], 1, 10000), 0) or 0
        madr = _v48_metric(node, ["damage_per_round", "adr", "damage"], 25, 150)
        ct_kpr = _v48_metric(node, ["ct_kills_per_round", "ct_kpr"], .2, 1.4)
        t_kpr = _v48_metric(node, ["t_kills_per_round", "t_kpr"], .2, 1.4)
        if mkpr is not None and count > 0:
            map_profiles[map_name] = {"maps": count, "long_kpr": mkpr, "recent_kpr": mkpr,
                                      "blended_kpr": mkpr, "adr": madr, "ct_kpr": ct_kpr,
                                      "t_kpr": t_kpr, "source": "BO3 JSON API via verified bridge"}
    if maps_count <= 0:
        maps_count = sum(int(x.get("maps", 0) or 0) for x in map_profiles.values())
    if rounds <= 0 and maps_count > 0:
        rounds = int(round(maps_count * 21.2))
    if kpr is None or not (.25 <= kpr <= 1.25) or (maps_count < V48_PROFILE_MIN_MAPS and rounds < V48_PROFILE_MIN_ROUNDS):
        return None
    team = str(_bo3_scalar(a, ["team_name", "current_team", "team"], "") or "")
    if not team and details:
        team = _v48_team_name_from_payload(details)
    if rating is not None and rating > 2:
        rating = clamp(1.0 + (rating - 6.27) * .11, .70, 1.35)
    record = {
        "player": nickname, "nickname": nickname, "slug": slug, "player_id": f"bo3:{pid or slug}",
        "team": team, "profile_maps": maps_count, "profile_rounds": rounds,
        "base_kpr": float(kpr), "dpr": float(dpr or LEAGUE_DPR), "adr": float(adr or LEAGUE_ADR),
        "rating": float(rating or LEAGUE_RATING), "kd": float(kpr / max(dpr or LEAGUE_DPR, .25)),
        "hs_pct": hs_pct, "opening_kpr": opening, "player_map_profiles": map_profiles,
        "profile_source": "BO3 JSON API", "profile_href": f"{BO3_WEB_BASE}/players/{slug}" if slug else "",
        "kpr_source": "bo3_reported_kpr", "provider": "BO3 JSON API", "updated_at": now_iso(),
        "identity_ids": {"player_id": f"bo3:{pid or slug}"}, "aliases": [player, nickname, slug],
    }
    return record


async def _v48_aiohttp_get_json(session: Any, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
    url = f"{BO3_API_BASE}{endpoint}"
    last_error: Optional[BaseException] = None
    for attempt in range(V48_HTTP_RETRIES):
        try:
            async with session.get(url, params=params, timeout=V48_DIRECT_TIMEOUT) as response:
                if response.status == 200:
                    return await response.json(content_type=None)
                text = await response.text()
                retryable = response.status in {408, 425, 429, 500, 502, 503, 504}
                if not retryable:
                    raise RuntimeError(f"HTTP {response.status} from {endpoint}: {text[:160]}")
                retry_after = safe_float(response.headers.get("Retry-After"), None)
                wait = min(12.0, retry_after if retry_after is not None else (1.25 * (2 ** attempt)))
                last_error = RuntimeError(f"HTTP {response.status} from {endpoint}: {text[:160]}")
                if attempt + 1 < V48_HTTP_RETRIES:
                    await asyncio.sleep(wait)
        except Exception as exc:
            last_error = exc
            if attempt + 1 < V48_HTTP_RETRIES:
                await asyncio.sleep(min(8.0, 1.25 * (2 ** attempt)))
    raise RuntimeError(str(last_error or f"Provider request failed: {endpoint}"))


async def _v48_fetch_one_bo3_profile(session: Any, semaphore: Any, player: str) -> Tuple[str, Optional[Dict[str, Any]], Dict[str, Any]]:
    async with semaphore:
        try:
            search = await _v48_aiohttp_get_json(session, "/filters/players", {
                "page[offset]": "0", "page[limit]": "6", "filter[discipline_id][eq]": "1",
                "with": "country", "search_text": player,
            })
            candidate = _bo3_player_candidate(search, player)
            if not candidate:
                return player, None, {"ok": False, "warning": "no confident BO3 player match"}
            slug = str(_bo3_scalar(attrs(candidate), ["slug"], "") or "")
            if not slug:
                return player, None, {"ok": False, "warning": "BO3 player result had no slug"}
            today = local_now().date().isoformat()
            start = (local_now().date() - timedelta(days=180)).isoformat()
            general_task = _v48_aiohttp_get_json(session, f"/players/{quote(slug)}/general_stats", {
                "filter[start_date_to]": today, "filter[start_date_from]": start,
            })
            maps_task = _v48_aiohttp_get_json(session, f"/players/{quote(slug)}/map_stats", {
                "filter[begin_at_to]": today, "filter[begin_at_from]": start,
            })
            accuracy_task = _v48_aiohttp_get_json(session, f"/players/{quote(slug)}/accuracy_stats", {
                "filter[begin_at_to]": today, "filter[begin_at_from]": start,
            })
            details_task = _v48_aiohttp_get_json(session, f"/players/{quote(slug)}", {"with": "team,country"})
            results = await asyncio.gather(general_task, maps_task, accuracy_task, details_task, return_exceptions=True)
            general = {} if isinstance(results[0], Exception) else results[0]
            map_stats = {} if isinstance(results[1], Exception) else results[1]
            accuracy = {} if isinstance(results[2], Exception) else results[2]
            details = {} if isinstance(results[3], Exception) else results[3]
            record = _v48_profile_record_from_bo3(player, candidate, general, map_stats, accuracy, details)
            if record is None:
                return player, None, {"ok": False, "warning": "BO3 response did not contain a validated KPR/sample"}
            return player, record, {"ok": True, "slug": slug}
        except Exception as exc:
            return player, None, {"ok": False, "warning": str(exc)}


async def _v48_fetch_bo3_matches_async(session: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    try:
        payload = await _v48_aiohttp_get_json(session, "/matches", {
            "scope": "widget-matches", "page[offset]": "0", "page[limit]": "100",
            "sort": "tier_rank,-start_date", "filter[matches.status][in]": "upcoming,current",
            "filter[matches.discipline_id][eq]": "1",
            "with": "teams,tournament,ai_predictions,games,streams",
        })
        return _bo3_materialize_matches(payload), {"ok": True, "rows": len(_bo3_materialize_matches(payload))}
    except Exception as exc:
        return [], {"ok": False, "warning": str(exc)}


async def _v48_batch_bo3_async(players: Sequence[str], include_matches: bool = True, include_teams: bool = False) -> Dict[str, Any]:
    try:
        import aiohttp
    except Exception as exc:
        return {"profiles": {}, "matches": [], "teams": {}, "status": {"ok": False, "warning": f"aiohttp unavailable: {exc}"}}
    timeout = aiohttp.ClientTimeout(total=max(45, V48_DIRECT_TIMEOUT * 3))
    connector = aiohttp.TCPConnector(limit=max(4, V48_DIRECT_CONCURRENCY * 2), ttl_dns_cache=300)
    async with aiohttp.ClientSession(headers=_BO3_HEADERS, timeout=timeout, connector=connector) as session:
        semaphore = asyncio.Semaphore(V48_DIRECT_CONCURRENCY)
        tasks = [_v48_fetch_one_bo3_profile(session, semaphore, p) for p in players]
        match_task = asyncio.create_task(_v48_fetch_bo3_matches_async(session)) if include_matches else None
        profile_results = await asyncio.gather(*tasks) if tasks else []
        matches, match_status = await match_task if match_task else ([], {"ok": False, "disabled": True})
        profiles: Dict[str, Dict[str, Any]] = {}
        failures: Dict[str, Any] = {}
        for player, record, status in profile_results:
            if record:
                profiles[normalize_name(player)] = record
            else:
                failures[player] = status
        teams: Dict[str, Dict[str, Any]] = {}
        if include_teams and V48_FETCH_TEAM_DATA:
            team_refs: Dict[str, Dict[str, Any]] = {}
            for match in matches:
                for team in match.get("teams") or []:
                    name = str(team.get("name") or "")
                    if name:
                        team_refs[normalize_team(name)] = team
            team_tasks = [_v48_fetch_one_bo3_team(session, semaphore, row) for row in team_refs.values()]
            for key, record, _status in (await asyncio.gather(*team_tasks) if team_tasks else []):
                if record:
                    teams[key] = record
        _v48_attach_profile_lineups(matches, profiles, teams)
    return {"profiles": profiles, "matches": matches, "teams": teams,
            "status": {"ok": bool(profiles or matches), "profiles": len(profiles), "matches": len(matches),
                       "teams": len(teams), "failed_profiles": len(failures),
                       "failures": dict(list(failures.items())[:20]), "match_status": match_status}}


def _v48_run_async(coro: Any) -> Any:
    # Streamlit can already own an event loop. Running the coroutine in a small
    # helper thread works in both Streamlit and Railway cron processes.
    result: Dict[str, Any] = {}
    error: Dict[str, BaseException] = {}
    def runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:
            error["error"] = exc
    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join(timeout=max(90, V48_DIRECT_TIMEOUT * 8))
    if thread.is_alive():
        raise TimeoutError("BO3 async provider batch timed out")
    if error:
        raise error["error"]
    return result.get("value")


def v48_prefetch_provider_data(players: Sequence[str], force: bool = False) -> Dict[str, Any]:
    unique = list(dict.fromkeys(str(x or "").strip() for x in players if str(x or "").strip()))
    bridge, bridge_status = load_provider_bridge(force=force)
    missing: List[str] = []
    for player in unique:
        local, _ = _playerstats_from_local(player)
        if local is not None and int(local.maps or 0) >= V48_PROFILE_MIN_MAPS:
            continue
        profile, _ = _v48_bridge_profile(player)
        if profile is None:
            missing.append(player)
    direct_status: Dict[str, Any] = {"ok": False, "disabled": not V48_DIRECT_BO3_ENABLED, "requested": len(missing)}
    if missing and V48_DIRECT_BO3_ENABLED and not _source_circuit_open("bo3_api"):
        try:
            direct_players = missing[:V48_DIRECT_MAX_PLAYERS]
            payload = _v48_run_async(_v48_batch_bo3_async(direct_players, include_matches=True, include_teams=False)) or {}
            direct_status = {**(payload.get("status") or {}), "requested": len(missing), "attempted": len(direct_players), "deferred": max(0, len(missing)-len(direct_players))}
            for key, record in (payload.get("profiles") or {}).items():
                V48_RUNTIME.setdefault("profiles", {})[key] = record
                profile, meta = _v48_profile_record_to_playerstats(str(record.get("player") or key), record, "BO3 JSON API direct")
                if profile is not None:
                    _persist_provider_profile(profile, {**meta, "player_map_profiles": record.get("player_map_profiles") or {}})
            if payload.get("matches"):
                V48_RUNTIME["matches"] = payload.get("matches") or []
            if payload.get("teams"):
                V48_RUNTIME["teams"].update(payload.get("teams") or {})
            if direct_status.get("ok"):
                _close_source_circuit("bo3_api")
            else:
                _trip_source_circuit("bo3_api", str(direct_status.get("warning") or "BO3 direct batch failed"), 15 * 60)
        except Exception as exc:
            direct_status = {"ok": False, "warning": str(exc), "requested": len(missing), "attempted": min(len(missing), V48_DIRECT_MAX_PLAYERS)}
            _trip_source_circuit("bo3_api", str(exc), 15 * 60)
    V48_RUNTIME["direct_status"] = direct_status
    return {"bridge": bridge_status, "direct": direct_status, "unique_players": len(unique), "missing_before_direct": len(missing)}


def v48_generate_bridge_cache(players: Sequence[str], previous: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    previous = previous if _v48_valid_bridge(previous) else {"profiles": {}, "matches": [], "teams": {}}
    previous_profiles = dict(previous.get("profiles") or {})
    refresh: List[str] = []
    for player in list(dict.fromkeys(str(x or "").strip() for x in players if str(x or "").strip())):
        existing = previous_profiles.get(normalize_name(player))
        age = _v48_json_age_seconds(existing) if isinstance(existing, dict) else None
        if not existing or age is None or age > 12 * 3600:
            refresh.append(player)
    payload = _v48_run_async(_v48_batch_bo3_async(refresh, include_matches=True, include_teams=True)) if (refresh or not previous.get("matches") or not previous.get("teams")) else {"profiles": {}, "matches": previous.get("matches") or [], "teams": previous.get("teams") or {}, "status": {"ok": True, "cached": True}}
    profiles = previous_profiles
    profiles.update(payload.get("profiles") or {})
    board_keys = {normalize_name(x) for x in players if str(x or "").strip()}
    now_dt = datetime.now(timezone.utc)
    for key, row in list(profiles.items()):
        if key in board_keys and isinstance(row, dict):
            row["last_seen_at"] = now_iso()
    # Keep active-board profiles plus the most recent verified records. This
    # prevents the GitHub cache from growing without bound.
    def _profile_sort(item: Tuple[str, Any]) -> Tuple[int, float]:
        key, row = item
        dt = _parse_iso_datetime((row or {}).get("last_seen_at") or (row or {}).get("updated_at")) if isinstance(row, dict) else None
        age = (now_dt-dt).total_seconds() if dt else 1e12
        return (1 if key in board_keys else 0, -age)
    valid_items=[]
    for key,row in profiles.items():
        dt=_parse_iso_datetime((row or {}).get("last_seen_at") or (row or {}).get("updated_at")) if isinstance(row,dict) else None
        age_days=(now_dt-dt).total_seconds()/86400 if dt else 9999
        if key in board_keys or age_days <= V48_PROFILE_RETENTION_DAYS:
            valid_items.append((key,row))
    valid_items=sorted(valid_items,key=_profile_sort,reverse=True)[:V48_MAX_CACHED_PROFILES]
    profiles=dict(valid_items)
    output = {
        "schema_version": V48_BRIDGE_SCHEMA,
        "generated_at": now_iso(),
        "model_version": MODEL_VERSION,
        "profiles": profiles,
        "matches": payload.get("matches") or previous.get("matches") or [],
        "teams": {**dict(previous.get("teams") or {}), **dict(payload.get("teams") or {})},
        "source_status": payload.get("status") or {},
        "board_player_count": len(list(dict.fromkeys(normalize_name(x) for x in players if x))),
        "refreshed_profiles": len(payload.get("profiles") or {}),
    }
    return output


# Never open four HLTV global pages on every refresh. The detailed profile path
# is database/bridge/BO3 JSON first. Legacy public-page pulls are opt-in only.
_v48_fetch_hltv_player_table_base = fetch_hltv_player_table

def fetch_hltv_player_table(days: int) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    if not V48_LEGACY_WEB_ENABLED:
        return {}, {"ok": False, "disabled": True, "provider": "HLTV", "rows": 0,
                    "warning": "HLTV disabled in v4.8; verified bridge/BO3/local/demo path is primary"}
    return _v48_fetch_hltv_player_table_base(days)


_v48_build_player_profile_base = build_player_profile

def build_player_profile(player_name: str, long_table: Dict[str, Dict[str, Any]], medium_table: Dict[str, Dict[str, Any]],
                         recent30_table: Dict[str, Dict[str, Any]], recent15_table: Dict[str, Dict[str, Any]]) -> Tuple[PlayerStats, Dict[str, Any]]:
    local, local_meta = _playerstats_from_local(player_name)
    if local is not None and int(local.maps or 0) >= V48_PROFILE_MIN_MAPS:
        return local, {**local_meta, "recovery_path": "verified Railway database/demo", "source_fresh": True,
                       "core_kpr_verified": True}
    profile, meta = _v48_bridge_profile(player_name)
    if profile is not None:
        _persist_provider_profile(profile, meta)
        return profile, meta
    # Direct batch profiles are merged into the same runtime index.
    candidates = _v48_bridge_candidates(player_name)
    for score, record in candidates:
        if score < .90:
            continue
        profile2, meta2 = _v48_profile_record_to_playerstats(player_name, record, "BO3 JSON API direct")
        if profile2 is not None:
            return profile2, {**meta2, "match_score": score, "recovery_path": "direct BO3 batch"}
    if V48_LEGACY_WEB_ENABLED:
        return _v48_build_player_profile_base(player_name, long_table, medium_table, recent30_table, recent15_table)
    empty = PlayerStats(player=player_name, maps=0, rounds=0, kpr=LEAGUE_KPR, source="")
    return empty, {"matched": False, "source_fresh": False, "core_kpr_verified": False,
                   "recovery_path": "failed", "source_failure": "No verified bridge, local/demo, or direct BO3 JSON profile"}


def _v48_match_score(row: Dict[str, Any], team: str, opponent: str, player: str = "", start_time: Any = None) -> float:
    teams = [str(x.get("name") or "") for x in (row.get("teams") or [])]
    score = 0.0
    if team:
        score += max([name_similarity(team, x) for x in teams] or [0]) * .45
    if opponent:
        score += max([name_similarity(opponent, x) for x in teams] or [0]) * .40
    if player:
        lineup = row.get("lineup_names") or []
        score += max([name_similarity(player, x) for x in lineup] or [0]) * .10
    if start_time:
        a = _parse_iso_datetime(start_time); b = _parse_iso_datetime(row.get("start_time"))
        if a and b:
            hours = abs((a - b).total_seconds()) / 3600
            score += max(0, .05 * (1 - min(hours / 12, 1)))
    return score


def _v48_bridge_match(team: str, opponent: str, player: str = "", start_time: Any = None) -> Tuple[str, Dict[str, Any]]:
    candidates: List[Tuple[float, Dict[str, Any]]] = []
    for row in V48_RUNTIME.get("matches") or []:
        if not isinstance(row, dict):
            continue
        score = _v48_match_score(row, team, opponent, player, start_time)
        candidates.append((score, row))
    if not candidates:
        return "", {"ok": False, "warning": "provider bridge has no matches"}
    score, row = max(candidates, key=lambda x: x[0])
    threshold = .68 if team and opponent else .42 if team else .0
    if score < threshold:
        return "", {"ok": False, "warning": "no confident provider match", "best_score": round(score, 3)}
    mid = str(row.get("provider_match_id") or row.get("match_id") or row.get("id") or "")
    return f"bridge://{mid}", {"ok": True, "provider": "verified provider bridge", "score": round(score, 3), "match": row}


_v48_discover_match_base = discover_hltv_match

def discover_hltv_match(team: str, opponent: str, player: str = "") -> Tuple[str, Dict[str, Any]]:
    url, meta = _v48_bridge_match(team, opponent, player)
    if url:
        return url, meta
    if V48_LEGACY_WEB_ENABLED:
        return _v48_discover_match_base(team, opponent, player)
    return "", {"ok": False, "provider": "none", "warning": "No verified bridge match and legacy web sources disabled"}


def _v48_match_context_from_record(row: Dict[str, Any], match_url: str) -> Dict[str, Any]:
    teams = row.get("teams") or []
    lineup_names = row.get("lineup_names") or []
    lineup_groups = row.get("lineup_groups") or []
    if not lineup_groups:
        for t in teams[:2]:
            roster = list(t.get("players") or t.get("roster") or [])
            lineup_groups.append({"team": t.get("name"), "players": roster})
            lineup_names.extend(roster)
    return {
        "match_url": match_url, "teams": teams, "format": row.get("format") or "BO3",
        "event": row.get("event") or "", "stage": row.get("stage") or "",
        "event_tier": row.get("event_tier") or "LOW/UNKNOWN", "environment": row.get("environment") or "UNKNOWN",
        "world_ranks": row.get("world_ranks") or [], "confirmed_maps": row.get("confirmed_maps") or [],
        "veto_actions": row.get("veto_actions") or [], "lineup_names": list(dict.fromkeys(lineup_names)),
        "lineup_groups": lineup_groups, "starting_side_hints": row.get("starting_side_hints") or {},
        "provider": "verified provider bridge", "provider_match_id": row.get("provider_match_id") or row.get("match_id") or "",
        "lineup_source": row.get("lineup_source") or "provider bridge",
    }


_v48_fetch_match_context_base = fetch_match_context

def fetch_match_context(match_url: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if str(match_url).startswith("bridge://"):
        mid = str(match_url).split("bridge://", 1)[1].strip("/")
        row = next((x for x in (V48_RUNTIME.get("matches") or []) if str(x.get("provider_match_id") or x.get("match_id") or x.get("id") or "") == mid), None)
        if row:
            age = _v48_json_age_seconds(row)
            return _v48_match_context_from_record(row, match_url), {"ok": True, "provider": "verified provider bridge",
                    "age_seconds": age if age is not None else _v48_json_age_seconds(V48_RUNTIME.get("bridge")),
                    "exact_lineup": bool(row.get("lineup_names") or row.get("lineup_groups")), "match_id": mid}
        return {}, {"ok": False, "provider": "verified provider bridge", "warning": "bridge match ID not found"}
    return _v48_fetch_match_context_base(match_url)


_v48_match_id_base = _match_id_from_url

def _match_id_from_url(url: str) -> str:
    if str(url).startswith("bridge://"):
        return str(url).split("bridge://", 1)[1].strip("/")
    return _v48_match_id_base(url)


def _v48_relationship_refs(obj: Any, rel_names: Sequence[str]) -> List[Tuple[str, str]]:
    if not isinstance(obj, dict):
        return []
    rels = obj.get("relationships") or (obj.get("attributes") or {}).get("relationships") or {}
    if not isinstance(rels, dict):
        return []
    wanted = {_v48_normalize_stat_key(x) for x in rel_names}
    refs: List[Tuple[str, str]] = []
    for key, node in rels.items():
        if _v48_normalize_stat_key(key) not in wanted:
            continue
        data = node.get("data") if isinstance(node, dict) else node
        entries = data if isinstance(data, list) else [data]
        for ref in entries:
            if isinstance(ref, dict):
                refs.append((str(ref.get("type") or ""), str(ref.get("id") or "")))
            elif ref not in [None, ""]:
                refs.append(("", str(ref)))
    return refs


def _v48_all_jsonapi_objects(data: Any) -> Dict[Tuple[str, str], Dict[str, Any]]:
    index: Dict[Tuple[str, str], Dict[str, Any]] = {}
    if not isinstance(data, dict):
        return index
    containers: List[Any] = [data.get("included") or []]
    for value in data.values():
        if isinstance(value, list):
            containers.append(value)
        elif isinstance(value, dict) and isinstance(value.get("data"), list):
            containers.append(value.get("data") or [])
    for container in containers:
        for obj in container if isinstance(container, list) else []:
            if not isinstance(obj, dict):
                continue
            oid = str(obj.get("id") or (obj.get("attributes") or {}).get("id") or "")
            typ = str(obj.get("type") or "")
            if oid:
                index[(typ, oid)] = obj
                index[("", oid)] = obj
    return index


def _v48_resolve_refs(index: Dict[Tuple[str, str], Dict[str, Any]], refs: Sequence[Tuple[str, str]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for typ, oid in refs:
        found = index.get((typ, oid)) or index.get(("", oid))
        if found:
            out.append(found)
    return out


def _v48_obj_name(obj: Any) -> str:
    return str(_bo3_scalar(obj, ["name", "title", "display_name", "nickname", "abbr", "abbreviation", "short_name"], "") or "")


def _v48_enrich_underdog_rows(rows: List[Dict[str, Any]], data: Any) -> List[Dict[str, Any]]:
    if not isinstance(data, dict):
        return rows
    index = _v48_all_jsonapi_objects(data)
    for row in rows:
        evidence_ids = [str(row.get(k) or "") for k in ["prop_id", "line_id", "appearance_id", "game_id", "player_id"]]
        related: List[Dict[str, Any]] = []
        for oid in evidence_ids:
            if oid and index.get(("", oid)):
                related.append(index[("", oid)])
        # Follow two relationship levels: line -> appearance/player/game -> teams.
        frontier = list(related)
        for _ in range(2):
            new: List[Dict[str, Any]] = []
            for obj in frontier:
                refs = _v48_relationship_refs(obj, ["appearance", "player", "athlete", "game", "match", "team", "teams", "opponents", "participants", "home_team", "away_team"])
                new.extend(_v48_resolve_refs(index, refs))
            related.extend(new); frontier = new
        names: List[str] = []
        team_names: List[str] = []
        matchup_texts: List[str] = []
        for obj in related:
            typ = normalize_name(obj.get("type") or "")
            name = _v48_obj_name(obj)
            if name:
                names.append(name)
            if "team" in typ or "opponent" in typ:
                if name: team_names.append(name)
            attrs_obj = attrs(obj)
            for key in ["matchup", "title", "description", "display_name", "name"]:
                val = attrs_obj.get(key)
                if isinstance(val, str) and any(sep in val.lower() for sep in [" vs ", " v ", " @ ", " versus "]):
                    matchup_texts.append(val)
            for key in ["home_team", "away_team", "team", "opponent"]:
                val = attrs_obj.get(key)
                if isinstance(val, dict):
                    nm = _v48_obj_name(val)
                    if nm: team_names.append(nm)
                elif isinstance(val, str) and val.strip():
                    team_names.append(val.strip())
        matchup = str(row.get("matchup") or "")
        if not matchup and matchup_texts:
            matchup = matchup_texts[0]
            row["matchup"] = matchup
        a, b = _teams_from_matchup(matchup or row.get("evidence") or "")
        candidates = list(dict.fromkeys(x.strip() for x in team_names + [a, b] if str(x or "").strip()))
        player_name = str(row.get("player") or "")
        candidates = [x for x in candidates if name_similarity(player_name, x) < .85]
        if not row.get("team") and len(candidates) == 1:
            row["team"] = candidates[0]
        if not row.get("team") and len(candidates) >= 2 and a:
            row["team"] = a
        if not row.get("opponent") and len(candidates) >= 2:
            row["opponent"] = next((x for x in candidates if not _team_name_matches(str(row.get("team") or ""), x)), candidates[1])
        row["identity_evidence"] = {"related_names": names[:20], "team_candidates": candidates[:8], "matchup_texts": matchup_texts[:5]}
    return rows


_v48_parse_underdog_base = _parse_underdog_top_level

def _parse_underdog_top_level(data: Any) -> List[Dict[str, Any]]:
    rows = _v48_parse_underdog_base(data)
    return _v48_enrich_underdog_rows(rows, data)


def _v48_attach_identity(prop: Dict[str, Any]) -> Dict[str, Any]:
    out = _enrich_prop_identity_v46(prop)
    profile, meta = _v48_bridge_profile(str(out.get("player") or ""), str(out.get("team") or ""))
    if profile is None:
        candidates = _v48_bridge_candidates(str(out.get("player") or ""))
        for score, record in candidates:
            if score >= .90:
                out.setdefault("team", str(record.get("team") or ""))
                break
    else:
        if not out.get("team") and profile.team:
            out["team"] = profile.team
    if not out.get("match_url"):
        url, mmeta = _v48_bridge_match(str(out.get("team") or ""), str(out.get("opponent") or ""),
                                       str(out.get("player") or ""), out.get("start_time"))
        if url:
            out["match_url"] = url
            match = mmeta.get("match") or {}
            teams = [str(x.get("name") or "") for x in (match.get("teams") or [])]
            if not out.get("team") and profile is not None and profile.team:
                out["team"] = max(teams, key=lambda x: name_similarity(profile.team, x)) if teams else profile.team
            if not out.get("opponent") and len(teams) >= 2:
                out["opponent"] = next((x for x in teams if not _team_name_matches(str(out.get("team") or ""), x)), teams[1])
    return out


_v48_build_full_board_base = build_full_board

def build_full_board(props: List[Dict[str, Any]], deep_enabled: bool = True) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    players = [str(x.get("player") or "") for x in props]
    prefetch = v48_prefetch_provider_data(players)
    enriched = [_v48_attach_identity(p) for p in props]
    board, status = _v48_build_full_board_base(enriched, deep_enabled)
    profiles = sum((safe_int(r.get("profile_maps"), 0) or 0) > 0 for r in board)
    projections = sum(r.get("projection") is not None for r in board)
    matches = sum(bool(r.get("match_url")) for r in board)
    teams = sum(bool(r.get("team")) for r in board)
    bridge_age = _v48_json_age_seconds(V48_RUNTIME.get("bridge"))
    recovery = {
        "lines_loaded": len(board), "verified_profiles": profiles, "matched_teams": teams,
        "matched_events": matches, "projections_generated": projections,
        "profile_coverage_pct": round(profiles / max(len(board), 1) * 100, 1),
        "match_coverage_pct": round(matches / max(len(board), 1) * 100, 1),
        "projection_coverage_pct": round(projections / max(len(board), 1) * 100, 1),
        "bridge_age_seconds": bridge_age, "bridge_repo": V48_BRIDGE_REPO,
        "bridge_branch": V48_BRIDGE_BRANCH, "prefetch": prefetch,
        "hltv_enabled": V48_LEGACY_WEB_ENABLED, "primary_path": "GitHub bridge → local/demo → direct BO3 JSON",
        "message": ("Verified provider profiles reached the projection engine." if projections else
                    "Underdog lines loaded, but the provider bridge/local/demo/direct BO3 paths produced no verified profiles. Run the Source Bridge workflow or configure CS2_BRIDGE_REPO."),
    }
    for row in board:
        row["model_version"] = MODEL_VERSION
        row["feature_fingerprint"] = MODEL_SCHEMA_FINGERPRINT
        if projections == 0:
            row["flags"] = list(dict.fromkeys(list(row.get("flags") or []) + ["VERIFIED DATA BRIDGE EMPTY — NO PROJECTION"] ))
    return board, {**status, "v48_source_recovery": recovery, "v47_provider_recovery": recovery,
                   "v46_source_recovery": recovery, "model_version": MODEL_VERSION,
                   "feature_fingerprint": MODEL_SCHEMA_FINGERPRINT}


_v48_parser_report_base = parser_consistency_report

def parser_consistency_report(board: Sequence[Dict[str, Any]], status: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    base = _v48_parser_report_base(board, status)
    n = len(board); profiles = sum(((safe_int(x.get("profile_maps"), 0) or 0) > 0) or bool(x.get("core_kpr_verified")) or (safe_float(x.get("adjusted_kpr"), None) is not None and 0.25 <= float(x.get("adjusted_kpr")) <= 1.25) for x in board)
    projections = sum(x.get("projection") is not None for x in board)
    if n and profiles == 0:
        base["score"] = 20.0
        base["grade"] = "LINES OK — STATS PROVIDER UNAVAILABLE"
        base["official_enabled"] = False
        base["shutdown_reason"] = "No verified profile reached the model; line parser itself is working"
    elif n and projections == 0:
        base["score"] = min(max(float(base.get("score", 0) or 0), 30.0), 50.0)
        base["grade"] = "PROFILES FOUND — MATCH/ROSTER CONTEXT INCOMPLETE"
        base["official_enabled"] = False
    base["provider_bridge"] = (status or {}).get("v48_source_recovery") or {}
    return base


# ============================================================
# V4.8 COMPLETE PROVIDER/MAP/TEAM RECOVERY OVERRIDES
# ============================================================

def _v48_team_name_from_payload(payload: Any) -> str:
    if not payload:
        return ""
    candidates: List[Tuple[int, str]] = []
    for node in flatten_json(payload):
        if not isinstance(node, dict):
            continue
        typ = normalize_name(object_type(node) or node.get("type") or "")
        a = attrs(node)
        name = str(_bo3_scalar(a, ["team_name", "current_team", "name", "title", "short_name"], "") or "").strip()
        if not name:
            continue
        priority = 0
        if "team" in typ and "tournament" not in typ:
            priority += 5
        if a.get("logo_url") or a.get("team_id"):
            priority += 2
        if a.get("nickname") or "player" in typ or "country" in typ:
            priority -= 4
        if priority > 0:
            candidates.append((priority, name))
    return max(candidates, key=lambda x: x[0])[1] if candidates else ""


def _v48_roster_from_payload(payload: Any) -> List[str]:
    names: List[str] = []
    for node in flatten_json(payload):
        if not isinstance(node, dict):
            continue
        typ = normalize_name(object_type(node) or node.get("type") or "")
        a = attrs(node)
        if "player" not in typ and not any(k in a for k in ["nickname", "nick_name", "player_id"]):
            continue
        name = str(_bo3_scalar(a, ["nickname", "nick_name", "display_name", "player_name", "name"], "") or "").strip()
        if name and normalize_name(name) not in {normalize_name(x) for x in names}:
            names.append(name)
    return names[:10]


def _v48_team_map_profiles(*payloads: Any) -> Dict[str, Dict[str, Any]]:
    output: Dict[str, Dict[str, Any]] = {}
    for payload in payloads:
        for node in flatten_json(payload):
            if not isinstance(node, dict):
                continue
            map_name = canonical_map_name(_bo3_scalar(node, ["map", "map_name", "name", "title", "slug"], ""))
            if map_name not in KNOWN_MAPS:
                continue
            maps = safe_int(_v48_metric(node, ["maps", "maps_count", "games", "count"], 1, 10000), 0) or 0
            win_pct = _v48_metric(node, ["win_percentage", "win_rate", "win_pct"], 0, 100)
            round_win = _v48_metric(node, ["round_win_percentage", "round_win_rate", "round_win_pct"], 0, 100)
            ct = _v48_metric(node, ["ct_round_win_percentage", "ct_win_percentage", "ct_round_win_pct"], 0, 100)
            tt = _v48_metric(node, ["t_round_win_percentage", "t_win_percentage", "t_round_win_pct"], 0, 100)
            avg_rounds = _v48_metric(node, ["average_rounds", "avg_rounds", "rounds_per_map"], 13, 40)
            picks = safe_int(_v48_metric(node, ["pick_count", "picks"], 0, 10000), 0) or 0
            bans = safe_int(_v48_metric(node, ["ban_count", "bans"], 0, 10000), 0) or 0
            if not any(v not in [None, 0] for v in [maps, win_pct, round_win, ct, tt, picks, bans]):
                continue
            old = output.get(map_name, {})
            output[map_name] = {
                **old, "maps": max(int(old.get("maps", 0) or 0), maps),
                "win_pct": win_pct if win_pct is not None else old.get("win_pct"),
                "round_win_pct": round_win if round_win is not None else (old.get("round_win_pct") or ((ct + tt) / 2 if ct is not None and tt is not None else None)),
                "ct_round_win_pct": ct if ct is not None else old.get("ct_round_win_pct"),
                "t_round_win_pct": tt if tt is not None else old.get("t_round_win_pct"),
                "avg_rounds": avg_rounds if avg_rounds is not None else (old.get("avg_rounds") or MAP_ROUND_BASE.get(map_name, 21.2)),
                "pick_count": max(int(old.get("pick_count", 0) or 0), picks),
                "ban_count": max(int(old.get("ban_count", 0) or 0), bans),
                "source": "BO3 JSON provider bridge",
            }
    return output


def _v48_team_record_from_bo3(team_ref: Dict[str, Any], detail: Any, general: Any, advanced: Any) -> Optional[Dict[str, Any]]:
    name = str(team_ref.get("name") or _v48_team_name_from_payload(detail) or "").strip()
    if not name:
        return None
    slug = str(team_ref.get("slug") or normalize_name(name).replace(" ", "-"))
    provider_id = str(team_ref.get("provider_id") or str(team_ref.get("team_id") or "").replace("bo3:", ""))
    roster = _v48_roster_from_payload(detail)
    map_profiles = _v48_team_map_profiles(detail, general, advanced)
    team_kpr = _v48_metric(general, ["kills_per_round", "team_kills_per_round", "kills"], 1.5, 5.0)
    deaths = _v48_metric(general, ["deaths_per_round", "team_deaths_per_round", "deaths"], 1.5, 5.0)
    total_maps = sum(int(v.get("maps", 0) or 0) for v in map_profiles.values())
    return {
        "team": name, "slug": slug, "team_id": str(team_ref.get("team_id") or f"bo3:{provider_id or slug}"),
        "provider_id": provider_id, "current_roster": roster[:5] if len(roster) >= 5 else roster,
        "map_profiles": map_profiles, "recent_maps": total_maps, "current_roster_maps": total_maps if len(roster) >= 5 else 0,
        "roster_stability": 1.0 if len(roster) >= 5 else clamp(len(roster) / 5.0, 0, .8),
        "kills_for_per_round": team_kpr, "deaths_allowed_per_round": deaths,
        "mapstats_samples": total_maps, "pick_counts": {m: int(v.get("pick_count", 0) or 0) for m, v in map_profiles.items()},
        "ban_counts": {m: int(v.get("ban_count", 0) or 0) for m, v in map_profiles.items()},
        "provider": "BO3 JSON API", "updated_at": now_iso(),
    }


async def _v48_fetch_one_bo3_team(session: Any, semaphore: Any, team_ref: Dict[str, Any]) -> Tuple[str, Optional[Dict[str, Any]], Dict[str, Any]]:
    name = str(team_ref.get("name") or "")
    key = normalize_team(name)
    slug = str(team_ref.get("slug") or normalize_name(name).replace(" ", "-"))
    if not name or not slug:
        return key, None, {"ok": False, "warning": "team name/slug unavailable"}
    async with semaphore:
        try:
            today = local_now().date().isoformat()
            start = (local_now().date() - timedelta(days=365)).isoformat()
            tasks = [
                _v48_aiohttp_get_json(session, f"/teams/{quote(slug)}", {"with": "players,country"}),
                _v48_aiohttp_get_json(session, f"/teams/{quote(slug)}/general_stats", {"filter[start_date_to]": today, "filter[start_date_from]": start}),
                _v48_aiohttp_get_json(session, f"/teams/{quote(slug)}/advanced_stats", {"filter[start_date_to]": today, "filter[start_date_from]": start}),
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            detail = {} if isinstance(results[0], Exception) else results[0]
            general = {} if isinstance(results[1], Exception) else results[1]
            advanced = {} if isinstance(results[2], Exception) else results[2]
            record = _v48_team_record_from_bo3(team_ref, detail, general, advanced)
            return key, record, {"ok": bool(record), "slug": slug}
        except Exception as exc:
            return key, None, {"ok": False, "warning": str(exc)}


def _v48_attach_profile_lineups(matches: List[Dict[str, Any]], profiles: Dict[str, Dict[str, Any]], teams: Dict[str, Dict[str, Any]]) -> None:
    for match in matches:
        groups: List[Dict[str, Any]] = []
        all_names: List[str] = list(match.get("lineup_names") or [])
        for team in (match.get("teams") or [])[:2]:
            team_name = str(team.get("name") or "")
            record = teams.get(normalize_team(team_name), {})
            roster = list(record.get("current_roster") or [])
            if not roster:
                roster = [str(row.get("player") or row.get("nickname") or key)
                          for key, row in profiles.items()
                          if _team_name_matches(team_name, str(row.get("team") or ""))]
            roster = list(dict.fromkeys(x for x in roster if x))[:5]
            groups.append({"team": team_name, "players": roster})
            all_names.extend(roster)
        match["lineup_groups"] = groups
        match["lineup_names"] = list(dict.fromkeys(all_names))
        match["lineup_source"] = "BO3 team roster bridge" if any(len(x.get("players") or []) >= 5 for x in groups) else "BO3 board-player roster bridge"


def _v48_provider_map_record(player: str, map_name: str) -> Dict[str, Any]:
    best: Dict[str, Any] = {}
    candidates = _v48_bridge_candidates(player)
    if candidates:
        record = candidates[0][1]
        maps = record.get("player_map_profiles") or record.get("map_profiles") or {}
        best = dict(maps.get(map_name) or maps.get(normalize_name(map_name)) or {})
    if not best:
        db = lookup_database_player(player) or {}
        maps = db.get("player_map_profiles") or db.get("map_profiles") or {}
        best = dict(maps.get(map_name) or maps.get(normalize_name(map_name)) or {})
    if not best:
        saved = load_json(DEEP_PLAYER_MAP_FILE, {})
        row = (saved.get(normalize_name(player)) or {}) if isinstance(saved, dict) else {}
        maps = row.get("maps") or {}
        best = dict(maps.get(map_name) or maps.get(normalize_name(map_name)) or {})
    return best


_v48_map_model_pre_bridge = build_player_map_profiles

def build_player_map_profiles(profile: PlayerStats, likely_maps: Sequence[str], target_time: Any = None, patch_era: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """Database/bridge/demo-first map model; HLTV is never required."""
    out: Dict[str, Dict[str, Any]] = {}
    statuses: Dict[str, Any] = {}
    era = patch_era or patch_era_for_time(target_time)
    target_dt = _parse_iso_datetime(target_time) or datetime.now(timezone.utc)
    era_start = _parse_iso_datetime(era.get("effective_from"))
    days_in_era = max(1.0, (target_dt - (era_start or target_dt - timedelta(days=180))).total_seconds() / 86400.0)
    era_factor = clamp(days_in_era / 180.0, .20, 1.0)
    for map_name in list(dict.fromkeys(canonical_map_name(x) for x in likely_maps if canonical_map_name(x)))[:7]:
        provider = _v48_provider_map_record(profile.player, map_name)
        demo = demo_profile_for(profile.player, map_name)
        p_maps = safe_int(provider.get("maps") or provider.get("long_maps") or provider.get("sample_maps"), 0) or 0
        p_rounds = safe_int(provider.get("rounds"), 0) or int(round(p_maps * 21.2)) if p_maps else 0
        p_kpr = safe_float(provider.get("blended_kpr"), None)
        if p_kpr is None: p_kpr = safe_float(provider.get("long_kpr"), None)
        if p_kpr is None: p_kpr = safe_float(provider.get("kpr"), None)
        recent_kpr = safe_float(provider.get("recent_kpr"), None)
        recent_maps = safe_int(provider.get("recent_maps"), 0) or 0
        demo_kpr = safe_float(demo.get("kpr"), None) if demo.get("denominator_verified") else None
        demo_rounds = safe_int(demo.get("rounds"), 0) or 0
        overall_verified = bool(profile.maps >= V48_PROFILE_MIN_MAPS or profile.rounds >= V48_PROFILE_MIN_ROUNDS) and .25 <= profile.kpr <= 1.25
        if p_kpr is None and demo_kpr is None and not overall_verified:
            statuses[map_name] = {"usable": False, "warning": "no verified map or overall sample"}
            continue
        center = p_kpr if p_kpr is not None else float(profile.kpr)
        w_map = clamp(p_maps / (p_maps + 14.0), 0, .82) * era_factor if p_kpr is not None else 0.0
        blended = float(profile.kpr) * (1 - w_map) + float(center) * w_map
        w_recent = clamp(recent_maps / (recent_maps + 12.0), 0, .35) if recent_kpr is not None else 0.0
        blended = blended * (1 - w_recent) + float(recent_kpr or blended) * w_recent
        w_demo = 0.0
        if demo_kpr is not None:
            w_demo = clamp(demo_rounds / (demo_rounds + 260.0), 0, .52) * era_recency_weight(demo.get("latest_event_time"), target_time, era)
            blended = blended * (1 - w_demo) + float(demo_kpr) * w_demo
        ct_base = safe_float(provider.get("ct_kpr"), None) or profile.ct_kpr or blended
        t_base = safe_float(provider.get("t_kpr"), None) or profile.t_kpr or blended
        dct, dt = safe_float(demo.get("ct_kpr"), None), safe_float(demo.get("t_kpr"), None)
        ct_rounds, t_rounds = safe_int(demo.get("ct_rounds"), 0) or 0, safe_int(demo.get("t_rounds"), 0) or 0
        wct = clamp(ct_rounds / (ct_rounds + 160.0), 0, .55) if dct is not None else 0
        wt = clamp(t_rounds / (t_rounds + 160.0), 0, .55) if dt is not None else 0
        ct_kpr = float(ct_base) * (1 - wct) + float(dct if dct is not None else ct_base) * wct
        t_kpr = float(t_base) * (1 - wt) + float(dt if dt is not None else t_base) * wt
        effective = p_maps + recent_maps * .65 + demo_rounds / 20.0
        confidence = clamp(32 + effective * 2.0, 32, 98)
        sources = []
        if p_kpr is not None: sources.append(str(provider.get("source") or "verified provider map KPR"))
        if demo_kpr is not None: sources.append("full-round demo KPR")
        if not sources: sources.append("verified overall profile shrunk to map baseline")
        out[map_name] = {
            "map": map_name, "maps": p_maps, "rounds": p_rounds, "kpr": round(float(center), 4),
            "recent_maps": recent_maps, "recent_kpr": round(float(recent_kpr), 4) if recent_kpr is not None else None,
            "demo_rounds": demo_rounds, "demo_kpr": round(float(demo_kpr), 4) if demo_kpr is not None else None,
            "demo_weight": round(w_demo, 4), "blended_kpr": round(clamp(blended, .38, 1.08), 4),
            "ct_kpr": round(clamp(ct_kpr, .34, 1.12), 4), "t_kpr": round(clamp(t_kpr, .34, 1.12), 4),
            "ct_demo_rounds": ct_rounds, "t_demo_rounds": t_rounds,
            "economy_kpr": demo.get("economy_kpr") or {}, "economy_rounds": demo.get("economy_rounds") or {},
            "dpr": safe_float(provider.get("dpr"), None), "adr": safe_float(provider.get("adr"), None),
            "rating": safe_float(provider.get("rating"), None), "opening_kpr": safe_float(provider.get("opening_kpr"), None) or profile.opening_kpr,
            "hs_pct": safe_float(provider.get("hs_pct"), None) or profile.hs_pct,
            "awp_kill_share": safe_float(provider.get("awp_kill_share"), None) or profile.awp_kill_share,
            "source": " + ".join(dict.fromkeys(sources)),
            "core_map_kpr_verified": p_kpr is not None or demo_kpr is not None,
            "overall_profile_fallback": p_kpr is None and demo_kpr is None,
            "patch_era_weight": round(era_factor, 4), "map_model_confidence": round(confidence, 1),
            "effective_map_sample": round(effective, 1),
        }
        statuses[map_name] = {"usable": True, "provider": provider, "demo": demo}
    if not out and V48_LEGACY_WEB_ENABLED:
        return _v48_map_model_pre_bridge(profile, likely_maps, target_time, patch_era)
    if out:
        saved = load_json(DEEP_PLAYER_MAP_FILE, {})
        if not isinstance(saved, dict): saved = {}
        saved[normalize_name(profile.player)] = {"updated_at": now_iso(), "maps": out}
        save_json(DEEP_PLAYER_MAP_FILE, saved)
    return out, {"ok": bool(out), "maps": list(out), "statuses": statuses, "patch_era": era.get("name"),
                 "provider_bridge_connected": True, "legacy_hltv_used": False}


_v48_team_model_pre_bridge = build_team_deep_profile

def build_team_deep_profile(team_id: str, slug: str, team_name: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    runtime = V48_RUNTIME.get("teams") or {}
    candidates = []
    for key, row in runtime.items():
        if not isinstance(row, dict): continue
        score = max(name_similarity(team_name, str(row.get("team") or key)),
                    1.0 if str(team_id or "") and str(team_id) == str(row.get("team_id") or "") else 0.0)
        candidates.append((score, row))
    if candidates:
        score, row = max(candidates, key=lambda x: x[0])
        if score >= .82:
            profile = dict(row)
            profile.setdefault("team_id", team_id)
            profile.setdefault("slug", slug)
            profile.setdefault("team", team_name)
            profile.setdefault("recent_matches", 0)
            profile.setdefault("recent_maps", sum(int(v.get("maps", 0) or 0) for v in (profile.get("map_profiles") or {}).values()))
            profile.setdefault("current_roster_maps", profile.get("recent_maps") if len(profile.get("current_roster") or []) >= 5 else 0)
            profile.setdefault("roster_stability", 1.0 if len(profile.get("current_roster") or []) >= 5 else clamp(len(profile.get("current_roster") or []) / 5.0, 0, .8))
            profile.setdefault("mapstats_samples", profile.get("recent_maps") or 0)
            return profile, {"ok": True, "provider": "verified provider bridge", "roster_fresh": bool(profile.get("current_roster")),
                             "pool_fresh": bool(profile.get("map_profiles")), "match_score": score}
    # Construct a safe partial roster from verified provider player profiles.
    roster = [str(row.get("player") or row.get("nickname") or key)
              for key, row in (V48_RUNTIME.get("profiles") or {}).items()
              if isinstance(row, dict) and _team_name_matches(team_name, str(row.get("team") or ""))]
    roster = list(dict.fromkeys(x for x in roster if x))[:5]
    if roster:
        partial = {"team_id": team_id, "slug": slug, "team": team_name, "current_roster": roster,
                   "recent_matches": 0, "recent_maps": 0, "current_roster_maps": 0,
                   "roster_stability": clamp(len(roster) / 5.0, 0, .8), "map_profiles": {},
                   "pick_counts": {}, "ban_counts": {}, "mapstats_samples": 0,
                   "updated_at": now_iso(), "provider": "verified provider player bridge"}
        return partial, {"ok": True, "provider": partial["provider"], "roster_fresh": False, "pool_fresh": False,
                         "warning": "partial roster from verified board profiles"}
    if V48_LEGACY_WEB_ENABLED:
        return _v48_team_model_pre_bridge(team_id, slug, team_name)
    snap, smeta = sqlite_latest_entity_snapshot("team_deep", str(team_id or normalize_team(team_name)), SOURCE_MAX_STALE_SECONDS["team_maps"])
    if snap:
        return snap, {**smeta, "ok": True, "provider": "local cumulative team snapshot", "roster_fresh": False, "pool_fresh": False}
    return {}, {"ok": False, "provider": "none", "warning": "No bridge/local team profile; projection will use conservative map priors and cannot be Official"}



# ============================================================
# V4.9 BATCH STATS MIRROR — SELF-CONTAINED STREAMLIT/RAILWAY RECOVERY
# ============================================================
# V4.8 still required a pre-populated GitHub cache or a live BO3 response.
# V4.9 removes that setup dependency. One batch request per time window is sent
# through Jina Reader to the public HLTV aggregate table. The batch table covers
# hundreds of players at once, is persisted locally, and is converted into
# conservative real-player profiles. No request is sent once per player.

MODEL_SCHEMA_FINGERPRINT = "v49_jina_batch_real_aggregate_synthetic_match_track_schema1"
SOURCE_RECOVERY_VERSION = "4.9"
V49_JINA_ENABLED = os.getenv("CS2_JINA_MIRROR_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
V49_JINA_BASE = os.getenv("CS2_JINA_READER_BASE", "https://r.jina.ai/").strip().rstrip("/") + "/"
V49_TABLE_CACHE_FILE = os.path.join(STORAGE_DIR, "cs2_hltv_batch_mirror.json")
V49_TABLE_FRESH_SECONDS = max(900, min(24*3600, int(float(os.getenv("CS2_MIRROR_FRESH_SECONDS", "21600") or 21600))))
V49_TABLE_STALE_SECONDS = max(V49_TABLE_FRESH_SECONDS, min(14*86400, int(float(os.getenv("CS2_MIRROR_STALE_SECONDS", "604800") or 604800))))
V49_REQUEST_TIMEOUT = max(15, min(90, int(float(os.getenv("CS2_MIRROR_TIMEOUT", "45") or 45))))
V49_MIN_BATCH_ROWS = max(25, int(float(os.getenv("CS2_MIRROR_MIN_ROWS", "80") or 80)))
V49_ALLOW_BO3_LAST_RESORT = os.getenv("CS2_BO3_LAST_RESORT", "false").strip().lower() in {"1", "true", "yes", "on"}
V49_RUNTIME: Dict[str, Any] = {"tables": {}, "statuses": {}, "synthetic_matches": {}, "profiles": {}, "teams": {}, "last_prefetch": {}}


def _v49_cache_payload() -> Dict[str, Any]:
    payload = load_json(V49_TABLE_CACHE_FILE, {})
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("schema_version", 1)
    payload.setdefault("tables", {})
    return payload


def _v49_age_seconds(value: Any) -> Optional[float]:
    dt = _parse_iso_datetime(value)
    if not dt:
        return None
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())


def _v49_target_url(days: int, side: str = "", map_name: str = "") -> str:
    start, end = _period_dates(max(int(days), 1))
    params: Dict[str, Any] = {"csVersion": "CS2", "startDate": start, "endDate": end, "minMapCount": 0}
    if side:
        params["side"] = side
    if map_name and map_name in HLTV_MAP_KEYS:
        params["maps"] = HLTV_MAP_KEYS[map_name]
    return f"{HLTV_BASE}/stats/players?{urlencode(params)}"


def _v49_clean_markdown_text(value: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", str(value or ""))
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" |\t")


def _v49_derive_aggregate_rates(rounds: int, kd_diff: float, kd: float, rating: float) -> Dict[str, Any]:
    rounds = max(int(rounds or 0), 0)
    kd = float(kd or 1.0)
    diff = float(kd_diff or 0.0)
    kills = deaths = 0.0
    method = "rating_shrunk_fallback"
    if rounds > 0 and abs(kd - 1.0) >= 0.012 and abs(diff) >= 1:
        try:
            deaths = diff / (kd - 1.0)
            kills = deaths + diff
            if deaths <= 0 or kills <= 0:
                kills = deaths = 0.0
            else:
                kpr0, dpr0 = kills / rounds, deaths / rounds
                if not (0.35 <= kpr0 <= 1.12 and 0.35 <= dpr0 <= 1.05):
                    kills = deaths = 0.0
                else:
                    method = "real_rounds_kd_diff_derived"
        except Exception:
            kills = deaths = 0.0
    if rounds <= 0:
        return {"kills": 0, "deaths": 0, "kpr": None, "dpr": None, "method": "no_rounds"}
    if kills <= 0 or deaths <= 0:
        # This path is only for KD values rounded to exactly 1.00. It is kept
        # conservative and receives an explicit warning/Track cap downstream.
        kpr = clamp(LEAGUE_KPR * (0.72 + 0.28 * max(float(rating or 1.0), .75)), .49, .88)
        dpr = clamp(kpr / max(kd, .72), .50, .88)
        kills, deaths = kpr * rounds, dpr * rounds
    else:
        raw_kpr, raw_dpr = kills / rounds, deaths / rounds
        rating_kpr = clamp(LEAGUE_KPR * (0.68 + .32 * max(float(rating or 1.0), .75)), .49, .90)
        # KD is rounded to two decimals on the aggregate table. Shrink the
        # algebraic estimate slightly toward a rating-based center.
        kpr = .78 * raw_kpr + .22 * rating_kpr
        dpr = clamp(kpr / max(kd, .72), .48, .90)
        kills, deaths = kpr * rounds, dpr * rounds
    return {"kills": int(round(kills)), "deaths": int(round(deaths)), "kpr": float(kills / rounds),
            "dpr": float(deaths / rounds), "method": method}


def _v49_parse_hltv_batch_markdown(markdown: str, source_url: str = "", pulled_at: str = "") -> Dict[str, Dict[str, Any]]:
    rows: Dict[str, Dict[str, Any]] = {}
    if not markdown:
        return rows
    player_re = re.compile(r"\[([^\]]+)\]\((?:https?://(?:www\.)?hltv\.org)?(/stats/players/(\d+)/([^)?#\s]+)[^)]*)\)", re.I)
    team_re = re.compile(r"\[([^\]]+)\]\((?:https?://(?:www\.)?hltv\.org)?/stats/teams/\d+/[^)]*\)", re.I)
    end_numbers = re.compile(r"(?:^|\s)(\d+)\s+(\d+)\s+([+-]?\d+)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*\|?\s*$")
    for raw_line in str(markdown).splitlines():
        pm = player_re.search(raw_line)
        if not pm:
            continue
        clean = _v49_clean_markdown_text(raw_line)
        nm = end_numbers.search(clean)
        if not nm:
            # Markdown table cells can contain extra spacing. Read the final
            # five numeric cells rather than numbers inside player nicknames.
            parts = [x.strip() for x in raw_line.split("|") if x.strip()]
            vals: List[str] = []
            for cell in parts[-7:]:
                c = _v49_clean_markdown_text(cell)
                if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", c):
                    vals.append(c)
            if len(vals) < 5:
                continue
            vals = vals[-5:]
            maps_s, rounds_s, diff_s, kd_s, rating_s = vals
        else:
            maps_s, rounds_s, diff_s, kd_s, rating_s = nm.groups()
        maps = safe_int(maps_s, 0) or 0
        rounds = safe_int(rounds_s, 0) or 0
        diff = safe_float(diff_s, 0) or 0
        kd = safe_float(kd_s, 1.0) or 1.0
        rating = safe_float(rating_s, 1.0) or 1.0
        rates = _v49_derive_aggregate_rates(rounds, diff, kd, rating)
        if rates.get("kpr") is None or maps <= 0 or rounds <= 0:
            continue
        teams = list(dict.fromkeys(_v49_clean_markdown_text(x) for x in team_re.findall(raw_line) if _v49_clean_markdown_text(x)))
        player = _v49_clean_markdown_text(pm.group(1))
        pid, slug = pm.group(3), pm.group(4).split("?")[0].strip("/")
        key = normalize_name(player)
        record = {
            "player": player, "nickname": player, "player_id": str(pid), "slug": slug,
            "team": teams[0] if teams else "", "teams": teams,
            "maps": maps, "profile_maps": maps, "rounds": rounds, "profile_rounds": rounds,
            "kills": rates["kills"], "deaths": rates["deaths"], "kpr": rates["kpr"],
            "base_kpr": rates["kpr"], "dpr": rates["dpr"], "kd": kd, "rating": rating,
            "adr": clamp(LEAGUE_ADR * (.58 * rating + .42 * (rates["kpr"] / LEAGUE_KPR)), 58, 96),
            "profile_href": urljoin(HLTV_BASE, pm.group(2)), "href": urljoin(HLTV_BASE, pm.group(2)),
            "provider": "Jina HLTV batch mirror", "kpr_source": "hltv_batch_real_aggregate_derived",
            "profile_warnings": ["KPR derived from real rounds, K-D difference and rounded K/D; exact page enrichment pending"],
            "updated_at": pulled_at or now_iso(), "generated_at": pulled_at or now_iso(),
            "source_url": source_url, "aggregate_method": rates["method"], "player_map_profiles": {},
        }
        old = rows.get(key)
        if old is None or maps > safe_int(old.get("maps"), 0):
            rows[key] = record
    return rows


def _v49_fetch_table(days: int, force: bool = False) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    days = int(days)
    key = f"players:{days}:both:all"
    if not force and key in V49_RUNTIME["tables"]:
        return V49_RUNTIME["tables"][key], V49_RUNTIME["statuses"].get(key, {"ok": True, "provider": "runtime mirror"})
    payload = _v49_cache_payload()
    cached = (payload.get("tables") or {}).get(key) or {}
    cached_rows = cached.get("rows") if isinstance(cached, dict) else None
    age = _v49_age_seconds(cached.get("updated_at")) if isinstance(cached, dict) else None
    if not force and isinstance(cached_rows, dict) and len(cached_rows) >= V49_MIN_BATCH_ROWS and age is not None and age <= V49_TABLE_FRESH_SECONDS:
        for row in cached_rows.values():
            if isinstance(row, dict):
                row["_source_fresh"] = age <= 24*3600; row["_source_age_seconds"] = age
        status = {"ok": True, "provider": "persistent Jina batch cache", "rows": len(cached_rows), "age_seconds": age, "days": days}
        V49_RUNTIME["tables"][key] = cached_rows; V49_RUNTIME["statuses"][key] = status
        return cached_rows, status
    target = _v49_target_url(days)
    mirror_url = V49_JINA_BASE + target
    if V49_JINA_ENABLED and not _source_circuit_open("jina_mirror"):
        try:
            response = requests.get(mirror_url, headers={"User-Agent": "OneWayPickz-CS2-v4.9", "Accept": "text/plain,*/*", "X-Return-Format": "markdown"}, timeout=V49_REQUEST_TIMEOUT)
            log_request("HLTV batch mirror", mirror_url, response.status_code, response.reason)
            if response.status_code == 200:
                pulled = now_iso()
                parsed = _v49_parse_hltv_batch_markdown(response.text, target, pulled)
                if len(parsed) >= V49_MIN_BATCH_ROWS:
                    payload.setdefault("tables", {})[key] = {"updated_at": pulled, "target_url": target, "rows": parsed}
                    save_json(V49_TABLE_CACHE_FILE, payload, force=True)
                    _close_source_circuit("jina_mirror")
                    status = {"ok": True, "provider": "Jina HLTV batch mirror", "rows": len(parsed), "status": 200,
                              "days": days, "age_seconds": 0, "target_url": target,
                              "body_bytes": len(response.content), "content_type": response.headers.get("content-type", "")}
                    V49_RUNTIME["tables"][key] = parsed; V49_RUNTIME["statuses"][key] = status
                    return parsed, status
                _trip_source_circuit("jina_mirror", f"batch parser returned only {len(parsed)} rows", 10*60)
            elif response.status_code in {403, 429}:
                _trip_source_circuit("jina_mirror", f"HTTP {response.status_code}", 15*60)
        except Exception as exc:
            log_request("HLTV batch mirror", mirror_url, 0, str(exc))
            _trip_source_circuit("jina_mirror", str(exc), 10*60)
    if isinstance(cached_rows, dict) and len(cached_rows) >= V49_MIN_BATCH_ROWS and age is not None and age <= V49_TABLE_STALE_SECONDS:
        for row in cached_rows.values():
            if isinstance(row, dict):
                row["_source_fresh"] = age <= 24*3600; row["_source_age_seconds"] = age
        status = {"ok": True, "provider": "stale persistent Jina batch cache", "rows": len(cached_rows), "age_seconds": age,
                  "days": days, "warning": "live batch mirror unavailable"}
        V49_RUNTIME["tables"][key] = cached_rows; V49_RUNTIME["statuses"][key] = status
        return cached_rows, status
    status = {"ok": False, "provider": "Jina HLTV batch mirror", "rows": 0, "days": days,
              "warning": "batch mirror unavailable and no usable persistent cache"}
    V49_RUNTIME["statuses"][key] = status
    return {}, status


def _v49_merge_profile_windows(players: Sequence[str], tables: Dict[int, Dict[str, Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    output: Dict[str, Dict[str, Any]] = {}
    for player in list(dict.fromkeys(str(x or "").strip() for x in players if str(x or "").strip())):
        matched: Dict[int, Tuple[Dict[str, Any], float]] = {}
        for days, table in tables.items():
            row, score = fuzzy_lookup_player(player, table)
            if row and score >= .78:
                matched[days] = (row, score)
        if not matched:
            continue
        base_days = 365 if 365 in matched else (180 if 180 in matched else max(matched))
        base = dict(matched[base_days][0])
        kpr = safe_float(base.get("kpr"), None)
        if kpr is None:
            continue
        # Neutral recent-form blend with sample shrinkage. Equal treatment is
        # used for hot and cold form to avoid an Over preference.
        window_weights = {365: .46, 90: .22, 30: .22, 15: .10}
        total_w = 0.0; form_kpr = 0.0; form_rating = 0.0
        form_windows: Dict[str, Any] = {}
        for days, nominal in window_weights.items():
            pair = matched.get(days)
            if not pair:
                continue
            row = pair[0]; maps = safe_int(row.get("maps"), 0) or 0
            shrink_target = {365: 45, 90: 24, 30: 12, 15: 5}[days]
            reliability = clamp(maps / max(shrink_target, 1), .12, 1.0)
            weight = nominal * reliability
            rk = safe_float(row.get("kpr"), kpr) or kpr
            rr = safe_float(row.get("rating"), safe_float(base.get("rating"), 1.0)) or 1.0
            form_kpr += weight * rk; form_rating += weight * rr; total_w += weight
            form_windows[f"{days}d_maps"] = maps
            form_windows[f"{days}d_kpr"] = round(rk, 4)
            form_windows[f"{days}d_rating"] = round(rr, 3)
        if total_w > 0:
            blended_kpr = clamp(form_kpr / total_w, .43, .98)
            blended_rating = clamp(form_rating / total_w, .72, 1.45)
            # Anchor half the estimate to the one-year baseline. This prevents a tiny 15-day
            # run from pushing every selection in one direction.
            blended_kpr = .50 * kpr + .50 * blended_kpr
        else:
            blended_kpr = kpr; blended_rating = safe_float(base.get("rating"), 1.0) or 1.0
        base.update({
            "player": player, "nickname": player, "base_kpr": round(blended_kpr, 5), "kpr": round(blended_kpr, 5),
            "rating": round(blended_rating, 4), "form_windows": form_windows,
            "provider": "Jina HLTV batch mirror", "updated_at": now_iso(), "generated_at": now_iso(),
            "aliases": list(dict.fromkeys([player, base.get("player"), base.get("slug")])),
            "kpr_source": "hltv_batch_real_aggregate_derived",
        })
        output[normalize_name(player)] = base
    return output


def _v49_build_team_index(profiles: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in profiles.values():
        if not isinstance(row, dict):
            continue
        team = str(row.get("team") or "").strip()
        if team:
            grouped[normalize_team(team)].append(row)
    teams: Dict[str, Dict[str, Any]] = {}
    for key, rows in grouped.items():
        rows = sorted(rows, key=lambda x: (safe_int(((x.get("form_windows") or {}).get("15d_maps")), 0), safe_int(x.get("maps"), 0)), reverse=True)
        roster = list(dict.fromkeys(str(x.get("player") or "") for x in rows if str(x.get("player") or "")))[:5]
        top = rows[:5]
        team_name = str(rows[0].get("team") or key)
        team_kpr = sum(safe_float(x.get("base_kpr") or x.get("kpr"), LEAGUE_KPR) or LEAGUE_KPR for x in top)
        team_dpr = sum(safe_float(x.get("dpr"), LEAGUE_DPR) or LEAGUE_DPR for x in top)
        teams[key] = {
            "team": team_name, "team_id": f"mirror-team:{hashlib.sha1(key.encode()).hexdigest()[:12]}",
            "slug": key.replace(" ", "-"),
            # Aggregate tables identify recent team associations, but they do not prove the announced five.
            # Keep them as candidates instead of falsely passing the lineup hard gate.
            "current_roster": [], "roster_candidates": roster, "current_roster_maps": 0,
            "roster_stability": 0.0, "recent_maps": 0, "recent_matches": 0,
            "map_profiles": {}, "pick_counts": {}, "ban_counts": {}, "mapstats_samples": 0,
            "kills_for_per_round": clamp(team_kpr, 2.4, 4.4), "deaths_allowed_per_round": clamp(team_dpr, 2.4, 4.4),
            "provider": "Jina HLTV batch team aggregate", "updated_at": now_iso(), "map_pool_verified": False,
        }
    return teams


def _v49_generate_profiles(players: Sequence[str], force: bool = False) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    tables: Dict[int, Dict[str, Dict[str, Any]]] = {}
    statuses: Dict[int, Dict[str, Any]] = {}
    for days in [365, 90, 30, 15]:
        table, status = _v49_fetch_table(days, force=force)
        tables[days] = table; statuses[days] = status
    profiles = _v49_merge_profile_windows(players, tables)
    covered = len(profiles)
    return profiles, {"ok": covered > 0, "provider": "Jina HLTV batch mirror", "profiles": covered,
                      "requested": len(set(normalize_name(x) for x in players if str(x or "").strip())),
                      "window_statuses": statuses}


def v49_generate_bridge_cache(players: Sequence[str], previous: Optional[Dict[str, Any]] = None,
                              board_rows: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    previous = previous if _v48_valid_bridge(previous) else {"profiles": {}, "matches": [], "teams": {}}
    fresh, status = _v49_generate_profiles(players, force=True)
    profiles = dict(previous.get("profiles") or {})
    # New mirror data replaces older mirror data, but never replaces an exact
    # demo/API profile with a larger verified sample.
    for key, row in fresh.items():
        old = profiles.get(key) or {}
        old_exact = str(old.get("kpr_source") or "") in {"real_kills_div_rounds", "hltv_reported_kpr", "bo3_reported_kpr", "demo_full_round_kpr"}
        if not old_exact or safe_int(row.get("maps"), 0) >= safe_int(old.get("maps"), 0):
            profiles[key] = row
    teams = dict(previous.get("teams") or {})
    teams.update(_v49_build_team_index(profiles))
    matches: List[Dict[str, Any]] = []
    for prop in board_rows or []:
        team, opp = str(prop.get("team") or "").strip(), str(prop.get("opponent") or "").strip()
        if not team or not opp:
            continue
        mid = hashlib.sha1(f"{normalize_team(team)}|{normalize_team(opp)}|{str(prop.get('start_time') or '')[:16]}".encode()).hexdigest()[:18]
        matches.append({"provider_match_id": f"ud-{mid}", "match_id": f"ud-{mid}", "start_time": prop.get("start_time"),
                        "format": "BO3", "event": str(prop.get("matchup") or "Underdog CS2"), "stage": "",
                        "event_tier": "LOW/UNKNOWN", "environment": "UNKNOWN", "confirmed_maps": [], "veto_actions": [],
                        "teams": [{"name": team, "team_id": (teams.get(normalize_team(team)) or {}).get("team_id") or f"mirror-team:{mid[:6]}a", "slug": normalize_team(team).replace(" ", "-")},
                                  {"name": opp, "team_id": (teams.get(normalize_team(opp)) or {}).get("team_id") or f"mirror-team:{mid[:6]}b", "slug": normalize_team(opp).replace(" ", "-")}],
                        "lineup_names": [], "lineup_groups": [], "lineup_source": "Underdog matchup; lineup unconfirmed",
                        "provider": "Underdog + Jina batch mirror", "updated_at": now_iso()})
    return {"schema_version": max(V48_BRIDGE_SCHEMA, 3), "generated_at": now_iso(), "profiles": profiles,
            "matches": matches or list(previous.get("matches") or []), "teams": teams,
            "source_status": {"jina_batch_mirror": status}, "refreshed_profiles": len(fresh)}


# Replace the four blocked HLTV table pulls with four batch-mirror tables.
def fetch_hltv_player_table(days: int) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    return _v49_fetch_table(days, force=False)


_v49_bridge_profile_previous = _v48_bridge_profile

def _v48_bridge_profile(player: str, team_hint: str = "") -> Tuple[Optional[PlayerStats], Dict[str, Any]]:
    candidates = _v48_bridge_candidates(player)
    for score, row in candidates:
        row_team = str(row.get("team") or "")
        teams = [row_team] + list(row.get("teams") or [])
        exact_name = normalize_name(row.get("player") or row.get("nickname") or "") == normalize_name(player)
        team_ok = not team_hint or not any(teams) or max([name_similarity(team_hint, x) for x in teams if x] or [0]) >= .72
        # A verified exact nickname/player-id match can outlive a transfer. In that case the current
        # Underdog team is accepted only as current context and the row is capped at Track.
        if score < .88 or (not team_ok and not exact_name):
            continue
        label = str(row.get("provider") or "Verified provider profile")
        profile, meta = _v48_profile_record_to_playerstats(player, row, label)
        if profile is not None:
            derived = str(row.get("kpr_source") or "").startswith("hltv_batch")
            warnings = list(profile.data_warnings or [])
            if derived and "BATCH AGGREGATE KPR — TRACK UNTIL EXACT/DEMO ENRICHMENT" not in warnings:
                warnings.append("BATCH AGGREGATE KPR — TRACK UNTIL EXACT/DEMO ENRICHMENT")
            if exact_name and team_hint and not team_ok:
                profile.team = team_hint
                warnings.append("CURRENT TEAM TAKEN FROM UNDERDOG — AGGREGATE TABLE TEAM MAY BE HISTORICAL")
            profile.data_warnings = list(dict.fromkeys(warnings))
            return profile, {**meta, "match_score": score, "recovery_path": label,
                              "core_kpr_verified": True, "aggregate_kpr_derived": derived,
                              "team_context_overridden": bool(exact_name and team_hint and not team_ok),
                              "source_fresh": bool(meta.get("source_fresh"))}
    return _v49_bridge_profile_previous(player, team_hint)


_v49_prefetch_previous = v48_prefetch_provider_data

def v48_prefetch_provider_data(players: Sequence[str], force: bool = False) -> Dict[str, Any]:
    # Load a prebuilt bridge when present, then self-heal with the batch mirror.
    bridge, bridge_status = load_provider_bridge(force=False)
    fresh, mirror_status = _v49_generate_profiles(players, force=force)
    existing = dict(V48_RUNTIME.get("profiles") or {})
    for key, row in fresh.items():
        old = existing.get(key) or {}
        old_exact = str(old.get("kpr_source") or "") in {"real_kills_div_rounds", "hltv_reported_kpr", "bo3_reported_kpr", "demo_full_round_kpr"}
        if not old_exact or safe_int(row.get("maps"), 0) >= safe_int(old.get("maps"), 0):
            existing[key] = row
    V48_RUNTIME["profiles"] = existing
    mirror_teams = _v49_build_team_index(existing)
    runtime_teams = dict(V48_RUNTIME.get("teams") or {})
    for key, row in mirror_teams.items():
        runtime_teams.setdefault(key, row)
    V48_RUNTIME["teams"] = runtime_teams
    V48_RUNTIME["bridge"] = {"schema_version": 3, "generated_at": now_iso(), "profiles": existing,
                             "matches": list(V48_RUNTIME.get("matches") or []), "teams": runtime_teams,
                             "source_status": {"jina_batch_mirror": mirror_status, "prebuilt_bridge": bridge_status}}
    missing = []
    for player in list(dict.fromkeys(str(x or "").strip() for x in players if str(x or "").strip())):
        local, _ = _playerstats_from_local(player)
        profile, _ = _v48_bridge_profile(player)
        if local is None and profile is None:
            missing.append(player)
    direct_status = {"ok": False, "disabled": True, "requested": len(missing),
                     "warning": "Per-player BO3 requests disabled; batch mirror prevents 403/429 request storms"}
    if missing and V49_ALLOW_BO3_LAST_RESORT:
        try:
            direct_status = _v49_prefetch_previous(missing, force=False).get("direct") or direct_status
        except Exception as exc:
            direct_status = {"ok": False, "disabled": False, "warning": str(exc), "requested": len(missing)}
    _trip_source_circuit("bo3_api", "disabled in v4.9: batch mirror is primary", 24*3600)
    V49_RUNTIME["last_prefetch"] = {"mirror": mirror_status, "bridge": bridge_status, "missing": len(missing)}
    return {"bridge": bridge_status, "mirror": mirror_status, "direct": direct_status,
            "unique_players": len(set(normalize_name(x) for x in players if str(x or "").strip())),
            "missing_after_mirror": len(missing)}


def _v49_synthetic_match(prop: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    team = str(prop.get("team") or "").strip(); opp = str(prop.get("opponent") or "").strip()
    if not team or not opp:
        return "", {}
    start = str(prop.get("start_time") or "")
    mid = hashlib.sha1(f"{normalize_team(team)}|{normalize_team(opp)}|{start[:16]}".encode()).hexdigest()[:18]
    team_rec = (V48_RUNTIME.get("teams") or {}).get(normalize_team(team), {})
    opp_rec = (V48_RUNTIME.get("teams") or {}).get(normalize_team(opp), {})
    row = {
        "provider_match_id": f"ud-{mid}", "match_id": f"ud-{mid}", "start_time": start,
        "format": "BO3", "event": str(prop.get("matchup") or "Underdog CS2 matchup"), "stage": "",
        "event_tier": "LOW/UNKNOWN", "environment": "UNKNOWN", "confirmed_maps": [], "veto_actions": [],
        "teams": [
            {"name": team, "team_id": team_rec.get("team_id") or f"mirror-team:{hashlib.sha1(normalize_team(team).encode()).hexdigest()[:12]}", "slug": normalize_team(team).replace(" ", "-")},
            {"name": opp, "team_id": opp_rec.get("team_id") or f"mirror-team:{hashlib.sha1(normalize_team(opp).encode()).hexdigest()[:12]}", "slug": normalize_team(opp).replace(" ", "-")},
        ],
        "lineup_names": [], "lineup_groups": [], "lineup_source": "Underdog matchup only — lineup unconfirmed",
        "provider": "Underdog matchup + Jina batch stats", "updated_at": now_iso(),
    }
    V49_RUNTIME["synthetic_matches"][f"ud-{mid}"] = row
    return f"mirror://ud-{mid}", row


_v49_attach_identity_previous = _v48_attach_identity

def _v48_attach_identity(prop: Dict[str, Any]) -> Dict[str, Any]:
    out = _v49_attach_identity_previous(prop)
    if not out.get("match_url"):
        url, _ = _v49_synthetic_match(out)
        if url:
            out["match_url"] = url
    return out


_v49_fetch_match_context_previous = fetch_match_context

def fetch_match_context(match_url: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if str(match_url).startswith("mirror://"):
        mid = str(match_url).split("mirror://", 1)[1].strip("/")
        row = V49_RUNTIME["synthetic_matches"].get(mid)
        if row:
            context = _v48_match_context_from_record(row, match_url)
            context["provider"] = "Underdog matchup + Jina batch mirror"
            context["lineup_names"] = []
            context["lineup_groups"] = []
            context["lineup_source"] = "unconfirmed"
            return context, {"ok": True, "provider": context["provider"], "age_seconds": 0,
                             "exact_lineup": False, "match_id": mid, "synthetic_match_context": True}
        return {}, {"ok": False, "provider": "Jina batch mirror", "warning": "synthetic match context missing"}
    return _v49_fetch_match_context_previous(match_url)


_v49_match_id_previous = _match_id_from_url

def _match_id_from_url(url: str) -> str:
    if str(url).startswith("mirror://"):
        return str(url).split("mirror://", 1)[1].strip("/")
    return _v49_match_id_previous(url)


_v49_team_model_previous = build_team_deep_profile

def build_team_deep_profile(team_id: str, slug: str, team_name: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    row = (V48_RUNTIME.get("teams") or {}).get(normalize_team(team_name))
    if isinstance(row, dict) and "Jina HLTV batch" in str(row.get("provider") or ""):
        profile = dict(row)
        profile.setdefault("team_id", team_id or row.get("team_id"))
        profile.setdefault("slug", slug or row.get("slug"))
        return profile, {"ok": True, "provider": row.get("provider"), "roster_fresh": False,
                         "pool_fresh": False, "team_map_fresh": False,
                         "warning": "team associations are aggregate candidates only; lineup and map pool remain unconfirmed"}
    return _v49_team_model_previous(team_id, slug, team_name)


_v49_build_board_previous = build_full_board

def build_full_board(props: List[Dict[str, Any]], deep_enabled: bool = True) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    board, status = _v49_build_board_previous(props, deep_enabled)
    profiles = sum((safe_int(r.get("profile_maps"), 0) or 0) > 0 for r in board)
    projections = sum(r.get("projection") is not None for r in board)
    matches = sum(bool(r.get("match_url")) for r in board)
    mirror_status = V49_RUNTIME.get("last_prefetch") or {}
    recovery = {
        "lines_loaded": len(board), "verified_profiles": profiles, "matched_teams": sum(bool(r.get("team")) for r in board),
        "matched_events": matches, "projections_generated": projections,
        "profile_coverage_pct": round(profiles / max(len(board), 1) * 100, 1),
        "match_coverage_pct": round(matches / max(len(board), 1) * 100, 1),
        "projection_coverage_pct": round(projections / max(len(board), 1) * 100, 1),
        "primary_path": "Jina batch mirror → persistent database/demo → optional prebuilt bridge",
        "mirror_status": mirror_status,
        "message": ("Batch mirror profiles reached the model. Unconfirmed lineup/veto rows remain Track/Pass until stronger context arrives."
                    if projections else "Underdog lines loaded, but the batch mirror and local database returned no usable player profiles."),
    }
    for row in board:
        row["model_version"] = MODEL_VERSION; row["feature_fingerprint"] = MODEL_SCHEMA_FINGERPRINT
        flags = [x for x in list(row.get("flags") or []) if "VERIFIED DATA BRIDGE EMPTY" not in str(x)]
        if "Jina HLTV batch" in str(row.get("profile_source") or ""):
            flags.append("BATCH PROFILE — LINEUP/VETO UNCONFIRMED")
            if row.get("status") in {"OFFICIAL", "PLAYABLE"}:
                row["status"] = "TRACK"; row["status_label"] = "⚠️ TRACK — BATCH PROFILE / PRE-VETO"
        row["flags"] = list(dict.fromkeys(flags))
    return board, {**status, "v49_source_recovery": recovery, "v48_source_recovery": recovery,
                   "v47_provider_recovery": recovery, "v46_source_recovery": recovery,
                   "model_version": MODEL_VERSION, "feature_fingerprint": MODEL_SCHEMA_FINGERPRINT}


# PrizePicks is optional consensus only. Stop request storms after one 403/429.

# ============================================================
# V5.0 DIRECT BO3 PROFILE CACHE — SINGLE-FILE SOURCE RECOVERY
# ============================================================
# V4.9's Jina mirror could be paused even though BO3 public player pages were
# available. V5.0 removes the mirror as a requirement. It loads verified BO3
# player pages in a controlled progressive batch, saves every successful
# profile to the persistent database, and reuses those profiles on future
# refreshes. No league-average KPR is used to create a projection.

APP_VERSION = "CS2 v5.0 — DIRECT BO3 PROFILE CACHE + PROGRESSIVE BOARD RECOVERY"
MODEL_VERSION = "OWP_CS2_KILLS_M12_5.0"
MODEL_SCHEMA_FINGERPRINT = "v50_direct_bo3_public_pages_progressive_cache_schema1"
SOURCE_RECOVERY_VERSION = "5.0"

V50_MAX_NEW_PROFILES = max(10, min(180, int(float(os.getenv("CS2_BO3_PROFILES_PER_REFRESH", "120") or 120))))
V50_WORKERS = max(1, min(10, int(float(os.getenv("CS2_BO3_PROFILE_WORKERS", "6") or 6))))
V50_PROFILE_TTL = max(3600, min(7 * 86400, int(float(os.getenv("CS2_BO3_PROFILE_TTL", "43200") or 43200))))
V50_INDEX_TTL = max(3600, min(14 * 86400, int(float(os.getenv("CS2_BO3_INDEX_TTL", "86400") or 86400))))
V50_INDEX_FILE = os.path.join(STORAGE_DIR, "cs2_bo3_player_index_v50.json")
V50_STATUS: Dict[str, Any] = {"provider": "BO3 direct public pages", "loaded": 0, "failed": 0, "remaining": 0}
V50_FETCH_LOCK = threading.Lock()


def _v50_record_from_profile(profile: PlayerStats, meta: Dict[str, Any]) -> Dict[str, Any]:
    map_profiles = dict(meta.get("player_map_profiles") or {})
    return {
        "player": profile.player,
        "nickname": profile.player,
        "player_id": profile.player_id,
        "slug": profile.slug,
        "team": profile.team,
        "maps": int(profile.maps or 0),
        "profile_maps": int(profile.maps or 0),
        "rounds": int(profile.rounds or 0),
        "profile_rounds": int(profile.rounds or 0),
        "kills": int(profile.kills or 0),
        "deaths": int(profile.deaths or 0),
        "kpr": float(profile.kpr),
        "base_kpr": float(profile.kpr),
        "dpr": float(profile.dpr),
        "adr": float(profile.adr),
        "rating": float(profile.rating),
        "kd": float(profile.kd),
        "hs_pct": profile.hs_pct,
        "opening_kpr": profile.opening_kpr,
        "ct_kpr": profile.ct_kpr,
        "t_kpr": profile.t_kpr,
        "player_map_profiles": map_profiles,
        "profile_source": profile.source,
        "profile_href": profile.href,
        "provider": "BO3 public professional statistics",
        "kpr_source": "bo3_reported_kpr",
        "profile_warnings": list(profile.data_warnings or []),
        "identity_ids": {"player_id": profile.player_id},
        "updated_at": now_iso(),
        "generated_at": now_iso(),
    }


def _v50_store_runtime_profile(profile: PlayerStats, meta: Dict[str, Any]) -> Dict[str, Any]:
    record = _v50_record_from_profile(profile, meta)
    key = normalize_name(profile.player)
    with V50_FETCH_LOCK:
        V48_RUNTIME.setdefault("profiles", {})[key] = record
    upsert_database_record(PLAYER_DATABASE_FILE, key, record)
    try:
        sqlite_store_entity_snapshot("player", str(profile.player_id or key), record,
                                     "BO3 public professional statistics", 0, now_iso())
    except Exception:
        pass
    return record


def _v50_load_saved_profiles(players: Sequence[str]) -> int:
    loaded = 0
    runtime = V48_RUNTIME.setdefault("profiles", {})
    for player in players:
        key = normalize_name(player)
        if key in runtime:
            continue
        row = lookup_database_player(player)
        if not isinstance(row, dict):
            continue
        maps = safe_int(row.get("profile_maps") or row.get("maps"), 0) or 0
        kpr = safe_float(row.get("base_kpr") or row.get("kpr"), None)
        if maps >= V48_PROFILE_MIN_MAPS and kpr is not None and 0.25 <= kpr <= 1.25:
            runtime[key] = row
            loaded += 1
    return loaded


def _v50_index_age(payload: Dict[str, Any]) -> float:
    dt = _parse_iso_datetime(payload.get("updated_at")) if isinstance(payload, dict) else None
    return (datetime.now(timezone.utc) - dt).total_seconds() if dt else 10**12


def _v50_build_bo3_index(force: bool = False) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    cached = load_json(V50_INDEX_FILE, {})
    if isinstance(cached, dict) and isinstance(cached.get("players"), dict) and not force and _v50_index_age(cached) <= V50_INDEX_TTL:
        return cached["players"], {"ok": True, "provider": "persistent BO3 identity index", "rows": len(cached["players"]), "age_seconds": _v50_index_age(cached)}

    # The filters endpoint can return a broad identity list in a few requests.
    # If it is unavailable, direct nickname slugs are still attempted.
    index: Dict[str, Dict[str, Any]] = {}
    statuses: List[Dict[str, Any]] = []
    _close_source_circuit("bo3")
    _close_source_circuit("bo3_api")
    for offset in [0, 250, 500, 750]:
        payload, status = _bo3_get_json("/filters/players", "BO3 bulk player identity index", {
            "page[offset]": str(offset),
            "page[limit]": "250",
            "filter[discipline_id][eq]": "1",
            "with": "country",
        }, ttl=V50_INDEX_TTL, allow_stale=True)
        statuses.append(status)
        rows = _bo3_payload_data(payload)
        if not rows:
            break
        for item in rows:
            a = attrs(item)
            name = str(_bo3_scalar(a, ["nickname", "nick_name", "display_name", "player_name", "name"], "") or "").strip()
            slug = str(_bo3_scalar(a, ["slug"], "") or "").strip().lower()
            pid = str(object_id(item) or _bo3_scalar(a, ["id", "player_id"], "") or "")
            if not name or not slug:
                continue
            rec = {"player": name, "nickname": name, "slug": slug, "player_id": pid}
            index[normalize_name(name)] = rec
            index.setdefault(normalize_name(slug.replace("-", " ")), rec)
        if len(rows) < 250:
            break
        if _source_circuit_open("bo3"):
            break
    if index:
        save_json(V50_INDEX_FILE, {"updated_at": now_iso(), "players": index}, force=True)
        return index, {"ok": True, "provider": "BO3 bulk player identity index", "rows": len(index), "statuses": statuses}
    if isinstance(cached, dict) and isinstance(cached.get("players"), dict):
        return cached["players"], {"ok": True, "provider": "stale persistent BO3 identity index", "rows": len(cached["players"]), "warning": "live identity index unavailable"}
    return {}, {"ok": False, "provider": "BO3 identity index", "rows": 0, "statuses": statuses,
                "warning": "BO3 identity index unavailable; direct nickname slugs will be used"}


def _v50_slug_list(player: str, index: Dict[str, Dict[str, Any]]) -> List[str]:
    alias = _alias_record(player)
    key = normalize_name(player)
    raw: List[Any] = []
    if key in index:
        raw.append(index[key].get("slug"))
    raw.extend(_bo3_slug_candidates(player, alias))
    compact = re.sub(r"[^a-z0-9]", "", key)
    raw.extend([compact, compact.replace("_", "-")])
    # A small number of provider slugs omit a trailing character (for example
    # 1NVISIBLEE -> 1nvisible). This is only tried after the exact slug.
    if len(compact) >= 6 and compact.endswith("e"):
        raw.append(compact[:-1])
    return list(dict.fromkeys(str(x or "").strip().lower() for x in raw if str(x or "").strip()))[:6]


def _v50_fetch_direct_profile(player: str, index: Dict[str, Dict[str, Any]]) -> Tuple[str, Optional[PlayerStats], Dict[str, Any]]:
    if _source_circuit_open("bo3"):
        return player, None, {"ok": False, "circuit_open": True, "warning": "BO3 provider circuit is open"}
    last_meta: Dict[str, Any] = {}
    for slug in _v50_slug_list(player, index):
        url = f"{BO3_WEB_BASE}/players/{quote(slug)}"
        page, status = http_get_text(url, "BO3 direct player profile", ttl=V50_PROFILE_TTL,
                                     headers=_BO3_HEADERS, timeout=24, allow_stale=True,
                                     stale_ttl=BO3_PROFILE_MAX_AGE)
        last_meta = status
        if not page:
            if status.get("circuit_open"):
                break
            continue
        # Verify the page identity before accepting the reported KPR.
        title = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
        title_text = strip_tags(title.group(1)) if title else ""
        title_name = re.split(r"\s*\(|\s+CS2\s+Stats", title_text, maxsplit=1, flags=re.I)[0].strip()
        if title_name and name_similarity(player, title_name) < .72 and name_similarity(player, slug.replace("-", " ")) < .82:
            continue
        profile, meta = _bo3_parse_player_html(page, player, slug, url)
        if profile is not None and int(profile.maps or 0) >= V48_PROFILE_MIN_MAPS:
            profile.team = profile.team or ""
            return player, profile, {**meta, "http_status": status, "recovery_path": "BO3 direct public page"}
    # One API search is used only after direct slugs fail. This avoids the old
    # one-search-plus-three-stat-requests storm for every player.
    if not _source_circuit_open("bo3"):
        try:
            profile, meta = _bo3_profile_from_api(player, _alias_record(player))
            if profile is not None and int(profile.maps or 0) >= V48_PROFILE_MIN_MAPS:
                return player, profile, {**meta, "recovery_path": "BO3 API search fallback"}
            last_meta = meta
        except Exception as exc:
            last_meta = {"ok": False, "warning": str(exc)}
    return player, None, last_meta


def _v50_profile_available(player: str) -> bool:
    local, _ = _playerstats_from_local(player)
    if local is not None and int(local.maps or 0) >= V48_PROFILE_MIN_MAPS:
        return True
    prof, _ = _v48_profile_record_to_playerstats(player, (V48_RUNTIME.get("profiles") or {}).get(normalize_name(player), {}), "BO3 cached profile")
    return prof is not None


def v48_prefetch_provider_data(players: Sequence[str], force: bool = False) -> Dict[str, Any]:
    unique = list(dict.fromkeys(str(x or "").strip() for x in players if str(x or "").strip()))
    _close_source_circuit("jina_mirror")
    _close_source_circuit("bo3_api")
    # A BO3 block is short-lived. The user-facing reset button also clears it.
    if force:
        _close_source_circuit("bo3")

    loaded_saved = _v50_load_saved_profiles(unique)
    index, index_status = _v50_build_bo3_index(force=False)
    missing = [p for p in unique if not _v50_profile_available(p)]
    selected = missing[:V50_MAX_NEW_PROFILES]
    successes = 0
    failures: List[Dict[str, Any]] = []
    if selected and not _source_circuit_open("bo3"):
        with ThreadPoolExecutor(max_workers=min(V50_WORKERS, len(selected))) as executor:
            futures = [executor.submit(_v50_fetch_direct_profile, player, index) for player in selected]
            for future in as_completed(futures):
                try:
                    player, profile, meta = future.result()
                except Exception as exc:
                    failures.append({"player": "", "warning": str(exc)})
                    continue
                if profile is not None:
                    _v50_store_runtime_profile(profile, meta)
                    successes += 1
                else:
                    failures.append({"player": player, "warning": str(meta.get("warning") or "profile unavailable")})

    # Re-load any profile written by another concurrent worker and build a team
    # index for the synthetic Underdog match context.
    _v50_load_saved_profiles(unique)
    runtime_profiles = dict(V48_RUNTIME.get("profiles") or {})
    runtime_teams = dict(V48_RUNTIME.get("teams") or {})
    for key, value in _v49_build_team_index(runtime_profiles).items():
        runtime_teams.setdefault(key, value)
    V48_RUNTIME["teams"] = runtime_teams
    V48_RUNTIME["bridge"] = {
        "schema_version": 5,
        "generated_at": now_iso(),
        "profiles": runtime_profiles,
        "teams": runtime_teams,
        "matches": list(V48_RUNTIME.get("matches") or []),
        "source_status": {"bo3_direct_profiles": {"ok": successes > 0 or bool(runtime_profiles), "loaded_now": successes}},
    }

    covered = sum(_v50_profile_available(p) for p in unique)
    remaining = max(0, len(unique) - covered)
    status = {
        "ok": covered > 0,
        "provider": "BO3 direct public profile cache",
        "unique_players": len(unique),
        "verified_profiles": covered,
        "loaded_from_saved_cache": loaded_saved,
        "loaded_this_refresh": successes,
        "attempted_this_refresh": len(selected),
        "remaining": remaining,
        "progressive_limit": V50_MAX_NEW_PROFILES,
        "index": index_status,
        "provider_circuit_open": _source_circuit_open("bo3"),
        "failures_sample": failures[:12],
        "message": (f"Loaded {covered}/{len(unique)} verified player profiles. " +
                    ("Refresh again to continue building the cache." if remaining else "Profile cache is complete for this board.")),
    }
    global V50_STATUS
    V50_STATUS = status
    V49_RUNTIME["statuses"] = {"bo3_direct_profiles": {"rows": covered, **status}}
    V49_RUNTIME["last_prefetch"] = status
    return {"direct_bo3": status, "unique_players": len(unique), "missing_after_direct": remaining}


def fetch_hltv_player_table(days: int) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    # Global HLTV/Jina table pulls are no longer required. Player profiles come
    # from BO3 public pages and persistent cache.
    return {}, {"ok": False, "disabled": True, "provider": "HLTV/Jina", "rows": 0,
                "warning": "disabled in v5.0; BO3 direct profile cache is primary"}


def _v48_bridge_profile(player: str, team_hint: str = "") -> Tuple[Optional[PlayerStats], Dict[str, Any]]:
    # First use exact persistent/runtime records.
    candidates = _v48_bridge_candidates(player)
    for score, row in candidates:
        if score < .86:
            continue
        profile, meta = _v48_profile_record_to_playerstats(player, row, str(row.get("provider") or "BO3 cached profile"))
        if profile is None:
            continue
        row_team = str(row.get("team") or "")
        exact = normalize_name(row.get("player") or row.get("nickname") or "") == normalize_name(player)
        if team_hint and row_team and not _team_name_matches(team_hint, row_team) and not exact:
            continue
        if team_hint and exact and (not row_team or not _team_name_matches(team_hint, row_team)):
            profile.team = team_hint
            profile.data_warnings = list(dict.fromkeys(list(profile.data_warnings or []) + ["CURRENT TEAM FROM UNDERDOG; PROVIDER TEAM MAY BE HISTORICAL"]))
        return profile, {**meta, "match_score": score, "recovery_path": "BO3 persistent/runtime cache", "core_kpr_verified": True}
    local, local_meta = _playerstats_from_local(player)
    if local is not None:
        if team_hint and not local.team:
            local.team = team_hint
        return local, {**local_meta, "recovery_path": "verified local/demo profile", "core_kpr_verified": True}
    return None, {"matched": False, "source_fresh": False, "core_kpr_verified": False,
                  "warning": "Player has not been loaded into the progressive BO3 cache yet"}


def build_player_profile(player_name: str, long_table: Dict[str, Dict[str, Any]], medium_table: Dict[str, Dict[str, Any]],
                         recent30_table: Dict[str, Dict[str, Any]], recent15_table: Dict[str, Dict[str, Any]]) -> Tuple[PlayerStats, Dict[str, Any]]:
    profile, meta = _v48_bridge_profile(player_name)
    if profile is not None and int(profile.maps or 0) >= V48_PROFILE_MIN_MAPS:
        return profile, meta
    empty = PlayerStats(player=player_name, maps=0, rounds=0, kpr=LEAGUE_KPR, source="")
    return empty, {"matched": False, "source_fresh": False, "core_kpr_verified": False,
                   "recovery_path": "progressive cache pending",
                   "source_failure": "Verified BO3/local/demo player profile is not loaded yet"}


_v50_build_full_board_base = build_full_board

def build_full_board(props: List[Dict[str, Any]], deep_enabled: bool = True) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    board, status = _v50_build_full_board_base(props, deep_enabled)
    recovery = status.get("v48_source_recovery") or {}
    recovery["v50_direct_bo3"] = dict(V50_STATUS)
    recovery["primary_path"] = "BO3 public player pages → persistent database/demo → conservative synthetic match context"
    recovery["message"] = V50_STATUS.get("message") or recovery.get("message")
    status["v50_source_recovery"] = recovery
    status["v48_source_recovery"] = recovery
    for row in board:
        row["model_version"] = MODEL_VERSION
        row["feature_fingerprint"] = MODEL_SCHEMA_FINGERPRINT
    return board, status


_v49_prizepicks_previous = fetch_prizepicks_cs2_board

def fetch_prizepicks_cs2_board() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if _source_circuit_open("prizepicks"):
        return [], {"ok": False, "provider": "PrizePicks", "paused": True,
                    "warning": "PrizePicks paused after 403/429; it is not required for projections"}
    rows, meta = _v49_prizepicks_previous()
    raw_status = ((meta.get("status") or {}).get("status") if isinstance(meta.get("status"), dict) else None)
    if raw_status in {403, 429}:
        _trip_source_circuit("prizepicks", f"HTTP {raw_status}", 30*60)
    return rows, meta


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
    use_prizepicks = st.checkbox("Use PrizePicks for free market consensus", value=False)
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
  <div class="sub-title">Maps 1–2 Kills · Direct BO3 profiles · Persistent cache · Demo center · Neutral calibration</div>
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
    with st.spinner("Pulling exact Underdog markets, map/side form, vetoes, roster timelines, opponent/SOS data, calibration, and correlated simulations..."):
        props, source_status = load_real_props(use_underdog, use_prizepicks, show_prizepicks_rows)
        update_line_history(props)
        board, board_status = build_full_board(props, deep_enabled=deep_data_enabled)
        st.session_state["cs2_board"] = board
        save_asof_projection_history(board, source_status)
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

_source_circuit=(st.session_state.get("cs2_board_status") or {}).get("source_circuit_breaker") or {}
if _source_circuit and not _source_circuit.get("official_enabled",False):
    st.warning(f"SOURCE {_source_circuit.get('grade','DEGRADED')} — Official picks are disabled until player, match-ID, freshness, and parser health recover. Health {_source_circuit.get('score',0):.1f}/100.")
elif _source_circuit:
    st.success(f"Source circuit healthy: {_source_circuit.get('score',0):.1f}/100.")

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

tab_live, tab_official, tab_saved, tab_grade, tab_calibration, tab_special, tab_slip, tab_livewatch, tab_bankroll, tab_data, tab_debug = st.tabs([
    "🎯 Live Projections",
    "🔥 Official Board",
    "📌 Saved Board",
    "✅ Grading + Learning",
    "📊 Calibration",
    "🧪 Specialized Markets",
    "🧩 Slip Correlation",
    "📡 Live Watch",
    "💵 Bankroll + Odds",
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
            save_calibration_state()
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
    if st.button("🧾 STAGE MANUAL RESULTS FOR REVIEW", use_container_width=True):
        out = grade_from_manual_dataframe(result_df, overwrite=overwrite_results)
        if out.get("staged"):
            st.success(f"Staged {out['staged']} rows for review; unmatched {out['unmatched']}.")
        else:
            st.warning(str(out))
    pending_manual = _manual_review_rows("PENDING")
    if pending_manual:
        st.subheader("Manual grade review queue")
        review_df = pd.DataFrame([{
            "Select": True, "Review ID": x.get("review_id"), "Player": x.get("player"),
            "Line": x.get("line"), "Actual Kills": x.get("actual_kills"),
            "Lean": (x.get("payload") or {}).get("snapshot",{}).get("lean"),
            "Match": (x.get("payload") or {}).get("snapshot",{}).get("matchup"),
        } for x in pending_manual])
        edited = st.data_editor(review_df, hide_index=True, use_container_width=True, key="manual_grade_review_editor")
        review_note = st.text_input("Manual grade review note", "Verified against official result")
        selected_ids = edited.loc[edited["Select"] == True, "Review ID"].astype(str).tolist() if not edited.empty else []
        c_review1,c_review2=st.columns(2)
        with c_review1:
            if st.button("APPROVE SELECTED MANUAL GRADES", use_container_width=True, type="primary"):
                out=approve_manual_grade_reviews(selected_ids,review_note);st.success(f"Approved {out['approved']}; skipped {out['skipped']}.");st.rerun()
        with c_review2:
            if st.button("REJECT SELECTED MANUAL GRADES", use_container_width=True):
                st.warning(f"Rejected {reject_manual_grade_reviews(selected_ids,review_note)} manual grades.");st.rerun()
    else:
        st.caption("No manual grades are waiting for review.")

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

with tab_calibration:
    st.markdown('<div class="section-title-pro">Probability Calibration + Walk-Forward Backtest</div>', unsafe_allow_html=True)
    state = save_calibration_state()
    ins = state.get("in_sample") or {}
    wf = state.get("walk_forward") or {}
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Graded Sample", ins.get("n", 0))
    c2.metric("Brier Score", f"{ins.get('brier'):.4f}" if ins.get("brier") is not None else "—")
    c3.metric("Log Loss", f"{ins.get('log_loss'):.4f}" if ins.get("log_loss") is not None else "—")
    c4.metric("Calibration Gap", f"{ins.get('ece'):.3f}" if ins.get("ece") is not None else "—")
    st.caption("Official probability calibration is considered ready only after at least 50 comparable graded rows. Walk-forward testing uses only earlier rows to predict later rows.")
    if ins.get("bins"):
        st.dataframe(pd.DataFrame(ins["bins"]), use_container_width=True, hide_index=True)
    st.subheader("Chronological walk-forward results")
    if wf.get("n"):
        w1, w2, w3 = st.columns(3)
        w1.metric("Test Rows", wf["n"])
        w2.metric("Walk-Forward Brier", f"{wf['brier']:.4f}")
        w3.metric("Walk-Forward Hit Rate", f"{wf['hit_rate']*100:.1f}%")
        st.dataframe(pd.DataFrame(wf.get("records", [])[-250:]), use_container_width=True, hide_index=True)
    else:
        st.info(wf.get("message", "More chronological graded projections are required."))
    history_rows = _read_jsonl(HISTORICAL_ASOF_FILE, 5000)
    st.metric("As-of Projection Records", len(history_rows))
    if history_rows:
        st.download_button("Download as-of history", pd.DataFrame(history_rows).to_csv(index=False).encode(), "cs2_asof_history.csv", "text/csv", use_container_width=True)
    st.markdown('<div class="section-title-pro">Direction Bias + Backtesting Dashboard</div>', unsafe_allow_html=True)
    bias_report = direction_bias_report()
    neutral = bias_report.get("neutrality") or {}
    b1,b2,b3,b4=st.columns(4)
    b1.metric("Over Bias", f"{neutral.get('over_bias'):+.2f}" if neutral.get('over_bias') is not None else "—")
    b2.metric("Under Bias", f"{neutral.get('under_bias'):+.2f}" if neutral.get('under_bias') is not None else "—")
    b3.metric("Direction Bias Gap", f"{neutral.get('bias_gap'):.2f}" if neutral.get('bias_gap') is not None else "—")
    b4.metric("Direction Neutrality", "BALANCED" if neutral.get("balanced") else "BUILDING")
    for section in ["direction","status","veto_state","event_tier","role","maps"]:
        rows_section=[]
        for key,val in (bias_report.get(section) or {}).items(): rows_section.append({"Group":key,**val})
        if rows_section:
            with st.expander(section.replace("_"," ").title(), expanded=section=="direction"):
                st.dataframe(pd.DataFrame(rows_section),use_container_width=True,hide_index=True)

with tab_special:
    st.markdown('<div class="section-title-pro">Specialized Market Research</div>', unsafe_allow_html=True)
    st.caption("These secondary markets remain Research/Track Only until each market has its own graded calibration sample.")
    rows = []
    for item in board:
        hs = headshot_prop_projection(item)
        m3 = maps_over_25_probability(item)
        rows.append({
            "Player": item.get("player"), "Matchup": f"{item.get('team')} vs {item.get('opponent')}",
            "Kills Projection": item.get("projection"), "Headshot Projection": round(hs.get("projection"), 2) if hs.get("projection") is not None else None,
            "Observed/Shrunk HS Rate": round((hs.get("hs_rate") or 0)*100, 1) if hs.get("hs_rate") is not None else None,
            "Role": item.get("role"), "AWP Share": item.get("awp_kill_share"),
            "Over 2.5 Maps Probability": round((m3.get("probability") or 0)*100, 1) if m3.get("probability") is not None else None,
            "Veto State": item.get("veto_state"), "Research Only": True,
        })
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("Refresh a real board first.")

with tab_slip:
    st.markdown('<div class="section-title-pro">Correlation-Aware Slip Builder</div>', unsafe_allow_html=True)
    eligible = [x for x in board if x.get("status") in {"OFFICIAL", "PLAYABLE"} and x.get("probability")]
    options = {f"{x.get('player')} {x.get('lean')} {x.get('line')} · {x.get('team')} vs {x.get('opponent')}": x for x in eligible}
    chosen = st.multiselect("Select 2–6 legs", list(options), max_selections=6)
    selected = [options[x] for x in chosen]
    if len(selected) >= 2:
        result = simulate_joint_slip(selected)
        c1, c2, c3 = st.columns(3)
        c1.metric("Joint Hit Probability", f"{result['joint_probability']*100:.2f}%")
        c2.metric("Independent Product", f"{result['independent_probability']*100:.2f}%")
        c3.metric("Correlation Effect", f"{result['correlation_adjustment']*100:+.2f}%")
        names = [x.get("player") for x in selected]
        st.dataframe(pd.DataFrame(result["correlation_matrix"], index=names, columns=names), use_container_width=True)
        if st.button("SAVE SLIP RESEARCH SNAPSHOT", use_container_width=True):
            logs = load_json(SLIP_HISTORY_FILE, [])
            logs = logs if isinstance(logs, list) else []
            logs.append({"saved_at": now_iso(), "legs": [{k:x.get(k) for k in ["player","line","lean","probability","team","opponent","prop_id"]} for x in selected], **result})
            save_json(SLIP_HISTORY_FILE, logs[-2000:])
            st.success("Slip research snapshot saved.")
    else:
        st.info("Select at least two Official/Playable legs. Same-match and same-team dependencies are simulated instead of multiplying probabilities blindly.")

with tab_livewatch:
    st.markdown('<div class="section-title-pro">Live Match Watch</div>', unsafe_allow_html=True)
    st.warning("This is an informational live pace tool—not an automatic live-betting instruction. Confirm the official score, economy, lineup and market before acting.")
    live_options = {f"{x.get('player')} · {x.get('team')} vs {x.get('opponent')}": x for x in board if x.get("projection") is not None}
    selected_name = st.selectbox("Prematch projection", [""] + list(live_options))
    if selected_name:
        row = live_options[selected_name]
        l1, l2, l3, l4 = st.columns(4)
        current_kills = l1.number_input("Current player kills", min_value=0, value=0, step=1)
        rounds_played = l2.number_input("Rounds completed", min_value=1, value=6, step=1)
        score_a = l3.number_input("Team score", min_value=0, value=3, step=1)
        score_b = l4.number_input("Opponent score", min_value=0, value=3, step=1)
        live = live_watch_projection(row.get("player"), int(current_kills), int(rounds_played), int(score_a), int(score_b), row)
        if live.get("ok"):
            c1, c2, c3 = st.columns(3)
            c1.metric("Live KPR", f"{live['live_kpr']:.3f}")
            c2.metric("Blended KPR", f"{live['blended_kpr']:.3f}")
            c3.metric("Updated Pace Projection", f"{live['live_projection']:.2f}")
            st.caption(live["note"])
            if st.button("SAVE LIVE WATCH SNAPSHOT", use_container_width=True):
                logs = load_json(LIVE_STATE_FILE, [])
                logs = logs if isinstance(logs, list) else []
                logs.append({"saved_at": now_iso(), "prop_id": row.get("prop_id"), **live})
                save_json(LIVE_STATE_FILE, logs[-5000:])
                st.success("Saved.")

with tab_bankroll:
    st.markdown('<div class="section-title-pro">Bankroll + Odds Shopping</div>', unsafe_allow_html=True)
    bankroll = st.number_input("Current bankroll", min_value=0.0, value=100.0, step=10.0)
    risk_pct = st.slider("Flat risk per play", 1.0, 3.0, 1.5, 0.1)
    pick_options = {f"{x.get('player')} {x.get('lean')} {x.get('line')}": x for x in board if x.get("probability")}
    pick_name = st.selectbox("Projection", [""] + list(pick_options), key="bankroll_pick")
    odds = st.number_input("Best available American odds", value=-110.0, step=5.0)
    if pick_name:
        rec = bankroll_recommendation(bankroll, pick_options[pick_name]["probability"], odds, risk_pct)
        c1, c2, c3 = st.columns(3)
        c1.metric("1–3% Flat Stake", f"${rec['flat_stake']:.2f}")
        c2.metric("Quarter Kelly Cap", f"${rec['quarter_kelly']:.2f}" if rec.get("quarter_kelly") is not None else "—")
        c3.metric("Model EV / $1", f"${rec['ev_per_dollar']:+.3f}" if rec.get("ev_per_dollar") is not None else "—")
        st.caption("Use the smaller of the flat-risk amount and quarter-Kelly amount. Never increase stake to chase losses.")
    st.subheader("Odds shopping table")
    odds_shop_text = st.text_area("Paste CSV: Player,Line,Lean,Book,American Odds", height=130, key="odds_shop_csv")
    if odds_shop_text.strip():
        try:
            odf = pd.read_csv(io.StringIO(odds_shop_text.strip()))
            st.dataframe(odf, use_container_width=True, hide_index=True)
            if st.button("SAVE ODDS SHOPPING HISTORY", use_container_width=True):
                st.success(f"Saved {save_book_odds_history(odf)} observations.")
        except Exception as exc:
            st.error(f"Odds table error: {exc}")


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

    st.markdown('<div class="section-title-pro">In-App Demo Center</div>', unsafe_allow_html=True)
    st.caption("Upload raw .dem files for in-app Awpy parsing, or upload Awpy kill-event CSV/JSON exports. Parsed data is stored on the Railway volume and used as controlled map/weapon/side history.")
    template=pd.DataFrame(columns=["player","map","match_id","match_rounds","round","headshot","weapon","side","opening","trade","team","opponent","opponent_rank","event_tier","event_time"])
    st.download_button("DOWNLOAD DEMO TELEMETRY CSV TEMPLATE",template.to_csv(index=False).encode("utf-8"),"cs2_demo_telemetry_template.csv","text/csv",use_container_width=True)
    st.caption("A valid KPR requires match_rounds/map_total_rounds. Kill-event rows without a full-map round denominator are used for role/headshot signals only, never KPR.")
    demo_files = st.file_uploader("Upload CS2 demos or telemetry exports", type=["dem", "csv", "json", "jsonl", "zip"], accept_multiple_files=True, key="demo_uploads")
    if st.button("PARSE + INGEST DEMO DATA", use_container_width=True):
        total = 0
        messages = []
        for f in demo_files or []:
            try:
                result=ingest_uploaded_demo_file(f)
                total += safe_int(result.get("added"),0) or 0
                for child in result.get("children",[]) if isinstance(result.get("children"),list) else []:
                    total += safe_int(child.get("added"),0) or 0
                messages.append({"file":f.name,**result})
            except Exception as exc:
                messages.append({"file":f.name,"ok":False,"message":str(exc)})
        st.success(f"Ingested {total} telemetry rows.") if total else st.warning("No telemetry rows were added.")
        st.write(messages)
    demo_db = load_json(DEMO_DATABASE_FILE, {})
    st.metric("Demo Map Profiles", len(demo_db) if isinstance(demo_db, dict) else 0)
    st.caption(f"Automatic demo dropbox: {DEMO_DROP_DIR}. A Railway collector/service can place .dem/.zip/CSV/JSONL files there; refresh automatically ingests new files once.")
    demo_status = {"events": _sqlite_count("demo_events"), "rounds": _sqlite_count("demo_rounds"), "profiles": len(demo_db) if isinstance(demo_db,dict) else 0}
    d1,d2,d3=st.columns(3);d1.metric("Demo Kill Events",demo_status["events"]);d2.metric("Demo Rounds",demo_status["rounds"]);d3.metric("Demo Profiles",demo_status["profiles"])
    st.caption("Demo data is inside the app workflow: upload here, or drop files into the Railway volume folder. Full-round demos immediately feed map KPR, CT/T, economy, role, and five-player allocation after refresh.")

    st.markdown('<div class="section-title-pro">Historical Backfill Importer</div>', unsafe_allow_html=True)
    st.caption("Import old team-map results, vetoes, starting sides, and roster events. Accepted CSV fields can include Team, Opponent, Map, Team Score, Opponent Score, Match ID, Date, Action, Started Side, Player, and Event Type.")
    backfill_file=st.file_uploader("Upload historical backfill CSV/JSON",type=["csv","json","jsonl"],key="v45_backfill_upload")
    if backfill_file is not None:
        try:
            raw_bytes=backfill_file.getvalue();name=backfill_file.name.lower()
            if name.endswith(".csv"): backfill_df=pd.read_csv(io.BytesIO(raw_bytes))
            elif name.endswith(".jsonl"): backfill_df=pd.read_json(io.BytesIO(raw_bytes),lines=True)
            else: backfill_df=pd.read_json(io.BytesIO(raw_bytes))
            st.dataframe(backfill_df.head(250),use_container_width=True,hide_index=True)
            if st.button("IMPORT HISTORICAL BACKFILL",use_container_width=True,type="primary"):
                st.success(ingest_historical_backfill(backfill_df,backfill_file.name));st.rerun()
        except Exception as exc: st.error(f"Backfill import error: {exc}")

    st.markdown('<div class="section-title-pro">Multi-Book Odds Consensus</div>', unsafe_allow_html=True)
    st.caption("Upload multiple books with Player, Line, Over Odds, Under Odds, Book, and optional Match ID. The model uses median no-vig probability and downgrades negative-value picks.")
    book_file=st.file_uploader("Upload multi-book odds CSV",type=["csv"],key="v45_multibook_upload")
    if book_file is not None:
        try:
            book_df=pd.read_csv(book_file);st.dataframe(book_df,use_container_width=True,hide_index=True)
            if st.button("SAVE MULTI-BOOK ODDS",use_container_width=True):st.success(save_multibook_odds_dataframe(book_df));st.rerun()
        except Exception as exc:st.error(f"Multi-book odds error: {exc}")

    st.markdown('<div class="section-title-pro">Patch / Map-Pool Eras</div>', unsafe_allow_html=True)
    patch_text = st.text_area("Patch era JSON", value=json.dumps(seed_patch_eras(), indent=2), height=220, key="patch_era_json")
    if st.button("SAVE PATCH ERA CONFIGURATION", use_container_width=True):
        try:
            parsed = json.loads(patch_text)
            if not isinstance(parsed, list) or not parsed:
                raise ValueError("A non-empty JSON list is required")
            save_json(PATCH_ERAS_FILE, parsed)
            st.success("Patch/map-pool eras saved. Refresh projections.")
        except Exception as exc:
            st.error(str(exc))

    st.markdown('<div class="section-title-pro">Player Profile Override CSV</div>', unsafe_allow_html=True)
    st.caption("Optional fallback for a player missing from public stats. Accepted fields: Player, Team, Maps, Rounds, KPR, DPR, ADR, Rating, KD, HS Pct.")
    st.subheader("Persistent CS2 Database")
    dbs = database_status()
    cols = st.columns(6)
    for col, key in zip(cols, ["players", "teams", "matches", "maps", "vetoes", "rosters"]):
        col.metric(key.title(), dbs.get(key, 0))
    st.caption("Only verified, matched records are stored. Default league values are never saved as player facts.")
    if st.button("Rebuild database index from saved projection exports", key="rebuild_db_index"):
        rebuilt = 0
        for path in Path(STORAGE_DIR).glob("*.csv"):
            try:
                frame = pd.read_csv(path)
                for rec in frame.to_dict("records"):
                    if int(rec.get("profile_maps") or 0) > 0:
                        save_projection_entities(rec)
                        rebuilt += 1
            except Exception:
                continue
        st.success(f"Indexed {rebuilt} verified projection records.")

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

    st.markdown('<div class="section-title-pro">Player / Team Mapping Recovery</div>', unsafe_allow_html=True)
    st.caption("Use this only when a public source cannot match an Underdog player. Save an exact HLTV player ID plus optional team, opponent, and match URL. The mapping persists on the Railway volume.")
    map_file=st.file_uploader("Upload player mapping CSV",type=["csv"],key="v46_mapping_upload")
    if map_file is not None:
        try:
            mdf=pd.read_csv(map_file);st.dataframe(mdf,use_container_width=True,hide_index=True)
            if st.button("SAVE PLAYER / MATCH MAPPINGS",use_container_width=True):
                cmap={normalize_name(c):c for c in mdf.columns};aliases=load_json(PLAYER_ALIAS_FILE,{})
                if not isinstance(aliases,dict):aliases={}
                saved=0
                for _,rr in mdf.iterrows():
                    pcol=cmap.get("player") or cmap.get("underdog player");player=str(rr.get(pcol,"")).strip() if pcol else ""
                    if not player:continue
                    def gv(*names):
                        for n in names:
                            c=cmap.get(normalize_name(n))
                            if c and rr.get(c) not in [None,""] and not pd.isna(rr.get(c)):return rr.get(c)
                        return ""
                    aliases[normalize_name(player)]={"alias":str(gv("HLTV Name","Alias") or player),"hltv_player_id":str(gv("HLTV Player ID","Player ID") or ""),"hltv_slug":str(gv("HLTV Slug","Slug") or normalize_name(player).replace(" ","-")),"team":str(gv("Team") or ""),"opponent":str(gv("Opponent") or ""),"match_url":str(gv("Match URL","HLTV Match URL") or ""),"saved_at":now_iso()};saved+=1
                save_json(PLAYER_ALIAS_FILE,aliases,force=True);st.success(f"Saved {saved} exact mappings. Refresh projections.");st.rerun()
        except Exception as exc:st.error(f"Mapping CSV error: {exc}")
    with st.expander("Add one mapping manually",expanded=False):
        mp=st.text_input("Underdog player",key="v46_map_player");mid=st.text_input("HLTV player ID",key="v46_map_pid");mslug=st.text_input("HLTV slug",key="v46_map_slug");mteam=st.text_input("Team",key="v46_map_team");mopp=st.text_input("Opponent",key="v46_map_opp");murl=st.text_input("HLTV match URL",key="v46_map_url")
        if st.button("SAVE THIS MAPPING",use_container_width=True):
            if not mp.strip():st.error("Player is required.")
            else:
                aliases=load_json(PLAYER_ALIAS_FILE,{})
                if not isinstance(aliases,dict):aliases={}
                aliases[normalize_name(mp)]={"alias":mp.strip(),"hltv_player_id":mid.strip(),"hltv_slug":mslug.strip() or normalize_name(mp).replace(" ","-"),"team":mteam.strip(),"opponent":mopp.strip(),"match_url":murl.strip(),"saved_at":now_iso()};save_json(PLAYER_ALIAS_FILE,aliases,force=True);st.success("Mapping saved. Refresh projections.");st.rerun()

    st.markdown('<div class="section-title-pro">Batch Stats Mirror + Optional GitHub Cache</div>', unsafe_allow_html=True)
    st.caption("V5.0 loads verified BO3 public player profiles in controlled batches and saves them permanently. If the full board is not complete on the first refresh, refresh again to continue.")
    bridge_payload, bridge_state = load_provider_bridge(force=False)
    b1,b2,b3,b4=st.columns(4)
    b1.metric("Cached Profiles",len(bridge_payload.get("profiles") or {}))
    b2.metric("Bridge Matches",len(bridge_payload.get("matches") or []))
    b3.metric("Bridge Age",f"{int((_v48_json_age_seconds(bridge_payload) or 0)/60)} min" if bridge_payload.get("generated_at") else "—")
    b4.metric("Cache Source",bridge_state.get("provider","Direct mirror on refresh"))
    bridge_upload=st.file_uploader("Upload cs2_provider_cache.json",type=["json"],key="v48_bridge_upload")
    bc1,bc2=st.columns(2)
    with bc1:
        if st.button("REFRESH BATCH MIRROR + CACHE",use_container_width=True,key="v48_reload_bridge"):
            V48_RUNTIME["bridge"]=None
            V49_RUNTIME["tables"]={};V49_RUNTIME["statuses"]={};_close_source_circuit("jina_mirror")
            current_players=[str(x.get("player") or "") for x in (st.session_state.get("cs2_board") or []) if str(x.get("player") or "")]
            profs,mstate=_v49_generate_profiles(current_players,force=True) if current_players else ({},{"ok":False,"warning":"Refresh the real board first."})
            payload,state=load_provider_bridge(force=True)
            if profs:st.success(f"BO3 profile cache loaded {len(profs)} current-board profiles. Refresh projections once.")
            elif payload.get("profiles"):st.success(f"Loaded {len(payload.get('profiles') or {})} cached profiles.")
            else:st.error(mstate.get("warning") or state.get("warning") or "BO3 profile cache and local/demo database are empty.")
            st.rerun()
    with bc2:
        if st.button("IMPORT UPLOADED BRIDGE",use_container_width=True,key="v48_import_bridge",disabled=bridge_upload is None):
            try:
                uploaded=json.loads(bridge_upload.getvalue().decode("utf-8"))
                if not _v48_valid_bridge(uploaded):raise ValueError("This is not a valid v4.9 provider cache.")
                save_json(V48_BRIDGE_LOCAL_FILE,uploaded,force=True);V48_RUNTIME["bridge"]=None;load_provider_bridge(force=False)
                st.success("Verified provider cache imported to the Railway volume.");st.rerun()
            except Exception as exc:st.error(f"Provider cache import failed: {exc}")

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
    health=model_health_report(board,st.session_state.get("cs2_line_source_status") or {})
    st.markdown('<div class="section-title-pro">Model Health + Readiness</div>', unsafe_allow_html=True)
    h1,h2,h3,h4=st.columns(4)
    h1.metric("Readiness Score",f"{health['score']:.1f}/100")
    h2.metric("Readiness Grade",health['grade'])
    h3.metric("Graded Projections",health['graded_binary'])
    h4.metric("Calibration",health['calibration_tier'])
    st.caption(health['note'])
    st.json(health,expanded=False)
    # Always recompute with the active model version. Do not reuse an older v4.5 parser snapshot.
    parser_health=parser_consistency_report(board,st.session_state.get("cs2_board_status") or {})
    st.markdown('<div class="section-title-pro">Parser Consistency Circuit</div>', unsafe_allow_html=True)
    p1,p2,p3=st.columns(3);p1.metric("Parser Health",f"{parser_health.get('score',0):.1f}/100");p2.metric("Parser Grade",parser_health.get("grade","—"));p3.metric("Official Enabled","YES" if parser_health.get("official_enabled") else "NO")
    if parser_health.get("checks"):st.dataframe(pd.DataFrame(parser_health["checks"]),use_container_width=True,hide_index=True)
    st.markdown('<div class="section-title-pro">Source Status</div>', unsafe_allow_html=True)
    provider=(st.session_state.get("cs2_board_status") or {}).get("v49_source_recovery") or (st.session_state.get("cs2_board_status") or {}).get("v48_source_recovery") or {}
    if provider:
        s1,s2,s3,s4,s5=st.columns(5)
        s1.metric("Underdog Lines",provider.get("lines_loaded",0))
        s2.metric("Verified Profiles",provider.get("verified_profiles",0))
        s3.metric("Matched Teams",provider.get("matched_teams",provider.get("bo3_profiles",0)))
        s4.metric("Matched Events",provider.get("matched_events",0))
        s5.metric("Projections",provider.get("projections_generated",0))
        if provider.get("projections_generated",0):
            st.success(provider.get("message") or "Verified projection pipeline is working.")
        else:
            st.error(provider.get("message") or "Lines loaded, but no verified statistics profiles were available.")
    circuit_cols=st.columns(3)
    mirror_rows=sum(safe_int((x or {}).get("rows"),0) or 0 for x in (V49_RUNTIME.get("statuses") or {}).values())
    circuit_cols[0].metric("BO3 Profile Cache", "PAUSED" if _source_circuit_open("jina_mirror") else (f"READY · {mirror_rows}" if mirror_rows else "READY"))
    circuit_cols[1].metric("Per-player BO3", "DISABLED" if not V49_ALLOW_BO3_LAST_RESORT else ("PAUSED" if _source_circuit_open("bo3_api") else "LAST RESORT"))
    if circuit_cols[2].button("Reset BO3 source + continue loading",use_container_width=True,key="reset_v48_source_circuits"):
        for source_name in ["jina_mirror","hltv","bo3_api","prizepicks"]:_close_source_circuit(source_name)
        V48_RUNTIME["bridge"]=None;V49_RUNTIME["tables"]={};V49_RUNTIME["statuses"]={};V49_RUNTIME["last_prefetch"]={}
        load_provider_bridge(force=True);st.success("Source circuits reset. Press Refresh Real Board + Projections once.");st.rerun()
    if _source_circuit_open("jina_mirror"):
        st.warning("The BO3 provider is temporarily paused after a failed response. Saved database/demo profiles remain available. Reset the source and refresh again after the cooldown.")
    else:
        st.info("V5.0 progressively loads verified BO3 public player pages with rate-limited concurrency and persistent caching.")
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
                "Format": row.get("match_format"), "Maps": format_likely_maps(row.get("likely_maps")),
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
        {"Variable": "CS2 core database", "Required": "Automatic", "Purpose": CORE_DB_FILE},
        {"Variable": "UNDERDOG_URL_OVERRIDE", "Required": "No", "Purpose": "Replacement public board endpoint if Underdog changes versions"},
        {"Variable": "PANDASCORE_TOKEN", "Required": "No", "Purpose": "Optional free schedule/roster fallback"},
        {"Variable": "CS2_JINA_MIRROR_ENABLED", "Required": "Automatic", "Purpose": "true; self-contained batch player-stat mirror"},
        {"Variable": "CS2_MIRROR_FRESH_SECONDS", "Required": "No", "Purpose": "Defaults to 21600 (6 hours)"},
        {"Variable": "CS2_BRIDGE_REPO", "Required": "Optional", "Purpose": "Only for an optional GitHub data-cache branch"},
        {"Variable": "CS2_BRIDGE_BRANCH", "Required": "No", "Purpose": "Defaults to data-cache"},
        {"Variable": "CS2_BO3_LAST_RESORT", "Required": "No", "Purpose": "Defaults false; avoids per-player 403/429 request storms"},
        {"Variable": "GITHUB_TOKEN", "Required": "For private bridge repo", "Purpose": "Reads private data-cache and supports optional backup"},
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
