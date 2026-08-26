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
| 批量导入 | 网页选择整个文件夹上传，或让服务器扫描指定导入目录 |
| 音频格式 | MP3、M4A、AAC、WAV、FLAC、OGG、OPUS、M4B |
| 大文件处理 | 8 MB 分片上传、失败自动重试、最大文件大小可配置 |
| 自动整理 | 使用 SHA-256 去重，通过 ffprobe 读取时长、码率和音频标签 |
| 资料管理 | 修改标题、分类、难度和标签，支持搜索、筛选、排序、分页和回收站 |
| 学习状态 | 收藏音频、记录播放位置，并在电脑和手机之间同步进度 |
| 移动播放 | 响应式播放器、前后 15 秒、倍速、A-B 循环、锁屏控制和后台播放 |
| 流媒体 | 登录后私有访问，支持 HTTP Range，可拖动进度且无需完整下载 |
| PWA | 手机浏览器可“添加到主屏幕”，以接近原生应用的方式打开 |

### 英语精读学习

| 功能 | 具体能力 |
|---|---|
| 内容导入 | 导入 EPUB、DOCX、TXT，保存到个人文章库 |
| 文本预处理 | 正文提取、清洗和章节整理 |
| 长难句分析 | 分析句子结构、语法成分及理解难点 |
| 词汇学习 | 词频与难度分层、生词管理、语境学习 |
| 阅读训练 | 阅读理解题、答题与结果记录 |
| 听写跟读 | 结合文章和音频进行逐句听写、跟读训练 |
| 写作反馈 | 使用 AI 对写作内容进行分析和反馈 |
| 模型配置 | 支持 DeepSeek、Qwen/DashScope 与 MiniMax TTS，可在网页设置中填写 Key |

### 管理与安全

- 首次访问创建唯一管理员账号，密码使用 Argon2 哈希保存。
- 所有文章、设置和音频接口均受登录会话保护。
- 使用 HttpOnly、SameSite Cookie 和 CSRF 校验；公网部署自动启用 Secure Cookie。
- SQLite WAL 保存用户、媒体、收藏、进度、书签和导入任务。
- 服务以非 root 用户运行，只监听 `127.0.0.1`，由 Caddy 提供 HTTPS。
- 提供存活与就绪健康检查、自动重启和更新前数据库备份。

## 使用流程

```text
管理员登录
  ├─ 导入文章 → 清洗 → 长难句 / 词汇 / 阅读 / 听写 / 写作
  └─ 导入音频文件夹 → 自动校验去重 → 分类整理 → 手机选择音频练习
                                                    └─ 自动同步收藏与进度
```

登录后访问：

- `/`：英语精读工作台
- `/media`：音频资料库与手机播放器
- `/login`：登录或首次创建管理员

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

登录后进入“音频库”，可以使用两种方式：

### 浏览器选择文件夹

点击“上传文件夹”，选择电脑中的音频目录。浏览器会保留相对目录信息，前端自动分片上传，适合从电脑直接整理资料。

### 扫描服务器目录

先通过 SFTP、SCP 或同步工具把音频放入：

```text
/srv/english-lab/import
```

然后在音频库中执行“扫描服务器目录”。系统只允许扫描已配置的导入根目录，导入后的文件会复制到受管理的媒体目录。原始导入文件不会自动删除。

## AI 模型配置（可选）

音频资料库本身不需要 AI Key。首次创建管理员后，系统会自动打开“偏好与模型设置”窗口。以后也可以点击网页右上角的“设置”。

网页可以完成以下配置，无需登录服务器或编辑 `.env`：

- 为文本分析、图片理解和 AI 朗读分别选择模型；
- 填写并测试 DeepSeek 和 Qwen API Key；
- 配置 Qwen TTS、DashScope ASR；
- 配置阿里云 OSS 音频中转；
- 配置 MiniMax TTS、声音、语速和模型；
- 调整阅读主题、字号与行距。

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
