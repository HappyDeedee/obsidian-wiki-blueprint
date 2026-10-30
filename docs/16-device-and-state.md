# 手机入口、声纹与空状态

## 手机与电脑同步

先完成 [LiveSync 手册](../system/runbooks/02-sync.md)。两台设备使用自己的同一数据库、加密口令和一致选项；模型 API 和上传 token 不能替代数据库认证。每台设备分别做新建→读回、修改→读回、附件哈希与离线恢复测试。电脑浏览器模拟窄屏不等于真实手机同步通过。

## 两种录音入口

**推荐先验证文件同步入口**：手机原生录音保存副本，将文件共享到自己 Vault 的 `00-Inbox/会议/待处理/`；等附件完整同步后，由唯一音频监听器投递会议接收器。不把普通随手放里的音频默认全部发送付费识别，不自动回扫历史。

**直接上传入口**：在自己的快捷指令/上传客户端中实现以下动作。仓库不包含作者已签名的安装包，不承诺点击历史 `/install/*.shortcut` 链接就能安装。

1. 接收共享文件或录音输出，先保留手机原件；取得真实音频文件和扩展名，不把文本或文件 URL 当音频字节。
2. 请求自己的 `POST <public_base_url>/upload`，请求体是原始音频字节，不能改成 multipart 字段。
3. 设置 `Authorization: Bearer <自己的 phone_token>` 与 `X-Audio-Extension`。接收器要求明确 Content-Length；若手机客户端只提供 chunked 上传，先用受控上传适配器缓冲并计算长度再转发，不假设原生动作一定兼容。
4. 仅从 LiveSync 来源投递的监听器才设置 `X-Obsidian-Source`、`X-Obsidian-Revision`；不要把手机绝对文件路径塞进去。
5. 读取 202/200 JSON 中任务身份和 duplicate；查询自己的 `/jobs/<64位内容哈希>`。上传成功后也保留原录音，不以“已收到”代替转写完成。
6. 录音、转写、纪要都能打开且导入回执存在后，再验收正式知识整理。

上传文件仍须符合 `Capture.accept()` 白名单和上限。听悟、声纹及格式转换各自支持的格式不同；上传接受不代表所有后续步骤都支持。真实 iOS/Android 客户端需逐设备验收；制作签名快捷指令时用搭建者自己的账号与配置，不嵌入作者凭证。

## 初始私有状态

`tools/services.py init` 提供：

| 文件 | 初始值 | 含义 |
| --- | --- | --- |
| `confirm/people-v2-seeds.json` | `{}` | 尚无经过审阅的人物画像草稿 |
| `confirm/people-domains.json` | `{}` | 尚无主人确认的领域授权 |
| `confirm/people_merge_state.json` | profiles/identities 为空 | 尚无已合并身份与人物版本 |
| `confirm/people.lock`、`.lock` | 空锁文件 | 避免新环境因文件不存在而直接失败 |
| `voiceprint/voices.json` | `{}` | 尚无私人声纹与身份 |
| `meeting/data/` | 空目录 | 后续保存音频、SQLite、原始结果、词典缓存 |
| `confirm/access.key` | 每次新生成 | 仅旧生产参考认证所需，不是本地 UI 密码 |

空文件不等于流程可运行。生产 `kb_person_attendance.py` 在领域表为空时会主动停止，目的是防止错误删除/重写人物页。保持人物任务关闭，完成真实身份和领域确认后再启用。生成目录若不是程序固定读取位置，部署时须按目录映射安装或修改加载器；不能只把示例 JSON 放在旁边。

## 人物画像如何从零建立

1. 本场转写产生 speaker 编号。声纹仅提出跨场候选，不能自行命名。
2. 主人在确认中心确认姓名，记录卡片、时间、声音及本场 speaker 对应。
3. 单独确认该人物可写入的知识领域；姓名确认不等于部门/岗位/上下级确认。`people-domains.json` 以卡片为键，包含 `voice/confirmed_by/domain/source/confirmed_name`；`confirmed_by` 必须为 owner。
4. 读取已确认身份支持的本人发言，按 meeting ID 与 `sentence-编号` 锚点生成经过审阅的种子。种子中的 one、relationship、style、stance、network、concerns、facts、promises 均须绑定 `{text, meeting, anchor, inferred}`；project_links 只关联已有项目与真实来源交集。
5. seeds 是维护草稿，不是主人确认。缺少对应原话的结论不显示；新会议先进入证据时间线，未经审阅不自动升为上层画像。
6. 撤销身份、改名或撤回领域时，对照身份依赖重新编译受影响页，保留历史与备份，不把同名当同人。

人物模块依赖 PyYAML，声纹模块依赖 numpy、PyAV、sherpa-onnx 和独立模型文件。模型路径、采样率、阈值须按公开源码设置并实际加载；示例 voiceprint JSON 不是现成的统一加载器。

**待适配的已知规则差异**：生产人物参考把短文件夹日期补为 20xx，且 `date_basis` 默认采用文件夹会期；这不满足公开通用的“缺年不补年”原则。新环境应先修正为显式、带证据的日期字段并测试，再启用人物页自动更新。不要用修改模板文字掩盖执行逻辑。

## 确认中心 UI 与后端

本地隔离版已复用原界面，采用 Cookie/CSRF。本包生产 `confirm/server.py` 仍是旧访问 key 协议，不能直接接到新 UI 后声称通了。先按[确认手册](../system/runbooks/06-confirmation.md)实现自己的认证适配：不把长效密钥置于 URL/前端源码，检查会话、CSRF、来源与受限音频路由。

答复→投稿→整理→最终回执的状态分别展示；声纹演示卡片不训练私人声纹。词典缓存、画像种子、音频和声音向量都留在自己的私有数据目录。
