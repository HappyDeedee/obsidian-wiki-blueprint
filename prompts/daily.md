# 每日增量提示词模板

读取当前规则与 ingest Skill，实际发现工具/权限。先 status，BUSY 结束；取得唯一 writer 后处理 pending。
先核确认答复，再会议、稳定日记和其他新/refresh来源；普通来源本轮最多3份，答复不挤占此限，仍遵守总预算。
候选只来自本轮 pending，不从旧总结回放；长来源按真实 read_range 读齐并 stage，接近预算留到下轮，不编覆盖。
原件/规则不改；数字、事实状态和时间核原文；新旧主题复用；部分提交同 token 同参数恢复。
以 durable commit/回读/收尾结果统计新成功、refresh、topic_repair、reused、stage、失败；释放自己的 writer。
不改模型/任务/预算，不创建第二控制器，不联网；报告结果与未处理范围，不能用任务 completed 代替入库。
