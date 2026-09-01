# AI English Intensive Reading Lab

一个可自行部署的英语精读与听力训练系统。管理员可以在网页中导入文章和整个音频文件夹，学习者可以在电脑或手机上登录同一台服务器，继续阅读、听写和音频练习。

项目仓库：<https://github.com/guai6mmt/ai-english-intensive-reading-lab>

## 最快部署

域名解析到 Debian/Ubuntu 服务器并开放 80/443 后，root 用户只需运行下面一行。将最后的 `english.example.com` 换成自己的域名：

```bash
curl -fsSL https://raw.githubusercontent.com/guai6mmt/ai-english-intensive-reading-lab/main/install.sh | bash -s -- english.example.com
```

完成后打开 `https://你的域名`：创建管理员 → 网页自动打开设置 → 按需填写 AI Key → 开始导入文章或音频。除域名外，不需要在服务器终端配置任何内容。

## 系统功能

### 私人音频资料库

| 功能 | 具体能力 |
|---|---|
| 批量导入 | 首页同时选择 EPUB + 音频 ZIP，或在音频库选择整个文件夹、扫描服务器导入目录 |
| 音频格式 | MP3、M4A、AAC、WAV、FLAC、OGG、OPUS、M4B |
| 大文件处理 | 8 MB 分片上传、失败自动重试、最大文件大小可配置 |
| 自动整理 | 使用 SHA-256 去重，通过 ffprobe 读取时长、码率和音频标签 |
| 文章配套 | 按期刊日期、栏目和栏目内顺序自动匹配文章与原版音频，低置信度结果单独提示 |
| 人工补配 | 自动匹配失败时，可试听并从下拉列表手动指定音频；自动结果与人工结果统一确认保存 |
| 资料管理 | 修改标题、分类、难度和标签，支持搜索、筛选、排序、分页和回收站 |
| 学习状态 | 收藏音频、记录播放位置，并在电脑和手机之间同步进度 |
| 移动播放 | 响应式播放器、前后 15 秒、倍速、A-B 循环、锁屏控制和后台播放 |
| 流媒体 | 登录后私有访问，支持 HTTP Range，可拖动进度且无需完整下载 |
| PWA | 可添加到主屏幕；文章可连同原版音频按篇离线保存，离线学习记录联网后补交 |
| 第三方播放器 | 通过只读 WebDAV 在支持该协议的手机 App 中浏览和播放服务器音频 |

### 英语精读学习

| 功能 | 具体能力 |
|---|---|
| 内容导入 | 导入 EPUB、DOCX、TXT；一期 EPUB 可与音频 ZIP 一次完成导入和配对 |
| 文本预处理 | 正文提取、清洗和章节整理 |
| 长难句分析 | 分析句子结构、语法成分及理解难点 |
| 词汇学习 | 正文点击查词、语境生词本、重复遇词、到期复习及 CSV/Anki/JSON 导出 |
| 阅读训练 | 阅读理解题、答题与结果记录 |
| 听写跟读 | 结合文章和音频进行逐句听写、跟读训练 |
| 写作反馈 | 使用 AI 对写作内容进行分析和反馈 |
| 模型配置 | 支持 DeepSeek、Qwen/DashScope 与 MiniMax TTS，可在网页设置中填写 Key |

### 管理与安全

- 首次访问创建唯一管理员账号，密码使用 Argon2 哈希保存。
- 所有文章、设置和音频接口均受登录会话保护。
- 使用 HttpOnly、SameSite Cookie 和 CSRF 校验；公网部署自动启用 Secure Cookie。
- SQLite WAL 保存用户、媒体、收藏、进度、书签和导入任务。
- API Key 等敏感网页配置使用服务器本地密钥加密后落盘，应用密码只保存 Argon2 哈希。
- WebDAV 仅在 HTTPS 下启用、只允许读取，并可为每台手机单独生成和吊销应用密码。
- 服务以非 root 用户运行，只监听 `127.0.0.1`，由 Caddy 提供 HTTPS。
- 提供存活与就绪健康检查、自动重启和更新前数据库备份。

## 使用流程

