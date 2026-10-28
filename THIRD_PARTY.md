# 工具来源与许可

核对日期：2026-10-28。下列是可选/参考上游，未把它们源码或依赖打包在本仓库。安装前从官方 README 核当前平台、版本、参数与许可，再固定实际测试的 commit；不使用浮动最新版作为已验收证据。

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
