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

ROOT = Path(__file__).resolve().parent
TEMPLATE_DIR = ROOT / "video_templates"

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


# ───────── 显示分段（保留兼容；当前视频导出按完整句子逐帧呈现）─────────

# 旧版按字符预算切句；新版在 app.py 中保持一句一帧，并通过动态字号适配长句。
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


_FALLBACK_H3_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8"><style>
*{margin:0;padding:0;box-sizing:border-box}html,body{width:1280px;height:720px;overflow:hidden;background:{{ bg }}}
body{font-family:{{ sans_en }};color:{{ fg }};-webkit-font-smoothing:antialiased}
.page{width:100%;height:100%;display:grid;grid-template-columns:1fr 320px;grid-template-rows:auto 1fr auto;gap:0 42px;padding:38px 54px 30px}
.title{grid-column:1/-1;border-bottom:1px solid {{ fg }};padding-bottom:14px;font-family:{{ serif_en }};font-style:italic;font-size:18px}
.main{padding-top:30px;display:flex;flex-direction:column;gap:{{ main_gap }}px}.en{font-family:{{ serif_en }};font-size:{{ en_size }}px;line-height:{{ en_leading }}}
.cn{font-family:{{ serif_cn }};font-size:{{ cn_size }}px;line-height:{{ cn_leading }};opacity:.88}.notes{border-left:1px solid {{ rule }};padding:30px 0 0 22px;overflow:hidden}
</style></head><body><div class="page"><div class="title">{{ title_en }} · {{ title_cn }}</div><main class="main"><p class="en">{{ sentence_en_html }}</p><p class="cn">{{ sentence_cn }}</p></main><aside class="notes">{{ vocab_html }}</aside></div></body></html>"""


_FALLBACK_V3_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8"><style>
*{margin:0;padding:0;box-sizing:border-box}html,body{width:540px;height:960px;overflow:hidden;background:{{ bg }}}
body{font-family:{{ sans_en }};color:{{ fg }};-webkit-font-smoothing:antialiased}.page{width:100%;height:100%;display:flex;flex-direction:column;padding:42px 34px 32px}
.title{border-bottom:1px solid {{ fg }};padding-bottom:16px;font-family:{{ serif_en }};font-style:italic;font-size:21px}.main{flex:1;padding-top:38px;display:flex;flex-direction:column;gap:20px}
.en{font-family:{{ serif_en }};font-size:{{ en_size }}px;line-height:{{ en_leading }}}.cn{font-family:{{ serif_cn }};font-size:{{ cn_size }}px;line-height:{{ cn_leading }};opacity:.88}
.vocab{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}
</style></head><body><div class="page"><div class="title">{{ title_en }} · {{ title_cn }}</div><main class="main"><p class="en">{{ sentence_en_html }}</p><p class="cn">{{ sentence_cn }}</p></main><section class="vocab">{{ vocab_html }}</section></div></body></html>"""


