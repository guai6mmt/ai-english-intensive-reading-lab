# AI English Intensive Reading Lab

英文精读与私人听力资料库。支持文章导入（EPUB / DOCX / TXT）、正文清洗、长难句分析、词汇分层、阅读答题、听写跟读、写作反馈，以及服务器音频文件夹导入和手机播放。

```
学习主线：文本检查 → 长难句 → 词汇 → 阅读答题 → 听写跟读 → 写作反馈
```

## 音频库与手机使用

- 管理网页可以选择整个电脑文件夹，批量导入 MP3 / M4A / AAC / WAV / FLAC / OGG / OPUS / M4B。
- 服务器可以扫描 `MEDIA_IMPORT_ROOT` 下的目录，并复制到受管理的媒体存储。
- 自动使用 SHA-256 去重，使用 ffprobe 读取时长、码率和标签。
- 支持分类、搜索、标签、难度、收藏、回收站和多设备播放进度。
- 播放接口支持 HTTP Range，可拖动进度、倍速播放、锁屏控制和后台播放。
- 网页包含 PWA manifest；手机浏览器打开后可以添加到主屏幕。

首次启动会显示管理员创建页面。以后所有文章、模型设置和音频接口都需要登录。

---

## Windows 一键启动（推荐）

> 适合用 CMD 或直接双击运行的场景。

### 第一步：下载项目

打开 CMD，进入你想放项目的文件夹，然后：

```cmd
git clone https://github.com/guai6mmt/ai-english-intensive-reading-lab.git
cd ai-english-intensive-reading-lab
```

