# 英语精读视频导出方案（路线 B：后端导素材 + 本地 ffmpeg 合成）

> 目标：复用系统现有内容，生成「主题背景 + 中文标题 + 逐句难词框 + 双语字幕（随音频推进）」的精读视频。
>
> **难词框只显示「单词 + 中文翻译」**，不放音标、不放词性——与听力模式生词区保持一致的极简风格。
>
> **本轮范围：先把 16:9（B 站）跑通。** 9:16 竖屏的设计已写好并保留（见 §3.5），但**本轮暂缓、不实现**，等 16:9 验证通过后再做。
>
> 实现路线：**路线 B** —— 后端把素材（音频 + ASS 字幕 + 背景图 + 一键 ffmpeg 脚本）打包导出，你在本地跑 ffmpeg 合成 MP4。验证通过后再升级到服务端一键导出（路线 C）。

## 0. 决策记录（已确认）

| 项 | 决定 |
|---|---|
| 难词展示 | **仅 单词 + 中文翻译**（不含音标、不含词性） |
| 视频比例 | **本轮仅 16:9（B 站）**；9:16（竖屏）设计保留、后续再做 |
| 难词选取 | **按每句内容**选难词（逐句，非全篇 Top N） |
| 背景图 | 后续根据内容用 AI 生成（本轮先用占位） |
| 实现路线 | 路线 B（后端导素材 + 本地 ffmpeg） |

---

## 1. 系统已有、可直接复用的资产

| 视频要素 | 来源（现有，已落地） | 字段 |
|---|---|---|
| 句子切分 | `listening_sentence_items()`（app.py:1743）/ `GET /api/articles/{id}/listening/sentences` | `index, para, text` |
| 中文翻译 + **逐句难词** | `POST /api/articles/{id}/listening/prepare`，缓存于 `article.sentence_analyses` | `translation`、`vocab[{term, meaning, note}]` |
| **逐句时间轴** + 整篇音频 | `aligned-audio/start` → `data/audio_cache/{key}.align.json` + `/audio/{key}.wav` | `alignments[{index, begin_ms, end_ms}]` |
| 背景/封面（临时） | EPUB 封面 `/covers/{id}.jpg`（已落地） | 临时占位 |
| 标题 / 出处 | `article.title`、`source.filename` | — |

> **关键复用**：逐句 `begin_ms/end_ms` + 双语文本 + 整篇音频 = 一份**双语 ASS 字幕**所需的全部数据。这套时间轴是做字幕视频最难的部分，已经有了。难词框只用到 `vocab` 里现成的 `term`（单词）和 `meaning`（中文翻译），**无需任何新增模块**。

> **⚠ provider 维度（务必注意）**：系统已支持**按文章选择 TTS provider（qwen / minimax）**。`aligned_audio_cache_key()`（app.py:1780）的输入里包含 `provider:voice / tts_model / asr_model`，因此**同一篇文章每个 provider 各生成一份独立的 `{key}.wav` 与 `{key}.align.json`**。
>
> 导出端点**不能假设只有一份**音频/对齐文件，必须先确定"导出哪个 provider 的版本"，再据此算出 `key` 来定位文件（详见 §3.2）。这一点在多 provider 特性合并后是新增的约束，原始草案未覆盖。

## 2. 需要新增的能力

1. **视频导出端点**：把素材打包为目录/zip；**按 provider 解析对齐/音频文件**（见 §3.2）。
2. **ASS 字幕生成**：双语字幕 + 逐句难词框（词+译），**本轮仅 16:9**；含 **文本转义 + WrapStyle 折行控制**（见 §3.3）。9:16 设计保留、暂缓。
3. **本地 ffmpeg 合成脚本**（随包导出，开箱即用；本轮仅 16:9）。
4. **（后续）AI 背景图生成**（不阻塞本轮验证）。

> 难词框直接用 `vocab` 的 `term + meaning`，**不需要音标/词性**，因此不引入任何新依赖。

---

## 3. 详细设计

