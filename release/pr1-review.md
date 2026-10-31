# 0.2.2：PR #1 修正与验收范围

本次补齐别名/词典/维护入口说明，新增可配置的别名维护 CLI 和 Linux service/timer 示例，并修正本地事务参考宿主。不是全栈一键安装发行。

- 全量与增量别名刷新分别使用 `tools/maintenance.py` 的两个子命令；默认只校验，显式 `--publish` 才发布。配置和状态留在使用者的私有目录，实际 MCP 宿主仍须独立集成。
- `CHECKPOINT`、`BUSY` 退出码为 2；完整发布和回读后才返回 `PASS`。定时器成功退出不代替业务回执。无候选、不完整索引或无效 YAML 时保留旧视图。
- 修正文档误写的职责：生产参考 `aliases-refresh.py` 是增量包装器；`aliases-incremental.py` 没有 CLI；`wiki-generated-refresh.py` 不会生成知识索引和总览。后二者的统一生成任务仍未提供。
- query/ingest/health 公共 Skill 升至 0.2.2。词典统一为先读取再区分缺失、已初始化空白、已有条目和读取失败；公开 Skill 不覆盖作者的私人 Skill、Bot 或任务。
- 本地参考宿主增加原子争用租约、来源快照/规则绑定、完整阅读及显式 stage、逐路径引文核对、正式目标限制、重复来源登记保护、持久 journal 同参数恢复。无法完成时不提前把来源标 done。
- 宿主仅支持单机可信操作者测试副本。没有认证、MCP transport、正式 lint 版本覆盖、索引/总览收尾、多端并发或断电恢复保证；恢复需原 token、原参数和未过期租约。不得宣称全部生产合同已经实现。

验收命令：

```text
python -m pip install -r system/maintenance/requirements.txt
python -m unittest discover -s tests -v
python -m unittest discover -s system/production-reference/meeting -p test_receiver.py -v
node --test system/production-reference/curation/test-knowledge-temporal.mjs tests/roles.test.mjs
python tools/check_public.py
```

新增回归覆盖：并发争用只授予一个租约、拒绝来源/规则改写、拒绝跨来源引文、同版登记不重复入队、两页提交中途失败后重开数据库恢复、别名发布备份及重复运行、不完整索引不写入、检查点不报发布完成。网络服务在测试中使用明确的内存夹具，未做真实 MCP 别名发布或收费云调用。真实设备同步、真实模型效果和全新 VM 部署不在本 PR 验收范围内。

本地 Windows 验收：86 项 Python 测试中 84 通过、2 项因创建符号链接权限不足而跳过；12 项会议测试通过；Node 测试 4 项通过（含时间规则的 40 项断言）；公开白名单、链接与 manifest 检查通过。以最终提交对应的 Ubuntu/Windows Actions 结果补充验证。

私人同步只读检查未完成：既有检查脚本依赖的运行文件不可读取，不能报告“全部同步通过”。本次没有修改私人生产系统。
