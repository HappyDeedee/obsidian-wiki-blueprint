# 随手放与 MarkItDown 接入

“随手放”负责减少手动分类。它并不意味着每种文件都能自动完整理解。原件、文字提取、人工确认和正式知识入库分别记状态。

## 放哪里，谁处理

| 输入 | 放置位置 | 当前程序处理方式 |
| --- | --- | --- |
| Markdown/日记 | `00-Inbox/随手放/` 或 `00-Inbox/日记/` | 直接进入整理读取；日记还可能等待稳定版本 |
| PDF、PPTX、DOCX、XLSX、XLS、HTML/HTM、TXT、CSV、JSON、XML、EPUB、IPYNB | `00-Inbox/随手放/` | 附件 worker 提取文字，派生到 `00-Inbox/已提取正文/` |
| PNG/JPG/JPEG/WEBP/GIF/BMP/TIF/TIFF | Inbox | 登记 `requires_ocr_or_vision`，本 worker 不会因 MarkItDown 有相关扩展就宣称已识图 |
| MP3/WAV/M4A/AAC/CAF/OGG/FLAC | 普通 Inbox | 仅登记音频；要转写须走会议入口 |
| 需要转写的录音 | `00-Inbox/会议/待处理/` | 音频监听器 → 听悟 → 完整转写和纪要；通用转换服务避让 |
| MP4/MOV/MKV/WEBM | 普通 Inbox | 待视频/字幕处理；会议上传接受格式另由接收器判断 |
| ZIP、EXE、未知格式 | Inbox | 不在白名单则登记 unsupported，不运行或递归解压 |

`.doc`、`.ppt`、`.pages`、`.numbers` 当前不在 worker 白名单。扫描 PDF 虽可能读出少量文字，仍不能代表页面图片、表格和图表已完整核验。对缺失内容保留待 OCR/识图任务。

## 实际调用链

```text
LiveSync 同步附件完整
 → attachment-bridge.mjs 列表与二进制读取
 → 校验源字节 SHA-256、大小和稳定时间
 → format-worker.py 单 worker 调度
 → 隔离进程执行 format-convert.py
 → MarkItDown(enable_plugins=False).convert_local(本地临时文件)
 → 非空文字、预算和状态校验
 → 派生 Markdown + 原附件路径/哈希
 → 已有整理队列萃取，而不是转换器直接写 Wiki
```

代码：[worker](../system/production-reference/format/format-worker.py)、[转换入口](../system/production-reference/format/format-convert.py)、[附件桥](../system/production-reference/curation/attachment-bridge.mjs)。转换入口本次已随包提供，不能只装 worker 漏掉它。

## 独立安装转换环境

本包固定 `markitdown[pdf,docx,pptx,xlsx,xls]==0.1.6`。这是本包选定并测试的版本，不声称是最新版本。依赖文件：[requirements.txt](../system/production-reference/format/requirements.txt)。

Windows 手动验证可用独立虚拟环境，每条分别运行：

```powershell
python -m venv 'D:\WikiPrivate\format-venv'
```

```powershell
& 'D:\WikiPrivate\format-venv\Scripts\python.exe' -m pip install -r 'system/production-reference/format/requirements.txt'
```

```powershell
& 'D:\WikiPrivate\format-venv\Scripts\python.exe' 'system/production-reference/format/format-convert.py' 'D:\WikiPrivate\fixtures\sample.docx' 'D:\WikiPrivate\fixtures\sample.md'
```

准备的是自己的测试文件，输出路径必须是新文件。脚本会拒绝覆盖已有输出，原件不会被修改。该命令只演示单文件提取，没有建立后台监听。

生产 worker 使用 Linux/systemd：虚拟环境位于 `/opt/personal-kb-format/.venv`，转换程序位于 `/opt/personal-kb-format/format-convert.py`；worker 和 state 安装在 `/srv/wiki/stack/format/`。须创建 `/var/lib/personal-kb-format/tmp` 及恰当目录权限。容器内附件桥安装到 `/app/curation/attachment-bridge.mjs`。

worker 的 systemd-run 使用 nobody、PrivateNetwork、ProtectSystem、NoNewPrivileges、320 MB 内存、CPU 配额、110 秒运行时间和受限输出目录。部署者须验证 sudo 规则和服务账号，不能为方便授予不受限的 systemd-run/docker root 权限；更换部署方式时保留相等隔离边界。Windows 手动转换没有这些生产隔离能力，不应用来运行不受信任文件。

## 稳定、去重和失败

当前 worker 规则写在代码中：文件稳定至少 60 秒，25 MB 以内，每轮最多 3 个转换；输出字符预算 100 万，进程输出文件还有系统限额。转换失败延后 30 分钟，达到 3 次后 needs_review。调整这些是部署策略变更，应更新代码、文档与测试，不能只修改无人读取的 JSON。

同一内容哈希可复用已有提取正文，但必须确认旧产物仍存在。源版本变化重新校验；写回带 `source_attachment/source_sha256/converter/conversion_status`。状态页是 `90-Logs/附件转换/当前状态.md`，由程序生成，不作为独立事实重新入库。

`format/inbox-status.py` 根据当前 source_id、版本与可信回执生成未整理视图；`temporal-inspect.mjs` 为结构巡检提供时间字段检查。本次一并补入，仍依赖自己的扩展 MCP，不能连只读基础服务冒充完成。

## 接到自己的宿主时必须验证

- 二进制桥只能读取授权 Inbox/测试目录，不能任意读取服务器文件；分块/附件完成状态与大小、哈希都要核对。
- 输出只能创建约定派生路径和状态页，不能覆盖原件或修改系统规则。
- MarkItDown 默认关闭外部插件，不配置 LLM、Document Intelligence 或其他外发服务；需要这些功能时单独配置和告知费用/数据范围。
- 未知格式、空输出、超时、损坏 Office、扫描件、重复内容、同名不同内容、未同步完成分别测试。
- 提取后的资料仍须结合原附件读取与语义审阅，再通过正常 prepare/commit/final-receipt 链。`TEXT_EXTRACTED` 不是“已入库”。

Microsoft 官方接入与隔离建议：[MarkItDown 仓库](https://github.com/microsoft/markitdown)。本包采用窄化的本地文件入口；不开放接受任意远程 URL 的公共转换 API。
