# 公开 MCP 宿主（开发中）

这不是完整的一键部署发布。当前目录补齐了可运行宿主；不能因为宿主测试通过，就把确认中心、调度、手机同步或安装器算作通过。

## 来源与接口

- `tools.production.json`：从已运行生产宿主的 `tools/list` 只读取得的 23 个完整 schema，其中 12 个 `curation_*`。不是按名字猜出的接口。
- `upstream.json`：上游 v0.9.0 的固定源码提交、MIT 许可、镜像 digest、上游锁文件哈希。
- `Dockerfile`：在固定镜像上增加此目录和原有公开 pipeline；没有复制作者的运行容器、私人配置或数据。
- `adapter.mjs`：唯一识别上游 bundle 的兼容层。附件读取有大小、版本和哈希校验；调用方不再猜 Vault 文件名。
- `runtime.mjs`：正式注册生产 curation schema、会议导入和角色策略。`read_attachment` / `read_image` 是**公开新增扩展**，不是生产 schema 中原有的工具。
- `meeting.mjs`：复用公开的生产会议渲染函数，真实写入音频/纪要/转写并回读，保留原始录音。听悟返回值本身不代表知识整理完成。

角色配置沿用 `read-only` / `proposal` / `curator` / `meeting`，它们分别对应读者、投稿者、整理者、会议服务。每次调用重新检查身份。整理者也不能直接覆盖原始资料，只能通过有日志的 pipeline；投稿仅允许指定 Inbox 目录下 create-only。

## 配置与启动

1. 在仓库外保存自己的私有 JSON，结构参考 `../config/host.example.json`。
2. 配置自己新建的 CouchDB、E2E 密码和四角色哈希注册表。**不要连接作者的数据库，也不要把私有 JSON 提交 Git。**
3. Linux 上构建：`docker build -f host/Dockerfile -t wiki-host:local .`。
4. 挂载私有目录到模板指定的位置，设置 `WIKI_CONFIG` 为容器内私有 JSON 的绝对路径；以同一个服务用户运行。通过 `node /wiki/host/bootstrap.mjs` 只初始化缺失模板页，再启动默认入口。
5. 使用 Linux host 网络。宿主仅监听 loopback；外部必须经过自己配置的 HTTPS 反向代理。禁止直接开放 CouchDB 或宿主端口。

路径可由 `WIKI_PATH_STATE`、`WIKI_PATH_REGISTRY`、`WIKI_PATH_MEETING_DATA`、`WIKI_PATH_UPSTREAM_DIST` 覆盖。CouchDB 字段可由对应 `WIKI_COUCHDB_*` 覆盖。凭证不要放命令行。

目前仍要求单个宿主进程拥有一个 journal 目录；同进程调用已串行。生产部署的跨进程监督锁和自动重启验收尚未完成，不要并排启动两个宿主。

## 验证及明确限制

```text
npm ci --prefix host --ignore-scripts
npm test --prefix host
python tools/ci_host.py
```

最后一条仅在**有 Docker 的 Linux 测试机**运行。它创建随机新数据库、真实启动固定上游 Vault、执行 HTTP 鉴权、pipeline 入库/回执/重跑、加密图片读回、会议导入、runtime 重建，然后清理自己的测试卷。会议转写是明确标注的虚构回放，没有真实云调用。

当前搜索是有界字面搜索，不宣称与生产 BM25 等效。历史队列为空，新来源通过 `curation_pending` 发现。长来源按实际 pipeline 先 stage，读完才 commit；短来源直接 commit。当前进程重建测试不等于服务器重启、备份恢复或完整 systemd 安装测试。完整发布门禁仍以顶层验收文件为准。
