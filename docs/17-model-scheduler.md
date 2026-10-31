# 模型一半：定时整理任务的选型、提示词与调度

24 小时整理 Worker 由两半拼起来：确定性宿主（租约、journal、写入、回读，见 [工具合同](04-tool-contract.md) 与 `system/reference-host/`）和**定时被唤醒的 Agent**。本文件只讲后一半：选什么平台跑、定时任务怎么写、提示词贴什么。

理解、判断、写提案由模型完成；租约、写入、回读由宿主完成。下面提供配置方法和提示词，**不提供可执行的模型循环，也不代表生产宿主或全栈安装器已经实现**。先按 [功能对照](09-parity-and-limits.md) 集成自己的宿主，再手动验收一份虚构来源，最后启用定时任务。

相关：确定性维护任务的调度见 [运维手册](../system/runbooks/08-operations.md) 的"程序化维护任务"节；任务分工见 [调度和控制器](../system/runbooks/07-growth.md)。

## 平台选型

选一个能**定时唤醒 Agent、注入 Skill 上下文、保管凭证、设费用上限**的平台或 CLI。不要仅凭平台名称判断能否接入，逐项检查：

1. 定时任务能按 cron 表达式或间隔触发，触发时带上 ingest Skill 全文（或等价指令）。
2. 模型的 API Key / 平台凭证由平台保管，不写进仓库、不进提示词。
3. 实际执行费用、总时长和工具调用上限，有失败告警、手动暂停；自动重试默认关闭。提示词里的预算要求不能代替平台或调用包装器的硬限制。
4. 任务日志独立可查，能看到每次唤醒的实际输出。

## 三种调度做法

**A. 平台内置定时任务**：先检查有没有同库任务；已有任务就修改原任务，不再创建一个。注入 ingest Skill 和下一节提示词，设置频率、整理身份及预算。平台负责唤醒、注入上下文、记日志；先手动运行并检查真实回执。

**B. cron + 可唤醒 Agent 的 CLI**：适合有服务器、Agent 可用命令行调用的场景。cron 只负责"到点喊一声"，实际干活的还是 Agent：

```text
# 仅展示 cron 形状；agent-run 是虚构命令，不能直接执行。
# 换成已经安装且手动验收过的 CLI 包装器，并配置日志轮转。
5 * * * * /usr/local/bin/agent-run --task wiki-ingest --prompt-file /etc/wiki/ingest-prompt.md >> /var/log/wiki-ingest.log 2>&1
```

**C. systemd timer（Linux）**：使用独立的 [wiki-agent.service.example](../system/deploy/wiki-agent.service.example) 和 [wiki-agent.timer.example](../system/deploy/wiki-agent.timer.example)，配置步骤见下节。别名任务的用户、凭证和退出码属于维护程序，不能只换它的 ExecStart 就拿来运行 Agent。

同一库的模型整理只选一个调度源；别名维护仍是另一种确定性任务。不要另开第二个整理控制器。

## systemd 示例怎样接入自己的 CLI

本节只适用于已集成宿主的 Linux 服务器；Windows/macOS 仍按本地 L0 手册使用。示例不会安装 CLI、实现模型循环或生成凭证。

1. 安装所选 CLI，准备仓库外的启动包装器。它必须加载完整 ingest Skill、下节提示词和私有 MCP 身份，执行真实的模型/工具循环；保管凭证，执行费用、工具调用和时长上限，记录事务断点，失败返回非零。**仓库没有提供这个包装器**；没有现成可验收的入口时，记录“模型循环尚未接入”，不要启用定时器。
2. 创建专用用户 `wiki-agent`，仅授予所需目录权限。代码和提示词只读，CLI 自己的状态目录可写；通过宿主的整理身份写知识，不能把知识库文件直接授权给 CLI 改写。以该用户手动运行，确认登录状态、HOME、CLI 沙箱和凭证均可用；交互式终端能用不等于此用户能用。
3. 将 service/timer 示例安装为 `wiki-agent.service` 和 `wiki-agent.timer`。把 service 的 ExecStart 改成上述包装器的**真实绝对路径**；示例中的 `/usr/local/libexec/wiki-agent-run` 只是待替换的位置。不要在 ExecStart 中写密钥。创建 `/etc/wiki/agent.env`，例如只设置 `WIKI_PROMPT_FILE`、`WIKI_AGENT_CONFIG` 的私有绝对路径，并让包装器实际读取它们。这些是包装器约定，不是各家 CLI 通用参数。配置文件只允许服务用户读取，目录不可被其他用户遍历。
4. 按实际预算调整 timer 的时间和 service 的超时。`TimeoutStartSec` 只是进程时限，不能限制费用；包装器还须能在到限或收到停止信号时记录未完成事务。被强制终止时按 journal 恢复，不自动重投。
5. 在测试库先检查单元语法，再手动启动。以下命令**逐条执行**：

```text
sudo systemd-analyze verify /etc/systemd/system/wiki-agent.service /etc/systemd/system/wiki-agent.timer
sudo systemctl daemon-reload
sudo systemctl start wiki-agent.service
sudo systemctl show wiki-agent.service -p Result -p ExecMainStatus
sudo journalctl -u wiki-agent.service -n 100 --no-pager
```

