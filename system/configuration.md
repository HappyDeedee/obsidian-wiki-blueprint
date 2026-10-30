# 配置与私有数据清单

配置原则：公开仓库只放字段和安全样例；实际值在使用者自己的私有目录生成。凭证不放在网页源码、URL、截图、Git 历史或日志中。原始手机号、姓名、声纹向量、音频、人物关系、会议内容不是搭建方法的一部分。

| 服务 | 必须配置 | 私有保存位置 / 注意事项 |
|---|---|---|
| 同步数据库 | HTTPS URL、数据库名、独立同步用户/密码 | `tools/deploy.py` 在指定私有目录生成；不使用作者数据库 |
| LiveSync 桌面/手机 | 同一数据库、同一 E2E passphrase、混淆与大小写选项、附件大小上限 | 手机 setup URI 含秘密，不能发进公开 issue |
| 原版只读 MCP | COUCHDB_*、VAULT_NAME、MCP_AUTH_TOKEN、BASE_URL、READ_ONLY | Compose 基础版只读；不是正式整理权限 |
| 扩展 MCP | 身份 hash registry、writer lease、journal、工作/缓存目录、允许路径 | 角色登记使用完整 `Bearer <token>` 的 SHA256；不会由 Skill 自动生效 |
| 会议接收器 | public_base_url、phone_token、signing_secret、app_key、access_key_id/secret、obsidian_mcp_url/token | [会议配置样例](config/meeting.example.json)；真实值不复制到源码 |
| 会议监听 | 稳定等待、附件上限、二进制桥入口、队列与任务库 | 不把原文件名当可靠事件日期；不反复创建任务 |
| 声纹 | 模型路径、16k 单声道处理、私有目录、聚类/匹配阈值 | 音频、向量、模型、试听片段均不进 Git；模型许可独立核查 |
| 确认中心 | 卡片目录、访问认证、cookie/CSRF、音频路由、当前 Wiki 名称 | 本地 UI 用自动生成本机密码；生产参考旧 key 入口须按手册升级 |
| 确认回写 | 问题 ID/version、答复来源、投稿身份、最终回执读取身份 | 已收到/已投稿/已采纳分别展示 |
| 人物与词典 | 真人确认事件、domain 授权、唯一词典路径、热词资源 ID | 不按固定名字名单放行；新环境不复制作者的人物或词条 |
| Agent平台 | 自己的租户/空间/用户/Bot/例行任务/看板 ID、模型、预算、任务开关 | [控制器样例](config/controller.example.json)；不复用源码中占位 ID |
| 运维 | 备份目录/保留策略、监控路径、时区、服务命名、磁盘预算 | 分开备份同步数据库、应用状态、任务库与密钥 |

## 目录映射

生产参考中的通用 Linux 安装位置：

| 仓库内容 | 新服务器示例位置 |
|---|---|
| `production-reference/curation/`、维护程序与 MCP 集成 | `/srv/wiki/stack/` |
| `production-reference/meeting/` | `/srv/wiki/meeting/` |
| `production-reference/controller/` | `/srv/wiki/controller/` |
| `production-reference/confirm/` | `/opt/kb-confirm/` |
| `production-reference/voiceprint/` | `/opt/kb-voiceprint/` |
| 确认状态、日志、身份库、音频缓存 | `/var/lib/kb-confirm/`、`/var/lib/kb-voiceprint/` |

这些是公开的新环境约定，不是作者机器地址。Windows/macOS 本机推荐先用隔离参考版，生产 Linux 代码放在自己的 Linux 服务器/虚拟机；`fcntl`、systemd、Docker 容器内接口不能直接在 Windows Python 下运行。

## 公开前清理的内容

新仓库历史只包含已清理后的文件。原私有 Git 历史、备份包、环境文件、账户数据、各端 Obsidian 插件完整配置、私钥、签名快捷指令、音频/转写/声纹/卡片日志均不复制。

`CONFIGURE_YOUR_ID`、`CONFIGURE_YOUR_UUID`、`REPLACE_WITH_YOUR_*` 和 `.invalid` 是待配置标记。接手 AI 必须枚举并替换为自己的值，再运行对应测试；不能猜出一个看起来能用的地址。

## 服务文件与环境接线

- `stack-credentials.example.json` 对应维护脚本的 `credentials.json` 字段。备份才需要数据库管理员密码；查询/投稿/整理服务使用不同最小权限文件，不能共享管理员配置。
- 复制到运行位置后设置 `WIKI_MCP_URL` 为**自己的已集成扩展宿主入口**。生产参考默认使用 `.invalid`，不会意外连到本地只读 MCP；基础 Compose 的只读端口没有整理权限。
- 确认队列目录需要新建私有 `access.key`，`answers_token`（投稿）、`minutes_token`（受限派生写入）、以及 `cloud.json`。`confirm-cloud.example.json` 默认 `mode:local`；历史云端分支只作协议参考，没有附云函数，不默认启用。
- `people-domains.json` / 人物种子等私有身份状态必须从空状态和使用者自己的确认建立，不复制作者数据。确切字段见读取它们的模块。
- 控制器还需自己的 `knowledge-maintenance-manifest.json`，记录当前规则/Skill/部署源码 hash；从自己实际发布结果生成，不能使用作者历史 manifest 或队列总量。
- 日报适配器读取 `WIKI_REPORT_ROUTINE_ID`、`WIKI_CURATION_ROUTINE_ID`、`WIKI_DAILY_ROUTINE_ID`、`WIKI_STRUCTURE_ROUTINE_ID` 和自己的租户/用户 ID。付费模型中继探测另需 `WIKI_ENABLE_PAID_PROBE=true`，默认不发模型请求。

## 新库的词典

将 [空词典模板](config/confirmed-dictionary.empty.md) 保存到自己的 `20-Wiki/综合/已确认词典.md`，维持唯一 JSON 块和 schema。初始为空；使用者确认后才写入带来源和日期的条目。正式整理和会议热词模块必须指向同一正本，不能复制作者词条。

## 服务配置补充

阅读[配置、提示词、热词、附件转换与初始化说明](../docs/12-service-setup.md)。本次补充可执行离线配置工具与三个缺失依赖入口；完整生产宿主、认证适配和真设备验收仍须逐项完成。