### 3.1 难词内容（仅 单词 + 中文翻译）

难词框直接取 `prepare` 已缓存的 `vocab[{term, meaning}]`（`note` 字段视频不使用），**不显示音标、不显示词性**，与听力模式生词区保持一致的极简风格——每个难词两行：**单词** + **中文翻译**。不需要任何音标/词性模块或新依赖。

### 3.2 视频导出端点（`app.py`）

```
POST /api/articles/{id}/video/export-package
body: { ratios: ["16:9"], provider?: "qwen"|"minimax", audio_format: "wav"|"mp3" }
```
> 本轮 `ratios` 固定 `["16:9"]`；`provider` 缺省时回退到该文章听力模式当前选用的 provider。

**前置条件（端点内自检/触发）**

0. **确定 provider 并算出 key（关键，先做）**：对齐/音频文件名 `{key}` 与 provider 强绑定（见 §1 ⚠）。
   - 取 `request.provider`；缺省则回退到该文章听力模式当前 provider（与前端 `audio-variants` 的选择逻辑一致）。
   - 用**纯函数** `_build_aligned_context(article, id, items, provider)`（app.py:3103，注释明确"不要求 provider 是当前设置"）算出 `key` 与 `align_path / audio_path`。
1. 该 provider 的对齐音频已生成（`align_path` + `audio_path` 都存在）→ 取时间轴；否则触发一次 `aligned-audio` 生成，或报错提示"请先在听力模式生成该 provider 的整篇音频"。
2. 已生成 `prepare`（`sentence_analyses` 有内容）→ 取翻译 + 难词；缺则内部调用一次。

> **🔒 音画同源铁律**：导出的 `audio.wav` 与 `subtitle_16x9.ass` 的时间轴**必须来自同一个 `key`（同一 provider）**。否则一旦换 provider，音频和字幕会整体错位、全程不同步。

**组装流程（伪代码）**
```
# 0) 解析 provider → key → 文件路径（务必音画同源）
provider = request.provider or article_current_audio_provider(article)   # 与前端一致
items    = listening_sentence_items(article)                             # index, para, text
ctx      = _build_aligned_context(article, id, items, provider)          # 纯函数，算 key/路径
assert ctx["align_path"].exists() and ctx["audio_path"].exists()         # 否则触发生成/报错

# 1) 取三份数据
prep   = sentence_analyses 里的 translation + vocab    # 逐句
align  = json.load(ctx["align_path"])["alignments"]    # 逐句 begin_ms/end_ms

# 2) 逐句组装：难词只取 词 + 中文翻译，并绑时间轴
for 每句 s:
    s.box_vocab = [{term: v.term, meaning: v.meaning} for v in s.vocab]   # 仅 词 + 译
    s.begin, s.end = align[s.index]

# 3) 导出素材包（本轮仅 16:9）
data/video_export/{id}/
  ├─ audio.wav                 (复制/转码自 ctx["audio_path"]，与字幕同源)
  ├─ subtitle_16x9.ass
  ├─ background_16x9.png       (本轮：封面/纯色占位)
  ├─ meta.json                 (title, source, provider, key, 句数, 时长ms)
  ├─ render.bat / render.sh    (现成 ffmpeg 命令)
  └─ README.txt                (使用说明)
返回: 该目录的下载链接(zip) 或 路径
```

### 3.3 ASS 字幕生成（双语 + 难词框，16:9）

**为什么用 ASS 而非 SRT**：SRT 只能放底部纯文字；ASS 支持**定位 + 多样式**，能同时画底部双语字幕 + 角落难词框 + 标题条。

