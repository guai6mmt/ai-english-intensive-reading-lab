# 公网部署要点

1. 将域名解析到服务器，把 `Caddyfile.example` 中的域名替换为真实域名。
2. FastAPI 只监听 `127.0.0.1:8010`，防火墙只开放 80/443。
3. 在 `.env` 中设置 `COOKIE_SECURE=true`。
4. 将 `MEDIA_IMPORT_ROOT` 指向管理员投放待扫描音频的目录。
5. 媒体目录应另外做增量异地备份；更新脚本只备份数据库和小型应用数据。
6. 如果媒体或导入目录放在 `/srv` 等外部路径，确保运行 systemd 服务的用户对媒体目录有读写权限、对导入目录有读取权限。

示例：

```bash
sudo cp deploy/Caddyfile.example /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

首次打开域名时，系统会要求创建唯一的管理员账号。密码不会明文保存。
