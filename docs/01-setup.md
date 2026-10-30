# 从空目录搭建

## 1. 先选择规模

**A：本机试用**：Python 3.11+、Obsidian（查看 Markdown）、能读文件的 Agent。本地 Wiki/UI 参考工具不需要第三方 Python 包；附件转换和完整测试还需下列固定版本 MarkItDown。先在全新目录跑通虚构示例。关闭电脑后任务不会继续运行。

**B：服务器长期运行**：A 的方法，加自己的 Markdown 存储或 LiveSync/CouchDB、MCP 服务、受限身份、持久任务控制器、备份和确认中心。服务器可以独立工作，但手机/Web 能否使用仍须实测。

Git 用于保存方法和代码；**公开 Git 仓库不是私人知识库同步盘**。不要把真实 vault 或 `.obsidian` 同步配置提交在这里。

## 2. 下载并验证方法包

在 GitHub 找到 `HappyDeedee/obsidian-wiki-blueprint`，克隆或下载。检查 README 版本与发布清单，阅读 AGENTS。

在仓库根目录逐条执行：

```powershell
python --version
python -m pip install "markitdown[pdf,docx,pptx,xlsx,xls]==0.1.6"
python -m unittest discover -s tests -v
python tools/check_public.py
python tools/wiki.py init --vault "../my-wiki-demo"
python tools/demo.py --vault "../my-wiki-demo"
```

`init` 只接受空目录；发生失败先看具体错误，不反复重建。成功后在 Obsidian 选择“打开已有文件夹作为库”，选择刚创建的 `my-wiki-demo`。不需要安装社区插件才能查看本地参考结果。

## 3. 装入四个 Skill

让 Agent 直接读取完整克隆中的四个 `skills/*/SKILL.md`。它们依赖仓库的 `docs/`、`system/` 与 `tools/`；保留整个目录才是完整安装方式。不要只复制 Skill 文件夹而丢掉它引用的搭建文档、配置模板和程序。

已有同名技能先另存备份并对比；不要覆盖私人库的现行生产技能。目标 Agent 能读 SKILL.md 不代表已经能读 Obsidian。

## 4. 配置读写能力

先用本地文件访问验证；需要远程时才部署 MCP。可选的 `es617/obsidian-sync-mcp` 提供基础 Obsidian 接入，有本地文件与 CouchDB 两种模式。构建、环境变量及启动命令必须以所选版本的上游 README 为准，见 THIRD_PARTY。

1. 获取上游源码，固定经测试的 commit，记录依赖锁文件与许可。
2. 本地模式指向**演示库**；CouchDB 模式先建立独立测试数据库，不把 Markdown 直接当 LiveSync 底层数据库记录写入。
3. 发现实际 `tools/list`；逐项映射 `read_note/list_notes/search_notes/get_note_metadata`，核参数与分页单位。
4. 实现/接入 docs/04 的 curation 扩展与角色网关；不能把基础读写服务当作具有事务和最小权限。
5. 独立配置 reader、proposer、curator 身份。确认中心使用用户自己的登录与受限提交接口，密钥不下发给网页。
6. 把 endpoint、令牌、同步口令放入自己的秘密管理或不入库的配置文件。`config/roles.example.json` 只是权限合同，不是任何产品可直接读取的配置。

仓库没有作者连接，也不需要申请作者环境权限。只读身份缺能力就报告缺项，不能借用整理管理员凭证绕过。

## 5. 启用同步与长期任务

如果需要 LiveSync：按 `vrtmrz/obsidian-livesync` 当前说明安装插件、配置自己的 CouchDB 与加密，先在临时库做双向测试。保存加密口令与恢复材料；服务端能够读取不等于手机已同步。不要同时给同一目录启用两个互不协调的同步系统。

按 docs/07 配置**一个**调度器。可使用 Rakazo、其他支持工具的 Agent 调度平台或自己的服务；Skill 不依赖其中某个品牌。平台是否自动加载 AGENTS、是否提供共享 Skill、定时任务和任务看板，必须实际确认。

## 6. 真实使用前验收

先按 docs/08 测试，确认：原件保留、改版不丢历史、同版不重复、旧页变更会阻止提交、确认答复能跟踪到最终知识页、查询返回可跳转依据、巡检报告覆盖真实。再由用户授权少量真实资料。

### 遇到这些情况先不要扩大范围

- `curation_*` 不存在：还缺生产适配器，不是假装调用即可。
- 仅有文件读写：可人工小批处理，但不能宣称持续整理/持久事务/全量验收。
- 程序 PASS 但答案仍答错：回到原文和知识页做语义核对。
- 同步测试失败：保留两端文件和冲突副本，不直接覆盖或清数据库。
- 模型/网络错误：保存断点、退避；不跳过长资料、不换新来源编号重复计数。