**ASS 文件结构**
```ass
[Script Info]
PlayResX: 1920
PlayResY: 1080
ScaledBorderAndShadow: yes
WrapStyle: 0            ; 0=智能折行(上行略宽，居中美观)；长英文句靠它+Margin 自动换行

[V4+ Styles]
; Name, Fontname, Fontsize, PrimaryColour, OutlineColour, ... Alignment, MarginL/R/V
Style: Title,   Source Han Serif SC, 54, &H00FFFFFF, &H00202020, ... 8, ...   ; 顶部标题
Style: SubEN,   Georgia,             56, &H00FFFFFF, &H00101010, ... 2, ...   ; 底部英文(大)
Style: SubZH,   Source Han Sans SC,  40, &H00D8D8D8, &H00101010, ... 2, ...   ; 底部中文(次级)
Style: VocabBox,Source Han Sans SC,  34, &H00FFFFFF, &H80000000, ... 9, ...   ; 难词框(半透明底)

[Events]
; 标题：整片常显（或前 N 秒）
Dialogue: 0,0:00:00.00,9:99:99.99,Title,,0,0,0,,中文标题…
; 每句：英文 + 中文（时间 = begin→end）
Dialogue: 0,0:00:03.20,0:00:07.10,SubEN,,0,0,80,,massive Western brands crossing over into the East
Dialogue: 0,0:00:03.20,0:00:07.10,SubZH,,0,0,30,,庞大的西方品牌跨入东方市场，
; 难词框：该句对应的难词（右上定位），仅「单词 + 中文翻译」，时间跟随该句
Dialogue: 0,0:00:03.20,0:00:07.10,VocabBox,,0,0,0,,{\pos(1560,150)}nurture 培养；滋养\Nchampion 捍卫；推广
```
- 双语两行用两条 Dialogue（不同 MarginV）。
- 难词框用 `\pos(x,y)` 定位；多词用 `\N` 分行；半透明底用 `OutlineColour`/`BackColour` + `BorderStyle=3`。每个难词为「单词 + 中文翻译」（可用空格或缩进分隔）。
- 颜色用 ASS 的 `&HAABBGGRR`（BGR + alpha，alpha 00=不透明）。

**🔧 文本转义（必做）—— 句子若含 `{ } \` 会破坏样式标签**

放进 Dialogue 文本字段的**任何原文**（英文句、中文译文、难词释义）都必须先过转义。否则正文里出现的 `{`/`}` 会被 libass 当成 override block 边界、`\` 会被当成 `\tag` 前缀，导致字幕错乱或文字丢失。

```python
def ass_escape(text: str) -> str:
    """转义将放入 ASS Dialogue 文本字段的内容，避免 { } \\ 破坏样式标签或布局。
    顺序很重要：必须先处理原文里已有的反斜杠，再加我们自己合法的 \\{ \\} \\N。"""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\\", "\\​")        # 反斜杠后插零宽空格(U+200B)，阻止其被当作 \tag 前缀
    text = text.replace("{", "\\{").replace("}", "\\}")  # 花括号是 override block 边界
    text = text.replace("\n", "\\N")                  # 段内真实换行 → ASS 硬换行
    return text
