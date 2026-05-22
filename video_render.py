"""Route C 视频渲染：把每个"显示帧"渲染成 HTML（H3 横屏 / V3 竖屏，照搬
claude.ai/design 的"学术杂志"第三版排版），用无头 Edge/Chrome 截成 PNG，
再用 ffmpeg 按逐帧时长 + 整篇音频合成 MP4。

设计来源：horizontals.jsx 的 H3「Marginalia」与 verticals.jsx 的 V3「Annotated」，
共享 theme.jsx 的调色板 / 字体 / WordCard 原子。这里全部用纯 HTML+内联样式还原，
不跑 React，数值与 JSX 保持一致（H3 基准 1280×720、V3 基准 540×960，截图时按
device-scale-factor 放大到 1920×1080 / 1080×1920）。
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

# ───────── 设计 token（镜像 theme.jsx）─────────

PALETTES: dict[str, dict[str, str]] = {
    "warm": {"bg": "#f4ede0", "bg2": "#ebe1cc", "fg": "#1d1812", "fg2": "#6b5d4a", "rule": "#d3c6ac", "accent": "#a8321f"},
    "rice": {"bg": "#fbfaf6", "bg2": "#f1efe7", "fg": "#1a1a1a", "fg2": "#6e6a60", "rule": "#d8d4c6", "accent": "#7a2e2e"},
    "ink": {"bg": "#171411", "bg2": "#211d18", "fg": "#ece4d2", "fg2": "#9a8d77", "rule": "#3a3329", "accent": "#d18a4a"},
    "sage": {"bg": "#eef0e8", "bg2": "#e3e6da", "fg": "#1c211b", "fg2": "#5e6657", "rule": "#c8cdbb", "accent": "#3a5a3a"},
}

# 本机字体回退：设计的首选 Source Serif 4 / Noto Serif SC 多数 Windows 机器没有，
# 退到 Georgia / 宋体 / 雅黑（系统自带）。如需完全还原，可在此接入 @font-face。
SERIF_EN = '"Source Serif 4","Source Serif Pro","EB Garamond",Georgia,"Times New Roman",serif'
SERIF_CN = '"Noto Serif SC","Songti SC","STSong","SimSun",serif'
SANS_EN = '"Inter",-apple-system,"Segoe UI",system-ui,sans-serif'
SANS_CN = '"Noto Sans SC","PingFang SC","Microsoft YaHei",sans-serif'


def get_palette(name: str | None) -> dict[str, str]:
    return PALETTES.get(name or "warm", PALETTES["warm"])


def _esc(text: Any) -> str:
    return html.escape(str(text or ""), quote=True)


# ───────── 显示分段（回答"如何分段"：句子过长则按子句切，时间按字符占比分配）─────────

# 各方向每帧英文字符预算（超过则切子段）。横屏主栏宽、竖屏窄，预算不同。
CHAR_BUDGET = {"16:9": 150, "9:16": 78}
# 切分优先级：先在子句边界(; :)，再破折号 / 长逗号，最后兜底按词软切。
_CLAUSE_RE = re.compile(r"(?<=[;:])\s+")
_SOFT_RE = re.compile(r"(?<=[,，—–-])\s+")


def segment_for_display(text: str, max_chars: int) -> list[str]:
    """把一个句子切成 ≤max_chars 的若干显示子段。短句原样返回（一句一帧）。"""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    def split_keep(parts: list[str]) -> list[str]:
        """贪心合并相邻片段，使每段尽量接近但不超过 max_chars。"""
        out: list[str] = []
        cur = ""
        for p in parts:
            p = p.strip()
            if not p:
                continue
            cand = f"{cur} {p}".strip() if cur else p
            if cur and len(cand) > max_chars:
                out.append(cur)
                cur = p
            else:
                cur = cand
        if cur:
            out.append(cur)
        return out

    # 1) 子句边界
    segs = split_keep(_CLAUSE_RE.split(text))
    # 2) 仍超长的，破折号/逗号再切
    refined: list[str] = []
    for s in segs:
        refined.extend(split_keep(_SOFT_RE.split(s)) if len(s) > max_chars else [s])
    # 3) 仍超长（无标点的长串）：按词软切
    final: list[str] = []
    for s in refined:
        if len(s) <= max_chars:
            final.append(s)
            continue
        cur = ""
        for w in s.split(" "):
            cand = f"{cur} {w}".strip() if cur else w
            if cur and len(cand) > max_chars:
                final.append(cur)
                cur = w
            else:
                cur = cand
        if cur:
            final.append(cur)
    return final or [text]


def distribute_time(segs: list[str], begin_ms: float, end_ms: float) -> list[tuple[float, float]]:
    """把 [begin,end] 按各子段字符占比切给每个子段，保证音画同源（始终在该句时间窗内）。"""
    if not segs:
        return []
    if len(segs) == 1:
        return [(begin_ms, end_ms)]
    total_chars = sum(len(s) for s in segs) or 1
    spans: list[tuple[float, float]] = []
    cursor = begin_ms
    dur = end_ms - begin_ms
    acc = 0
    for i, s in enumerate(segs):
        acc += len(s)
        seg_end = end_ms if i == len(segs) - 1 else begin_ms + dur * (acc / total_chars)
        spans.append((cursor, seg_end))
        cursor = seg_end
    return spans


# ───────── 共享原子（镜像 theme.jsx）─────────

def _kicker(text: str, pal: dict[str, str], size: float = 10, extra: str = "") -> str:
    return (
        f'<span style="font-family:{SANS_EN};font-size:{size}px;letter-spacing:2px;'
        f'text-transform:uppercase;font-weight:600;color:{pal["accent"]};{extra}">{_esc(text)}</span>'
    )


def _progress(index: int, total: int, pal: dict[str, str]) -> str:
    pct = (index / total * 100) if total else 0
    return (
        f'<div style="width:100%;height:2px;background:{pal["rule"]};position:relative">'
        f'<div style="position:absolute;left:0;top:0;bottom:0;width:{pct:.2f}%;background:{pal["accent"]}"></div></div>'
    )


def _counter(index: int, total: int, pal: dict[str, str], sep: str = " / ") -> str:
    return (
        f'<span style="font-family:{SANS_EN};font-size:11px;color:{pal["fg2"]};'
        f'font-variant-numeric:tabular-nums;letter-spacing:1.2px;font-weight:600">'
        f'<span style="color:{pal["accent"]}">§{index:02d}</span>'
        f'<span style="opacity:.5">{sep}{total:02d}</span></span>'
    )


def _mark_vocab(text: str, words: list[dict[str, Any]], pal: dict[str, str]) -> str:
    """把出现在句中的难词加下划线 + 强调色（仅整词、忽略大小写）。"""
    vocab = {str(w.get("en", "")).lower() for w in words if w.get("en")}
    parts = re.split(r"(\b[A-Za-z'][A-Za-z'\-]*\b)", text)
    out: list[str] = []
    for p in parts:
        if p.lower() in vocab and p.strip():
            out.append(
                f'<span style="color:{pal["accent"]};border-bottom:1px solid {pal["accent"]};'
                f'padding-bottom:1px">{_esc(p)}</span>'
            )
        else:
            out.append(_esc(p))
    return "".join(out)


def _word_card_boxed(w: dict[str, Any], pal: dict[str, str]) -> str:
    """WordCardBoxed：左边框卡片，词 / 音标 / 词性+中文。"""
    return (
        f'<div style="background:{pal["bg2"]};border-left:2px solid {pal["accent"]};'
        f'padding:9px 11px;min-width:0;display:flex;flex-direction:column;gap:2px;box-sizing:border-box">'
        f'<span style="font-family:{SERIF_EN};font-weight:600;font-size:17px;color:{pal["fg"]};'
        f'letter-spacing:-.1px;line-height:1.15;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{_esc(w.get("en"))}</span>'
        f'<span style="font-family:{SANS_EN};font-size:10px;color:{pal["fg2"]};font-style:italic;'
        f'line-height:1.2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{_esc(w.get("ipa"))}</span>'
        f'<div style="display:flex;align-items:baseline;gap:5px;margin-top:2px;min-width:0">'
        f'<span style="font-family:{SANS_EN};font-size:10px;color:{pal["accent"]};font-style:italic;flex:0 0 auto">{_esc(w.get("pos"))}</span>'
        f'<span style="font-family:{SERIF_CN};font-size:12.5px;color:{pal["fg"]};line-height:1.3;'
        f'min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{_esc(w.get("cn"))}</span>'
        f'</div></div>'
    )


def _footnote_entry(w: dict[str, Any], pal: dict[str, str], n: int, last: bool) -> str:
    """H3 侧栏的编号脚注式难词条目。"""
    border = "none" if last else f"0.5px dotted {pal['rule']}"
    return (
        f'<div style="display:grid;grid-template-columns:22px 1fr;gap:0 10px;padding:8px 0;border-bottom:{border}">'
        f'<span style="font-family:{SANS_EN};font-size:10px;color:{pal["accent"]};font-weight:700;'
        f'line-height:1.6;font-variant-numeric:tabular-nums">{n:02d}</span>'
        f'<div style="min-width:0">'
        f'<div style="display:flex;align-items:baseline;gap:6px;flex-wrap:wrap">'
        f'<span style="font-family:{SERIF_EN};font-weight:600;font-size:16px;color:{pal["fg"]}">{_esc(w.get("en"))}</span>'
        f'<span style="font-family:{SANS_EN};font-size:10px;color:{pal["fg2"]};font-style:italic">{_esc(w.get("ipa"))}</span>'
        f'<span style="font-family:{SANS_EN};font-size:9px;color:{pal["accent"]};font-style:italic">{_esc(w.get("pos"))}</span>'
        f'</div>'
        f'<div style="font-family:{SERIF_CN};font-size:12.5px;color:{pal["fg"]};margin-top:2px;opacity:.85">{_esc(w.get("cn"))}</div>'
        f'</div></div>'
    )


def _doc(body: str, pal: dict[str, str], width: int, height: int) -> str:
    """包成一张精确尺寸的整页 HTML（无滚动条、纸张底色铺满）。"""
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<style>*{margin:0;padding:0;box-sizing:border-box}"
        f"html,body{{width:{width}px;height:{height}px;overflow:hidden;background:{pal['bg']};"
        "-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}</style></head>"
        f"<body><div style='width:{width}px;height:{height}px;background:{pal['bg']};color:{pal['fg']}'>{body}</div></body></html>"
    )


# ───────── H3「Marginalia」横屏 1280×720 → 1920×1080 ─────────

def render_h3(frame: dict[str, Any], pal: dict[str, str]) -> str:
    s = frame["sentence"]
    words = frame.get("words", [])[:6]
    en_size, cn_size = 50, 22
    kicker_bits = " · ".join(b for b in ["Essay", frame.get("author", ""), frame.get("year", "")] if b)

    title_bar = (
        f'<div style="grid-column:1/-1;grid-row:1;display:flex;align-items:center;gap:16px;'
        f'padding-bottom:14px;border-bottom:1px solid {pal["fg"]}">'
        f'<div style="width:24px;height:24px;background:{pal["accent"]};flex-shrink:0"></div>'
        f'<div style="flex:1;min-width:0">'
        f'<div style="display:flex;flex-direction:column;gap:1.7px">'
        f'{_kicker(kicker_bits, pal, 8.5)}'
        f'<div style="font-family:{SERIF_EN};font-style:italic;font-weight:500;font-size:18.7px;'
        f'color:{pal["fg"]};letter-spacing:-.2px;line-height:1.1;margin-top:1.7px">{_esc(frame.get("titleEn"))}'
        f'<span style="font-family:{SERIF_CN};font-style:normal;font-size:15.3px;color:{pal["fg2"]};margin-left:8.5px">· {_esc(frame.get("titleCn"))}</span>'
        f'</div></div></div>'
        f'<div style="display:flex;align-items:center;gap:14px">{_counter(s["index"], s["total"], pal)}</div>'
        f'</div>'
    )

    main_col = (
        f'<div style="grid-column:1;grid-row:2;display:flex;flex-direction:column;justify-content:flex-start;'
        f'gap:30px;min-width:0;padding:34px 8px 0 0">'
        f'<div>{_kicker("EN", pal, 9, "margin-bottom:8px;display:block")}'
        f'<p style="font-family:{SERIF_EN};font-size:{en_size}px;line-height:1.22;color:{pal["fg"]};'
        f'letter-spacing:-.4px;text-wrap:pretty">{_mark_vocab(s["en"], words, pal)}</p></div>'
        f'<div style="width:60px;height:1px;background:{pal["rule"]}"></div>'
        f'<div>{_kicker("CN · 译文", pal, 9, "margin-bottom:8px;display:block")}'
        f'<p style="font-family:{SERIF_CN};font-size:{cn_size}px;line-height:1.7;color:{pal["fg"]};'
        f'opacity:.85;letter-spacing:1px">{_esc(s["cn"])}</p></div></div>'
    )

    notes = "".join(_footnote_entry(w, pal, i + 1, i == len(words) - 1) for i, w in enumerate(words))
    sidebar = (
        f'<div style="grid-column:2;grid-row:2;border-left:0.5px solid {pal["rule"]};padding-left:24px;'
        f'display:flex;flex-direction:column;min-height:0">'
        f'<div style="display:flex;align-items:baseline;gap:10px;margin-bottom:12px">'
        f'{_kicker("Notes · 难词", pal, 10)}<div style="flex:1;height:1px;background:{pal["rule"]}"></div></div>'
        f'<div style="flex:1;display:flex;flex-direction:column;overflow:hidden">{notes}</div></div>'
    )

    footer = (
        f'<div style="grid-column:1/-1;grid-row:3;padding-top:14px">{_progress(s["index"], s["total"], pal)}</div>'
    )

    body = (
        f'<div style="width:100%;height:100%;display:grid;grid-template-columns:1fr 340px;'
        f'grid-template-rows:auto 1fr auto;gap:0 48px;padding:40px 56px 30px;box-sizing:border-box;'
        f'position:relative;overflow:hidden">{title_bar}{main_col}{sidebar}{footer}</div>'
    )
    return _doc(body, pal, 1280, 720)


# ───────── V3「Annotated」竖屏 540×960 → 1080×1920 ─────────

def render_v3(frame: dict[str, Any], pal: dict[str, str]) -> str:
    s = frame["sentence"]
    words = frame.get("words", [])[:6]
    en_size, cn_size = 42, 21

    title_bar = (
        f'<div style="padding:0 36px 18px;border-bottom:1px solid {pal["fg"]};margin-bottom:26px">'
        f'<div style="display:flex;justify-content:space-between;align-items:baseline">'
        f'{_kicker("Close Reading · 精读", pal, 10)}{_counter(s["index"], s["total"], pal)}</div>'
        f'<div style="font-family:{SERIF_EN};font-style:italic;font-size:22px;color:{pal["fg"]};'
        f'margin-top:6px;letter-spacing:-.2px">{_esc(frame.get("titleEn"))}'
        f'<span style="font-family:{SERIF_CN};font-style:normal;color:{pal["fg2"]};margin-left:8px;font-size:18px">· {_esc(frame.get("titleCn"))}</span>'
        f'</div></div>'
    )

    sentence = (
        f'<div style="padding:48px 36px 0;flex:1 1 auto;display:flex;flex-direction:column;justify-content:flex-start;gap:24px">'
        f'<p style="font-family:{SERIF_EN};font-size:{en_size}px;line-height:1.3;color:{pal["fg"]};'
        f'letter-spacing:-.3px;text-wrap:pretty">{_mark_vocab(s["en"], words, pal)}</p>'
        f'<div style="height:1px;background:{pal["rule"]}"></div>'
        f'<p style="font-family:{SERIF_CN};font-size:{cn_size}px;line-height:1.7;color:{pal["fg"]};'
        f'opacity:.88;letter-spacing:1px">{_esc(s["cn"])}</p></div>'
    )

    # 难词：每行 3 个，全部显示（视频不能滑动，绝不隐藏）。底部高度随词数自然增长。
    if words:
        cards_html = "".join(_word_card_boxed(w, pal) for w in words)
        strip = (
            f'<div style="margin-top:24px">'
            f'<div style="display:flex;align-items:baseline;gap:10px;padding:0 36px;margin-bottom:12px">'
            f'{_kicker("Vocabulary · 难词", pal, 10)}<div style="flex:1;height:1px;background:{pal["rule"]}"></div>'
            f'<span style="font-family:{SANS_EN};font-size:9px;color:{pal["fg2"]};letter-spacing:1px">{len(words)} words</span></div>'
            f'<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;padding:0 36px">{cards_html}</div>'
            f'<div style="margin-top:22px;padding:0 36px">{_progress(s["index"], s["total"], pal)}</div></div>'
        )
    else:
        strip = f'<div style="margin-top:24px;padding:0 36px">{_progress(s["index"], s["total"], pal)}</div>'

    body = (
        f'<div style="width:100%;height:100%;display:flex;flex-direction:column;padding:44px 0 32px;'
        f'box-sizing:border-box;position:relative;overflow:hidden">{title_bar}{sentence}{strip}</div>'
    )
    return _doc(body, pal, 540, 960)


RATIO_SPEC = {
    # ratio: (renderer, base_w, base_h, device_scale_factor → 输出 target_w×target_h)
    "16:9": {"render": render_h3, "w": 1280, "h": 720, "dsf": 1.5, "out_w": 1920, "out_h": 1080, "design": "H3 Marginalia"},
    "9:16": {"render": render_v3, "w": 540, "h": 960, "dsf": 2.0, "out_w": 1080, "out_h": 1920, "design": "V3 Annotated"},
}


# ───────── 本地合成脚本（服务器只出 HTML，截图 + ffmpeg 都在用户本机跑）─────────

def write_concat_list(frames: list[dict[str, Any]], list_path: Path) -> None:
    """concat demuxer 清单：每帧 PNG 持续 duration_ms（最后一帧需重复一次收尾）。"""
    lines: list[str] = []
    for f in frames:
        png = Path(f["png"]).name
        dur = max(0.04, f["dur_ms"] / 1000.0)
        lines.append(f"file '{png}'")
        lines.append(f"duration {dur:.3f}")
    if frames:
        lines.append(f"file '{Path(frames[-1]['png']).name}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# render.bat：用本机 Chrome/Edge 把每帧 HTML 截成 PNG（device-scale-factor 放大到成片分辨率），
# 再用本机 ffmpeg 按 frames.txt 合成 out.mp4。@@DSF@@/@@W@@/@@H@@ 按比例填充。
_LOCAL_BAT = r"""@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal
set "BROWSER="
for %%P in ("%ProgramFiles%\Google\Chrome\Application\chrome.exe" "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe") do if exist "%%~P" set "BROWSER=%%~P"
if "%BROWSER%"=="" ( echo [ERROR] No Chrome/Edge found. Please install Chrome or Edge. & pause & exit /b 1 )
echo Using browser: %BROWSER%
echo Rendering frames to PNG ...
for %%F in (f*.html) do "%BROWSER%" --headless=new --disable-gpu --hide-scrollbars --no-sandbox --disable-extensions --user-data-dir="%CD%\.chrome-profile" --force-device-scale-factor=@@DSF@@ --window-size=@@W@@,@@H@@ --default-background-color=00000000 --screenshot="%%~nF.png" "file:///%CD:\=/%/%%F" >nul 2>&1
where ffmpeg >nul 2>&1
if errorlevel 1 ( echo [ERROR] ffmpeg not found on PATH. Install ffmpeg ^(https://ffmpeg.org^) then re-run. & pause & exit /b 1 )
echo Assembling out.mp4 ...
ffmpeg -y -f concat -safe 0 -i frames.txt -i audio.wav -c:v libx264 -pix_fmt yuv420p -r 25 -c:a aac -b:a 192k -shortest -vsync vfr out.mp4
echo Done. Output: out.mp4
pause
"""

_LOCAL_SH = r"""#!/bin/sh
set -e
cd "$(dirname "$0")"
BROWSER=""
for c in google-chrome google-chrome-stable chromium chromium-browser "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"; do
  if command -v "$c" >/dev/null 2>&1; then BROWSER="$c"; break; fi
  if [ -x "$c" ]; then BROWSER="$c"; break; fi
done
[ -z "$BROWSER" ] && { echo "[ERROR] No Chrome/Chromium found."; exit 1; }
echo "Using browser: $BROWSER"
for f in f*.html; do
  "$BROWSER" --headless=new --disable-gpu --hide-scrollbars --no-sandbox --user-data-dir="$PWD/.chrome-profile" --force-device-scale-factor=@@DSF@@ --window-size=@@W@@,@@H@@ --default-background-color=00000000 --screenshot="${f%.html}.png" "file://$PWD/$f" >/dev/null 2>&1
done
command -v ffmpeg >/dev/null 2>&1 || { echo "[ERROR] ffmpeg not found."; exit 1; }
ffmpeg -y -f concat -safe 0 -i frames.txt -i audio.wav -c:v libx264 -pix_fmt yuv420p -r 25 -c:a aac -b:a 192k -shortest -vsync vfr out.mp4
echo "Done. Output: out.mp4"
"""


def _fill(tpl: str, ratio: str) -> str:
    spec = RATIO_SPEC[ratio]
    return (tpl.replace("@@DSF@@", str(spec["dsf"]))
               .replace("@@W@@", str(spec["w"]))
               .replace("@@H@@", str(spec["h"])))


def render_bat(ratio: str) -> str:
    return _fill(_LOCAL_BAT, ratio)


def render_sh(ratio: str) -> str:
    return _fill(_LOCAL_SH, ratio)
