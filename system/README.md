# 整套知识系统：给使用者和协助搭建的 AI

本目录不是作者生产服务的连接包。它包含从实际运行源码整理出的**76 份脱敏服务参考文件、现有确认中心界面、独立本地启动程序、同步服务配置、搭建步骤与验收合同**。

## AI 进入仓库后怎么做

先读根目录 [AGENTS.md](../AGENTS.md)，再读 [系统总图](architecture.md)、[当前状态](STATE.md)、[配置表](configuration.md)。根据使用者设备选择路径，按 [搭建检查表](INSTALL-CHECKLIST.md) 一步一步执行。你需要实际配置、运行和检查，不要让使用者自己拼出隐藏依赖。

| 目标 | 从哪里开始 | 当前边界 |
|---|---|---|
| 本机体验原确认中心 UIUX | [本地启动](runbooks/01-local.md) | 可运行；虚构数据；无真实模型/同步/声纹 |
| 手机、电脑共享一个 Wiki | [多端同步](runbooks/02-sync.md) | 提供 CouchDB + 只读 MCP 的独立配置；须部署自己的 HTTPS 服务和 LiveSync |
| Agent 萃取、更新和巡检 | [扩展 MCP](runbooks/03-mcp.md)、[知识自生长](runbooks/07-growth.md) | 脱敏生产源码与能力合同；不能把原版 MCP 当作已经含所有扩展 |
| 录音到纪要、完整转写 | [会议链路](runbooks/04-meetings.md) | 接自己的听悟账户，首次提交会产生第三方费用 |
| 声纹库、试听、人物页 | [声纹与人物](runbooks/05-voiceprint.md) | 模型和私有音频不随仓库发布；需要单独安装与授权 |
| 确认、答复、回执、词典 | [确认闭环](runbooks/06-confirmation.md) | 本地版与生产参考的接口区别有明确说明 |
| 备份、升级、故障恢复 | [维护恢复](runbooks/08-operations.md) | 多个状态库要一并备份；同步不能代替备份 |

## 安装等级，逐级验收

1. **L0 本地参考**：新目录可启动网页，答复进入参考 Wiki，正式提交后回执才显示已入库。
2. **L1 同步基础**：自己的 CouchDB/HTTPS、桌面/手机 LiveSync、只读 MCP；需双向文件与附件验证。
3. **L2 知识整理**：扩展 MCP、独立身份、写入租约、版本保护、调度、语义阅读与验收。
4. **L3 会议确认**：录音监听、听悟、声纹、人工身份、人物页、确认中心、词典和最终回执。

只通过 L0 不得报告 L3 已建好。76 份生产参考源码不是已验收的跨平台发行版；其中的 Linux 路径、平台数据库接口和外部服务仍须由接手 AI 按手册适配。

## 代码在哪里

- [confirmation-ui/index.html](confirmation-ui/index.html)：复用实际运行页面的布局、样式、卡片、词典、交互与播放器；公开版改为配置化库名及 Cookie/CSRF 接口。
- [production-reference/](production-reference/)：脱敏后的实际服务实现，按角色分组；入口和依赖见 [源码地图](source-map.md)。不在目录中执行所有脚本，不把其中的历史部署假设自动视为新环境事实。
- [deploy/](deploy/)：无私人值的基础同步配置。只读 MCP 有意不提供整理写权限。
- [tools/system.py](../tools/system.py)：跨平台的独立本地演示启动、检查和备份恢复。
- [tools/deploy.py](../tools/deploy.py)：为使用者生成新的私有凭证；不启动服务，不读取作者配置。

实际测试和缺项见 [STATE.md](STATE.md) 与 [发布验收](../release/verification.md)。

## 服务配置补充

阅读[配置、提示词、热词、附件转换与初始化说明](../docs/12-service-setup.md)。本次补充可执行离线配置工具与三个缺失依赖入口；完整生产宿主、认证适配和真设备验收仍须逐项完成。
