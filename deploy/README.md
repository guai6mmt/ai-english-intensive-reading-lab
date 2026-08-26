# Linux 公网部署与运维

主 README 中的一键安装脚本已经包含依赖、运行用户、数据目录、systemd、Caddy HTTPS 和健康检查配置。正常部署不需要手工复制本目录中的 Caddyfile。

## 一键部署

先把域名解析到服务器并开放 TCP 80/443，然后在项目目录运行：

```bash
sudo env DOMAIN=english.example.com bash scripts/server_install_or_update.sh
```

脚本会生成以下部署结构：

```text
/srv/english-lab/
├── data/       # SQLite 数据库与应用数据
├── media/      # 受管理的音频文件
└── import/     # 允许后台扫描的导入目录

/etc/systemd/system/ai-english-lab.service
/etc/caddy/Caddyfile.d/ai-english-lab.caddy
```

应用只监听 `127.0.0.1:8010`。Caddy 对外监听 80/443、自动申请证书，并把请求反向代理给应用。

`Caddyfile.example` 仅用于阅读和自定义参考；一键安装会自动生成等价配置。

## 更新

```bash
bash scripts/server_safe_update.sh
```

更新流程包括：

1. 检查当前服务状态；
2. 在线备份 SQLite 数据库和小型应用数据；
3. 从 `origin/main` 执行 fast-forward 更新；
4. 安装或更新 Python 依赖；
5. 进行 Python 和 JavaScript 校验；
6. 受控重启并等待健康检查通过。

备份默认存放在项目的 `backups/`，仅保留最近 5 份。媒体文件不会打包进更新备份，应单独做增量或异地备份。

## 权限和网络

- systemd 服务使用执行安装命令的普通用户运行；如果直接使用 root 安装，则自动创建 `englishlab` 系统用户。
- 数据、媒体和导入目录权限默认为 `750`。
- `.env` 权限设置为 `600`。
- 公网只应开放 SSH、TCP 80 和 TCP 443，不要直接开放应用端口 8010。
- 媒体目录需要服务用户读写，导入目录需要服务用户读取。

## 自定义配置

用环境变量覆盖默认值后，重新运行安装脚本即可：

```bash
sudo env \
  DOMAIN=english.example.com \
  PORT=8020 \
  APP_USER=englishlab \
  ENGLISH_LAB_DATA_DIR=/mnt/english-lab/data \
  MEDIA_STORAGE_ROOT=/mnt/english-lab/media \
  MEDIA_IMPORT_ROOT=/mnt/english-lab/import \
  bash scripts/server_install_or_update.sh
```

已有 `.env` 不会被删除，脚本只自动更新数据目录、媒体目录、导入目录、API 文档开关和 Cookie 安全设置。AI 服务 Key 会被保留。

## 检查与排错

```bash
# 应用状态
sudo systemctl status ai-english-lab

# 应用日志
sudo journalctl -u ai-english-lab -f

# Caddy 状态和证书日志
sudo systemctl status caddy
sudo journalctl -u caddy -f

# 本机健康检查
curl -fsS http://127.0.0.1:8010/health/ready

# 验证 Caddy 配置
sudo caddy validate --config /etc/caddy/Caddyfile
```

如果证书申请失败，优先检查域名解析、云服务器安全组、主机防火墙以及 80/443 端口占用情况。

## 恢复数据库

停止服务后，将目标备份复制回实际数据目录：

```bash
sudo systemctl stop ai-english-lab
sudo cp backups/app-YYYYMMDD-HHMMSS.db /srv/english-lab/data/app.db
sudo chown --reference=/srv/english-lab/data /srv/english-lab/data/app.db
sudo systemctl start ai-english-lab
```

恢复前建议先复制一份当前数据库。不要在服务运行时直接覆盖 SQLite 文件。
