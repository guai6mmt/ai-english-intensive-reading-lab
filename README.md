# AI English Intensive Reading Lab

英文长文章精读工具。支持文章导入、正文清洗、句子/段落分析、词汇学习、阅读答题、听写和写作反馈。

默认端口统一使用 `8010`。

## 一键在 Mac mini 上运行

首次拉取：

```bash
cd ~/Projects
git clone https://github.com/guai6mmt/ai-english-intensive-reading-lab.git
cd ai-english-intensive-reading-lab
```

可选：复制环境变量文件，填入 API key。

```bash
cp .env.example .env
nano .env
```

一键启动：

```bash
bash scripts/mac_start.sh
```

脚本会自动：

- 创建 `.venv`
- 安装 Python 依赖
- 读取 `.env`
- 创建 `data/`
- 用 `8010` 端口启动开发服务

打开：

```text
http://127.0.0.1:8010
```

以后在 Mac mini 上开发，也只需要：

```bash
cd ~/Projects/ai-english-intensive-reading-lab
bash scripts/mac_start.sh
```

## 一键部署到 Ubuntu 服务器

在 Mac mini 上运行：

```bash
cd ~/Projects/ai-english-intensive-reading-lab
bash scripts/deploy_to_server.sh 用户名@服务器IP "本次修改说明"
```

例子：

```bash
bash scripts/deploy_to_server.sh ubuntu@1.2.3.4 "update deployment scripts"
```

脚本会自动：

- 检查 `app.py` 和前端脚本
- 提交当前修改
- 推送到 GitHub
- SSH 到服务器
- 如果服务器没有项目，就自动 clone
- 如果服务器已有项目，就自动 pull
- 安装或更新依赖
- 创建或更新 systemd 服务
- 使用 `0.0.0.0:8010` 启动服务
- 重启服务

部署完成后打开：

```text
http://服务器IP:8010
```

如果你的服务器部署目录不是默认的 `/opt/ai-english-intensive-reading-lab`，可以这样指定：

```bash
REMOTE_DIR=/home/ubuntu/ai-english-intensive-reading-lab bash scripts/deploy_to_server.sh ubuntu@1.2.3.4 "deploy"
```

## 服务器首次手动部署

如果你已经 SSH 到服务器里，也可以直接在服务器上执行：

```bash
git clone https://github.com/guai6mmt/ai-english-intensive-reading-lab.git /opt/ai-english-intensive-reading-lab
cd /opt/ai-english-intensive-reading-lab
bash scripts/server_install_or_update.sh
```

脚本会自动创建 systemd 服务，服务名是：

```text
ai-english-lab
```

## Mac 修改后的重复部署流程

最简单的循环：

```bash
# 1. Mac mini 上修改代码

# 2. 本地跑起来看看
bash scripts/mac_start.sh

# 3. 一键提交、推送、部署到服务器
bash scripts/deploy_to_server.sh ubuntu@服务器IP "描述这次修改"
```

如果你想自己手动提交 GitHub，也可以：

```bash
git status
git add .
git commit -m "描述这次修改"
git push origin main
```

然后服务器上：

```bash
cd /opt/ai-english-intensive-reading-lab
git pull --ff-only origin main
bash scripts/server_install_or_update.sh
```

## 常用命令

查看服务器服务状态：

```bash
sudo systemctl status ai-english-lab
```

查看服务器日志：

```bash
journalctl -u ai-english-lab -f
```

重启服务器服务：

```bash
sudo systemctl restart ai-english-lab
```

查看 8010 端口：

```bash
ss -lntp | grep 8010
```

## 配置 AI key

Mac 和服务器都可以用 `.env`：

```bash
cp .env.example .env
nano .env
```

内容示例：

```env
DEEPSEEK_API_KEY=你的 DeepSeek API Key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
QWEN_API_KEY=你的 Qwen / DashScope API Key
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-plus
```

也可以直接在网页里的“模型设置”里保存。网页设置会写入 `data/settings.json`。

## 数据位置

运行数据都在：

```text
data/
```

包括：

- 上传文章
- API 设置
- AI 分析缓存
- 生词本
- 学习进度

`data/` 不会提交到 GitHub。

备份服务器数据：

```bash
cd /opt/ai-english-intensive-reading-lab
tar -czf ~/ai-english-lab-data-$(date +%F).tar.gz data
```

## 项目结构

```text
.
├── app.py
├── requirements.txt
├── scripts/
│   ├── mac_start.sh
│   ├── deploy_to_server.sh
│   └── server_install_or_update.sh
├── static/
│   ├── index.html
│   ├── app.js
│   └── styles.css
├── data/
└── README.md
```

## 注意事项

- 端口固定用 `8010`。
- 不再使用 `8000`，避免和已有服务冲突。
- `data/`、`.env`、`.venv/` 都不会上传 GitHub。
- Ubuntu 服务器需要能从 Mac mini SSH 登录。
- 服务器安全组或防火墙需要放行 TCP `8010`。
