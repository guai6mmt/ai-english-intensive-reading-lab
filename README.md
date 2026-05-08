# AI English Intensive Reading Lab

英文长文章精读工具。支持文章导入（EPUB / DOCX / TXT）、正文清洗、长难句分析、词汇分层、阅读答题、听写跟读和写作反馈。前端按 paper-and-ink 美学重设计，含三种主题（纸本 / 清冷 / 夜读）。

默认端口统一使用 `8010`。

```
学习主线：文本检查 → 长难句 → 词汇 → 阅读答题 → 听写跟读 → 写作反馈
```

---

## 目录

- [一、快速入门](#一快速入门)
- [二、Windows 全流程](#二windows-全流程)
- [三、Mac 全流程](#三mac-全流程)
- [四、服务器无停机部署](#四服务器无停机部署)
- [五、常用运维命令](#五常用运维命令)
- [六、配置 AI Key](#六配置-ai-key)
- [七、数据与备份](#七数据与备份)
- [八、项目结构](#八项目结构)
- [九、常见问题](#九常见问题)

---

## 一、快速入门

### 系统要求

| 平台 | 必需 | 可选 |
|---|---|---|
| Windows 10/11 | Python 3.10+、Git | PowerShell 5.1+（系统自带）、Node.js（前端语法检查） |
| macOS 12+ | Python 3.10+（推荐 Homebrew）、Git | Node.js |
| Ubuntu 22.04+ 服务器 | Python 3.10+、Git、`sudo` 权限 | nginx（反代时使用） |

仓库地址：`https://github.com/guai6mmt/ai-english-intensive-reading-lab.git`

---

## 二、Windows 全流程

### 2.1 首次：从 GitHub 下载到本地运行

打开 **PowerShell**：

```powershell
# 选一个工作目录
cd D:\project

# 克隆仓库
git clone https://github.com/guai6mmt/ai-english-intensive-reading-lab.git
cd ai-english-intensive-reading-lab

# （可选）配置 API Key
Copy-Item .env.example .env
notepad .env

# 一键启动（会自建虚拟环境、装依赖、读 .env、起服务）
powershell -ExecutionPolicy Bypass -File scripts\win_start.ps1
```

> 如果 PowerShell 提示脚本被禁用，请先在管理员 PowerShell 里执行：
> `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`

或者用兼容性更好的批处理：

```powershell
.\scripts\win_start.bat
```

启动成功后浏览器打开：

```
http://127.0.0.1:8010
```

### 2.2 日常：Windows 上修改后推回 GitHub

```powershell
cd D:\project\ai-english-intensive-reading-lab

# 1. 拉取最新代码（避免落后）
git pull origin main

# 2. 启动服务并修改代码（可一边跑一边改）
powershell -ExecutionPolicy Bypass -File scripts\win_start.ps1
# Ctrl + C 停止

# 3. 查看变更
git status
git diff

# 4. 提交并推送（直接推 main，不开新分支）
git add .
git commit -m "本次改动的简要说明"
git push origin main
```

### 2.3 Windows 修改 → 推 GitHub → 服务器无停机更新

如果你在 Windows 上开发，但服务器是 Linux，**推荐用 Git Bash**（Git for Windows 自带）来执行部署脚本，因为它兼容 bash：

```bash
# 在 Git Bash 中
cd /d/project/ai-english-intensive-reading-lab
bash scripts/deploy_safe.sh ubuntu@服务器IP "本次修改说明"
```

它会：本地语法检查 → `git push origin main` → SSH 到服务器执行 `server_safe_update.sh`（备份 data → 拉代码 → 装依赖 → 平滑重启 → 健康检查）。

如果不想本地直接推服务器，**只想推 GitHub**，那就只做 2.2 步骤，然后让服务器在自己时间窗口里执行 `bash scripts/server_safe_update.sh`。

---

## 三、Mac 全流程

### 3.1 首次：从 GitHub 下载到本地运行

打开 **Terminal**：

```bash
mkdir -p ~/Projects && cd ~/Projects
git clone https://github.com/guai6mmt/ai-english-intensive-reading-lab.git
cd ai-english-intensive-reading-lab

# （可选）配置 API Key
cp .env.example .env
nano .env

# 一键启动
bash scripts/mac_start.sh
```

`mac_start.sh` 会自动：

- 创建 `.venv` 虚拟环境
- `pip install -r requirements.txt`
- 加载 `.env`
- 创建 `data/`
- `uvicorn app:app --reload --host 127.0.0.1 --port 8010`

打开浏览器：

```
http://127.0.0.1:8010
```

### 3.2 日常：Mac 上修改后推回 GitHub

```bash
cd ~/Projects/ai-english-intensive-reading-lab

# 1. 拉取最新
git pull origin main

# 2. 起服务，边跑边改（uvicorn --reload，保存自动热加载）
bash scripts/mac_start.sh
# Ctrl + C 停止

# 3. 查看变更并推送
git status
git diff

git add .
git commit -m "本次改动的简要说明"
git push origin main
```

### 3.3 Mac 修改 → 推 GitHub → 服务器无停机更新

```bash
cd ~/Projects/ai-english-intensive-reading-lab

# 一步到位：本地校验 → push GitHub → 远程平滑重启
bash scripts/deploy_safe.sh ubuntu@服务器IP "本次修改说明"
```

如果你的远端目录不是 `/opt/ai-english-intensive-reading-lab`，可以这样指定：

```bash
REMOTE_DIR=/home/ubuntu/ai-english-intensive-reading-lab \
  bash scripts/deploy_safe.sh ubuntu@1.2.3.4 "deploy"
```

> 旧的 `deploy_to_server.sh` 仍然可用，但会直接 restart 而不做语法 / 健康检查。**生产环境推荐 `deploy_safe.sh`。**

---

## 四、服务器无停机部署

> 当 Windows 或 Mac 上修改完代码并 push 到 GitHub 后，怎样在不影响线上访问的前提下部署到服务器？

下面三种方案任选其一。**方案 A** 最简单，适合 95% 的场景；**方案 B** 是 zero-downtime（真正零中断）；**方案 C** 是手动逐步排查。

### 方案 A：脚本一键平滑更新（推荐，~1s 中断）

在服务器上：

```bash
cd /opt/ai-english-intensive-reading-lab
bash scripts/server_safe_update.sh
```

或者从开发机一键远程触发：

```bash
# 本地（Mac 或 Windows + Git Bash）
bash scripts/deploy_safe.sh ubuntu@服务器IP "本次修改说明"
```

`server_safe_update.sh` 的执行步骤：

1. **保护数据**：先把 `data/` 打包成 `data-backup-YYYYMMDD-HHMMSS.tar.gz`，自动只保留最近 5 份
2. **拉取代码**：`git fetch origin main && git pull --ff-only`
3. **更新依赖**：`pip install -r requirements.txt`（运行中的进程不受影响）
4. **代码校验**：`python ast.parse` 检查 `app.py`；`importlib` 测试模块；`node --check static/app.js`
5. **平滑重启**：`sudo systemctl restart ai-english-lab`（typical 1 秒以内）
6. **健康检查**：循环 curl `http://127.0.0.1:8010/api/library`，最多等 20 秒；若不通就报警提示日志

整个过程**只在第 5 步有约 1 秒的请求中断**，因为 systemd 会先杀旧进程再起新进程。`data/` 在第 1 步已被备份，**绝不会因为升级丢失用户数据**。

### 方案 B：双实例 blue-green 切换（真正零中断）

适合：需要严格保证服务不中断、有 nginx 反代或 LB。

**一次性准备**（在服务器上）：

1. 复制一份 systemd 服务文件，监听不同端口：

```bash
sudo cp /etc/systemd/system/ai-english-lab.service \
        /etc/systemd/system/ai-english-lab-green.service

sudo sed -i 's/--port 8010/--port 8011/' \
        /etc/systemd/system/ai-english-lab-green.service

sudo systemctl daemon-reload
sudo systemctl enable --now ai-english-lab-green
```

2. 用 nginx 做 upstream 反代（端口 80 → 8010）：

```nginx
upstream lab_blue  { server 127.0.0.1:8010; }
upstream lab_green { server 127.0.0.1:8011; }

upstream lab_active { server 127.0.0.1:8010; }   # 当前激活的

server {
  listen 80;
  location / {
    proxy_pass http://lab_active;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  }
}
```

**每次发布**：

```bash
cd /opt/ai-english-intensive-reading-lab
git pull --ff-only origin main
.venv/bin/pip install -r requirements.txt --quiet

# 1. 先更新 green（备机）
sudo systemctl restart ai-english-lab-green

# 2. 健康检查 green
curl -fsS http://127.0.0.1:8011/api/library

# 3. nginx 把 lab_active 切到 green，reload 配置（无连接中断）
sudo sed -i 's|upstream lab_active.*|upstream lab_active { server 127.0.0.1:8011; }|' /etc/nginx/sites-enabled/default
sudo nginx -s reload

# 4. 再更新 blue
sudo systemctl restart ai-english-lab

# 下次发布时，再把 lab_active 切回 8010
```

> 如果你只有一台服务器一个端口直接对外，方案 A 已经足够。**不要为了零中断把架构搞得过度复杂**。

### 方案 C：纯手动逐步部署

适合：想看清每一步发生什么。

```bash
cd /opt/ai-english-intensive-reading-lab

# 1. 备份 data
tar -czf data-backup-$(date +%F).tar.gz data

# 2. 拉新代码
git fetch origin main
git status                       # 确认本地无未提交修改
git pull --ff-only origin main

# 3. 装依赖（不影响运行进程）
source .venv/bin/activate
pip install -r requirements.txt

# 4. 语法检查
python -c "import app; assert hasattr(app, 'app')"
node --check static/app.js   # 如安装了 node

# 5. 重启
sudo systemctl restart ai-english-lab

# 6. 看日志
journalctl -u ai-english-lab -n 30 --no-pager
curl -fsS http://127.0.0.1:8010/api/library
```

### 4.1 服务器首次手动部署（全新服务器）

只有第一次需要这步，后面用方案 A：

```bash
git clone https://github.com/guai6mmt/ai-english-intensive-reading-lab.git \
          /opt/ai-english-intensive-reading-lab
cd /opt/ai-english-intensive-reading-lab
bash scripts/server_install_or_update.sh
```

它会：装系统包（`python3 python3-venv python3-pip`）→ 建 `.venv` → 装 pip 依赖 → 写 systemd 服务文件 `ai-english-lab.service` → `enable + start` → 放行 ufw `8010` 端口。

---

## 五、常用运维命令

| 操作 | 命令 |
|---|---|
| 查看服务状态 | `sudo systemctl status ai-english-lab` |
| 查看实时日志 | `journalctl -u ai-english-lab -f` |
| 重启服务 | `sudo systemctl restart ai-english-lab` |
| 停止服务 | `sudo systemctl stop ai-english-lab` |
| 启动服务 | `sudo systemctl start ai-english-lab` |
| 查看 8010 端口占用 | `ss -lntp \| grep 8010` |
| 查看磁盘占用 | `du -sh data/ .venv/` |
| 检查 GitHub 远端最新 commit | `git log origin/main -1 --pretty=format:'%h %s (%ar)'` |

---

## 六、配置 AI Key

Mac / Windows / 服务器都使用 `.env`（不会被 git 提交）：

```bash
cp .env.example .env
# 编辑文件填入真实 key
```

`.env` 内容示例：

```env
DEEPSEEK_API_KEY=你的 DeepSeek API Key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
QWEN_API_KEY=你的 Qwen / DashScope API Key
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-plus
```

**或者**直接在网页右上角 → 设置 → 模型 → 保存，会写入 `data/settings.json`，覆盖 `.env`。

---

## 七、数据与备份

所有运行数据都在 `data/` 目录：

```
data/
├── library.json        # 文章库
├── vocabulary.json     # 生词本
├── outputs.json        # AI 反馈记录
├── progress.json       # 学习进度
├── settings.json       # 网页里保存的模型配置（覆盖 .env）
└── uploads/            # 上传的 EPUB / DOCX / TXT 原文件
```

`.gitignore` 已忽略 `data/`、`.env`、`.venv/`、`__pycache__/`，**不会被推到 GitHub**。

### 手动备份服务器数据

```bash
cd /opt/ai-english-intensive-reading-lab
tar -czf ~/ai-english-lab-data-$(date +%F).tar.gz data
```

### 自动备份

`server_safe_update.sh` 在每次部署前会自动备份 `data/` 到 `data-backup-YYYYMMDD-HHMMSS.tar.gz`，保留最近 5 份。

### 恢复

```bash
cd /opt/ai-english-intensive-reading-lab
sudo systemctl stop ai-english-lab
mv data data.broken
tar -xzf data-backup-20260508-123000.tar.gz
sudo systemctl start ai-english-lab
```

---

## 八、项目结构

```
ai-english-intensive-reading-lab/
├── app.py                          # FastAPI 后端 + AI 调用
├── V6_english_analyzer.py          # 难度 / 词频 / CEFR 分析器
├── requirements.txt
├── README.md
├── .env.example
├── .gitignore
├── scripts/
│   ├── win_start.ps1               # Windows PowerShell 启动
│   ├── win_start.bat               # Windows 批处理启动
│   ├── mac_start.sh                # Mac / Linux 本地启动
│   ├── server_install_or_update.sh # 服务器首次部署（systemd）
│   ├── server_safe_update.sh       # 服务器无停机更新（推荐）
│   ├── deploy_to_server.sh         # 旧版：直接 restart 部署
│   └── deploy_safe.sh              # 新版：本地校验 + 远程平滑重启
├── static/
│   ├── index.html                  # 重设计后的单页 shell
│   ├── app.js                      # vanilla JS 渲染逻辑
│   └── styles.css                  # paper-and-ink 主题 + tokens
├── design/                         # （仅本地）Claude Design 高保真原型
└── data/                           # 运行数据，gitignore
```

---

## 九、常见问题

**Q：端口 8010 被占用？**
临时换端口：

```bash
PORT=8020 bash scripts/mac_start.sh
# Windows
$env:PORT="8020"; .\scripts\win_start.ps1
```

服务器同样支持：

```bash
PORT=8020 bash scripts/server_install_or_update.sh
```

**Q：Windows 提示「无法加载文件 ... win_start.ps1，因为在此系统上禁止运行脚本」？**
管理员 PowerShell 里执行：

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

或直接用 `.bat` 版：`.\scripts\win_start.bat`

**Q：Mac 上 `python3` 不存在？**

```bash
brew install python@3.12
```

**Q：服务器更新后 502 / 端口不通？**
看日志：

```bash
journalctl -u ai-english-lab -n 100 --no-pager
```

通常是依赖未装齐 / `.env` 未配置 / 端口被占用，按提示修正后再 `sudo systemctl restart ai-english-lab`。

**Q：怎样直接在 GitHub 网页 / Codespace 上改？**
在 GitHub 网页编辑后会自动产生 commit 到 `main`。然后服务器执行 `bash scripts/server_safe_update.sh` 即可拉取并重启。

**Q：怎样回滚？**

```bash
cd /opt/ai-english-intensive-reading-lab
git log --oneline -10                # 找想回滚到的 commit
git checkout <commit-sha>            # detach HEAD
sudo systemctl restart ai-english-lab
# 验证 OK 后改成新分支或直接重置 main：
# git checkout main && git reset --hard <commit-sha>
```

---

## 注意事项

- **端口固定 `8010`**，不再使用 8000，避免和已有服务冲突。
- **服务器防火墙** / 云安全组需放行 TCP `8010`（如使用反代则放行 80/443）。
- `data/`、`.env`、`.venv/` 不会上传 GitHub。
- SSH 方式部署需 Mac / Windows 能免密登录服务器（`ssh-copy-id user@server`）。
- 修改 / 推送时**不创建分支**，直接对 `main` 操作 —— 与本仓库的简单工作流保持一致。