验收：一份虚构来源得到 commit_key 和 verify 通过，重复运行不重复入库；权限不足被拒绝；CLI 非零退出让服务显示失败。模拟 BUSY 时结束本轮并明确报告“未入库”。模板没有 `SuccessExitStatus=2`，也没有自动重启；包装器若把 BUSY 转为正常退出，必须在输出中保留 BUSY 状态，服务成功不等于入库成功。日志只写必要回执标识和状态，不输出密钥、原文或模型的完整请求。

手动验收通过、确认同库没有其他整理调度后，才执行 `sudo systemctl enable --now wiki-agent.timer`。用 `sudo systemctl list-timers wiki-agent.timer` 核对下次运行；暂停用 `sudo systemctl disable --now wiki-agent.timer`，它不会停止已经运行的 service，若需停止本轮，先核对断点再执行 `sudo systemctl stop wiki-agent.service`。

## 定时任务提示词（注入 Skill 后使用）

操作名表示能力，不是所有宿主都使用的同名工具。首次必须读取实际 `tools/list` 及参数 schema，核对所选身份的权限；按 [工具合同](04-tool-contract.md) 和宿主文档映射后使用，不能凭名称猜参数。缺少必要能力时停止接入，不能绕过宿主直接写文件。

```text
你是目标知识库的整理 Worker。本轮只做一件事：把待处理来源变成知识或待确认，全部写入走确定性宿主。

先决条件：
1. 先读目标库 99-System/知识库规则.md；每轮核对规则版本/哈希，未变化才可复用已读规则。
2. 按 ingest Skill（随本任务注入）执行；本轮只用整理身份，没有整理授权就结束，不降级写。

本轮步骤：
1. 调宿主 pending 取待处理来源；没有就写一条空跑记录，结束。
2. 优先级：确认答复 > 会议 > 日记 > 其他。确认答复不占普通来源数量额度，但同样受总费用、时长和工具调用上限约束。到限保存进度，剩余留待下轮，不报全部完成。
3. 每份来源单独取得 writer 租约，记录 owner、token 和有效期。返回 BUSY → 结束本轮，不等待、不换身份重做。不要把一份来源的 token 用于另一份来源。
4. 用本来源和候选旧页调用 prepare，绑定来源快照、规则版本和旧页版本。按返回的连续范围从 offset=0 读完来源；用 context_read 读完 prepare 声明的相关旧页。旧页没有声明或读取不完整时，不得提交更新。需补充上下文时按宿主合同重新准备，不能沿用失效证据。
5. 结合原文与旧页生成提案：逐项判定 create / update / covered / none，每项附原文逐字短引文；无可提炼内容判 no_knowledge 并说明原因，不空写。调用 stage 登记并核验连续阅读证据；stage 不计入库。字段和调用顺序服从当前宿主 schema；缺能力就停止，不跳过。
6. 使用同一事务 commit → verify；verify 通过才算成功。记录 commit_key 与回读结果，再安全释放本来源租约；下一份来源重新 acquire → prepare → 读取 → 提案/stage → commit → verify → release。
7. 全程在租约到期前按宿主规则 renew；失去租约立即停止写入。结束时报告来源、判定、commit_key、verify 结果、未读范围、剩余来源和预算停止原因。

铁律：
- 不直接写知识文件，一切写入走宿主；宿主返回的错误按 ingest Skill 的失败恢复处理。
- 不调用联网搜索；需外部核对的标"需外部"留给巡检，不把外部候选写入正式知识。
- 失败写失败记录，不静默跳过；"尝试过""读过"不算成功。
- 部分提交不等于未发生：保留 token、原提案和 journal。在有效租约内，按宿主恢复合同以同一事务及相同参数恢复；不能换 token 或身份自动重投。
- 只释放自己持有的租约。未解决的部分提交不能放进无条件 finally-release；先按宿主恢复合同处理。租约已过期或无法恢复时停止这一项、保留断点并报警，交运维核对 journal，不继续写也不宣称成功。
```

把模板存到仓库外的私有位置（如 `/etc/wiki/ingest-prompt.md`），按自己库的目录名替换"目标库"相关路径。不要把提示词里的身份、路径或凭证提交 Git。

参考宿主的租约默认只有 300 秒，一个 token 绑定一份来源快照和同一提案。长文和慢模型必须在过期前续租；读取或 stage 成功不能延长租约。生产适配器若有不同的事务/恢复语义，以实际 schema 和合同为准，并先做失败恢复验收。参考实现的部分提交恢复也不能代替生产恢复方案。

## 频率与预算建议

| 任务 | 建议频率 | 说明 |
|---|---|---|
| 普通增量整理 | 每小时 | 普通来源每轮限量（如 3 份）；确认答复优先、不占普通额度，仍受总预算约束 |
| 每日结构巡检 | 每天 | 调 health Skill 结构部分；交叉核对真实入库数 |
| 每周内容巡检 | 每周 | 调 health Skill 内容/时效部分；在预算内分批 |
| 别名/派生视图刷新 | 见运维手册 | 确定性任务，走 [运维手册](../system/runbooks/08-operations.md) 的入口 |

每轮设最大来源数和费用上限；当天有未整理来源而真实入库数为 0，报每日入库故障（见 health Skill），不写成"任务完成"。
