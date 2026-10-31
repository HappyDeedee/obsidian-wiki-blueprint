# 备份、升级、巡检与恢复

## 部署记录留在使用者自己的环境

建立私有部署清单：操作系统、运行用户、镜像 digest、源码 commit、依赖锁、模型 hash、目录、域名、证书、任务 ID、权限登记、启用时间和验收结果。公开文件只放字段结构和虚构示例，不提交真实 `.env`、设备连接串、快捷配置二维码或备份。

每次规则/流程变更，检查：知识库规则/入口、本机 Skills、已部署 Skills、平台共享 Skills、Bot 长期指令、例行任务、看板、真实工具权限、控制器发布清单。逐项记“已同步/无需修改/未通过”和实际回读证据；版本相同或任务结束不代表已经生效。

## 生产备份是一组一致的数据

先暂停新的模型写入和接收任务，等已有提交到安全断点；记录当前队列/租约/未完成任务，再冻结相关写入。至少备份：

1. CouchDB 原始文档、附件、必要 `_local`/同步元数据、数据库安全设置。Markdown 导出不等于完整同步库备份；加密后的备份仍需原 E2EE 口令才能恢复。
2. MCP 队列、事务、journal、版本、问题登记与索引状态。
3. 会议 SQLite、原件、原始结果、导入/计费任务 ID 和回执。
4. 确认卡、事件、ack、词典、延迟任务和入库反馈。
5. 声纹模型版本、人工声音库、向量、自动候选及人物授权/撤销状态。
6. 控制器/调度状态、私有配置、证书和密钥。按隐私资料加密保管。

SQLite 要在停止写入后复制或使用一致性 backup API。Docker 卷不能在数据库活动写入中简单打包后就称可恢复。CouchDB 备份可参考 `maintenance/vault-client.py` 的原始导出逻辑，但须在自己的 CouchDB/LiveSync 版本做完整恢复演练。

## 恢复顺序

恢复到**新目录/新卷/隔离域名**，先不用真实手机连接。核文件 hash 与配置，依次启动数据库 → 同步/只读查询 → 状态/事务检查 → 会议结果/确认回执 → 声纹归属 → 单写入控制器。未完成事务沿原 ID 恢复；已有音频任务沿原 job 恢复，避免重付转写费用。

验证原文和附件字节、E2EE 解密、人物撤销状态、问题版本、投稿/回执区别以及队列唯一计数。确认后再切换设备；不要让新旧服务同时整理同一个库。

## 升级与回滚

保存旧版本/配置，固定新版 digest/commit，先演示环境再自己的小范围真实样本。配置修改前回读并检测并发变化，升级后从运行位置回读。保持使用者模型、任务开关与负责人，除非本次明确更改。

发生失败时保留原件和断点。不能清空 `_local`、删队列、重建同步库、换身份重投来“消除报错”。回滚程序不等于回滚数据；已提交的知识通过可追溯更正处理，不直接删历史。

## 程序化维护任务

先区分“可运行的公开入口”和“需要适配的生产参考”。下列维护入口依赖使用者已经集成的 MCP 宿主；本 PR 没有安装该宿主，也没有实现全栈一键部署。

| 任务 | 入口 | 建议频率 | 实际职责及限制 |
|---|---|---|---|
| 别名表全量刷新 | `tools/maintenance.py ... aliases-full --publish` | 首次启用或主题页批量变更后 | 重读全部别名候选，校验完整性、取得 writer、备份和回读；没有候选时保留旧视图 |
| 别名表增量刷新 | `tools/maintenance.py ... aliases-incremental --publish` | 每日或检查点续跑 | 每批默认 40 项，`CHECKPOINT` 只保存读取进度，不发布；最终 `PASS` 才算发布成功 |
| 待处理队列与状态刷新 | `system/production-reference/maintenance/wiki-generated-refresh.py` | 完成私有集成后 | 参考程序只在配置启用 G 时运行 pending queue，并尝试 inbox status；不生成知识索引或总览 |
| 知识索引、总览 | 本 PR 未提供统一生成入口 | 未启用 | 模板存在不等于生成任务已部署；查询须说明实际覆盖，不能编造任务成功 |
| 搜索目录对账 | `system/production-reference/curation/index-catalog-reconcile.mjs` | 集成实际宿主后 | 需要该宿主的索引适配；不是独立命令，不写库内笔记 |
| 每日结构巡检 | health Skill 的结构部分 | 每日 | 需要实际 Agent/程序调度；结构 PASS 不证明语义通过 |
| 每周内容/时效巡检 | health Skill 的内容和到期复核部分 | 每周 | 需要模型及来源核验；见 [巡检运维](../../docs/07-operations.md) |

