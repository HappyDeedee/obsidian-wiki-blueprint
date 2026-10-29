# Wiki 接口与正式整理权限

基础 Compose 运行的是上游**只读** MCP。先确认它能读懂自己 LiveSync 数据库，再实施整理扩展。不要直接改 `READ_ONLY=false` 就宣称完整整理系统上线。

## 身份分工

- reader：读/列/搜，普通查询不写日志与队列。
- proposal：只新建问答投稿和待确认答复；不能覆盖已有文件或正式知识。
- curator：沿 writer lease 和版本化提交更新正式知识；不能改系统规则、覆盖原件或绕过校验。
- meeting：固定 `import_meeting` 入口，依据持久任务清单验证产物；不允许任意路径写入。

参考身份策略在 `production-reference/curation/mcp-role-policy.mjs`，配置生成器会生成独立令牌及 hash registry。但原版 MCP 不读取这个 registry；必须在自己的构建中挂接中间件，不能只把 JSON 放进目录。

## 整理扩展的宿主接线

先阅读 [源码地图](../source-map.md)、[扩展合同](../../docs/04-tool-contract.md) 与 `curation-pipeline.mjs` 的实际导出。宿主提供：

1. 能读取/精确更新自己知识库的 Vault 后端；完整二进制能力独立实现，不让 Markdown 工具上传音频。
2. 持久的来源队列、状态、事务 journal、历史备份、版本 hash 与单写租约。
3. prepare/context_read/stage/commit、pending/status、lint_prepare/lint_save、verify、writer acquire/release 的工具 schema。
4. 请求级身份验证与每次工具调用的角色、工具和路径检查。未知角色默认拒绝；已撤销会话也拒绝。
5. 相同 token/相同参数的重放恢复；并发变化时拒绝，不能换 token 重做已提交来源。

这里是明确的源码集成工作；仓库没有分发原服务器编译宿主覆盖文件。因此后续 AI 需要在使用者自己的部署分支完成并测试，不能冒称已在公共版替用户实现。

## 权限验收

至少逐项真实调用并保存结果：无 token、错误 token、reader commit、proposal 覆盖同名/越目录、路径穿越、系统规则改写、会议 token 普通写入、失效 writer、旧目标版本、重复提交、提交后崩溃恢复。正向调用后回读实际成果与 journal。

原生产参考中有兼容性的普通写工具及平台假设，接手 AI 仍需审查其范围；不要仅凭“源自生产”认定安全。对新环境应优先使用限制明确的固定事务入口，测试原件和规则无法被普通整理身份覆盖。

接入确认中心的投稿令牌、入库反馈读取身份、人物页维护身份分别配置最小权限。不要为了某个服务暂时接不上，就把主人最高权限令牌交给所有进程。
