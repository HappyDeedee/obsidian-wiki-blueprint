# 本地参考工具

只有 Python 标准库，不联网、不调用模型、不连接作者 MCP。运行前看 docs/09 的限制。

## 典型顺序

1. `wiki.py init --vault 新空目录`
2. 在演示库的 `00-Inbox/随手放/` 写一份虚构原文。
3. `wiki.py prepare --vault 演示库 --source 库内原文路径 --target 20-Wiki/实体/主题.md`
4. 读完原文与旧页，生成本地 JSON 提案；不要把真实提案提交公开仓库。
5. `wiki.py commit --vault 演示库 --token 上步返回token --plan 私人提案.json`
6. 用 reader 执行 read/query/health，核原文、成果和回执。

所有参数都实际填写；示例中的“演示库”等是说明文字。各命令的详细参数用 `python tools/wiki.py 命令 --help` 查看。

## 本地提案格式

```json
{
  "quotes": ["原文中实际存在的一小段文字"],
  "updates": [{
    "path": "20-Wiki/实体/主题.md",
    "content": "带元数据、结论、事实状态与来源的完整 Markdown"
  }]
}
```

path 必须与 prepare 的 targets 一致。它是本地简化格式，**不是生产 curation_commit schema**。本程序把整份输出纳入事务，旧页完整保存在私有 journal；生产 Agent 仍应读齐后精确生成变化，而不是用旧快照覆盖。

## 确认闭环

question 建卡 → answer 仅保存待核原话 → curator 读取答复、对照问题/原文 → prepare 使用答复路径且 operation=topic_repair → commit → resolve 使用同答复 answer_id 与最终 commit token。

未有正式回执不能 resolve；题目版本或目标 hash 已变就停。CLI 不提供改题、supersedes、未知关闭、合并等生产管理操作，这些合同在 docs/05，不能假装已经由 CLI 完整实现。

## 断点与回滚

每次提交前把旧页完整内容保存在 `.wiki-state/transactions/`，此目录是私人数据，勿提交 Git。部分 durable 失败沿原 token 与完全相同 JSON 重试；不要改计划或删 journal。

目录锁意外遗留时先确认没有写入者运行，再保留证据、人工移走锁，恢复原事务；本程序不自动偷锁。不要让同步器或其他编辑器同时写此参考库。

`rollback --token ...` 仅允许最近完成事务且所有当前字节仍与提交结果相同；新生成文件先归档到私有 rollback-archive 再移出，新来源原件不动，旧页按备份恢复。**rollback 本身不是崩溃原子事务**，中途故障需从私有 journal 备份人工恢复；不能把它用于生产多设备库。
