# 服务配置与搭建总入口

给第一次接触本系统的 AI：本仓库是方法、源码和搭建资料的交接包。先在自己的新目录验证，再逐步接入自己的账号和服务。完整生产系统还有[宿主集成任务](../system/source-map.md)，不能把参考源码直接当作安装完成。

## 先读这些

| 要解决的问题 | 应读文件 |
| --- | --- |
| 整套系统按什么顺序搭建 | [复建合同](11-rebuild-contract.md)、[安装核对表](../system/INSTALL-CHECKLIST.md) |
| 各服务怎么连，哪些东西不能混用 | 本页、[系统架构](../system/architecture.md)、[配置清单](../system/configuration.md) |
| 听悟怎么开通、提示词是什么、热词怎么建设 | [听悟与词典](13-tingwu-and-vocabulary.md)、[完整提示词](../prompts/tingwu-minutes.txt) |
| 随手放支持什么、MarkItDown 怎么接 | [附件转换](14-inbox-and-markitdown.md) |
| 目录为何这样分、怎么萃取并持续更新 | [结构与生长](15-vault-and-growth.md)、[数据模型](02-vault-model.md) |
| 手机怎么上传、人物与声纹如何从零配置 | [设备与状态初始化](16-device-and-state.md) |
| 哪些已经验证，哪些还没验证 | [当前状态](../system/STATE.md)、[发行验证](../release/verification.md) |

## 必需服务与可选服务

| 服务 | 什么时候需要 | 本包给出的入口 | 使用者还要提供什么 |
| --- | --- | --- | --- |
| Python 3.11+ | 本地演示、会议服务、辅助程序 | `tools/`、会议 requirements | 自己的运行目录与服务账号 |
| Node.js 24 | 本仓库 CI 使用的版本；整理模块与宿主运行 | `production-reference/curation/` | 自己的宿主、依赖与状态存储 |
| Obsidian + Self-hosted LiveSync | 电脑和手机共用同一 Vault | [同步手册](../system/runbooks/02-sync.md) | 各设备插件、同一同步参数与端到端加密口令 |
| CouchDB + HTTPS 代理 | 自建同步中继 | 固定镜像摘要的 Compose、Caddy 示例 | 自己的服务器、域名、TLS 与备份 |
| MCP 读写服务 | AI 实际读取和维护知识 | 只读基础镜像、工具合同、扩展业务模块 | 注册工具、身份与目录权限、writer lease、日志持久化 |
| AI 执行器与模型接口 | 语义萃取、查询、候选整理 | 四个 Skill、任务提示词、控制器参考 | 自己的模型、预算、账户和平台适配；无需使用作者的平台 |
| 通义听悟 | 会议转写与结构化纪要 | 接收器、分析提示词、结果校验、热词管理 | 自己的听悟 AppKey、RAM 凭证、额度和授权录音 |
| MarkItDown | PDF/Office 等附件转文字 | 转换入口、worker 与固定版本 requirements | 独立虚拟环境；生产后台隔离运行配置 |
| 确认中心 | 人工答疑和身份确认 | 现有 UI、本地后端、生产后端参考 | 自己的登录、TLS、答复身份与最终回执读取权限 |
| 声纹与人物页 | 需要跨场说话人建议时 | 四个声纹脚本及人物更新代码 | 自己的模型文件、依赖、私人音频、人工确认事件 |
| 定时器与备份 | 后台持续运行时 | 单一控制器、服务清单、运维手册 | 自己的任务 ID、开关、时间、备份与恢复目标 |

这不是“每种服务都必须付费”。纯文字本地验证可以先完成；不使用会议时不启用听悟，不使用声纹时保持声纹与人物任务关闭。手机真实同步需要分别安装和测试。

## 配置生成器：先产生自己的空状态

在仓库根目录运行，每个代码块是一条命令。以下 Windows 路径是使用者可替换的示例，必须位于仓库外：

```powershell
python tools/services.py init --private-dir 'D:\WikiPrivate\service-inputs'
```

它生成 `meeting/config.json`、新的手机上传令牌与签名密钥、空人物/声纹状态及锁文件。存在的目录会被拒绝覆盖；不会注册角色、启动后台任务或请求听悟。

用编辑器填写 `meeting/config.json` 的账户、地址和 meeting 角色令牌，然后检查：

```powershell
python tools/services.py doctor --private-dir 'D:\WikiPrivate\service-inputs'
```

初始出现 `BLOCKED_INPUTS` 是正确结果：占位账户尚不能启动。`INPUT_SHAPE_READY` 仅证明输入形状通过检查，尚未证明云账号权限、域名、宿主和手机可用。Windows 须另限制该私有目录 ACL，不能把 Python 的 chmod 当作 Windows 权限验收。

离线查看真实请求参数，或导出与程序一致的提示词：

```powershell
python tools/services.py parameters
```

```powershell
python tools/services.py export-profile --output 'D:\WikiPrivate\tingwu-profile'
```

## 四种连接分别配置

1. **文件同步中继**：LiveSync ↔ CouchDB，传的是笔记和附件。
2. **知识工具服务**：AI ↔ MCP，提供读取、投稿、整理、验证等有权限的工具。
3. **模型服务或模型代理**：执行器 ↔ 模型接口，处理 AI 推理与计费；不是文件数据库。
4. **手机上传或远程电脑入口**：手机 ↔ 会议接收器，或浏览器 ↔ 自己的远程电脑服务；不是 LiveSync 的替代品。

每条连接都有自己的认证。不要共用 CouchDB 管理员密码、手机 token、听悟 AccessKey、MCP writer token 和网页登录密码。

## 配置确实由谁读取

| 配置 | 读取程序 | 生效验证 |
| --- | --- | --- |
| 会议 JSON | `receiver.py --config`，`Capture.config()` | doctor 后再启动，测试 `/health`，最后走真实短录音 |
| 听悟 Prompt 与参数 | `meeting_analysis.py:MINUTES_PROMPT/analysis_parameters` | 本包导出的 TXT/JSON 与 Python 函数逐字/逐字段比对 |
| 热词与词典缓存 | `kb_dictionary_cache.py`、`Tingwu.confirmed_parameters()` | 读取词典哈希，云端 List/Get 回读后才使用 PhraseId |
| 附件格式与资源上限 | `format-worker.py:SUPPORTED/classify/run` | 逐类样例、哈希、转换回执、失败重试记录 |
| 人物空状态 | `kb_person_attendance.py`、`kb_person_profile.py` | 身份和领域未确认时不启用；种子须经证据审阅 |
| 声纹示例 JSON | 目前是部署契约，四个脚本没有统一加载器 | 必须按脚本参数、模型路径适配，不能声称放入 JSON 即生效 |
| Agent平台环境变量与控制器 JSON | 控制器及平台适配层 | 逐项验证自身平台读取位置、任务 ID、开关和实际运行结果 |

生产参考中仍有固定 Linux 路径和平台内部依赖。把私有配置生成在任意目录，不会自动让所有旧脚本改读该目录；部署时须按[目录映射](../system/configuration.md)安装或完成参数化，并逐个读回。不要把仓库外配置复制进 Git。
