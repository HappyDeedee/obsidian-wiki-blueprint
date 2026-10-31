
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
