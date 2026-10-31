# 模型一半：定时整理任务的选型、提示词与调度

24 小时整理 Worker 由两半拼起来：确定性宿主（租约、journal、写入、回读，见 [工具合同](04-tool-contract.md) 与 `system/reference-host/`）和**定时被唤醒的 Agent**。本文件只讲后一半：选什么平台跑、定时任务怎么写、提示词贴什么。

一句话：模型一半不是一段可复制的代码，而是一个"定时唤醒的 Agent + ingest Skill"的运行配置。理解、判断、写提案是模型在干；租约、写入、回读是宿主在干。两者不互相冒充。

相关：确定性维护任务的调度见 [运维手册](../system/runbooks/08-operations.md) 的"程序化维护任务"节；任务分工见 [调度和控制器](../system/runbooks/07-growth.md)。

## 平台选型

选一个能**定时唤醒 Agent、注入 Skill 上下文、保管凭证、设费用上限**的平台。作者自用 Rockdo；同类选择如 OpenClaw、爱马仕等 Agent 平台均可，关键看四条：

1. 定时任务能按 cron 表达式或间隔触发，触发时带上 ingest Skill 全文（或等价指令）。
2. 模型的 API Key / 平台凭证由平台保管，不写进仓库、不进提示词。
3. 有每轮费用上限、失败告警、手动暂停；自动恢复默认关闭。
4. 任务日志独立可查，能看到每次唤醒的实际输出。

## 三种调度做法

**A. 平台内置定时任务（推荐）**：在平台的定时任务/计划任务里新建任务，粘贴下一节的提示词模板，设置频率。平台负责唤醒、注入上下文、记日志。

**B. cron + 可唤醒 Agent 的 CLI**：适合有服务器、Agent 可用命令行调用的场景。cron 只负责"到点喊一声"，实际干活的还是 Agent：

```text
# 每小时 05 分唤醒整理任务（示例，按自己平台的 CLI 改写）
5 * * * * /usr/local/bin/agent-run --task wiki-ingest --prompt-file /etc/wiki/ingest-prompt.md >> /var/log/wiki-ingest.log 2>&1
```

**C. systemd timer**：写法参考 [运维手册](../system/runbooks/08-operations.md) 的 `wiki-aliases.service.example` / `.timer.example`，把 ExecStart 换成唤醒 Agent 的命令。同一库只选一种调度源，避免重复执行。

## 定时任务提示词（可直接复制）

```text
你是目标知识库的整理 Worker。本轮只做一件事：把待处理来源变成知识或待确认，全部写入走确定性宿主。

先决条件：
1. 先读目标库 99-System/知识库规则.md；已读且未变化不重复读。
2. 按 ingest Skill（随本任务注入）执行；本轮只用整理身份，没有整理授权就结束，不降级写。

本轮步骤：
1. 调宿主 pending 取待处理来源；没有就写一条空跑记录，结束。
2. writer_acquire(owner=本任务名)。返回 BUSY → 结束本轮，不等待、不换身份重做。
3. 优先级：确认答复 > 会议 > 日记 > 其他。答复不计入普通资料数量上限。
4. prepare 后从 offset=0 连续读完来源全文；没读齐不做任何判断。
5. 逐项判定 create / update / covered / none，每项附原文逐字短引文；无可提炼内容判 no_knowledge 并说明原因，不空写。
6. commit → verify。verify 通过才算本轮成功。
7. writer_release。写本轮报告：来源、判定、commit_key、verify 结果、未读范围。

铁律：
- 不直接写知识文件，一切写入走宿主；宿主返回的错误按 ingest Skill 的失败恢复处理。
- 不调用联网搜索；需外部核对的标"需外部"留给巡检，不把外部候选写入正式知识。
- 失败写失败记录，不静默跳过；"尝试过""读过"不算成功。
```

把模板存到仓库外的私有位置（如 `/etc/wiki/ingest-prompt.md`），按自己库的目录名替换"目标库"相关路径。不要把提示词里的身份、路径或凭证提交 Git。

## 频率与预算建议

| 任务 | 建议频率 | 说明 |
|---|---|---|
| 普通增量整理 | 每小时 | 每轮限量（如 3 份来源）；确认答复优先且不限量 |
| 每日结构巡检 | 每天 | 调 health Skill 结构部分；交叉核对真实入库数 |
| 每周内容巡检 | 每周 | 调 health Skill 内容/时效部分；在预算内分批 |
| 别名/派生视图刷新 | 见运维手册 | 确定性任务，走 [运维手册](../system/runbooks/08-operations.md) 的入口 |

每轮设最大来源数和费用上限；当天有未整理来源而真实入库数为 0，报每日入库故障（见 health Skill），不写成"任务完成"。
