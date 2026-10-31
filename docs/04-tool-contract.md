# 工具、角色和生产适配合同

这是**接口行为要求，不是已经安装的服务**。按目标服务实际 tools/list 映射，不能猜参数或把本文 schema 直接发给不同实现。

## 角色

| 身份 | 允许 | 必须拒绝 |
|---|---|---|
| reader | 列表、搜索、正文、元数据 | 所有写入、租约、commit |
| proposer | reader + 唯一新建问答/答复投稿 | 覆盖/删除、正式知识写入、伪造主人确认 |
| curator | pending、prepare、stage、commit、verify、lint、短写租约 | 改原件、规则、账号、模型、任务配置 |
| owner-maintainer | 明确授权下维护规则/发布/恢复 | 超出本次授权的批量删除或配置变更 |

服务端从已认证身份决定权限，不接受请求体 `role/admin/user_confirmed:true` 自报授权。工具名、路径、操作类型都校验。分开凭证；查询工具不能因后台有 curator 密钥便自动升级。

路径检查：只允许库内相对路径，规范化后拒绝绝对路径、`..`、反斜杠歧义、符号链接逃逸、Windows ADS/设备名、非授权目录和非预期扩展。投稿必须原子 create-if-absent，不先检查后覆盖。服务绑定受限网络、TLS、认证与审计；token 不进入日志和模型正文。

**本地参考 CLI 的 role 只是防误操作，不是恶意本机用户隔离。** 拿到文件系统权限的人仍可直接改文件；生产必须在网关/服务端实施以上边界。

## 基础读取

| 能力 | 输入与返回要求 |
|---|---|
| read_note | path、Unicode offset、limit；返回正文、next_offset、total_chars、sha256/read_range |
| list_notes | folder、分页、总量、index_revision；日期筛选必须明确支持 |
| search_notes | folder、1–2 核心词、offset/revision；返回只用于定位 |
| get_note_metadata | frontmatter（含 YAML 列表）、出链/反链/版本 |

INDEX_CHANGED 从同一查询第一页重查并去重；偏移单位不一致时适配，不把字节 offset 直接当 Unicode 字符。搜索无命中不能证明不存在。BM25/别名匹配是实现选择，不能宣称为语义检索。

## curation 扩展

| 接口能力 | 最小行为 |
|---|---|
| writer_acquire/release | 单一写者，owner、token、有效期/续约；BUSY 立即返回；只能释放自己的 token |
| pending/status | 可信来源 ID、paths、内容 hash、purpose、kind、真实状态/断点；排除日志/生成物 |
| prepare | 绑定租约、来源快照、规则及旧页版本；返回 token、连续 source read_range 和 contexts |
| context_read | 同事务旧页分段和 hash；不冒充来源阅读 |
| stage | 累积阅读证据；只接受当前快照与连续范围；不计成功 |
| commit | 写前验证与 durable journal；同 token 同参数幂等恢复；程序生成索引、台账、状态和回读 |
| verify | 按持久 commit_key/source_id/operation 核对成果及 maintenance_finalized；不能信任自填 outputs |
| lint_prepare/save | 版本化页范围、真实覆盖和证据；普通覆盖与 temporal 覆盖分开 |

接口可带 `curation_` 前缀；这套名字不是 MCP 标准。没有这些接口时只能做明确范围的人工流程，不能宣称具备完整生产保证。

### 变更提案的语义形状

```json
{
  "source_id": "source-demo-001",
  "operation": "new",
  "quotes": [{"path": "00-Inbox/随手放/示例.md", "text": "下周验证可行性"}],
  "updates": [{"path": "20-Wiki/实体/示例.md", "mode": "create", "content": "经核证的 Markdown"}],
  "overview_decision": {"action": "unchanged", "reason": "未改变整体理解"}
}
```

生产还需 candidates、contradictions、预期旧页版本、规则版本、读取范围回执和验证结果。实际参数名以实现 schema 为准。本地参考工具接受较小合同，见 `tools/wiki.py`，不伪装成生产 MCP。

### journal 必须能回答

谁、哪项授权、哪份来源的哪个 hash、原页版本、哪些范围被读、哪些字面证据、写入计划/备份、已完成步骤、最终输出 hash、验证状态、索引/日志收尾是否完成、是否 reused。

普通 Inbox 的 index 可以为 null，不能转成历史队列 index=0；身份由 durable journal 返回的 commit_key 决定。历史队列有独立断点，不能为了增量重置它。部分成功和恢复中均不向人显示“已入库”。

## 仓库参考程序

提供 init/read/query/prepare/commit/question/answer/resolve/health/rollback。使用进程级目录锁、精确来源/目标 SHA、备份、原子单文件替换及 journal 恢复。它不提供分段 stage、持久生产租约、独立 OS 身份、MCP transport、全文语义核验、完整 YAML/Obsidian 索引，也不承诺同步引擎的并发写入安全。

平台无关的最小参考宿主见 `system/reference-host/`：用 SQLite + 文件系统实现本章 "curation 扩展" 的全部最小行为（租约、pending、prepare、连续读取、stage、commit 四重验证与幂等恢复、verify、lint），stdlib only。它是"确定性程序"一半的最小形态；身份认证、MCP transport、多机并发仍须按本章在生产宿主中实现。