> 没有 Git？到 [git-scm.com](https://git-scm.com/download/win) 下载安装后重新打开 CMD。

### 第二步：配置 AI Key（可选，首次需要）

```cmd
copy .env.example .env
notepad .env
```

填入你的 API Key，保存关闭。如果暂时没有 Key，可以先跳过，之后在网页设置里填。

### 第三步：启动

```cmd
start.bat
```

脚本会自动完成：
1. 检查 Python 是否安装
2. 创建虚拟环境 `.venv`（首次约需 1 分钟）
3. 安装所有依赖
4. 加载 `.env` 配置
5. 启动服务，**并自动打开浏览器**

启动成功后，浏览器会自动打开：

```
http://127.0.0.1:8010
```

按 **Ctrl+C** 停止服务。

---

### 以后每次使用

进入项目文件夹，直接运行：

```cmd
start.bat
```

或者双击 `start.bat` 文件也可以。

---

### 拉取最新代码后重启

```cmd
git pull origin main
start.bat
```

---

## 系统要求

| 项目 | 要求 |
|---|---|
| Python | 3.10 或更高版本（[下载地址](https://www.python.org/downloads/)，安装时勾选 **Add Python to PATH**） |
| Git | 任意版本（[下载地址](https://git-scm.com/download/win)） |
| 操作系统 | Windows 10 / 11 |

---

## 配置 AI Key

`.env` 文件示例（复制自 `.env.example`）：

```env
DEEPSEEK_API_KEY=你的 DeepSeek API Key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash

QWEN_API_KEY=你的 Qwen / DashScope API Key
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-plus

# 可选：听力模式整篇音频时间轴对齐
DASHSCOPE_API_KEY=你的 DashScope API Key
QWEN_ASR_MODEL=qwen3-asr-flash-filetrans
OSS_ACCESS_KEY_ID=你的 OSS AccessKey ID
OSS_ACCESS_KEY_SECRET=你的 OSS AccessKey Secret
OSS_BUCKET=你的 Bucket 名称
OSS_ENDPOINT=https://oss-cn-hangzhou.aliyuncs.com
OSS_TEMP_PREFIX=asr-temp/
```

也可以直接在网页右上角 → **设置** → 填入 Key → 保存，效果相同。

---

## 常见问题

**Q：提示「Python not found」？**
到 [python.org](https://www.python.org/downloads/) 下载安装，安装时务必勾选「Add Python to PATH」，然后重新打开 CMD。

**Q：端口 8010 已被占用？**
在 CMD 里先设置 PORT 变量再启动：
```cmd
set PORT=8020 && start.bat
```

**Q：浏览器没有自动打开？**
手动访问 `http://127.0.0.1:8010`。

**Q：依赖安装很慢或失败？**
可以先换国内镜像源：
```cmd
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```
然后重新运行 `start.bat`。

**Q：如何完全重装？**
删除 `.venv` 文件夹，再运行 `start.bat`，会重新创建虚拟环境并安装依赖。

---

## Mac / Linux

```bash
git clone https://github.com/guai6mmt/ai-english-intensive-reading-lab.git
cd ai-english-intensive-reading-lab
cp .env.example .env && nano .env   # 填入 API Key
bash scripts/mac_start.sh
```

打开 `http://127.0.0.1:8010`。

第一次打开时创建管理员账号。密码至少 10 个字符。

---

## 服务器部署（Linux）

首次部署：

```bash
git clone https://github.com/guai6mmt/ai-english-intensive-reading-lab.git /opt/ai-english-intensive-reading-lab
cd /opt/ai-english-intensive-reading-lab
bash scripts/server_install_or_update.sh
```

服务只监听 `127.0.0.1:8010`，不能直接暴露到公网。请按照 [deploy/README.md](deploy/README.md) 配置 Caddy/Nginx 和 HTTPS，并在 `.env` 中设置：

```env
COOKIE_SECURE=true
MEDIA_STORAGE_ROOT=/srv/english-lab/media
MEDIA_IMPORT_ROOT=/srv/english-lab/import
```

后续安全更新（会执行一次短暂重启）：

```bash
cd /opt/ai-english-intensive-reading-lab
bash scripts/server_safe_update.sh
```

或从本地一键推送并重启远端：

```bash
bash scripts/deploy_safe.sh ubuntu@服务器IP "本次修改说明"
```

> 首次使用 `server_safe_update.sh` 前，需先手动 `git pull origin main` 把脚本拉到服务器。
> 从旧版本首次升级到音频库版本时，请重新运行一次 `server_install_or_update.sh`，以应用新的 systemd 用户和监听地址。

---

## 数据文件

所有数据在 `data/` 目录，不会上传到 GitHub：

```
data/
├── library.json      # 文章库
├── vocabulary.json   # 生词本
├── outputs.json      # AI 反馈
├── progress.json     # 学习进度
├── settings.json     # 网页保存的模型设置
├── uploads/          # 上传的原始文章
├── app.db            # 用户、会话、音频库和播放进度
└── media/            # 受管理的音频文件
```

---

## 项目结构

```
ai-english-intensive-reading-lab/
├── start.bat                       # Windows 一键启动（双击或 CMD 运行）
├── app.py                          # FastAPI 后端
├── V6_english_analyzer.py          # 难度 / 词频分析
├── requirements.txt
├── requirements-dev.txt
├── english_lab/                    # 登录、数据库、健康检查和媒体库模块
├── tests/                          # API 与流媒体集成测试
├── deploy/                         # HTTPS 反向代理示例
├── .env.example
├── scripts/
│   ├── win_start.bat               # 同 start.bat（scripts 内的备用版本）
│   ├── win_start.ps1               # PowerShell 版启动
│   ├── mac_start.sh                # Mac / Linux 本地启动
│   ├── server_install_or_update.sh # 服务器首次部署（systemd）
│   ├── server_safe_update.sh       # 服务器备份、校验和安全更新
│   └── deploy_safe.sh              # 本地校验 + 远程安全重启
├── static/
│   ├── index.html
│   ├── app.js
│   ├── media.html / media.js       # 音频管理与手机播放器
│   ├── auth.js / login.html        # 会话登录
│   ├── manifest.webmanifest        # PWA
│   └── styles.css
└── data/                           # 运行数据（gitignore）
```
