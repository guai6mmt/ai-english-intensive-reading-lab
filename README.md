# AI English Intensive Reading Lab

一个可自行部署的英语精读与听力训练系统。管理员可以在网页中导入文章和整个音频文件夹，学习者可以在电脑或手机上登录同一台服务器，继续阅读、听写和音频练习。

项目仓库：<https://github.com/guai6mmt/ai-english-intensive-reading-lab>

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

适用于 Debian、Ubuntu 及其常见云服务器。部署前只需：

1. 准备一个域名，并将域名的 A/AAAA 记录解析到服务器。
2. 在云平台安全组或防火墙中开放 TCP `80` 和 `443`。
3. 使用 `root`，或者确保当前普通账号可以使用 `sudo`。

### 第一次安装

本仓库当前为私有仓库。GitHub 已停止支持使用账号登录密码执行 Git 操作，因此服务器需要先进行一次授权。长期部署推荐使用只能读取这一个仓库的 Deploy Key，不要把 GitHub 密码、Token 或私钥写进命令和脚本。[GitHub Deploy Key 官方说明](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys)

以下命令适用于你当前这种 `root@服务器` 的登录方式。先生成服务器专用密钥：

```bash
install -d -m 700 /root/.ssh
test -f /root/.ssh/ai_english_deploy || ssh-keygen -t ed25519 -f /root/.ssh/ai_english_deploy -C "ai-english-lab-server" -N ""
cat /root/.ssh/ai_english_deploy.pub
```

复制输出的整行公钥，进入 GitHub 仓库的 **Settings → Deploy keys → Add deploy key**，填写标题并粘贴公钥。保持 **Allow write access** 未勾选。

授权完成后，将项目克隆到 `/opt`。不要克隆到 `/root`：应用会以非 root 用户运行，无法读取 `/root` 中的项目文件。

```bash
cd /opt
GIT_SSH_COMMAND="ssh -i /root/.ssh/ai_english_deploy -o IdentitiesOnly=yes" git clone git@github.com:guai6mmt/ai-english-intensive-reading-lab.git
cd /opt/ai-english-intensive-reading-lab
git config core.sshCommand "ssh -i /root/.ssh/ai_english_deploy -o IdentitiesOnly=yes"
```

第一次连接 GitHub 如果出现 `Are you sure you want to continue connecting`，核对主机为 `github.com` 后输入 `yes`。

> 所有终端命令都应从代码块复制。命令中的仓库地址必须是纯地址，前后不能带用于网页排版的方括号或圆括号。

然后执行唯一一条配置命令，把示例域名替换成你自己的域名：

```bash
env DOMAIN=english.example.com bash scripts/server_install_or_update.sh
```

如果使用的是普通 sudo 账号，则在安装命令前加 `sudo`：

```bash
sudo env DOMAIN=english.example.com bash scripts/server_install_or_update.sh
```

脚本会自动完成：