```text
管理员登录
  ├─ 导入一期（EPUB + 音频 ZIP）→ 自动配对 → 逐句同步
  ├─ 单独导入文章 → 清洗 → 长难句 / 词汇 / 阅读 / 听写 / 写作
  ├─ 导入音频文件夹 → 自动校验去重 → 分类整理
  └─ 内容配套 → 自动匹配 → 人工补配 / 试听确认 → 文章页播放原版音频
                                                    └─ 手机同步收藏与进度
```

登录后访问：

- `/`：英语精读工作台
- `/media`：音频资料库与手机播放器
- `/login`：登录或首次创建管理员

## 在手机播放器中连接音频库

一键 HTTPS 部署完成后，在文章库打开“设置 → 手机远程访问”：

1. 输入设备名称，例如“我的 iPhone”，点击“生成应用密码”；
2. 立即复制服务器地址、管理员用户名和一次性显示的应用密码；
3. 在手机播放器中新增 WebDAV 服务器，粘贴这三项信息；
4. 在播放器中按资料分类浏览和练习音频。

远程入口是只读的：手机播放器不能修改或删除服务器内容。建议每台设备使用不同的应用密码；手机丢失时，只需在网页中吊销对应密码，不必修改管理员登录密码。为避免账号在网络中明文传输，HTTP 本地运行不会启用此功能。

## Linux 服务器一键部署（推荐）

适用于 Debian、Ubuntu 及其常见云服务器。仓库已经公开，安装过程不需要 GitHub 账号、密码、Token 或 SSH Key。

部署前只需要：

1. 准备一个域名，并将域名的 A/AAAA 记录解析到服务器。
2. 在云平台安全组或防火墙中开放 TCP `80` 和 `443`。
3. 使用 root 登录服务器；普通用户也可以使用下面的 sudo 命令。

### 一条命令完成安装

root 用户直接复制下面这一行，只把最后的示例域名换成自己的域名：

```bash
curl -fsSL https://raw.githubusercontent.com/guai6mmt/ai-english-intensive-reading-lab/main/install.sh | bash -s -- english.example.com
```

普通用户使用：

```bash
curl -fsSL https://raw.githubusercontent.com/guai6mmt/ai-english-intensive-reading-lab/main/install.sh | sudo bash -s -- english.example.com
```

域名是 HTTPS 建立前唯一必须提供的服务器信息，无法等网页启动后再设置。除此之外，不需要在终端填写任何配置。

脚本会自动完成：

