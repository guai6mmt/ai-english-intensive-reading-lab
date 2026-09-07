# Read · 英语阅读与句子复习

一个围绕 **读文章 → 听原音 → 点句精读 → 收藏复习** 的私人英语学习系统。

## 功能

| 功能 | 使用方式 |
| --- | --- |
| 文章库 | 导入 EPUB、DOCX、TXT；先按杂志筛选，再选择该杂志的板块，也可搜索标题；筛选选择会自动保留 |
| 舒适阅读 | 电脑采用居中单栏，手机自动适配；保留原文段落；标准、大、特大三档字号 |
| 原版音频 | 播放与文章关联的原音频；底部播放器支持暂停、拖动进度以及 0.5×–2× 共 15 档速度；播放位置与语速保存在服务器，可跨设备继续 |
| 跟读高亮 | 优先读取缓存的原版音频时间轴，必要时自动准备；播放和拖动进度时高亮当前句，并在句子离开画面时跟随滚动 |
| AI 句子精读 | 点击完整句子，结合文章上下文给出中文译文、句子主干、长难句意群拆分和语法解析；支持 DeepSeek、通义千问及其兼容服务地址；成功结果缓存在服务器 |
| 句子本 | 收藏完整英文句子、中文译文与文章来源；可回到原文；重复收藏不会新增重复条目 |
| 间隔复习 | 全部句子、今日复习；先看英文，再展开译文；选择“还不熟 / 记住了 / 很熟悉”安排后续复习 |
| 学习记录备份 | 句子本中可下载 JSON，包含句子、旧生词及复习记录 |
| 内容导入 | 文章可独立上传；EPUB 可同时上传配套音频 ZIP；配对结果在音频管理页确认 |
| 音频管理 | 设置中进入音频管理，支持文件夹上传、服务器扫描、搜索、回收站和文章配对 |
| 登录与配置 | 首次访问创建管理员；登录会话、请求防伪保护；网页填写 AI 密钥，服务器加密保存 |
| 一键维护 | Windows 双击启动；服务器一键安装与安全更新；更新前完整备份，启动后健康检查，失败尝试恢复旧代码 |

主导航只保留 **文章库、句子本、设置**。音频管理放入设置，阅读时不显示复杂的管理按钮。

### 本次精简

已删除一键分析及整篇教学分析接口，包括文本检查、总览、段落分析、长难句分析、词汇分析、阅读题、听写/写作批改和学习包。删除旧多面板前端、旧听力练习页面、独立分析器和视频导出模板/代码。

点击单词查词已由点击句子 AI 翻译替代。新句子本仅显示收藏的句子；原有单词记录仍保留，可通过学习记录 JSON 导出，不会伪造句子译文或清空旧记录。部分底层音频、词典、WebDAV 和复习兼容接口继续保留，以兼容已有数据和客户端。

### 阅读说明

- 原版音频需要先与文章关联。没有配套音频时仍可正常阅读和翻译；系统不会把合成语音标为原版音频。
- 本版支持边听边读。高亮状态会显示精确对齐和补间句数；原版音频首次生成精确时间轴需要配置 Qwen ASR 与 OSS，结果会缓存，之后直接读取。
- 首次句子精读需要网络和有效 AI 配置；调用失败会明确提示，不使用本地占位解释冒充 AI 结果。再次点击可重试。
- 句子译文与解析、收藏、复习和音频进度存放在服务器；字号及上次点击的句子位置存放在当前浏览器。
- 句子本一次最多展示 2,000 条，可通过导出下载全部记录。
- 手机可直接通过服务器 HTTPS 地址使用。不提供离线翻译；升级会清理旧版浏览器缓存，服务器材料不受影响。

## 本地一键启动

Windows 双击 `start.bat`。首次启动安装依赖，随后访问提示的本地地址。

Mac / Linux：

```bash
bash scripts/mac_start.sh
```