1. 安装 Python、FFmpeg、SQLite，并通过 [Caddy 官方软件源](https://caddyserver.com/docs/install#debian-ubuntu-raspbian)安装 Caddy；
2. 创建虚拟环境并安装项目依赖；
3. 创建 `/srv/english-lab` 下的数据、媒体和待导入目录；
4. 生成 `.env` 并自动写入安全的服务器配置；
5. 创建并启动 systemd 服务；
6. 配置 Caddy HTTPS、压缩和安全响应头；
7. 执行健康检查并显示最终访问地址。

安装完成后打开 `https://你的域名`，按照页面提示创建管理员账号。整个服务器部署过程不需要手工编辑 `.env`、Caddyfile 或 systemd 文件。

> Caddy 需要域名已经正确解析，才能自动申请 HTTPS 证书。DNS 刚修改时可能需要等待解析生效。

### 没有域名，仅在服务器本机使用

```bash
bash scripts/server_install_or_update.sh
```

普通用户运行时使用 `sudo bash scripts/server_install_or_update.sh`。此时应用仅监听 `http://127.0.0.1:8010`，不会直接暴露到公网。以后有域名时，用带 `DOMAIN` 的命令重新运行即可补齐 HTTPS 配置。

### 一键更新

```bash
cd /opt/ai-english-intensive-reading-lab
bash scripts/server_safe_update.sh
```

之前设置的 Deploy Key 会继续用于 `git pull`，不需要再次输入用户名或密码。更新脚本会拉取 `main` 最新代码、安装新增依赖、检查代码、备份实际数据目录、受控重启并验证健康状态。数据库备份保存在项目的 `backups/` 目录，默认保留最近 5 份。

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

私有仓库需要先登录 GitHub。推荐安装 Git for Windows 后通过弹出的 Git Credential Manager 浏览器窗口登录；如果终端要求输入 `Password`，必须填写 Personal Access Token，不能填写 GitHub 账号密码。[GitHub HTTPS 鉴权说明](https://docs.github.com/en/get-started/git-basics/about-remote-repositories#cloning-with-https-urls)

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

私有仓库同样需要先配置 GitHub SSH Key，或者在 HTTPS 的 `Password` 提示中使用 Personal Access Token，不能使用账号登录密码。

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

音频资料库本身不需要 AI Key。需要长难句分析、写作反馈、ASR 或 TTS 时，在网页右上角进入“设置”，填写相应服务的 Key 即可。

也可以编辑项目根目录的 `.env`：

```env
DEEPSEEK_API_KEY=你的 DeepSeek API Key
QWEN_API_KEY=你的 Qwen / DashScope API Key
DASHSCOPE_API_KEY=你的 DashScope API Key
MINIMAX_API_KEY=你的 MiniMax API Key
```

完整配置示例见 [.env.example](.env.example)。修改服务器 `.env` 后执行：

```bash
systemctl restart ai-english-lab
```

普通用户需要在命令开头添加 `sudo`。

## 一键部署的可选参数

默认配置已经适合个人服务器。如需调整，可在安装命令前传入：

| 参数 | 默认值 | 用途 |
|---|---|---|
| `DOMAIN` | 空 | 公网域名；设置后自动安装 Caddy 并启用 HTTPS |
| `PORT` | `8010` | 应用本机监听端口 |
| `SERVICE_NAME` | `ai-english-lab` | systemd 服务名称 |
| `APP_USER` | 执行 sudo 的普通用户 | 服务运行用户 |
| `ENGLISH_LAB_DATA_DIR` | `/srv/english-lab/data` | 数据库和应用数据目录 |
| `MEDIA_STORAGE_ROOT` | `/srv/english-lab/media` | 受管理音频存储目录 |
| `MEDIA_IMPORT_ROOT` | `/srv/english-lab/import` | 允许服务器扫描的导入目录 |

例如，修改端口和媒体磁盘位置：

```bash
env DOMAIN=english.example.com PORT=8020 MEDIA_STORAGE_ROOT=/mnt/audio/library bash scripts/server_install_or_update.sh
```

普通 sudo 用户需要在命令开头添加 `sudo`。

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
├── .env.example                    # 完整可选配置
└── requirements.txt                # Python 运行依赖
```

## 常见问题

**GitHub 提示 `Invalid username or token`？**

仓库是私有仓库，GitHub 不接受账号登录密码。服务器按照“Linux 服务器一键部署”章节添加只读 Deploy Key；使用 HTTPS 时，则在 `Password` 提示中填写 Personal Access Token。不要把 Token 拼进 URL，以免进入 Shell 历史记录。

**执行安装后提示运行用户无法读取项目目录？**

不要把项目放在 `/root` 下。按照文档把仓库克隆到 `/opt/ai-english-intensive-reading-lab`，然后重新执行安装命令。

**网页能打开，但手机不能访问？**

手机不能使用服务器上的 `127.0.0.1` 地址。公网服务器请使用一键部署时填写的 HTTPS 域名，并检查 80/443 端口是否开放。

**HTTPS 证书申请失败？**

确认域名解析到了当前服务器，80/443 没有被其他程序占用，并查看 `journalctl -u caddy -f`；普通用户在命令前添加 `sudo`。

**服务器目录扫描不到文件？**

文件必须位于 `/srv/english-lab/import` 或自定义的 `MEDIA_IMPORT_ROOT` 中，而且运行服务的用户必须有读取权限。

**Windows 提示 Python not found？**

安装 Python 3.10+，勾选“Add Python to PATH”，重新打开 CMD 后运行 `start.bat`。

**端口 8010 已被占用？**

Windows：

```cmd
set PORT=8020 && start.bat
```

服务器：重新执行带 `PORT=8020` 的一键配置命令。
