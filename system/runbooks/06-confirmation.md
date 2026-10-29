# 确认中心：复用界面，接好真实结果

`confirmation-ui/index.html` 保留了实际运行页面的布局、样式、知识卡/发言人卡、已处理列表、词典、试听播放器和交互。公开修改集中在配置化、访问方式和演示提示；不用后续 AI 重新猜一套设计。

## 两种后端必须分清

| 项目 | 可直接运行的本地版 | 脱敏生产参考 |
|---|---|---|
| 入口 | `tools/system.py` | `production-reference/confirm/server.py` 及配套任务 |
| 认证 | 本机密码 → HttpOnly Cookie、CSRF | 原始实现含历史认证方式，必须接安全的同源会话适配层 |
| 数据 | 独立文件参考 Wiki，虚构卡片 | MCP 问题登记、音频任务、声音库、最终回执 |
| 答复 | 立即 create-only 投稿 | 保留原延迟/撤销/回写机制并检查任务真正执行 |
| 入库 | 本地 `Wiki.resolve` 验证回执 | `kb_feedback.py` 核正式 journal + ID/version + 页面 |

公开 UI 读 `window.KBC_CONFIG`（`api`、`csrf`、`vault`、`profile`），通过同源 Cookie 请求 API。**它不能直接接仍要求旧 URL 密钥的生产 server.py。** 后续 AI 需提供会话认证/转接层，验证请求身份后调用原动作；不把主密钥放进 URL、网页、localStorage 或分享链接。公网 Cookie 另需 Secure、HTTPS、会话过期和登录限速；本地参考只允许回环访问。

## 要打通的数据

1. `make_question_cards.py` 只从真实问题登记读取稳定 ID/version，先查已有知识和词典，再决定是否问人。不能每次巡检换一个 ID 重复问。
2. 用户的“补充/选择/拿不准/稍后/无意义”是不同动作。草案只供核对；点击看过或 Agent 生成一个草案，都不是主人答复。
3. `kb_qwrite.py`、`kb_sync.py` 将有来源的答复写到 `00-Inbox/待确认答复`，保留问题 ID/version、身份和原页面版本；未知不写成事实。
4. 整理 Agent 读取答复、旧知识和原始证据，沿既有事务正式提交。`kb_receipt_reader.py` / `kb_feedback.py` 验证最终回执。UI 分别显示“已收到/待核证/已入库/失败”，不能凭投稿文件存在变成“已入库”。
5. `kb_confirmed_dictionary.py` 是已确认词条的统一来源；词典缓存、卡片生成、名字展示和听悟热词都读取同一来源及版本。词典变化后更新派生展示，不改原音频/原始 JSON。
6. 手机确认事件必须带稳定事件 ID；服务端去重、回执/ack，断网重传只处理一次。访问手机确认页使用自己的 HTTPS 域名和登录，不分享作者地址或密钥。

## API 和撤销

本地接口支持登录、`config.js`、`GET /kbc?a=list`、`GET /kbc?a=vocabulary`、`POST /kbc` 的 `resolve/undo`。写请求需 `X-Wiki-CSRF`，知识卡必须带 `question_version`。`received`、`adopted` 和拒绝原因可在测试中核对。

本地版不支持生产 delayed-write、不自动创建人物页，也不自动将答复采纳为知识。已投稿/已入库后“撤回”会拒绝删除历史，需另建可追溯更正。完整生产撤销按 [声纹手册](05-voiceprint.md) 和原代码重算对应派生内容，不能只改卡片颜色。

## 验收

先通过本地登录/版本/答复/最终回执测试，再在自己的服务测试浏览器与手机：未登录不可读写、伪造卡片/过期版本拒绝、音频只可读允许的 ID、断网重传不重复、撤销后错误归属消失、页面展示能跳回真实知识页。服务 HTTP 200 与网页截图都不能代替这些检查。