手动运行（Python 3.12 推荐）：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 8010
```

首次打开创建管理员，再进入“设置”填写 AI 服务商、密钥、模型和服务地址。密钥留空保存时保留原有密钥。阅读与原版音频播放不依赖 AI 密钥。

## 服务器一键安装 / 更新

适用于 Debian / Ubuntu、systemd。域名应已解析到服务器，首次安装配置 Caddy HTTPS。

**首次安装及后续更新使用同一条命令**，将 `english.example.com` 替换为自己的域名：

```bash
curl -fsSL https://raw.githubusercontent.com/guai6mmt/ai-english-intensive-reading-lab/main/install.sh | sudo bash -s -- english.example.com
```

默认代码目录：`/opt/ai-english-intensive-reading-lab`。检测到已有仓库后走安全更新，不会重新生成数据目录配置。

如果原来部署在其他目录，请在那个目录执行以下命令。它会先从 GitHub 获取新版更新器，兼容仍在使用旧脚本的服务器：

```bash
git fetch origin main && git show origin/main:scripts/server_safe_update.sh | ENGLISH_LAB_APP_DIR="$PWD" bash
```

之后可直接在项目目录执行：

```bash
bash scripts/server_safe_update.sh
```

非默认端口或服务名可指定：

```bash
PORT=8010 SERVICE_NAME=ai-english-lab bash scripts/server_safe_update.sh
```

### 为什么更新后材料还在

代码与运行数据分别管理。更新只拉取 Git 中的代码，**不删除或初始化现有运行数据**；数据库升级仅新增翻译缓存表。安装器优先沿用 `.env` 中已配置的目录，无配置但存在旧 `data/` 时继续使用旧目录。

| 配置 | 内容 |
| --- | --- |
| `ENGLISH_LAB_DATA_DIR` | `library.json` 文章、`uploads/` 原文件、封面、`app.db` 账号/句子/进度、AI 设置与 `.settings.key` |
| `MEDIA_STORAGE_ROOT` | 原版音频文件，可能位于数据目录外 |
| `MEDIA_IMPORT_ROOT` | 待扫描的服务器导入材料 |

全新服务器默认数据目录为 `/srv/english-lab/data`，其下 `media/`、`import/` 为默认音频及导入目录；旧部署继续沿用旧路径，包括 `/srv/english-lab/media` 等外置目录。

安全更新会：

1. 检查本地代码是否有未提交修改或分支分叉；有冲突就停止，不强制覆盖。
2. 获取远端版本，停止服务，完整备份数据、数据库、音频、导入目录和 `.env`。
3. 更新代码和依赖，校验代码并重新启动服务。
4. 检查 `/health/ready`；失败时尝试切回旧提交、恢复旧依赖并重新启动原服务。不会自动覆盖用户数据。

备份保存在项目 `backups/full-日期时间.tar.gz`，或 `BACKUP_DIR` 指定目录；内含 `manifest.json` 标明原始路径。备份不会自动删除。**音频库越大，备份和暂停服务时间越长，需预留足够空间。** 备份失败会停止更新并尝试恢复原服务。代码回退不代表数据库降级；本次迁移为向后兼容的新增表。

更换服务器时应恢复整套目录及 `.env`，特别是 `.settings.key`，并保持原来的绝对路径，或按备份清单调整配置与文章源路径。只恢复代码仓库不会恢复材料。不要使用 `git clean -fdx` 清理部署目录。

## 本地修改后，一条命令发布

在 Git Bash / Mac / Linux 中运行（需配置 GitHub 推送权限和服务器 SSH 登录）：

```bash
bash scripts/deploy_safe.sh user@server "本次修改说明"
```

该命令检查代码、提交修改、推送当前仓库 `main`，再通过 SSH 执行安全更新。默认远端目录为 `/opt/ai-english-intensive-reading-lab`；可以使用 `REMOTE_DIR`、`PORT`、`BRANCH` 修改。`deploy_to_server.sh` 也统一使用相同安全流程。

## 验证与维护

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
node --check static/app.js
```

测试覆盖账号权限、音频导入/流播放/进度、文章解析与配对、句子翻译缓存/失败处理/收藏/复习，以及更新路径保留与完整备份。浏览器验证使用隔离测试数据和模拟 AI 响应，不代表真实服务商连通性测试。

查看服务器状态：

```bash
sudo systemctl status ai-english-lab
sudo journalctl -u ai-english-lab -n 100 --no-pager
```

更多恢复说明见 [部署维护文档](deploy/README.md)。
