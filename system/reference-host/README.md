# 最小参考宿主（curation_* 合同的平台无关实现）

这是 `docs/04-tool-contract.md` 里 "curation 扩展" 的**最小可运行实现**：只用 Python 标准库（sqlite3 实现租约/队列/持久 journal，文件系统存 Wiki），不依赖任何厂商平台、容器或外部服务。在自己的电脑或服务器上就能跑，用来先验证"整理事务"这套机制，再决定生产宿主怎么做。

## 它在系统里的位置

```text
模型（Agent，按 ingest Skill 理解来源、提出变更）
        ↓  调用本宿主（确定性程序）
SQLite 租约/队列/journal ＋ 原子文件写入 ＋ 写后回读
        ↓
Markdown 知识库（你的 Vault）
```

模型负责理解和提出变更，确定性程序负责权限、版本、事务、索引、台账和回读——这正是本仓库的方法论。本宿主是"确定性程序"这一半的最小形态。

## 合同对照

| 合同能力（docs/04） | 本实现 |
|---|---|
| writer_acquire/release | 单一写者；owner/token/有效期/续约；BUSY 立即返回；只能释放自己的 token；过期自动清理 |
| pending/status | 可信 source_id、paths、内容 SHA256、purpose、kind、真实状态/断点；`90-Logs/`、`00-Test/` 拒绝入库 |
| prepare | 绑定租约、来源快照（内容哈希）、规则版本（读 `99-System/知识库规则.md` 的 frontmatter）、旧页版本；返回 token、连续 source read_range、contexts |
| context_read | 只能读 prepare 时声明过的旧页；Unicode 字符偏移；记录证据，不冒充来源阅读 |
| source_read | 来源分段连续读取（offset 必须接着上一段）；快照变化即拒绝 |
| stage | 校验已累积阅读证据的连续性；**不计成功** |
| commit | 写前四重验证（租约/来源快照/旧页版本/引文逐字命中已 stage 的来源正文）；先写 journal 再写文件；同 token 同参数幂等恢复；部分失败沿原事务恢复；原子单文件替换（os.replace）+ 写前备份；写后回读 |
| verify | 按 commit_key 重读 journal + 磁盘文件哈希 + 来源状态，不信任自填 outputs |
| lint_prepare/save | 版本化页范围、真实覆盖、证据路径；normal/temporal 分开 |

## 快速开始

```bash
# 1. 准备一个空目录做 vault（或指向已有库的副本，先别指真库）
python3 system/reference-host/curation_host.py --vault /srv/wiki/vault --state /var/lib/kb-curation pending

# 2. 走一遍完整事务（示例）
T=$(python3 system/reference-host/curation_host.py --vault /srv/wiki/vault --state /var/lib/kb-curation \
  acquire --owner curator-1 --purpose demo | python3 -c "import json,sys; print(json.load(sys.stdin)['token'])")
# 注册来源、prepare、source-read、stage、commit、verify、release ……
# 完整演示脚本见 tests/test_reference_host.py（可读作使用手册）
```

也可以直接 `import curation_host` 当库用：`CurationHost(vault_root, state_dir)`。

## 定时运行（24h Worker 的确定性一半）

见 `cron.example`。注意：cron 只能调度"确定性动作"（如 `pending` 检查、失败 journal 告警）；**真正理解来源并提出变更的是模型**，需要另配一个定时 Agent 任务（按 ingest Skill 调用本宿主），两者合起来才是 24 小时整理 Worker。

## 边界（生产前必须补）

- **无身份认证**：单可信操作者假设。生产必须在网关/服务端做身份鉴别，服务端从已认证身份决定权限，不接受自报 role（见合同）。
- **无 MCP transport**：Python API 与 CLI 和工具调用 1:1 对应；MCP 宿主适配由使用者按 `docs/04` 实现。
- **无语义搜索**：读取是精确文件/偏移操作；搜索沿用 Vault 自己的索引。
- **单机 SQLite**：不承诺跨同步并发写入安全；多写者场景用生产的单写控制器。
- **无模型调用**：本宿主不调任何模型、不联网。

## 文件

| 文件 | 说明 |
|---|---|
| `curation_host.py` | 实现 + CLI（stdlib only） |
| `worker-loop.md` | 模型一半的回路规格：在自己平台上实现定时整理 Worker 的运行契约 |
| `schema.sql` | SQLite 表结构（文档镜像，程序内建同版本） |
| `cron.example` | 定时任务示例 |
| `requirements.txt` | 无第三方依赖（stdlib only） |
| `README.md` | 本文件 |
