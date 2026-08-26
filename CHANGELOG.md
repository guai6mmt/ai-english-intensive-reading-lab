# Changelog

## v0.3-audio-library - 2026-08-26

- 新增管理员首次设置、Argon2 密码哈希、服务器会话和 CSRF 防护。
- 新增 SQLite WAL 数据库，保存用户、会话、音频、收藏、进度和导入任务。
- 新增电脑文件夹批量导入、8MB 大文件分片、失败重试和 SHA-256 去重。
- 新增服务器允许目录扫描、ffprobe 音频验证和元数据读取。
- 新增响应式音频管理页、手机播放器、倍速、A-B 循环和锁屏控制。
- 新增 HTTP Range 私有音频流、回收站、搜索、分页和多设备进度同步。
- 新增 PWA manifest、应用壳缓存、健康检查和 GitHub Actions 测试。
- 加固 systemd 部署：专用非 root 用户、仅本机监听、HTTPS 反向代理说明和 SQLite 在线备份。

## archive-before-audio-library-20260826

音频资料库改造前的完整基线，对应提交 `b47cefbb355a3bb3e484ed3b6e6788a77a8a3389`。
