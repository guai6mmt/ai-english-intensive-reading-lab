# AI English Intensive Reading Lab

这是一个面向《经济学人》等英文长文章的 AI 精读与输出训练系统。它不是普通阅读器，而是围绕一篇文章建立完整学习闭环：

```text
导入文章 -> 文本检查 -> 文章总览 -> 段落分析 -> 句子/长难句 -> 词汇掌握 -> 阅读答题 -> 听写跟读 -> 写作反馈
```

系统使用：

- DeepSeek：文本检查、文章总览、段落分析、句子分析、长难句解析、词汇筛选、阅读题生成
- Qwen：阅读答题评分、听写纠错、词汇造句纠错、写作批改

如果没有配置 API key，系统会自动使用本地降级分析，保证页面和学习流程可以先跑起来。

## 文件结构

```text
.
├── app.py                    # FastAPI 后端与 AI 教学接口
├── static/
│   ├── index.html            # 三栏式学习工作台
│   ├── styles.css            # 页面样式
│   └── app.js                # 前端学习流程交互
├── data/                     # 运行后自动生成，本地学习数据
│   ├── uploads/              # 上传文件
│   ├── library.json          # 文件、文章、AI分析缓存
│   ├── vocabulary.json       # 生词本
│   ├── outputs.json          # 阅读/写作/口语产出
│   └── progress.json         # 学习进度
├── V6_english_analyzer.py    # 旧版脚本，保留不动
└── README.md
```

## 启动方式

先安装依赖。建议始终用同一个 Python 环境执行 `pip` 和 `uvicorn`：

```powershell
cd D:\project\自动化
python -m pip install -r requirements.txt
python -m uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

如果你使用 Anaconda，则先进入目标环境，再安装和启动：

```powershell
conda activate 你的环境名
cd D:\project\自动化
python -m pip install -r requirements.txt
python -m uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

不要混用 `D:\Anaconda\Scripts\uvicorn.exe` 和另一个 Python 的包目录；如果直接运行 `uvicorn` 报 `ModuleNotFoundError: No module named 'fastapi'`，通常就是当前环境没有安装依赖，或 `uvicorn` 来自另一个 Python 环境。

```powershell
uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

浏览器打开：

```text
http://127.0.0.1:8000
```

如果端口被占用：

```powershell
uvicorn app:app --reload --host 127.0.0.1 --port 8010
```

## 配置 DeepSeek 和 Qwen

PowerShell 临时配置：

```powershell
$env:DEEPSEEK_API_KEY="你的 DeepSeek API Key"
$env:QWEN_API_KEY="你的 Qwen / DashScope API Key"
uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

可选模型配置：

```powershell
$env:DEEPSEEK_MODEL="deepseek-v4-flash"
$env:QWEN_MODEL="qwen-plus"
```

默认接口地址：

```text
DEEPSEEK_BASE_URL=https://api.deepseek.com
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

如果你使用兼容 OpenAI Chat Completions 的私有网关，也可以覆盖：

```powershell
$env:DEEPSEEK_BASE_URL="你的 DeepSeek 兼容地址"
$env:QWEN_BASE_URL="你的 Qwen 兼容地址"
```

页面左侧“文件管理与学习数据”会显示 DeepSeek 和 Qwen 是否已配置。

### DeepSeek 常见排查

如果已经输入了 DeepSeek API Key 但功能仍然走本地 fallback，优先检查这几项：

- 先点击“保存模型设置”，再运行文章分析；现在“测试 DeepSeek”会直接使用表单里的临时输入，不必先保存也能测试。
- API Key 输入框留空时会保留已保存的 key，不会再误删 `data/settings.json` 里的配置。
- `Base URL` 可以填写 `https://api.deepseek.com`；如果误填成完整的 `/chat/completions` 地址，后端会自动规范化为 SDK 需要的 base URL。
- 推荐模型：`deepseek-v4-flash`、`deepseek-v4-pro`；如果模型不存在、账户余额不足或 key 无效，测试结果会显示真实错误。
- 本项目要求 AI 返回 JSON。DeepSeek 正常连接但返回非 JSON 时，后端会显示“AI returned non-JSON content”，并继续使用本地 fallback，避免页面中断。

PowerShell 示例：