_FALLBACK_LISTEN_SCROLL_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8"><style>
*{margin:0;padding:0;box-sizing:border-box}html,body{width:1280px;height:720px;overflow:hidden;background:#0f1115;color:#e8ecf3}
body{font-family:{{ sans_en }}}.lp{width:100%;height:100%;display:grid;grid-template-columns:7fr 3fr;grid-template-rows:auto 1fr auto;grid-template-areas:"top top" "article vocab" "controls vocab"}
.top{grid-area:top;background:#161a22;padding:13px 28px 14px}.title{font-family:{{ serif_en }};font-size:21px;font-weight:600}.title-cn{font-family:{{ serif_cn }};font-size:14px;color:#a5adbb;margin-top:3px}.article{grid-area:article;overflow:hidden;padding:30px 70px}.scroll{height:100%;overflow:hidden}
.p{font-family:{{ serif_en }};font-size:25px;line-height:1.72;color:#a5adbb;margin-bottom:1em}.read{color:rgba(232,236,243,.28)}.current{color:#fff;background:rgba(255,209,102,.16);border-left:4px solid #ffd166;padding-left:10px;margin-left:-14px}
.vocab{grid-area:vocab;background:#161a22;border-left:1px solid rgba(255,255,255,.08);padding:18px;overflow:hidden}.cn{font-family:{{ serif_cn }};font-size:{{ translation_size }}px;line-height:1.42;margin-bottom:14px}
.grid{display:grid;grid-template-columns:repeat({{ vocab_cols }},1fr);gap:8px}.controls{grid-area:controls;background:#161a22;padding:16px 70px}.bar{height:5px;background:#1d222c}.fill{height:100%;width:{{ progress_pct }}%;background:#ffd166}
</style></head><body><div class="lp"><div class="top"><div class="title">{{ title_en }}</div><div class="title-cn">{{ title_cn }}</div></div><main class="article"><div class="scroll" id="articleScroll">{{ article_html }}</div></main><aside class="vocab"><div class="cn">{{ translation }}</div><div class="grid">{{ vocab_html }}</div></aside><footer class="controls"><div class="bar"><div class="fill"></div></div></footer></div><script>(()=>{const b=document.getElementById('articleScroll');const c=b&&b.querySelector('[data-current=true]');if(b&&c)b.scrollTop=Math.max(0,c.offsetTop-(b.clientHeight-c.offsetHeight)/2)})()</script></body></html>"""


def _load_template(name: str, fallback: str) -> str:
    try:
        return (TEMPLATE_DIR / name).read_text(encoding="utf-8")
    except OSError:
        return fallback


def _render_template(name: str, fallback: str, context: dict[str, Any]) -> str:
    template = _load_template(name, fallback)

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        return str(context.get(key, match.group(0)))

    return re.sub(r"{{\s*([A-Za-z0-9_]+)\s*}}", replace, template)


def _text_band(text: str, bands: list[tuple[int, float]]) -> float:
    """Pick a font size from length thresholds, keeping full sentences on one frame."""
    n = len(text or "")
    for limit, size in bands:
        if n <= limit:
            return size
    return bands[-1][1]


def _learning_words(words: list[dict[str, Any]], sentence: str, short_max: int = 4, long_max: int = 3) -> list[dict[str, Any]]:
    """Keep the vocabulary strip readable: fewer words for long sentences."""
    limit = long_max if len(sentence or "") > 170 else short_max
    return words[:limit]


def _listen_vocab_entry(w: dict[str, Any]) -> str:
    meta = " ".join(part for part in [str(w.get("ipa") or "").strip(), str(w.get("pos") or "").strip()] if part)
    return (
        '<div class="lp-vocab-item">'
        f'<div class="lp-vocab-term">{_esc(w.get("en"))}</div>'
        f'{f"<div class=\"lp-vocab-meta\">{_esc(meta)}</div>" if meta else ""}'
        f'<div class="lp-vocab-meaning">{_esc(w.get("cn"))}</div>'
        '</div>'
    )


def _listen_article_html(sentences: list[dict[str, Any]], current_index: int) -> str:
    groups: list[tuple[int, list[dict[str, Any]]]] = []
    for item in sentences:
        para = int(item.get("para") or 0)
        if not groups or groups[-1][0] != para:
            groups.append((para, []))
        groups[-1][1].append(item)

    html_parts: list[str] = []
    for _para, rows in groups:
        sent_html: list[str] = []
        for row in rows:
            idx = int(row.get("index", 0))
            classes = ["lp-sent"]
            if idx < current_index:
                classes.append("read")
            if idx == current_index:
                classes.append("current")
            current_attr = ' data-current="true"' if idx == current_index else ""
            sent_html.append(
                f'<span class="{" ".join(classes)}" data-idx="{idx}"{current_attr}>{_esc(row.get("text"))} </span>'
            )
        html_parts.append(f'<p class="lp-para p">{"".join(sent_html)}</p>')
    return "".join(html_parts)


def render_listen_scroll_16x9(frame: dict[str, Any], pal: dict[str, str]) -> str:
    """Listening-mode video frame: full article scroll state + all vocab for current sentence."""
    s = frame["sentence"]
    words = frame.get("words", [])
    n_words = len(words)
    vocab_cols = 1 if n_words <= 5 else 2 if n_words <= 16 else 3
    vocab_term_size = 18 if n_words <= 6 else 15 if n_words <= 10 else 12.5 if n_words <= 16 else 10.5
    vocab_cn_size = 13 if n_words <= 6 else 11.5 if n_words <= 10 else 10 if n_words <= 16 else 8.8
    vocab_meta_size = 11 if n_words <= 6 else 9.5 if n_words <= 16 else 8
    vocab_pad_y = 8 if n_words <= 8 else 6 if n_words <= 12 else 4 if n_words <= 18 else 3
    translation = str(s.get("cn") or "")
    translation_size = _text_band(translation, [(70, 21), (120, 18), (9999, 16)])
    if n_words > 16:
        translation_size = min(translation_size, 15)
    progress_pct = (s["index"] / s["total"] * 100) if s.get("total") else 0
    vocab_html = "".join(_listen_vocab_entry(w) for w in words) or '<div class="lp-empty">本句无标记生词</div>'
    article_html = _listen_article_html(frame.get("article_sentences", []), int(s.get("source_index", s["index"] - 1)))
    return _render_template("listen_scroll_16x9.html", _FALLBACK_LISTEN_SCROLL_TEMPLATE, {
        "serif_en": SERIF_EN, "serif_cn": SERIF_CN, "sans_en": SANS_EN, "sans_cn": SANS_CN,
        "title_en": _esc(frame.get("titleEn")),
        "title_cn": _esc(frame.get("titleCn")),
        "sentence_index": f'{int(s["index"]):02d}',
        "sentence_total": f'{int(s["total"]):02d}',
        "article_html": article_html,
        "translation": _esc(translation),
        "vocab_html": vocab_html,
        "vocab_cols": vocab_cols,
        "vocab_term_size": vocab_term_size,
        "vocab_cn_size": vocab_cn_size,
        "vocab_meta_size": vocab_meta_size,
        "vocab_pad_y": vocab_pad_y,
        "translation_size": translation_size,
        "progress_pct": f"{progress_pct:.2f}",
    })


# ───────── H3「Marginalia」横屏 1280×720 → 1920×1080 ─────────

def render_h3(frame: dict[str, Any], pal: dict[str, str]) -> str:
    s = frame["sentence"]
    en_text = s.get("en", "")
    cn_text = s.get("cn", "")
    words = _learning_words(frame.get("words", []), en_text)
    en_size = _text_band(en_text, [(90, 48), (130, 44), (180, 39), (240, 34), (9999, 30)])
    cn_size = _text_band(cn_text, [(55, 30), (90, 28), (135, 26), (9999, 24)])
    en_leading = 1.23 if en_size >= 34 else 1.18
    cn_leading = 1.5 if cn_size >= 26 else 1.42
    main_gap = 24 if len(en_text) <= 150 else 16
    kicker_bits = " · ".join(b for b in ["Essay", frame.get("author", ""), frame.get("year", "")] if b)
    notes = "".join(_footnote_entry(w, pal, i + 1, i == len(words) - 1) for i, w in enumerate(words))
    progress_pct = (s["index"] / s["total"] * 100) if s.get("total") else 0
    return _render_template("h3_16x9.html", _FALLBACK_H3_TEMPLATE, {
        "bg": pal["bg"], "bg2": pal["bg2"], "fg": pal["fg"], "fg2": pal["fg2"],
        "rule": pal["rule"], "accent": pal["accent"],
        "serif_en": SERIF_EN, "serif_cn": SERIF_CN, "sans_en": SANS_EN, "sans_cn": SANS_CN,
        "kicker": _esc(kicker_bits), "title_en": _esc(frame.get("titleEn")),
        "title_cn": _esc(frame.get("titleCn")),
        "sentence_index": f'{int(s["index"]):02d}', "sentence_total": f'{int(s["total"]):02d}',
        "sentence_en_html": _mark_vocab(en_text, words, pal), "sentence_cn": _esc(cn_text),
        "vocab_html": notes, "vocab_count": len(words), "progress_pct": f"{progress_pct:.2f}",
        "en_size": en_size, "cn_size": cn_size, "en_leading": en_leading,
        "cn_leading": cn_leading, "main_gap": main_gap,
    })


# ───────── V3「Annotated」竖屏 540×960 → 1080×1920 ─────────

def render_v3(frame: dict[str, Any], pal: dict[str, str]) -> str:
    s = frame["sentence"]
    en_text = s.get("en", "")
    cn_text = s.get("cn", "")
    words = _learning_words(frame.get("words", []), en_text)
    en_size = _text_band(en_text, [(80, 42), (120, 38), (165, 34), (220, 30), (9999, 27)])
    cn_size = _text_band(cn_text, [(55, 28), (90, 26), (130, 24), (9999, 22)])
    en_leading = 1.26 if en_size >= 34 else 1.18
    cn_leading = 1.48 if cn_size >= 24 else 1.38

    # 难词：学习视频中只保留最关键的 3-4 个，避免挤占完整句子的阅读空间。
    cards_html = "".join(_word_card_boxed(w, pal) for w in words)
    progress_pct = (s["index"] / s["total"] * 100) if s.get("total") else 0
    return _render_template("v3_9x16.html", _FALLBACK_V3_TEMPLATE, {
        "bg": pal["bg"], "bg2": pal["bg2"], "fg": pal["fg"], "fg2": pal["fg2"],
        "rule": pal["rule"], "accent": pal["accent"],
        "serif_en": SERIF_EN, "serif_cn": SERIF_CN, "sans_en": SANS_EN, "sans_cn": SANS_CN,
        "title_en": _esc(frame.get("titleEn")), "title_cn": _esc(frame.get("titleCn")),
        "sentence_index": f'{int(s["index"]):02d}', "sentence_total": f'{int(s["total"]):02d}',
        "sentence_en_html": _mark_vocab(en_text, words, pal), "sentence_cn": _esc(cn_text),
        "vocab_html": cards_html, "vocab_count": len(words), "progress_pct": f"{progress_pct:.2f}",
        "en_size": en_size, "cn_size": cn_size, "en_leading": en_leading,
        "cn_leading": cn_leading, "main_gap": 20,
    })


RATIO_SPEC = {
    # ratio: (renderer, base_w, base_h, device_scale_factor → 输出 target_w×target_h)
    "16:9": {"render": render_h3, "w": 1280, "h": 720, "dsf": 1.5, "out_w": 1920, "out_h": 1080, "design": "H3 Marginalia"},
    "9:16": {"render": render_v3, "w": 540, "h": 960, "dsf": 2.0, "out_w": 1080, "out_h": 1920, "design": "V3 Annotated"},
    "listen-scroll-16:9": {"render": render_listen_scroll_16x9, "w": 1280, "h": 720, "dsf": 1.5, "out_w": 1920, "out_h": 1080, "design": "Listening Scroll"},
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
for %%P in ("%ProgramFiles%\Google\Chrome\Application\chrome.exe" "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe") do if not defined BROWSER if exist "%%~P" set "BROWSER=%%~P"
if "%BROWSER%"=="" ( echo [ERROR] No Chrome/Edge found. Please install Chrome or Edge. & pause & exit /b 1 )
echo Using browser: %BROWSER%
echo Rendering frames to PNG ...
for %%F in (f*.html) do "%BROWSER%" --headless=new --disable-gpu --hide-scrollbars --no-sandbox --disable-extensions --user-data-dir="%CD%\.chrome-profile" --force-device-scale-factor=@@DSF@@ --window-size=@@W@@,@@H@@ --default-background-color=00000000 --screenshot="%CD%\%%~nF.png" "file:///%CD:\=/%/%%F" >nul 2>&1
if not exist "f0000.png" ( echo [ERROR] No frames were captured. Make sure Chrome/Edge is installed and this folder is writable ^(avoid system-protected paths^). & pause & exit /b 1 )
where ffmpeg >nul 2>&1
if errorlevel 1 ( echo [ERROR] ffmpeg not found on PATH. Install ffmpeg ^(https://ffmpeg.org^) then re-run. & pause & exit /b 1 )
echo Assembling out.mp4 ...
ffmpeg -y -f concat -safe 0 -i frames.txt -i audio.wav -vf "fps=25,format=yuv420p" -c:v libx264 -c:a aac -b:a 192k -shortest out.mp4
if errorlevel 1 ( echo [ERROR] ffmpeg failed. No out.mp4 was produced. & pause & exit /b 1 )
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
  "$BROWSER" --headless=new --disable-gpu --hide-scrollbars --no-sandbox --user-data-dir="$PWD/.chrome-profile" --force-device-scale-factor=@@DSF@@ --window-size=@@W@@,@@H@@ --default-background-color=00000000 --screenshot="$PWD/${f%.html}.png" "file://$PWD/$f" >/dev/null 2>&1
done
ls f*.png >/dev/null 2>&1 || { echo "[ERROR] No frames were captured. Check the browser install and folder permissions."; exit 1; }
command -v ffmpeg >/dev/null 2>&1 || { echo "[ERROR] ffmpeg not found."; exit 1; }
if ! ffmpeg -y -f concat -safe 0 -i frames.txt -i audio.wav -vf "fps=25,format=yuv420p" -c:v libx264 -c:a aac -b:a 192k -shortest out.mp4; then
  echo "[ERROR] ffmpeg failed. No out.mp4 was produced."
  exit 1
fi
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