### 先手动验收别名维护，再开定时任务

1. 在仓库根目录安装 `python -m pip install -r system/maintenance/requirements.txt`。复制 `system/config/maintenance.example.json` 到仓库外的私有配置文件，填自己的 `mcp_url`、维护身份 `mcp_token`、仓库外绝对路径 `state_dir`；Linux 配置权限设为 600。环境变量 `WIKI_MCP_URL`、`WIKI_MCP_TOKEN`、`WIKI_STATE_DIR` 可以覆盖对应字段。不要把配置、缓存或回执提交 Git。
2. 宿主必须实际支持 `search_notes`（含完整 SearchPage 标记）、整页 `read_note`、`write_note` 和 `curation_writer_acquire/release`，返回格式须与 production-reference 的 `vault-client.py` 和 `wiki-generated-views.py` 兼容。先在自己的测试库确认权限和格式；最小 Python 参考宿主没有这些 MCP 读取工具，不能直接填进此配置。
3. 执行以下命令；`PRIVATE_CONFIG` 要换成自己的仓库外配置路径。首次不要带 `--publish`，检查 READY 后再发布：

```text
python tools/maintenance.py --config PRIVATE_CONFIG aliases-full
python tools/maintenance.py --config PRIVATE_CONFIG aliases-full --publish
python tools/maintenance.py --config PRIVATE_CONFIG aliases-incremental --publish
```

4. 发布后读回 `20-Wiki/别名表.md`，确认别名链接对应主题页，原页和来源未修改；重复运行应 `changed:false`。`READY` 是未发布的校验结果；`CHECKPOINT`、`BUSY` 的退出码为 2，不算发布成功；`FAIL` 为 1。增量检查点可在下一轮继续，但高频修改或候选超过索引上限可能一直不能收尾；此时保持旧视图、停任务检查，不能把重试次数算成功。
5. 手动 PASS 后再选择一种调度源。同一库只选一个，避免重复执行。Linux 示例见 `system/deploy/wiki-aliases.service.example` 和 `.timer.example`：将文件安装为 `wiki-aliases.service/timer`；预先创建专用用户 `wiki-maintenance`，授予代码只读及私有配置读取、state_dir 写入权限；在 `/etc/wiki/maintenance.env` 填 `WIKI_CHECKOUT`（绝对仓库路径）和 `WIKI_CONFIG`（私有配置路径）。确认 `/usr/bin/python3` 已安装上述依赖后执行 `sudo systemctl daemon-reload`、`sudo systemctl start wiki-aliases.service`，查日志确认实际 JSON，再执行 `sudo systemctl enable --now wiki-aliases.timer`。`systemctl is-active` 不代替发布回执；示例不是自动安装器。

Windows/macOS 仅在本地 L0 演示中运行 CLI；本节的 Linux 定时器不适用于它们。各任务需登记执行者、权限、费用上限、暂停条件和实际回读方式；本别名程序不调用模型。其他 Agent 任务见 [调度和控制器](07-growth.md)。

失败不静默跳过。索引不完整、源变化或 YAML 错误时不发布，保留旧视图；写入失败需核对回读与 `state_dir/generated-view-backups`，不能无条件覆盖恢复。进程被终止后可能留下 `state_dir/aliases.lock`，确认没有运行进程后再手动移除。原件、队列和同步数据不得清空；自动恢复默认关闭。

## 能报告到哪一级

本地测试通过、服务器服务健康、容器集成通过、真实模型语义通过、电脑/真手机同步通过分别记录。没有拿到手机端证据，就写“手机待验收”。当前仓库真实测试和已知未实现项统一见 `release/verification.md`，不能从文档中推断它们已经实现。
