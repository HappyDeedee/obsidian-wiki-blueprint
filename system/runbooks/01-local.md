# 本地启动：先复用现有确认中心

需要 Python 3.11+。Windows 用 PowerShell；macOS/Linux 若命令为 `python3`，替换命令开头即可。先克隆完整仓库，不只复制单个 Skill。

在仓库根目录逐条执行：

```powershell
python tools/system.py init --home "../my-wiki-local" --demo
python tools/system.py doctor --home "../my-wiki-local"
python tools/system.py serve --home "../my-wiki-local"
```

打开终端显示的本机地址。访问密码在新安装目录的 `private/access.json`，用文本编辑器打开，复制 `password` 值到登录框。密码随机生成，不是仓库里的默认密码；不要发到公开 issue。

启动后看到原确认中心的界面布局、知识确认、发言人、已处理、词典和播放器样式。示例没有录音/向量，发言人卡仅演示交互，不会调用声纹模型或造正式人物页。

本地服务只绑定回环地址，不提供远程手机访问。请勿把它通过公网端口转发暴露。手机使用完整版须按后续手册部署自己的生产认证和 HTTPS。

## 验证答复与回执

1. 打开知识确认卡，输入一句自己的虚构答复。
2. 检查新安装目录 `vault/00-Inbox/待确认答复/`。页面应显示“已收到，待整理核证”，不能显示已入库。
3. 整理 AI 使用 `tools/wiki.py` 的 `prepare` → 读取原文与旧页 → `commit` → `resolve`，更新这条答复明确指向的页面。参数见 `python tools/wiki.py --help` 与 [本地演示源码](../../tools/demo.py)。
4. 只有正确答复 ID、问题版本、相同目标版本和最终回执核验通过，网页才显示已入库。
5. “拿不准”“没意义”只保留状态，不编造事实。参考版答复立即投稿；没有生产版十分钟延迟窗口。投稿后撤回不会删除历史，需要另建可追溯更正提案。

自动化小闭环和故障测试：

```powershell
python -m unittest discover -s tests -v
node --test system/production-reference/curation/test-knowledge-temporal.mjs
```

## 备份与恢复

先在服务终端按 Ctrl+C，停止所有参考写入者。再逐条执行：

```powershell
python tools/system.py backup --home "../my-wiki-local" --destination "../my-wiki-backup"
python tools/system.py restore --source "../my-wiki-backup" --home "../my-wiki-restored"
python tools/system.py doctor --home "../my-wiki-restored"
```

备份也包含访问密码与私人答复，按私有资料保管。恢复只允许新目录，不覆盖已有库。这个备份命令只适用于本地参考目录，不能用它代替 CouchDB 和生产服务的备份。