```
- 文章正文出现 `{ } \` 的概率极低，本函数主要是**兜底**，确保任何文本都不会破坏渲染。
- **职责分工**：`\N` 只用于把原文里**真实存在的换行**转成 ASS 换行；**英文长句的自动折行不要手动塞 `\N`**，交给 `WrapStyle: 0` + `SubEN` 的 `MarginL/MarginR`（左右留白）让 libass 自动均匀折行，居中更美观。难词框/标题这类需要精确控制的，才用 `\N` 手动断行。

### 3.4 16:9 版式（B 站，1920×1080）

```
┌───────────────────────────────────────────────┐
│ [左上] 栏目/来源小标签        [顶部横条] 中文标题  │
│                                                 │
│        （全屏主题背景图 + 轻微暗化遮罩）          │
│                              ┌──────────────┐   │
│                              │  难词框        │   │  ← 右上，半透明白底
│                              │ nurture       │   │     逐句更新
│                              │ 培养；滋养      │   │     (单词 + 中文翻译)
│                              └──────────────┘   │
│                                                 │
│           massive Western brands…  (英文，大)    │  ← 底部居中双语
│           庞大的西方品牌跨入东方市场，(中文，次级) │
│                            [右下] 参考文献：…     │
└───────────────────────────────────────────────┘
```
- 字号参考：标题 ~48–54、英文 ~52–60、中文 ~36–42、难词框 ~30–36。
- 底部字幕 MarginV ~60–90；难词框 `\pos` 约 (1560,140) 起。
- 英文长句：设 `SubEN` 的 `MarginL/MarginR`（如各 ~300）限定文字宽度，配合 `WrapStyle:0` 自动折行。

### 3.5 ⏸ 9:16 版式（竖屏，1080×1920）—— 本轮暂缓，设计保留

> **本轮（16:9 优先）不实现以下竖屏设计**。保留在此供后续阶段（B2）直接取用。竖屏宽度只有 1080，**不能把难词框放在角落浮层**，必须**纵向分区堆叠**——这是与 16:9 最大的不同。

```
┌───────────────────┐  1080 × 1920
│                   │
│   主题背景图        │  ① 顶部 ~38%（约 0–730px）
│   + 中文标题(压图)  │     背景图铺满该区，底部渐变压暗，标题叠在图上
├───────────────────┤
│  ┌─────────────┐  │  ② 难词区 ~26%（约 730–1230px）
│  │ 难词卡片      │  │     整宽半透明卡片，逐句更新
│  │ nurture      │  │     竖屏建议每句最多显示 1–2 个词
│  │ 培养；滋养     │  │     (词多则只取该句最难的 1–2 个)
│  └─────────────┘  │
├───────────────────┤
│  massive Western  │  ③ 字幕区 ~26%（约 1230–1730px）
│  brands crossing… │     英文(大,可折行) + 中文，字号比 16:9 更大
│  庞大的西方品牌…    │
├───────────────────┤
│ 进度条 ▍▍▍   出处  │  ④ 底部 ~10%（约 1730–1920px）
└───────────────────┘
```

**与 16:9 的差异点（逐条，后续做 B2 时参考）**
| 元素 | 16:9 | 9:16 |
|---|---|---|
| 背景图 | 全屏铺满 | 仅顶部 ~38% |
| 标题 | 顶部独立横条 | **叠在背景图上** |
| 难词框 | 右上角浮层 | **整宽卡片，独立分区** |
| 难词数量 | 可多个 | **每句限 1–2 个**（窄屏空间；需难度排序，见 §6） |
| 字幕字号 | 中等 | **更大**（手机观看） |
| 字幕宽度 | 居中留白多 | 接近满宽 |

> 升级到路线 C（服务端 HTML→帧渲染）时，这套 9:16 分区可直接转成一个独立的竖屏 HTML 模板。

### 3.6 本地 ffmpeg 合成（随包导出脚本）

**16:9（本轮）**
```bash
ffmpeg -loop 1 -i background_16x9.png -i audio.wav \
  -vf "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,ass=subtitle_16x9.ass:fontsdir=fonts" \
  -c:v libx264 -tune stillimage -pix_fmt yuv420p -r 25 \
  -c:a aac -b:a 192k -shortest out_16x9.mp4
```

**⏸ 9:16（暂缓，B2 再用）**
```bash
ffmpeg -loop 1 -i background_9x16.png -i audio.wav \
  -vf "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,ass=subtitle_9x16.ass:fontsdir=fonts" \
  -c:v libx264 -tune stillimage -pix_fmt yuv420p -r 25 \
  -c:a aac -b:a 192k -shortest out_9x16.mp4
