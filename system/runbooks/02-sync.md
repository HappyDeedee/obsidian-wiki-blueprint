# 电脑、手机与常开服务的同步

## 先理解这条链

电脑 Obsidian ↔ LiveSync 插件 ↔ HTTPS 反代 ↔ CouchDB ↔ LiveSync 插件 ↔ 手机 Obsidian。Agent 通过服务器 MCP 读取同一份数据库。手机和电脑不需要互相在线，但都需要有机会完成上传/下载。

参考上游：[Self-hosted LiveSync](https://github.com/vrtmrz/obsidian-livesync)、[CouchDB 配置](https://github.com/vrtmrz/obsidian-livesync/blob/main/docs/setup_own_server.md)、[Obsidian Sync MCP](https://github.com/es617/obsidian-sync-mcp)。插件版本变化时，以自己安装版本的官方说明核对设置字段。

## 1. 在自己的常开服务器准备

Linux 服务器，或自己的电脑 Docker 引擎。电脑关机后本机服务不可用；需要持续服务就放常开主机。先在空环境测试，不连接真实旧库。

在完整仓库根目录执行：

```powershell
python tools/deploy.py init --directory "../my-sync-private" --public-url "https://wiki.example.invalid" --vault-name "MyWiki"
```

正式运行时，把 `.invalid` 域名替换成**自己的真实 HTTPS 域名**。这个命令只生成自己的新密码/E2E passphrase/角色令牌，不启动服务、不修改现成库。配置在仓库外；限制目录权限，不打印完整配置或加入 Git。

```powershell
docker compose --env-file "../my-sync-private/stack.env" -f system/deploy/compose.yaml config --quiet
docker compose --env-file "../my-sync-private/stack.env" -f system/deploy/compose.yaml up -d couchdb
python tools/deploy.py init-db --directory "../my-sync-private"
docker compose --env-file "../my-sync-private/stack.env" -f system/deploy/compose.yaml up -d mcp-reader
```

`init-db` 需要 CouchDB 就绪；首次启动等待健康后再运行。若数据库/用户已经存在，程序拒绝替换其密码与权限；先检查现有数据。它不提供清库重试。中途失败由维护者核对已创建状态再恢复，不能反复重置。

基础服务端口只绑定本机回环。按 [Caddyfile.example](../deploy/Caddyfile.example) 在同一主机配置自己的 TLS 代理：`/sync/` 转发 CouchDB、`/mcp` 转发只读 MCP。若代理在容器里，`localhost` 是代理容器自己，须改用同一网络服务名；不能照搬宿主机写法。

不要公开数据库管理端口、关闭 TLS 校验或用无认证的任意来源 CORS。基线 CORS 列出 Obsidian 桌面/iOS 与本地来源；Android 或其他客户端来源以实际请求核验，按需添加精确来源，不盲目改成任意来源。

## 2. 连接全新桌面库

创建新测试 Vault，安装 Self-hosted LiveSync。填写自己的 `/sync` HTTPS URL、数据库 `wiki`、同步用户与密码。启用 E2E 加密时使用配置中生成的 `COUCHDB_PASSPHRASE`；基础配置关闭属性混淆、大小写敏感，插件和 MCP 必须一致。

如另行启用属性混淆，新版插件可能需要自己的 recovery code / ID derivation key。把该设置同时交给自己的 MCP，不能使用作者的值。修改既有库加密或混淆设置前，按插件官方迁移流程备份，不直接切换参数。

## 3. 手机连接同一测试库

手机安装 Obsidian 和同一插件，创建新测试库。用自己的 setup URI 或逐项配置连接；URI 内含秘密，只在自己的设备间传递。手机库名影响 Obsidian 深链接；确认中心 `vault` 配置使用对应名称。各端目录不必相同。

不要对同一个目录同时启用多个会改文件的同步引擎。需要保留其他同步服务时，由维护者先制定迁移方案。iPhone 后台持续运行、锁屏录音、快捷指令输出类型是独立能力，不能由服务器配置保证。

## 4. 必须实际验收

| 测试 | 通过证据 |
|---|---|
| 桌面→手机 / 手机→桌面 | 各创建含唯一标记的测试 Markdown，在对端打开正文核对 |
| 附件双向 | 使用获授权的小音频/图片，核文件大小与 SHA256；不仅看文件名 |
| 服务器读同一正文 | 只读 MCP 查询并回读唯一标记 |
| 权限 | 未授权读取被拒绝；只读 MCP 写入被拒绝 |
| 离线重连 | 离线新增后重连，检查到达及冲突历史 |
| 大音频 | 分别核手机插件上限、CouchDB 请求/文档上限、监听器上限、磁盘预算 |

CouchDB `_up` 和网页打开只证明服务可达，不证明 LiveSync 编码、E2E 解密、附件 chunk 还原或真实手机同步成功。容器 CI 也不能替代真手机验收。

备份须含 CouchDB 数据卷、自己的凭证与 passphrase；这是恢复数据所需。同步会复制删除与错误修改，所以不能代替独立备份。
