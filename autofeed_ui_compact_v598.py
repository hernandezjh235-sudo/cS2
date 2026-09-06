from __future__ import annotations

from pathlib import Path


COMPACT_CSS = r'''
/* v5.9.8 compact projection-card UI — visual only, no model/data changes */
.pick-card,.official-card{
  padding:10px 11px!important;
  border-radius:14px!important;
  margin-bottom:8px!important;
  box-shadow:0 0 16px rgba(255,0,30,.16)!important;
}
.player-name{font-size:18px!important;line-height:1.08!important;}
.big-number{font-size:27px!important;line-height:1!important;}
.metric-grid{gap:6px!important;margin-top:7px!important;margin-bottom:7px!important;}
.metric-box{min-height:52px!important;padding:7px 6px!important;border-radius:10px!important;}
.metric-label{font-size:9px!important;line-height:1.1!important;letter-spacing:.2px!important;}
.metric-value{font-size:15px!important;line-height:1.05!important;}
.badge{padding:3px 7px!important;font-size:9px!important;margin:2px 2px!important;}
.muted{font-size:10px!important;line-height:1.25!important;}
.small-muted{font-size:9px!important;line-height:1.2!important;}
.section-title-pro{margin:10px 0 6px!important;}
@media(max-width:850px){
  .block-container{padding:.38rem .38rem .8rem .38rem!important;}
  .pick-card,.official-card{padding:8px 8px!important;border-radius:12px!important;margin-bottom:6px!important;}
  .player-name{font-size:15px!important;}
  .big-number{font-size:22px!important;}
  .metric-grid{grid-template-columns:repeat(2,minmax(0,1fr))!important;gap:4px!important;}
  .metric-box{min-height:46px!important;padding:5px!important;}
  .metric-label{font-size:8px!important;}
  .metric-value{font-size:13px!important;}
  .badge{font-size:8px!important;padding:2px 5px!important;}
  .muted{font-size:9px!important;}
  .small-muted{font-size:8px!important;}
}
'''


def _replace_once(src: str, old: str, new: str) -> tuple[str, bool]:
    if old not in src:
        return src, False
    return src.replace(old, new, 1), True


def patch_app(path: Path) -> bool:
    src = path.read_text(encoding="utf-8")
    original = src

    # Inject CSS after all existing base/mobile card rules so these compact
    # values win without touching any data, projection, grading or gating code.
    if "v5.9.8 compact projection-card UI" not in src:
        marker = "</style>"
        idx = src.find(marker)
        if idx >= 0:
            src = src[:idx] + COMPACT_CSS + "\n" + src[idx:]

    # Main board: two cards per row, matching the compact reference layout.
    old = '''        for row in card_rows:\n            render_pick_card(row, official_style=row.get("status") == "OFFICIAL")'''
    new = '''        _card_cols = st.columns(2, gap="small")\n        for _card_i, row in enumerate(card_rows):\n            with _card_cols[_card_i % 2]:\n                render_pick_card(row, official_style=row.get("status") == "OFFICIAL")'''
    src, _ = _replace_once(src, old, new)

    # Strict official section.
    old = '''        for row in official[:_preview_limit]:\n            render_pick_card(row, official_style=True)'''
    new = '''        _official_cols = st.columns(2, gap="small")\n        for _card_i, row in enumerate(official[:_preview_limit]):\n            with _official_cols[_card_i % 2]:\n                render_pick_card(row, official_style=True)'''
    src, _ = _replace_once(src, old, new)

    # Playable section.
    old = '''        for row in playable[:_preview_limit]:\n            render_pick_card(row, official_style=False)'''
    new = '''        _playable_cols = st.columns(2, gap="small")\n        for _card_i, row in enumerate(playable[:_preview_limit]):\n            with _playable_cols[_card_i % 2]:\n                render_pick_card(row, official_style=False)'''
    src, _ = _replace_once(src, old, new)

    if src != original:
        path.write_text(src, encoding="utf-8")
        return True
    return False
