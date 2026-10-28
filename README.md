# Obsidian Wiki Blueprint

把零散资料变成**有来源、会更新、能核对、可以追问**的个人 Wiki。

这不是把文件丢给 AI 做摘要，也不是只安装三个 Skill。完整方法是：

```text
接收原件 → 提取可读正文 → 分段阅读 → 对照旧知识 → 萃取/更新主题
                                                     ↓
只读查询 ← 知识页与原文 ← 核验提交与回执 ← 待确认 → 主人答复 → 核证采纳
                         ↑
              每日结构巡检 / 每周内容巡检 / 到期复核
```

本仓库从一套实际使用中的私人知识库中抽出方法，**重新编写公开规则、示例和搭建资料**，不包含作者知识库、私人 MCP 地址、账号、令牌、设备路径、数据库或生产备份。旧私有仓库不需要公开。

## 先说清能得到什么

| 内容 | 本仓库提供 | 不应误解成 |
|---|---|---|
| 方法论 | 来源、实体、概念、综合、项目、时间、证据与历史规则 | 只做逐文件摘要 |
| Agent Skill | ingest / query / health 三个脱敏技能，以及搭建入口 | Agent 自动获得你的知识库权限 |
| 初始知识库 | 空白目录、规则、索引、元数据模板 | 作者的私人知识库 |
| 可运行参考程序 | 本地单机的初始化、版本保护提交、确认卡/答复、查询、巡检、回滚演示 | 原生产 MCP 或完整生产服务的开源镜像 |
| 完整搭建蓝图 | MCP 能力合同、角色隔离、确认中心、调度、同步、备份及验收 | 克隆即可一键部署手机同步、网页确认中心、云端控制器 |

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

## 把这段交给其他 Agent

> 先读 README.md、AGENTS.md、docs/00-agent-bootstrap.md 和 docs/09-parity-and-limits.md。帮我在自己的环境建立这套 Wiki：先列工具和权限缺项，再创建隔离演示库，完成本地闭环测试。然后依据 docs/01-setup.md 分阶段接入我的 Obsidian、Agent 和同步。不得假定仓库带有作者 MCP，不猜地址/令牌，不修改已有库、不重置队列、不创建重复调度。缺扩展时按 docs/04-tool-contract.md 实现适配器并测试；未实现的必须明确标出来。先用虚构材料证明接收、萃取、确认、查询和巡检，再申请切换真实资料。

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

三个技能位于 [skills/](skills/)，安装位置取决于目标 Agent；可以只在任务中明确加载，不必全局安装。已有同名 Skill 先备份，不要用公开版覆盖仍在服务私人环境的生产版本。

## 状态与许可

公开版本：`0.1.0`。本地参考程序仅支持可信单机文件目录；不提供网络鉴权、分布式锁、CouchDB 同步或网页服务。发布验收见 [release/verification.md](release/verification.md)。示例不包含真实人物、业务资料或音频。

本仓库原创公开内容按 MIT 发布；第三方依赖保留各自许可，不把上游源码或服务打包冒充本仓库能力。
