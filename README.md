# Obsidian Wiki Blueprint

把零散资料变成**有来源、会更新、能核对、可以追问**的个人 Wiki，并打通电脑/手机同步、会议、声纹与网页确认中心。

**AI 进入仓库后先读 [AGENTS.md](AGENTS.md)，再按 [完整系统入口](system/README.md) 和 [搭建检查表](system/INSTALL-CHECKLIST.md) 实际执行。** 无需另找一段提示词；运行环境不会自动加载 `AGENTS.md` 时，显式打开该文件。搭建 Skill 是 [obsidian-wiki-build](skills/obsidian-wiki-build/SKILL.md)。

## 一条命令体验原确认中心界面

先有 Python 3.11+，在完整仓库根目录执行：

```powershell
python tools/system.py launch --home "../my-wiki-local" --demo
```

打开终端给出的本机地址，访问密码在新安装目录的 `private/access.json`。初次只允许新空目录；再次启动读取原安装，不重置数据。页面复用实际运行的布局、样式、卡片、词典与播放器。虚构示例不调用模型、转写或声纹。更多步骤见 [本地手册](system/runbooks/01-local.md)。

完整服务按 L0 本地 → L1 同步 → L2 整理 → L3 会议/声纹/确认推进：脱敏运行源码、同步 Compose、私有配置生成器和八份接线手册都在 [system/](system/)。**生产宿主集成与逐设备验收仍须完成，不能把本地启动称为整套生产一键安装。**

这不是把文件丢给 AI 做摘要，也不是只安装三个 Skill。完整方法是：

```text
接收原件 → 提取可读正文 → 分段阅读 → 对照旧知识 → 萃取/更新主题
                                                     ↓
只读查询 ← 知识页与原文 ← 核验提交与回执 ← 待确认 → 主人答复 → 核证采纳
                         ↑
              每日结构巡检 / 每周内容巡检 / 到期复核
```

本仓库从实际使用的私人系统中抽出方法，重新编写公开规则和示例，并加入脱敏服务参考源码。这里不包含作者知识库、私人 MCP 连接值、账号标识、令牌、设备路径、数据库、声音或生产备份。旧私有仓库继续私有。

## 先说清能得到什么

| 内容 | 本仓库提供 | 不应误解成 |
|---|---|---|
| 方法论 | 来源、实体、概念、综合、项目、时间、证据与历史规则 | 只做逐文件摘要 |
| Agent Skill | build / ingest / query / health 四个脱敏技能 | Agent 自动获得你的知识库权限 |
| 初始知识库 | 空白目录、规则、索引、元数据模板 | 作者的私人知识库 |
| 可运行参考程序 | 本地登录网页、版本保护提交、答复/回执、查询、巡检、备份恢复 | 已完成真实手机、声音模型或云服务验收 |
| 服务搭建包 | 实际 UIUX、脱敏运行源码、基础同步 Compose、配置生成器和操作手册 | 原版 MCP 已自动加载所有扩展；完整生产版克隆即用 |

**“同一套”指工作方法与行为合同。** 本地参考版可以跑通一个完整小闭环；服务器生产版还需按能力合同实现/接入适配层，详见 [功能对照](docs/09-parity-and-limits.md)。`curation_*` 是扩展接口，不是普通 Obsidian MCP 默认自带。

## 新人从这里开始

需要 Python 3.11+；不调用模型、不需要 API Key，就能先跑示例。在仓库根目录逐条执行：

```powershell
python tools/wiki.py init --vault "../my-wiki-demo"
python tools/demo.py --vault "../my-wiki-demo"
python tools/wiki.py query --vault "../my-wiki-demo" --text "演示项目"
python tools/wiki.py health --vault "../my-wiki-demo"
python -m unittest discover -s tests -v
python tools/check_public.py
```

每条命令单独执行。`init` 只允许新建空目录，不覆盖现有知识库；示例是虚构资料，重复运行不会重复计数。请先看 [逐步搭建](docs/01-setup.md)，不要把演示直接指向已有库。

接入真实 Agent 后，模型负责理解和提出变更，确定性程序负责权限、版本、事务、索引、台账和回读。没有模型参与的演示**不证明模型已正确理解资料**。

## 接手 AI 的阅读入口

根目录 `AGENTS.md` 已接好阅读顺序与完成要求。按 [系统总图](system/architecture.md)、[配置表](system/configuration.md)、[源码地图](system/source-map.md)、[搭建检查表](system/INSTALL-CHECKLIST.md) 执行，缺项先实现并测试。最终交付使用者自己的部署清单、真实回执、未完成项和恢复点。

## 阅读导航

1. [Agent 建库入口](docs/00-agent-bootstrap.md)
2. [逐步搭建与必要工具](docs/01-setup.md)
3. [目录、元数据和证据模型](docs/02-vault-model.md)
4. [资料接收、萃取与长文续读](docs/03-ingestion.md)
5. [MCP、角色及程序合同](docs/04-tool-contract.md)
6. [确认中心：先查再问、答复、入库反馈](docs/05-confirmation-center.md)
7. [查询、时间线和综合投稿](docs/06-query.md)
8. [巡检、任务、同步与恢复](docs/07-operations.md)
9. [验收与故障注入](docs/08-acceptance.md)
10. [公开版与生产能力对照](docs/09-parity-and-limits.md)
11. [隐私与发布维护](SECURITY.md)、[依赖与致谢](THIRD_PARTY.md)

四个技能位于 [skills/](skills/)，安装位置取决于目标 Agent；保留完整仓库以解析引用。已有同名 Skill 先备份，不要用公开版覆盖仍在服务私人环境的生产版本。

## 状态与许可

公开版本：`0.2.0`。本地网页仅允许回环地址，文件 Wiki 不支持跨同步并发锁；同步服务单独部署，生产扩展需按源码地图适配。发布验收见 [release/verification.md](release/verification.md)。示例不包含真实人物、业务资料或音频。

本仓库原创公开内容按 MIT 发布；第三方依赖保留各自许可，不把上游源码或服务打包冒充本仓库能力。
