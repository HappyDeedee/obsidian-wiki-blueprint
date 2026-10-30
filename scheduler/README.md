# 通用调度与模型萃取（开发中）

`agent.mjs` 已提供真实流程，不需要新 AI 临时写循环：

```text
取 pending → 取得唯一 writer lease → prepare → 读完旧页 context
→ 模型生成严格 JSON → 长文 stage / 短文或已读完全文 commit
→ verify_saved 回读 → 保存本轮报告 → 释放 lease
```

模型不拿 Vault 密码，不直接执行 shell 或写 Wiki。它只输出工具参数，宿主检查生产 schema、引文、路径、原件版本和回执。先保存模型结果，再提交；相同请求可复用已保存结果。

## 接线

私有配置结构参考 `config/host.example.json`。`agent.mcp_token` 使用新用户自己的 curator 身份；连接地址只允许本机。配置预算与模型后，由已有 systemd timer 或 cron 执行：

```text
WIKI_CONFIG=/absolute/private/config.json node scheduler/agent.mjs
```

支持两个真实模型传输：

- `model.mode=api`：自己的 OpenAI 兼容 HTTPS 接口，`base_url` 是含版本前缀、不含 `/chat/completions` 的基址。`WIKI_MODEL_API_KEY` 可覆盖私有配置中的 key。需要模型支持 `response_format=json_object`。没有自动更换模型或服务商的降级。
- `model.mode=command`：自己的 CLI 适配器。`model.command` 是绝对可执行路径加参数的数组；程序不经过 shell。适配器从 stdin 接收一份 JSON，从 stdout 返回一份 JSON 工具参数，诊断应写 stderr。仓库**未附 Codex/Claude 专用 CLI 适配器**，不能声称已经支持所有 CLI 参数版本。

二者都必须明确设置 `allow_model_calls=true`，否则返回 NEEDS_USER，不调用模型。部署测试用 `replay`，回放文件必须标记 `mock:true` 且包含**精确请求 SHA256** 对应的响应；换资料不能套用旧响应。

预算为每次运行请求数、输入字符上限、输出 token 上限、响应字节数和超时。字符上限不是准确 token 计费预估，仍需用户在服务商配置总额/告警。

## 重跑与失败边界

- 长文超出本轮段数时返回 CONTINUE，保存进度，后续运行在同一有效 lease 下续读。
- HTTP/CLI 请求结果不确定时不自动重发，避免重复计费。私有状态保留 IN_FLIGHT，需核实自己的服务商账单/响应后处理。
- 模型 JSON、证据或旧页版本检查不通过，不会伪造成功。保存原提案，失败后不自动篡改源码或覆盖资料。
- 运行中 lease 已过期的恢复尚不能全自动处理，目前明确返回 NEEDS_USER。这是仍待补齐的恢复缺口，不属于“一键部署已全部完成”。
- 每次只处理一个来源，未实现通用平台 Bot 导入、全套巡检调度或日报写回知识库。当前报告存放于私有 `generic-agent` 状态目录。

测试中的模型是明确的确定性测试替身，实际驱动公开 production pipeline，包括 stage、commit、verify；这不证明真实模型的语义提炼质量。本任务没有执行付费模型调用。
