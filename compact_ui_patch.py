#!/usr/bin/env python3
"""UI-only compact card patch for the generated Railway web app.

This intentionally touches presentation CSS only. Projection logic, data gates,
provider/cache logic, line visibility, grading, and model outputs are unchanged.
"""
from __future__ import annotations

import sys
from pathlib import Path


def replace_once(text: str, old: str, new: str) -> str:
    if old in text:
        return text.replace(old, new, 1)
    return text


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: compact_ui_patch.py <generated_app.py>", file=sys.stderr)
        return 2

    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")

    # Main prop cards: materially shorter/narrower visual footprint.
    text = replace_once(
        text,
        ".pick-card {\n  background:linear-gradient(145deg,#fff,#f2f2f4);\n  color:#08080a;\n  border:2px solid rgba(255,47,61,.60);\n  border-radius:22px;\n  padding:20px;\n  box-shadow:0 0 26px rgba(255,0,30,.18);\n  margin-bottom:16px;\n}",
        ".pick-card {\n  background:linear-gradient(145deg,#fff,#f2f2f4);\n  color:#08080a;\n  border:1.5px solid rgba(255,47,61,.60);\n  border-radius:14px;\n  padding:11px 12px;\n  box-shadow:0 0 18px rgba(255,0,30,.14);\n  margin-bottom:8px;\n}",
    )
    text = replace_once(
        text,
        ".official-card {\n  background:linear-gradient(145deg,#160006,#070708);\n  color:#fff;\n  border:2px solid rgba(255,47,61,.90);\n  border-radius:22px;\n  padding:20px;\n  box-shadow:0 0 32px rgba(255,0,30,.30);\n  margin-bottom:16px;\n}",
        ".official-card {\n  background:linear-gradient(145deg,#160006,#070708);\n  color:#fff;\n  border:1.5px solid rgba(255,47,61,.90);\n  border-radius:14px;\n  padding:11px 12px;\n  box-shadow:0 0 20px rgba(255,0,30,.22);\n  margin-bottom:8px;\n}",
    )

    # Typography + metric tiles: keep the same information, use less vertical space.
    text = text.replace(".player-name {font-size:25px;font-weight:950;}", ".player-name {font-size:18px;font-weight:950;line-height:1.08;}")
    text = text.replace(".big-number {font-size:42px;font-weight:950;line-height:1.02;}", ".big-number {font-size:29px;font-weight:950;line-height:1;}")
    text = text.replace(".muted {color:#74747d!important;font-size:13px;}", ".muted {color:#74747d!important;font-size:11px;line-height:1.25;}")
    text = text.replace(".small-muted {color:#74747d!important;font-size:12px;}", ".small-muted {color:#74747d!important;font-size:10px;line-height:1.2;}")
    text = text.replace("display:inline-block;padding:6px 11px;border-radius:999px;", "display:inline-block;padding:4px 8px;border-radius:999px;font-size:10px;")
    text = text.replace(".metric-grid {display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;margin:12px 0;}", ".metric-grid {display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:6px;margin:7px 0;}")
    text = text.replace(".metric-box {background:rgba(255,255,255,.88);border:1px solid rgba(20,20,24,.14);border-radius:15px;padding:12px;min-height:82px;}", ".metric-box {background:rgba(255,255,255,.88);border:1px solid rgba(20,20,24,.14);border-radius:10px;padding:7px 6px;min-height:56px;}")
    text = text.replace(".metric-label {font-size:11px;font-weight:850;letter-spacing:.04em;text-transform:uppercase;color:#6c6c74!important;}", ".metric-label {font-size:9px;font-weight:850;letter-spacing:.03em;text-transform:uppercase;color:#6c6c74!important;}")
    text = text.replace(".metric-value {font-size:22px;font-weight:950;margin-top:5px;}", ".metric-value {font-size:16px;font-weight:950;margin-top:3px;line-height:1.05;}")
    text = text.replace(".hr-soft {border-top:1px solid rgba(100,100,110,.25);margin:13px 0;}", ".hr-soft {border-top:1px solid rgba(100,100,110,.25);margin:7px 0;}")

    # Mobile: preserve the compact multi-metric row instead of turning each tile into
    # a tall single-column stack. This is the biggest density improvement on iPhone.
    text = text.replace(
        " .big-title{font-size:29px}.big-number{font-size:31px}.player-name{font-size:21px}",
        " .big-title{font-size:26px}.big-number{font-size:25px}.player-name{font-size:16px}",
    )
    text = text.replace(
        " .pick-card,.official-card{padding:12px;border-radius:14px;margin-bottom:10px}",
        " .pick-card,.official-card{padding:8px 9px;border-radius:12px;margin-bottom:7px}",
    )
    text = text.replace(
        " .metric-grid{grid-template-columns:repeat(2,minmax(0,1fr));}",
        " .metric-grid{grid-template-columns:repeat(3,minmax(0,1fr));gap:5px;}",
    )
    text = text.replace(
        " .metric-box{min-height:66px;padding:9px}",
        " .metric-box{min-height:50px;padding:6px 5px}",
    )
    text = text.replace(" .metric-value{font-size:18px}", " .metric-value{font-size:14px}")

    # Do not collapse custom card internals to one column on mobile.
    text = text.replace(
        ' .pick-card div[style*="grid-template-columns"],.official-card div[style*="grid-template-columns"]{grid-template-columns:1fr!important;}\n',
        "",
    )

    path.write_text(text, encoding="utf-8")
    print(f"compact UI patch applied: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
