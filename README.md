# AI English Intensive Reading Lab

一个用于英文长文章精读的 FastAPI 小应用。支持文章导入、正文清洗、句子/段落分析、词汇学习、阅读答题、听写和写作反馈。

主要技术栈：

- 后端：FastAPI
- 前端：原生 HTML/CSS/JavaScript
- AI 接口：DeepSeek / Qwen，兼容 OpenAI Chat Completions 风格接口
- 本地数据：`data/` 目录中的 JSON 文件

## 项目结构

```text
.
├── app.py
├── requirements.txt
├── static/
│   ├── index.html
│   ├── app.js
│   └── styles.css
├── data/                 # 运行时生成，不提交 GitHub
└── README.md
```

`data/` 里会保存 API key、上传文章、分析缓存、生词本和学习进度，已在 `.gitignore` 中排除。

## Mac mini 本地开发

首次拉取：

```bash
cd ~/Projects
git clone https://github.com/guai6mmt/ai-english-intensive-reading-lab.git
cd ai-english-intensive-reading-lab
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

设置环境变量：

```bash
export DEEPSEEK_API_KEY="你的 DeepSeek API Key"
export DEEPSEEK_BASE_URL="https://api.deepseek.com"
export DEEPSEEK_MODEL="deepseek-v4-flash"

export QWEN_API_KEY="你的 Qwen / DashScope API Key"
export QWEN_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
export QWEN_MODEL="qwen-plus"
```

本地启动：

```bash
python -m uvicorn app:app --reload --host 127.0.0.1 --port 8010
```

浏览器打开：

```text
http://127.0.0.1:8010
```

也可以在页面的“模型设置”中保存 API key、模型和主模型。页面设置会写入 `data/settings.json`，该文件不会上传到 GitHub。

## Ubuntu 服务器部署

以下示例假设部署目录为 `/opt/ai-english-intensive-reading-lab`，服务端口为 `8010`。

### 1. 安装系统依赖

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip nginx
```

### 2. 拉取项目

```bash
sudo mkdir -p /opt/ai-english-intensive-reading-lab
sudo chown $USER:$USER /opt/ai-english-intensive-reading-lab
git clone https://github.com/guai6mmt/ai-english-intensive-reading-lab.git /opt/ai-english-intensive-reading-lab
cd /opt/ai-english-intensive-reading-lab
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### 3. 配置环境变量

创建环境文件：

```bash
sudo nano /etc/ai-english-lab.env
```

写入：

```env
DEEPSEEK_API_KEY=你的 DeepSeek API Key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
QWEN_API_KEY=你的 Qwen / DashScope API Key
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-plus
```

保护权限：

```bash
sudo chmod 600 /etc/ai-english-lab.env
```

### 4. 创建 systemd 服务

```bash
sudo nano /etc/systemd/system/ai-english-lab.service
```

写入：

```ini
[Unit]
Description=AI English Intensive Reading Lab
After=network.target

[Service]
WorkingDirectory=/opt/ai-english-intensive-reading-lab
EnvironmentFile=/etc/ai-english-lab.env
ExecStart=/opt/ai-english-intensive-reading-lab/.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8010
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

启动服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ai-english-lab
sudo systemctl status ai-english-lab
```

查看日志：

```bash
journalctl -u ai-english-lab -f
```

### 5. 配置 Nginx 反向代理

```bash
sudo nano /etc/nginx/sites-available/ai-english-lab
```

写入：

```nginx
server {
    listen 80;
    server_name 你的域名或服务器IP;

    client_max_body_size 100m;

    location / {
        proxy_pass http://127.0.0.1:8010;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

启用配置：

```bash
sudo ln -s /etc/nginx/sites-available/ai-english-lab /etc/nginx/sites-enabled/ai-english-lab
sudo nginx -t
sudo systemctl reload nginx
```

如果使用域名，建议再用 Certbot 配置 HTTPS。

## Mac 修改后部署到服务器

推荐流程是：Mac mini 修改代码，提交到 GitHub，服务器从 GitHub 拉取最新代码并重启服务。

### 1. Mac mini 修改并本地验证

```bash
cd ~/Projects/ai-english-intensive-reading-lab
source .venv/bin/activate
python -m uvicorn app:app --reload --host 127.0.0.1 --port 8010
```

修改完成后做基础检查：

```bash
python -B -c "import ast, pathlib; ast.parse(pathlib.Path('app.py').read_text(encoding='utf-8'))"
node --check static/app.js
```

如果 Mac 上没有 Node，可以跳过 `node --check`，但修改前端脚本后建议安装 Node 再检查。

### 2. 提交并推送到 GitHub

```bash
git status
git add app.py static/index.html static/app.js static/styles.css README.md requirements.txt .gitignore
git commit -m "描述本次修改"
git push origin main
```

不要提交：

- `data/`
- `.venv/`
- API key
- 上传的原文文件
- 本地缓存文件

### 3. 服务器拉取并重启

SSH 到服务器：

```bash
ssh 用户名@服务器IP
```

更新代码：

```bash
cd /opt/ai-english-intensive-reading-lab
git pull --ff-only origin main
source .venv/bin/activate
python -m pip install -r requirements.txt
sudo systemctl restart ai-english-lab
sudo systemctl status ai-english-lab
```

如果服务异常：

```bash
journalctl -u ai-english-lab -n 100 --no-pager
```

## 常用维护命令

本地查看状态：

```bash
git status
git log --oneline -5
```

服务器重启服务：

```bash
sudo systemctl restart ai-english-lab
```

服务器查看实时日志：

```bash
journalctl -u ai-english-lab -f
```

服务器查看端口：

```bash
ss -lntp | grep 8010
```

## 数据备份

学习数据在服务器项目目录的 `data/` 中。升级代码前通常不需要动它。

备份：

```bash
cd /opt/ai-english-intensive-reading-lab
tar -czf ~/ai-english-lab-data-$(date +%F).tar.gz data
```

恢复：

```bash
cd /opt/ai-english-intensive-reading-lab
tar -xzf ~/ai-english-lab-data-YYYY-MM-DD.tar.gz
sudo systemctl restart ai-english-lab
```

## AI 配置说明

默认接口：

```text
DEEPSEEK_BASE_URL=https://api.deepseek.com
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

推荐模型：

```text
DEEPSEEK_MODEL=deepseek-v4-flash
QWEN_MODEL=qwen-plus
```

应用页面中可以选择主模型。保存后，文章分析、句子分析、阅读反馈和写作反馈都会优先使用主模型。每次 AI 输出顶部会显示实际使用的 provider/model，方便确认当前结果来自哪个模型。

## 注意事项

- 生产部署不要使用 `--reload`。
- 服务器 API key 建议放在 `/etc/ai-english-lab.env`，不要写进 Git。
- `data/` 是个人学习数据，不提交 GitHub。
- EPUB/DOCX 解析效果通常优于 PDF。
- 本工具建议用于个人学习，不要公开分发受版权保护的原文内容。