1. 从公开 GitHub 仓库下载最新代码到 `/opt/ai-english-intensive-reading-lab`；
2. 安装 Python、FFmpeg、SQLite，并通过 [Caddy 官方软件源](https://caddyserver.com/docs/install#debian-ubuntu-raspbian)安装 Caddy；
3. 创建虚拟环境、运行用户及 `/srv/english-lab` 数据目录；
4. 自动生成服务端安全配置，不写入任何假的 API Key；
5. 创建并启动 systemd 服务；
6. 配置 Caddy HTTPS、压缩和安全响应头；
7. 执行健康检查并显示最终访问地址。

安装完成后打开 `https://你的域名`，创建管理员账号。系统随后会自动打开网页设置面板，在网页中填写模型和 API Key。音频资料库不需要 AI Key，可以直接使用。

> Caddy 需要域名已经正确解析，才能自动申请 HTTPS 证书。DNS 刚修改时可能需要等待解析生效。

### 一键更新

再次执行完全相同的一键安装命令即可更新。脚本会识别现有安装、拉取公开仓库的最新代码并保留数据库、媒体和网页配置。也可以运行项目内的 `bash scripts/server_safe_update.sh`，更新前会自动备份数据库。

### 常用运维命令

以下命令按 root 用户编写；普通用户需要在命令开头添加 `sudo`。

```bash
# 查看状态
systemctl status ai-english-lab

# 查看实时日志
journalctl -u ai-english-lab -f

# 重启服务
systemctl restart ai-english-lab
```

更详细的公网部署与备份说明见 [deploy/README.md](deploy/README.md)。

## Windows 一键启动

### 第一次使用

```cmd
git clone https://github.com/guai6mmt/ai-english-intensive-reading-lab.git
cd ai-english-intensive-reading-lab
start.bat
```

也可以直接双击 `start.bat`。脚本会检查 Python、创建 `.venv`、安装依赖、启动服务并打开浏览器：

```text
http://127.0.0.1:8010
```

第一次打开时创建管理员账号，密码至少 10 个字符。按 `Ctrl+C` 停止服务，以后仍然运行 `start.bat` 即可。

系统要求：Windows 10/11、Python 3.10 或更高版本、Git。安装 Python 时需要勾选 **Add Python to PATH**。

## macOS / Linux 本地启动

```bash
git clone https://github.com/guai6mmt/ai-english-intensive-reading-lab.git
cd ai-english-intensive-reading-lab
bash scripts/mac_start.sh
```

打开 `http://127.0.0.1:8010`。

## 导入音频

登录后可以使用三种方式：

### EPUB 与音频 ZIP 一次导入

在文章库点击“导入一期 EPUB + 音频 ZIP”，选择一期 EPUB 和对应的 ZIP 压缩包。系统会安全校验压缩包、逐条导入支持的音频格式，并综合期号、栏目、栏目内顺序、标题相似度和文章篇幅/音频时长生成配对预览。低置信度项目可以在表格中手动修改，确认后才会保存一对一关系。

压缩包必须为 ZIP；RAR/7z 请先解压后使用下面的“选择文件夹”。压缩包只用于临时导入，不会长期保留。系统会拒绝路径穿越、异常压缩比、超大单文件和超出总大小限制的压缩包。

### 浏览器选择文件夹

点击“上传文件夹”，选择电脑中的音频目录。浏览器会保留相对目录信息，前端自动分片上传，适合从电脑直接整理资料。

### 扫描服务器目录

先通过 SFTP、SCP 或同步工具把音频放入：

```text
/srv/english-lab/import
```

然后在音频库中执行“扫描服务器目录”。系统只允许扫描已配置的导入根目录，导入后的文件会复制到受管理的媒体目录。原始导入文件不会自动删除。

## 将文章与原版音频配套

文章和音频导入完成后，进入“音频库 → 内容配套”：

1. 选择 EPUB/DOCX/TXT 文章来源和对应的音频集合。
2. 点击“自动匹配”。系统会比较期刊日期、栏目名称、栏目数量和栏目内顺序。
3. 检查“建议复核”和“待手动匹配”项目；可以先试听，再从下拉列表选择正确音频。
4. 点击“确认并保存配套”。系统会检查重复占用，确保一篇文章和一条音频都是一对一关系。

保存后，文章卡片会显示“原版音频”，阅读页可以直接播放；音频列表也会显示“配套文章”入口。以后重新打开配套管理时，已确认的人工关系会优先保留，不会被自动算法覆盖。

## 四阶段精听与错句复习

从文章页进入“听力模式”后，可以按以下顺序完成一轮精听：

1. **盲听**：隐藏正文，只依靠声音理解句意。
2. **听写**：逐句输入听到的内容，可设置重复次数、句间停顿、倍速和单句循环。
3. **纠错**：自动标出漏词、错词和多词，并给出建议重点重听的连读或弱读片段。
4. **跟读**：对照原句模仿节奏并进行 1–5 星流畅度自评。

练习结果会按登录用户写入 SQLite。得分偏低或已到复习时间的句子会自动进入“错句复习队列”，再次打开对应文章时可以直接跳到该句。

已配套原版音频时，系统会优先使用 Qwen ASR 生成并缓存精确句子时间轴；需要在设置中配置 Qwen ASR 和 OSS。未配置或少量句子无法匹配时，播放不会中断，而是自动使用语速加权估算补齐。

## 点击查词、生词本与复习

在文章正文点击任意英文单词即可打开语境词典。查询优先使用已保存词条和服务器词典缓存；本地没有结果且已配置文本模型时，会结合当前句补充词形、音标、词性、中英文释义和语境说明。

加入生词本时会同时保存文章、句子和来源。再次遇到同一单词会增加遇见次数并保留新的语境，而不是建立重复词条。生词本提供四档自评的自适应复习队列，并可导出 CSV、Anki TSV 或包含复习日志的 JSON 备份。旧版 `vocabulary.json` 会在每个用户首次打开生词本时自动迁移到 SQLite。

## PWA 与离线文章

通过浏览器“添加到主屏幕”安装后，可以在文章阅读工具栏点击“离线保存”。系统只下载当前文章和它配套的原版音频，不会自动缓存整期音频。离线时可以重新打开已缓存的文章、播放音频、查看已缓存词义，并继续提交生词或复习操作；这些小型写操作会在恢复网络后自动补交。退出登录会清除该浏览器中的私人运行缓存。

## AI 模型配置（可选）

音频资料库本身不需要 AI Key。首次创建管理员后，系统会自动打开“偏好与模型设置”窗口。以后也可以点击网页右上角的“设置”。

网页可以完成以下配置，无需登录服务器或编辑 `.env`：

- 为文本分析、图片理解和 AI 朗读分别选择模型；
- 填写并测试 DeepSeek 和 Qwen API Key；
- 配置 Qwen TTS、DashScope ASR；
- 配置阿里云 OSS 音频中转；
- 配置 MiniMax TTS、声音、语速和模型；
- 调整阅读主题、字号与行距。
- 生成手机 WebDAV 应用密码、扫码复制地址，以及查看或吊销已授权设备。

保存的密钥不会在页面中明文回显。音频上传、整理和普通播放可以完全不配置 AI 服务。

## 数据与备份

服务器一键部署默认使用：

```text
/srv/english-lab/
├── data/       # SQLite 数据库及应用数据
├── media/      # 已纳入管理的音频文件
└── import/     # 等待网页扫描的音频文件
```

本地运行默认使用项目的 `data/` 目录。运行数据、媒体、`.env` 和备份均被 `.gitignore` 排除，不会提交到 GitHub。

更新脚本会备份数据库和小型应用数据，但不会重复打包通常较大的媒体目录。请另外对 `/srv/english-lab/media` 做定期增量或异地备份。

## 项目结构

```text
ai-english-intensive-reading-lab/
├── install.sh                      # 公开仓库服务器一键安装入口
├── app.py                          # FastAPI 主应用
├── english_lab/                    # 登录、数据库、健康检查、音频库 API
├── static/                         # 精读页面、音频管理页、手机播放器和 PWA
├── tests/                          # API、认证、上传和流媒体集成测试
├── deploy/                         # 公网部署说明及 Caddy 示例
├── scripts/
│   ├── server_install_or_update.sh # Linux 服务器一键安装与配置
│   ├── server_safe_update.sh       # 备份、拉取、校验、重启和健康检查
│   ├── deploy_safe.sh              # 从本地校验并触发远程更新
│   ├── mac_start.sh                # macOS / Linux 本地启动
│   └── win_start.ps1               # Windows PowerShell 启动
├── start.bat                       # Windows 双击启动
├── .env.example                    # 高级运维参考，正常部署无需编辑
└── requirements.txt                # Python 运行依赖
```

## 常见问题

**一键安装时仍然要求 GitHub 用户名？**

确认使用的是 README 中以 `raw.githubusercontent.com` 开头的完整一键命令。公开仓库不需要 GitHub 登录；脚本会自动把旧安装的远端地址改为公开 HTTPS 地址。

**网页能打开，但手机不能访问？**

手机不能使用服务器上的 `127.0.0.1` 地址。公网服务器请使用一键部署时填写的 HTTPS 域名，并检查 80/443 端口是否开放。

**HTTPS 证书申请失败？**

确认域名解析到了当前服务器，80/443 没有被其他程序占用，并查看 `journalctl -u caddy -f`；普通用户在命令前添加 `sudo`。

**服务器目录扫描不到文件？**

文件必须位于 `/srv/english-lab/import` 中，而且运行服务的用户必须有读取权限。更简单的方式是在音频库网页直接选择电脑文件夹上传。

**Windows 提示 Python not found？**

安装 Python 3.10+，勾选“Add Python to PATH”，重新打开 CMD 后运行 `start.bat`。

**端口 8010 已被占用？**

Windows：

```cmd
set PORT=8020 && start.bat
```

服务器默认端口由脚本管理，不需要对公网开放 8010。如果端口确实冲突，请查看高级部署说明。
