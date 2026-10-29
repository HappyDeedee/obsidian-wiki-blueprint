# 源码地图与部署接线

来源：2026-10-29 从实际运行服务器读取应用源码后脱敏。文件清单与公开文件 hash 见 [source-catalog.json](source-catalog.json)。不含原运行状态、连接值、密钥、模型或私人数据。

## 入口及依赖

| 目录 | 主入口 | 依赖 / 读取与产出 |
|---|---|---|
| `production-reference/meeting/` | `receiver.py --config ...` | Python、阿里云 SDK、`meeting_analysis.py`、`mcp_export.py`、确认词典/热词模块；任务 SQLite、音频、听悟结果 → 固定 MCP import |
| 同目录 | `inbox_listener.py --config ...` | Linux、Docker、`meeting-bridge/inbox-audio-bridge.mjs`；LiveSync 附件 → 接收器队列 |
| `production-reference/meeting-bridge/` | `meeting-import.mjs` | 对应上游 Vault 的二进制/笔记能力；固定任务清单 → 纪要、完整转写、原件登记 |
| `production-reference/voiceprint/` | `vp_extract.py` → `vp_cluster.py` → `vp_clips.py` → `vp_quotes.py` | numpy、PyAV、sherpa-onnx、单独模型；只写私有目录 |
| `production-reference/confirm/` | `kb_voiceprint_watch.py` | 接收器任务和结果、声纹脚本、持久去重/锁；新会议 → 声纹/卡片/状态报告 |
| 同目录 | `make_speaker_cards.py`、`vp_predict.py` | 私有声音库与已确认样本；预测只是候选 |
| 同目录 | `make_question_cards.py` | `kb_register_read.py`、词典缓存、`server.py` 同一把锁；问题登记 → 稳定卡片 |
| 同目录 | `server.py` | Linux `fcntl`、状态目录、认证、UI；确认卡 → 答案/撤销事件。原 HTTP 认证接口须适配公开 UI，见确认手册 |
| 同目录 | `kb_qwrite.py`、`kb_sync.py` | 投稿身份和 MCP；真实答复 → `confirmation-response`，保留延迟撤销窗口与历史 |
| 同目录 | `kb_minutes.py` | 完整转写/原始结果、已确认词典；派生纪要和转写名字/身份来源标识 |
| 同目录 | `kb_feedback.py`、`kb_receipt_reader.py` | 最终 journal/回执；把“投稿”与“入库”分开展示 |
| 同目录 | `kb_person_attendance.py`、`kb_person_reconcile.py`、`kb_person_profile.py`、`kb_person_daily.py` | 人工身份与 domain 授权、完整转写、writer lease、人物页/词典；撤销重算原有归属 |
| 同目录 | `kb_confirmed_dictionary.py`、`kb_dictionary_cache.py`、`kb_tingwu_phrases.py` | 唯一词典 → 脱敏候选、避免重复确认、更新自己的听悟热词资源 |
| `production-reference/curation/` | `createPipeline()` in `curation-pipeline.mjs` | 宿主注入真实 vault/队列/状态；租约、prepare、context_read、stage、commit、lint；不是 CLI 入口 |
| 同目录 | `curation-verifier.mjs`、`confirmation-response-policy.mjs` | 结构版本/字面证据/回读与答复 ID/version 校验；不验证所有语义 |
| 同目录 | `mcp-role-policy.mjs`、`mcp-client-bearer-auth.mjs`、`mcp-atomic-proposal.mjs` | 宿主 MCP 请求处理及身份登记；每次调用查角色，投稿 create-only；未挂载宿主就没有权限隔离效果 |
| 同目录 | 时间、阅读预算、缓存、暂停与诊断模块 | 只在对应主流程调用；不得把诊断脚本当自动修复 |
| `production-reference/controller/` | `curation-controller.py --loop` | Linux、平台容器、`curation-controller-db.ts`、策略/续跑模块、自己的配置和身份 |
| 同目录 | `curation-controller-db.ts` | 直接依赖 Rakazo 的 Prisma/job/tenant 接口；必须固定自己的平台版本与 actor/routine ID |
| 同目录 | `kb-daily-report.py`、`daily_ingestion_audit.py` | 实际任务/回执 → 日报与超时报警；不是 `completed` 状态即成功 |
| `production-reference/maintenance/` | `obsidian-structure.py`、`wiki-generated-refresh.py` | MCP、只读扫描、派生视图、空闲写锁；程序报告不等于模型语义巡检 |
| `production-reference/format/` | `format-worker.py` | 自己的配置、附件/格式提取适配；不要自动将所有二进制当笔记正文 |

## 很关键的缺口：运行宿主

本仓库公开的是可读的自定义业务源码，**没有把原服务器镜像、编译后的私有宿主覆盖文件、第三方 node_modules 或整个平台数据库一起打包**。原版 `obsidian-sync-mcp` 不会因为目录中出现这些 `.mjs` 就自动加载它们。

接手 AI 必须完成以下适配，并把结果提交到使用者自己的部署仓库：

1. 固定 `es617/obsidian-sync-mcp` 源码版本；公开参考使用上游 `0.9.0` 时代能力，基础镜像使用观察到的不可变 digest。
2. 在自己的源码构建里接入 `createPipeline`、verifier 和角色中间件；函数签名从本目录实际导出读取，不凭名称猜参数。
3. 注册 [能力合同](../docs/04-tool-contract.md) 中每个扩展工具，校验输入、writer token、版本和身份；按照相同 token/相同参数恢复提交。
4. 接入二进制读取/导入适配器，固定 `import_meeting` 的任务清单与授权路径；不能给会议 token 普通全库写权限。
5. 为每个工具做独立的正反向测试，再给整理 Agent 使用。查询/投稿身份不能取得租约和 commit；不能仅依赖提示词拒绝。
6. 在 Linux 新环境测试，保留镜像 digest、源码 commit、依赖锁和回执。此步骤不是本仓库基础 Compose 已替使用者完成的工作。

这项集成目前仍须 AI/维护者实施；仓库将其列为明确任务，不能对使用者宣称“生产一键完成”。本地参考版提供了可运行的小闭环，用来先验证概念、界面和交接方式。

## 为什么不直接运行所有参考文件

部分文件含 Linux 默认路径、平台内部 API、模块导入时行为和历史兼容逻辑。新的使用者没有作者同名账号、相同队列或已有状态。先适配配置与数据合同，再按服务单独启动。不要把 76 个文件循环执行；不要将代码模板放进真实知识库让它们变成资料源。

## 本地辅助来源与可选依赖

两个 `backup_consistent.py` 来自作者本地维护代码，作为控制器和维护脚本的必要辅助模块；其余 74 份业务源码从运行服务只读采集后脱敏、适配。`source-catalog.json` 分别标记来源类型与公开字节 hash。原文件名保留便于追踪，不代表可以无配置直接运行。

平台特定的可选模型观测模块和历史云函数没有随仓库分发；按各模块的显式降级/关闭分支运行。若启用相应平台功能，应在自己的构建中补上依赖并独立验收，不得连到作者平台或沿用历史事故 ID。