```

**注意**
- **中文字体**：ASS Style 的 `Fontname` 必须是本机/`fontsdir` 里存在的字体（如「思源黑体 / Source Han Sans SC」「微软雅黑」）。导出包可附字体或在 README 说明安装。
- 静态背景 + ffmpeg `-tune stillimage` 编码很快；`-shortest` 让视频长度对齐音频。
- 想要背景轻微运动（Ken Burns 缓慢放大）后续可加 `zoompan`。

### 3.7 （后续）AI 背景图生成

- 本轮占位：用书封面或纯色/渐变背景，先把流程跑通。
- 后续：根据文章标题/主题用**文生图**生成 16:9 主题图。
  - 注意：现配置的 Qwen-VL 是「看图理解」，**不是文生图**；需另接图像生成模型（如通义万相 wanx / 其它）。
- 此步独立、不阻塞字幕视频验证。

---

## 4. 依赖与改动清单

**新增 pip 依赖**：无（难词框只用现成的 `term + meaning`，不引入音标/词性模块）。

**本地工具**：`ffmpeg`（用户机器安装一次）。

**代码改动**
- `app.py`：
  - 新增 `POST /video/export-package` 端点（本轮 `ratios=["16:9"]`）。
  - **provider 解析**：复用 `_build_aligned_context()`（app.py:3103）按 provider 算 `key`/路径，保证音画同源。
  - ASS 生成函数 + `ass_escape()`（§3.3）+ 打包逻辑；难词框只取 `term + meaning`。
  - 新增 `data/video_export/` 目录与（可选）`/video_export` 静态挂载。
- **不改动**现有音频拼接 / 对齐 / prepare 逻辑（只读复用）。
- 前端导出按钮留到 B3。
- 9:16 相关（`subtitle_9x16.ass`、9:16 背景/脚本）本轮**不写**。

---

## 5. 分阶段验证计划（每阶段可单独验收）

| 阶段 | 内容 | 验收标准 |
|---|---|---|
| **B0** | 后端按 provider 取对齐数据，导出**双语字幕（先 SRT）+ 同源音频**，本地 ffmpeg 合成 16:9 MP4 | 字幕与语音**逐句对齐**、双语正确；换 provider 不错位 |
| **B1** | 升级 ASS：加**难词框（词+译）+ 标题** + 16:9 完整版式 + **文本转义/WrapStyle** | 难词框逐句更新、长句自动折行、含 `{}\` 的句子不崩 |
| **B3** | 前端「导出视频包」按钮 / 批量导出（仍仅 16:9） | 一键导出某篇 → 拿到可直接 ffmpeg 的素材包 |
| ⏸ **B2** | （暂缓）增加 9:16 版式（§3.5 分区） | 16:9 验证通过后再启动 |

> 验证用真实文章：需先对该文章跑过 `aligned-audio`（出时间轴）和 `prepare`（出翻译+难词）。

---

## 6. 风险与注意点

- **provider 维度**：同一文章每个 provider 各一份对齐/音频文件；导出端点必须按 provider 定位 `key`，且导出音频与字幕须**同源**（见 §3.2）。
- **ASS 文本转义**：句子含 `{ } \` 会破坏样式标签，必须经 `ass_escape()`（§3.3）；英文长句靠 `WrapStyle:0` + Margin 自动折行，不要手动塞 `\N`。
- **（竖屏才有的）难词排序**：9:16 每句限 1–2 词，但 `vocab` 无难度分；做 B2 前需定义"取最难 1–2 个"的依据。本轮 16:9 可全显示，暂不涉及。
- **中文字体**：ffmpeg 烧录 ASS 依赖 fontconfig/字体存在，跨机器要带字体或指明。
- **视频时长**：长文章 → 长视频；音频为 TTS 朗读，注意整片时长是否适合平台。
- **版权**：内容来自经济学人/新华社等导入书籍，**公开上传需注意版权与出处标注**，请自行把握。

---

## 7. 落地顺序建议（聚焦 16:9）

1. 写导出端点 **B0 版**（双语 SRT + 音频，仅 16:9），并实现 **provider 解析**（复用 `_build_aligned_context` 算 key、保证音画同源），跑通 ffmpeg 16:9，确认音画字幕同步。
2. 升级 ASS（B1）：难词框（词+译）+ 标题 + **`ass_escape` 转义 + `WrapStyle` 折行**，完成 16:9 完整版式。
3. 16:9 满意后，再考虑 9:16（B2，设计见 §3.5）、AI 背景图、服务端一键导出（路线 C）。

> 确认本方案后，从第 1 步开始实现。
