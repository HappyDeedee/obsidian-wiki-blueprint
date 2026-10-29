# 工具来源与许可

核对日期：2026-10-29。下列是上游软件/方法。公开包增加自定义业务源码模板，但不附第三方 node_modules、模型或插件二进制。安装前从官方 README 核当前平台、版本、参数与许可，再固定实际测试的 commit；不使用浮动最新版作为已验收证据。

| 组件 | 作用 | 必需程度 |
|---|---|---|
| Python 3.11+（python.org） | 本仓库参考程序 | 本地示例必需，无 pip 依赖 |
| Obsidian（obsidian.md） | Markdown 查看/编辑 | 方法可纯文件使用；Obsidian体验需要 |
| 自己的 Agent | 阅读、语义萃取、查询回答与内容巡检 | 真正 AI 工作流必需；demo 无需 |
| `SamurAIGPT/llm-wiki-agent` | 来源→实体/概念→关联 Wiki 的方法参考 | 方法来源，不是运行依赖 |
| `es617/obsidian-sync-mcp` | 基础文件/CouchDB MCP 接入 | 远程接入选项；不自带本蓝图全部扩展 |
| `vrtmrz/obsidian-livesync` / CouchDB | Obsidian 多设备服务端同步 | 服务器/手机同步选项 |
| `microsoft/markitdown` | 可读文档正文转换 | 附件可选；不等于 OCR/全格式完整支持 |
| 自己的转写/OCR/视觉服务 | 非文本原件可读化 | 按资料种类选择，费用与隐私另行配置 |
| Rakazo 或其他调度平台 | 常驻 Agent、共享 Skill/任务 | 自动化选项，不是特定品牌硬依赖 |

方法曾参考 llm-wiki-agent 的固定基线 `a17ab5df66da9b74a0eaeb664a67d662d0fb166d`，公开规则为独立改写；不宣称完整继承上游图谱功能。本仓库没有复制其实现代码。第三方软件遵守各自 LICENSE 与服务条款，MIT 只涵盖本仓库原创内容。

公开项目定位方式：在 GitHub 按上述 owner/repo 查找官方仓库；避免同名镜像和未经核实的一键安装脚本。后续部署记录应写实际 commit、依赖锁文件、发布日期和测试结果。

## 运行源码与模型

`system/production-reference/` 是从实际运行应用中摘取并脱敏的自定义业务实现，包含接口适配；它依赖上游宿主 API，不是整个平台源码发行包。公开修改包括路径/身份/连接脱敏、自动恢复重新授权和测试夹具补充。原业务实现的历史假设见源码地图。

基础 MCP 上游 `es617/obsidian-sync-mcp`：本次核对源码基线 `4759a8f123ac0dfafa88229821060fd7df566abc`，包版本 `0.9.0`；MIT，Copyright (c) 2026 Enrico Santagati and contributors。部署时保留其 LICENSE，并固定自己构建的 commit/digest。本仓库 Compose 使用实际观察到的不可变镜像 digest；不等同保证镜像与该源码基线逐字一致。

Self-hosted LiveSync 按其 MIT 许可使用；Apache CouchDB 按 Apache-2.0 使用；Caddy 按 Apache-2.0 使用。安装者从各项目官方发布核许可和版本，保留必要通知。Obsidian 本体遵守其官方许可，不随本仓库重新分发。

声纹依赖 numpy、PyAV、sherpa-onnx，会议依赖阿里云 SDK/听悟服务，格式转换可用 MarkItDown。模型权重与样本单独获取并核许可；软件许可不代表获得处理他人声音的授权。版本锁和模型 SHA-256 由搭建者写进自己的私有部署清单。