```powershell
cd D:\project\自动化
$env:DEEPSEEK_API_KEY="你的 DeepSeek API Key"
$env:DEEPSEEK_BASE_URL="https://api.deepseek.com"
$env:DEEPSEEK_MODEL="deepseek-v4-flash"
uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

如果使用页面设置，不需要每次设置环境变量；页面会把配置保存到 `data/settings.json`。
页面的“模型设置”现在提供模型下拉选项；DeepSeek 可在 `deepseek-v4-flash` 和 `deepseek-v4-pro` 之间切换，Qwen 可在常用模型之间切换。
同时可以选择“主模型”。保存后，文章总览、段落分析、句子分析、长难句、词汇、阅读反馈、听写反馈和写作反馈都会优先调用主模型。每次 AI 输出顶部会显示实际使用的 provider/model，便于确认当前结果来自哪个模型。

## 文章清洗

导入文章后，阅读区默认展示“清洗后的正文”。系统会自动删除日期时间、订阅提示、相关文章引导、来源链接和短标题碎片，只保留文章主干内容。

如果已经导入过旧文章，直接重新打开文章即可应用新的规则；需要彻底重建缓存时，可以在左侧“文件管理与学习数据”里点击“重建文章库”。

## 页面布局

### 左侧：文章选择与学习导航

左侧不再让上传区和统计区长期占用大面积空间，而是改为：

- 文件管理与学习数据：折叠入口
- 文章筛选：搜索、题材、语言特点、难度
- 文章库
- 最近学习
- 收藏文章
- 生词本
- 产出中心

文章会自动带有 AI 标签：

- 难度：B1/B2/C1/C2
- 题材：经济、科技、社会、文化、教育、商业、时事等
- 语言特点：长难句较多、学术词汇较多、逻辑连接词较多、适合精读、适合听写、适合写作模仿

### 中间：文章学习区

中间区域只展示真正服务学习的内容：

- 标题和标签
- 清爽正文
- 段落编号
- 可点击句子
- 学习模块 tab

统计信息被隐藏到“文章信息”按钮中，不再长期干扰阅读。

### 右侧：AI 教师面板

右侧根据用户操作动态显示：

- 单句分析
- 句子朗读
- 答题反馈
- 写作反馈
- 听写纠错
- 词汇造句纠错

## 学习模块说明

### 1. 文本检查

进入学习前先运行 AI 文本检查。

检查内容：

- 格式错误修正
- 多余换行修正
- OCR 识别错误疑点
- 标点符号修正
- 段落结构整理
- 明显拼写错误检查

系统保留：

- 原文版本
- AI 清洗版本

可以在页面中切换显示。

### 2. 文章总览

由 DeepSeek 生成：

- 文章主旨
- 核心观点
- 文章结构
- 关键词汇
- 背景知识
- 阅读难点提醒

目标是让学生先建立认知框架，再进入精读。

### 3. 段落分析

由 DeepSeek 逐段分析：

- 段落主旨
- 段落功能
- 段内逻辑关系
- 重要表达
- 可模仿写作结构
- 中文辅助理解

该模块替代旧版“段落地图”，重点训练段落理解和论证结构识别。

### 4. 句子与长难句

长难句解析包括：

- 句子主干
- 修饰成分拆解
- 逻辑关系
- 理解顺序
- 自然中文解释
- 可积累表达
- 仿写任务

点击正文任意句子，可以在右侧 AI 教师面板中生成单句分析。

### 5. 词汇掌握

由 DeepSeek 从文章中筛选真正值得学习的词，而不是罗列所有生词。

词汇分层：

- 核心必会词
- 阅读理解词
- 写作可用词
- 学术表达词
- 熟词僻义词

每个词条包括：

- 语境义
- 原文语境
- 常见搭配
- 近义词辨析
- 例句/仿写任务
- AI 造句纠错
- 加入生词本

### 6. 阅读答题

由 DeepSeek 生成阅读理解输出题。

训练流程：

1. 用户先用英文回答
2. 提交前不显示参考答案
3. Qwen 评分和反馈
4. 显示优化答案和参考答案

反馈维度：

- 内容是否准确
- 是否回答问题
- 逻辑是否清晰
- 语法是否正确
- 词汇是否自然
- 是否可以使用更高级表达

### 7. 听写跟读

听写训练基于长难句生成。

四轮训练：

1. 整体听
2. 逐句听
3. 对照纠错
4. 跟读模仿

提交后由 Qwen 或本地对比给出：

- 原文
- 用户输入
- 漏听词汇
- 拼写或多余词
- 弱读、连读、吞音提示
- 为什么容易听错

### 8. 写作输出

写作任务包括：

- 80 词中立摘要
- 150 词观点短评
- 段落结构仿写

Qwen 批改维度：

- 内容准确性
- 结构完整性
- 语法问题
- 词汇自然度
- 是否使用文章表达
- 修改版参考
- 下一步建议

写作产出会自动保存到产出中心。

## API 接口概览

### 基础

```text
GET  /api/config
GET  /api/library
POST /api/upload
GET  /api/articles/{article_id}
POST /api/articles/{article_id}/favorite
```

### AI 教学接口

```text
POST /api/articles/{article_id}/text-check
POST /api/articles/{article_id}/overview
POST /api/articles/{article_id}/paragraphs/analyze
POST /api/articles/{article_id}/long-sentences
POST /api/articles/{article_id}/sentence/analyze
POST /api/articles/{article_id}/vocabulary/analyze
POST /api/articles/{article_id}/vocabulary/sentence-feedback
POST /api/articles/{article_id}/reading/questions
POST /api/articles/{article_id}/reading/grade
POST /api/articles/{article_id}/dictation/feedback
POST /api/articles/{article_id}/writing/feedback
```

### 学习数据

```text
GET  /api/vocabulary
POST /api/vocabulary
GET  /api/outputs
POST /api/outputs
GET  /api/progress
POST /api/progress
```

## 数据保存

所有数据默认保存在本地 `data/` 目录中。

删除 `data/` 会清空：

- 上传文件
- 文章库
- AI 分析缓存
- 生词本
- 产出中心
- 学习进度

## 推荐使用流程

每篇文章建议这样学：

1. 运行文本检查，切换查看原文和清洗版。
2. 生成文章总览，先理解主旨、背景和阅读难点。
3. 进入段落分析，判断每段功能和逻辑。
4. 生成长难句解析，学习句子主干和理解顺序。
5. 点击正文难句，查看单句 AI 分析。
6. 生成词汇分析，只收藏真正想掌握的词。
7. 用英文回答阅读题，提交给 Qwen 评分。
8. 做听写，提交后查看漏听和弱读提示。
9. 完成摘要或观点短评，提交给 Qwen 批改。
10. 到产出中心复盘自己的阅读回答和写作。

## 注意事项

- EPUB 解析效果通常优于 PDF。
- PDF 结构化解析当前未启用，建议使用 EPUB 或 DOCX。
- AI 输出使用 JSON 模式，便于稳定渲染。
- 如果 API key 未配置，系统会走本地降级逻辑。
- 本工具建议用于个人学习，不要公开分发受版权保护的原文内容。
