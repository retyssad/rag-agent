# 实际验收记录

日期：2026-10-14 ~ 2026-10-15，Asia/Hong_Kong。以下仅列实际执行结果，不用模拟结果冒充真实服务验收。

## 交付与运行

- FastAPI 后端、Vue 3 + TypeScript + Pinia 前端、Alembic 迁移、导入 CLI、PowerShell 启动脚本和 Compose 配置已经实现。
- 当前采用全 Docker 运行，五个服务 `mysql/redis/milvus/backend/frontend` 均健康；页面 <http://127.0.0.1:5173>，API文档 <http://127.0.0.1:8000/docs>。
- 验收期间曾使用用户指定的中转站 `deepseek-v4.1-flash`，其后切换为 DeepSeek 官方 `https://api.deepseek.com`（实测支持 `deepseek-flash`、`deepseek-v4-pro`，不支持 `deepseek-v4.1-flash`）。**当前配置已改为阿里百炼 Qwen**：`CHAT_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1`、`CHAT_MODEL=qwen3.8-flash`，对话与 Embedding/Rerank 使用同一个百炼密钥。密钥未写入源码/文档/镜像。
- 已导入16位虚构教练、34篇演示文档，生成36个子块；真实百炼向量为1024维，SQL知识版本/索引版本均为1。

## 自动化检查

执行：`.venv\Scripts\python.exe -m pytest -c backend\pytest.ini -q`，环境变量 `RUN_INTEGRATION=1`。

最终结果：**25 passed**，约60秒。包含：

- 意图路由、直接知识路径、平台检索路径、混合任务；
- 硬条件筛选、多轮预算修改保留条件、前两位指代比较；
- SQL人工问题持久化、重新创建Repository后恢复、重复回复幂等、过期回复拒绝；
- 空候选不自动放宽、选择明确放宽选项后重新执行；
- 取消/新消息替代时旧运行不能写回结果；
- 引用归属/逐字原文、缺资料提示、父块去重、更新/下架及缓存失效；
- 模型协议适配、维度不匹配、服务失败/Redis降级、结构化函数调用校验、重复工具限步；
- REST创建/查询/取消、SSE重放和数据库状态恢复；
- API重启不会打断独立CLI评估运行；
- **真实MySQL**迁移、幂等种子与硬条件查询；
- **真实Redis**缓存、NX锁和pub/sub；
- **真实Milvus 2.6.2 / PyMilvus 2.6.2**中文分析器、dense、BM25 Function、候选范围模板过滤、索引更新/删除。

Milvus集成用例使用确定性测试向量，验证的是存储行为；真实模型调用另见下面记录。

## 账号扩展验收（2026-10-15 新增：决策报告 / 预登记子 Agent / 收藏 / 跨会话画像）

### 迁移
- 新增 `0004_user_features.py`（`favorites`/`preregistrations`/`user_profiles`）。实际执行三种路径：
  - **新库 SQLite**：`alembic upgrade head` → 三表存在，`alembic_version=0004`。
  - **模拟旧库 SQLite**：删掉三表并把版本退回 `0003` 后重跑 → 三表补齐；再次执行幂等无变化。
  - **已有 MySQL 容器卷**：`alembic -c backend/alembic.ini upgrade head` 实际执行 → 三表生成，`alembic_version=0004`，`favorites` 含 `uq_favorites_user_coach` 唯一约束，`preregistrations` 列为 `id/user_id/coach_id/session_id/slots/note/status/created_at/updated_at`。

### 自动化检查
- 新增 `backend/tests/test_features.py`（9 用例）：预登记子 Agent 走通并落库、4 周表 28 天且标记已登记格子、未指定教练暂停、未给具体时间暂停、**不可约星期暂停而非重试超时**、非法时段被拒（过去/越界/非开放时段）、画像跨会话聚合、收藏幂等、报告字段完整、两账号越权一律 404。
- 全量：`pytest -c backend\pytest.ini -q` → **48 passed, 3 skipped**（skip 为需 `RUN_INTEGRATION=1` 的真实存储用例）。

### 真实服务与页面实测（Docker：mysql/redis/milvus/backend/frontend 均 healthy）
- **预登记子 Agent 真实链路**：真实 Qwen 路由识别 `coach_preregistration`，`delegate_preregistration` → 子 Agent 调用 `get_coach_availability`/`create_preregistration`/`finish`，落库状态“已登记 · 待教练确认”，trace 中 `stages=route,delegate:preregistration` 均 ok。
- **决策报告端点**：真实完成一次带膝盖不适/耐心偏好的推荐，`GET /api/runs/{id}/report` 返回 18 个字段，含 2 位候选、4 条证据、逐条理由的 `source_title`/`source`、`score_label`、`hard_matches`、`pending`；前端报告视图渲染需求、候选对比表、逐条依据、待确认项、证据引用列表与免责声明，`@media print` 隐藏应用外壳。截图确认表格列宽正常、无竖排挤压。
- **账号面板**：`GET /api/me/profile` 实测聚合“减脂(11)、南山区(11)、耐心(11)、预算 300~300、膝盖不舒服(1)、18 条需求版本”（来自该账号历史会话），收藏与预登记计数正确。
- **4 周表**：`GET /api/coaches/coach-01/schedule` 返回 28 天；林知夏档期为周一/三/五 18–22，网格只在这三天给出可约小时，其余显示“休息”，与实际档期一致。
- **真实 bug 1（已修复）**：要求不可约星期（林知夏周二）时，真实模型对同一非法时段反复重试直到 240s 工具超时，整次运行 `failed`。修复：子 Agent 收到 `ValidationFailed` 时终止循环并把结论交回主 Agent；主 Agent 暂停并给出该教练可约星期选项。浏览器复测：出现“无法预登记你提出的时段…请改选该教练可约的时间”，选项为周一/周三/周五。
- **真实 bug 2（已修复）**：预登记追问恢复时，原消息里的星期与用户补充答案冲突，任务文本仍是原句。修复：恢复时若意图为 `coach_preregistration`，用补充答案替换任务文本。浏览器复测：选“周三”后运行 `completed`，结果卡与账号面板登记表均出现“已登记 · 待教练确认”。
- **真实 bug 3（已修复）**：失败时把内部细节甩给用户（`服务执行失败（TimeoutError），请检查数据库或模型/检索服务连接。`，以及 `retrieval 达到最大工具步骤…` 里的子 Agent 角色名）。修复：新增 `Orchestrator.user_facing_error`，供应商面向用户消息（含 HTTP 状态码）与我们自己的中文校验提示原样保留，超时/库异常/意外异常统一给出不含类名与堆栈的如实说明；技术细节只进 trace 与日志。容器内实测：`TimeoutError`→“处理超时，这次没有完成…”，`pymysql` 连接错误→“这次任务没有完成，请稍后重试…”，`ProviderError(503)` 仍原样展示。新增 `test_features.py` 两个用例锁定该行为。
- **真实 bug 4（已修复）**：50 位目录下并发压测时命中 `IndexError: list index out of range`（trace 定位在 `delegate:preregistration`）。根因：`delegate_preregistration` 始终注册给主 Agent，live 模式下模型可能在没有预登记意图、且 `coach_ids` 为空时误调该工具，子 Agent 用 `main['coach_ids'][0]` 索引空列表崩溃。修复：主 Agent 闭包与子 Agent 均先校验 `coach_ids` 恰为 1 位，否则返回可修正错误（让模型改走澄清），不再越界。新增 `test_preregistration_without_coach_id_errors_instead_of_crashing` 锁定。
- **真实 bug 5（已修复，界面）**：“我的账号 → 预登记”里日期被压成竖排。根因：CSS 规则 `.preregister-list li` 同时命中**外层卡片**和**嵌套的时段 `li`**，并把卡片设成 `flex + space-between` 横向三列，时段列表被挤到极窄导致换行。修复：外层卡片改纵向块，内层时段显式 `display:block; white-space:nowrap`。浏览器截图确认日期已横排。
- **真实 bug 6（已修复，数据泄漏）**：真实运行中模型把**自己的解释文字连用户ID、session ID** 写进预登记的 `note` 字段并展示给用户（如“用户 a84f0fe2…（session 53b0e2ce…）仅提出‘周一’可用，未指定具体日期…）。修复：服务端新增 `sanitize_note` —— 备注超过 200 字或含 hex 标识/`session`/解释性措辞则整条丢弃（不信任模型输出）；`note` 上限从 500 降到 200；子 Agent 提示明确 note 只能是用户自己的一句话补充。已清理实时库中已泄漏的那条记录。新增 `test_sanitize_note_drops_model_prose_and_internal_ids` 锁定。
- **已知精度限制**：真实模型把“周三”登记为周一 18:00–22:00（合法但不够精确的时段），说明时间解析仍可能偏离用户原话；当前只保证“不登记非法或过去时段、不代替用户选择”，精确到星期仍需人工核对。

## 账号清理入口（2026-10-15 新增：删除预登记 / 清空画像）

- 迁移 `0005_profile_reset.py`：`user_profiles` 加 `reset_at`。实测新库（`create_all` 含该列）、模拟旧库（删列回退 `0004` 后重跑补列）、真实 MySQL（`alembic upgrade head` → 版本 `0005`）三条路径均正确。
- 端点：`DELETE /api/me/preregistrations/{id}`（单条）、`DELETE /api/me/preregistrations?coach_id=`（清空，可按教练限定）、`DELETE /api/me/profile`（清空画像并重置累积）。均按账号校验归属。
- 行为实测（浏览器 + 库核对）：单条删除后预登记 2→1、四周表对应格子同步消失；“清空画像”后画像为空、会话统计归零，并显示“已清空过，画像只统计 2026-10-15 之后的新会话”；再次读取画像仍为空（**不会从历史会话重算回来**），聊天记录保留（实测 3 个会话仍在）；清空后新增一次会话，画像重新累积为 `增肌`、`sessions=1`。
- 新增 `test_preregistration_delete_and_clear`、`test_profile_reset_stops_accumulating_history_but_keeps_sessions`。全量：`pytest -c backend\pytest.ini -q` → **55 passed, 3 skipped**；`npm run build` 通过。
- 验收副作用已还原：验收过程删除了 `mark` 的一条预登记并重置过其画像，之后已恢复该登记、把 `reset_at` 清回空并重算画像（`减脂 17、塑形 5`，3 会话）。

## 追加 40 位教练（2026-10-15，共 90 位）

- 生成器 `generate_catalog.py` 扩展为支持**追加**：新增 `--offset`（姓名池下标）、`--id-start`（编号起点）、`--price-shift`（价格网格错位）、`--no-platform`（追加批次不重复输出平台文档）；姓名池扩到 90 个。
- **首要风险与验证**：追加绝不能动已有 50 位。实测重构后 `--count 50`（offset=0）输出与既有 `catalog-50.json` **逐字节一致**（`a==b` 为 True），因此首批可复现；`NO_DOCS`/`TEACHING_ONLY` 对首批沿用原固定集合，追加批次才按比例生成，避免改变既有教练的资料分布。
- 追加批次实测无冲突：40 位（`coach-51..coach-90`）、70 篇文档；ID / 姓名 / 价格与既有 50 位**全部零交集**；40 组 (姓名,价格,区域,目标,档期) 两两唯一；含 3 位无文档、4 位仅教学资料。
- 导入（**不加 `--replace`**）实测：MySQL `coaches=90`（`coach-01..coach-90`）、有效文档 `161`、有效父块 `163`、`revision=3`、`index_revision=3`；既有 `coach-01=白慕言` 未被改动；重复导入 `changed:false`（幂等）。
- 真实端到端：新目录下“减脂/南山区/预算400/工作日晚上/耐心”返回 8 位候选，**新旧教练混合**（`coach-01/17/33/49` 与新增 `coach-65/89` 同列），评分正常，引用逐字命中当前原文（`quote in text`）。
- Milvus 集合 `coach_evidence_v1` 报告实体数 256（93 旧 + 163 新，旧数据为软删除未 compaction）；检索结果由 SQL 按父块当前版本二次校验，实测无陈旧内容泄漏。
- 全量：`pytest -c backend\pytest.ini -q` → **62 passed, 3 skipped**。

## 我的知识库（2026-10-15 新增：用户专属，与通用库隔离）

- **新增依赖**：`pypdf==6.20.1`（PDF 文本抽取）、`python-multipart==0.0.20`（文件上传），已用 `uv pip compile ... --universal` 重新生成 `backend/requirements.lock.txt`；容器内实测两者可导入且版本一致。
- **迁移 `0006`**：新增 `user_documents` / `user_parent_chunks` / `user_knowledge_state`。真实 MySQL 执行 `alembic upgrade head` → 版本 `0006`，三表存在。
- **隔离实测（核心要求）**：两个账号 A/B，A 上传 3 份文档后——A 能检索并带引用回答；B 的文档列表为空、提问返回“你的知识库还没有文档”、按 A 的父块 ID 取原文返回 **404**、删除 A 的文档也 404。SQL 按 `owner_id` 过滤，Milvus 用独立集合 `user_kb_v1` + `owner_id` 表达式过滤，与通用集合 `coach_evidence_v1` 完全不同。
- **真实端到端**：真实百炼问答命中正确内容（“一水肌酸冲击法每天 20 克分 4 次、5–7 天，维持量 3–5 克/天”），返回引用；**校验引文 ID 不出现在正文**（沿用主库的 `strip_inline_ids` 规则）。
- **检索同构**：原始/改写 query → 稠密 + BM25 双路召回 → chunk_id 去重后 RRF 融合 → 独立 Reranker 精排 → `parent_id` 回填父块，与主库一致。
- **文件抽取**：`.md/.markdown/.txt`（UTF-8/GB18030）与 `.pdf`（pypdf）实测通过；`.png`、空文件明确 400。带文本层的中文 PDF（reportlab 生成）抽取正常。
- **真实 bug 1（已修复）**：首次 live 上传 500。Milvus `upsert` 要求**所有字段都出现**，而子块构造漏了 `file_type` → `DataNotMatchException`。修复并加 `test_child_fields_cover_expected_milvus_schema`（demo 模式测不到，因此单独锁字段集）。
- **真实 bug 2（已修复）**：`POST /api/kb/query` 在 live 分支 500 —— `strip_inline_ids` 从 `app.providers` 导入，实际定义在 `app.agents`。修复并加 `test_live_answer_dependencies_are_importable` 锁住 live 分支的导入。
- **真实 bug 3（已修复，体验）**：对**空知识库**提问原本抛 `RuntimeError` 变成报错页；改为明确说明“你的知识库还没有文档。先上传 PDF 或 Markdown，再提问”。
- **索引失败不假装成功**：上传成功但索引失败时返回 503 并提示“重建索引”，状态保持未同步；不会返回 201 让用户以为可用。
- **示例文件**：`data/kb-samples/` 5 份（蛋白粉、肌酸/补剂、力量训练、减脂营养、补水 PDF）。
- 全量：`pytest -c backend\pytest.ini -q` → **62 passed, 3 skipped**；`npm run build`（含 `vue-tsc`）通过；浏览器实测文档列表、PDF/MD 徽标、删除按钮、基于文档的问答与 5 条引用均正常。

## 50 位虚构目录导入（2026-10-15）

- 新增 `backend/scripts/generate_catalog.py`（确定性、可复现）：生成 50 位教练，价格/区域/专长目标/授课方式/档期两两组合互不相同（实测 50 组唯一、50 个不同价格、8 个区域、16 个目标）；刻意留 4 位**无任何文档**、5 位**仅教学资料**，用于验证“资料不足如实标注”。
- 实际导入：`python -m scripts.import_data data/demo/catalog-50.json --replace` → MySQL `coaches=50`、有效文档 `91`、有效父块 `93`、`revision=2`、`index_revision=2`；Milvus 集合实体数 `93`，与父块数一致。重复执行 `changed:false`（幂等）。
- 真实端到端实测（浏览器 live）：新目录下“减脂/南山区/预算420/工作日晚上/耐心”返回 5 位候选（林肃之、白慕言等新教练），逐条依据引用新版原文，`hard_matches` 全绿；“塑形/龙岗区/350/工作日晚上”无完全匹配时**如实暂停**并说明“仅放宽区域后有 11 位候选”。
- **测试隔离（重要）**：MySQL 集成测试原本会把 `data/demo/dataset.json`（16 位 fixture）灌入**应用库**并断言 `coach-01/02`。切到 50 位目录后这会静默覆盖真实数据。已改为在独立 schema `coach_guide_it` 上运行（自动创建/授权/迁移，无 root 权限则 skip 而非污染应用库）；实测 3 个集成用例在 `RUN_INTEGRATION=1` 下全部通过，应用库 `coaches=50`、`coach-01=白慕言` 未被改动。
- 总数核对：全量 `pytest -c backend\pytest.ini -q` → **52 passed, 3 skipped**；`npm run build`（含 `vue-tsc`）通过。


前端：`npm run build` 成功，包含 vue-tsc 类型检查。Vue固定3.5.44，Vite固定6.4.4。`npm audit --registry https://registry.npmjs.org` 实际返回 **0 vulnerabilities**。默认本机镜像源不提供审计API，审计使用官方npm源。

`docker compose --profile app build` 已成功构建两个镜像，且启动时执行MySQL Alembic成功。容器中 `python -m scripts.import_data data/demo/dataset.json --validate-only` 成功。`docker compose config --quiet`、Python编译与PowerShell脚本语法检查通过。

## 真实供应商验收

实际调用用户指定端点：

- 中转站 `https://sekirocloud.shop/v1/chat/completions`，模型 `deepseek-v4.1-flash`：中文知识回答成功、function tool_calls成功；
- 百炼 `text-embedding-v4`：批量embedding成功、1024维检查通过；
- 百炼 `gte-rerank-v2`：真实query-document精排成功；例如“新手减脂”对两篇文本返回0.95078和0.07907，**这些数值是文本相关性，非教练成功概率**；
- 真实索引查询的原始/改写两条query分别dense+BM25召回，RRF融合及外部精排、SQL父块回填成功；
- `scripts.verify_live` 完整多Agent推荐成功，得到2位符合预算300/南山/工作日晚间的候选；
- 前端真实服务中提交包含膝盖不适/耐心偏好的推荐，完成候选表与引用；随后预算改400，区域、时间、偏好、自述条件保留，候选扩为4位；
- 真实人工介入检查：等待→重新读取持久状态→回复→重复回复被判为已处理→恢复完成、2位候选，见 `docs/hitl-live.json`。

中转站 JSON mode 在实测中仍返回过 Markdown 文本，因此结构化输出改为模型调用 `submit_structured_result` 的函数参数，并经Pydantic校验；没有使用宽松自由文本解析替代工具调用。

## 固定小型评估

`data/evaluation/cases.json` 包含8个业务场景和4个资料检索问题。

- `docs/evaluation-demo.json`：模拟适配器实测，8/8业务通过，Recall@5=1.0，MRR=1.0；只作演示回归验证。
- `docs/evaluation-live-initial.json`：真实模式首轮7/8通过，预算修改的前置执行被开发期间API重启打断，保留原始失败报告。
- `docs/evaluation-live.json`：其余场景保留首轮实测结果，预算修改使用真实页面已成功持久化的会话历史，重新执行最后一条更新消息并校验后合并报告；最终8/8业务通过，4个资料问题Recall@5=1.0，MRR=1.0。该记录是**分次测量后的回归结果**，不是宣称一次从头运行零失败。新增用例记录其run_id和测量时间。

小型样例库和人为挑选的问题过于简单，以上指标不代表实际推荐质量、复杂约束表现或大规模检索质量。真实资料导入后应重新建立评估集并测量。

## 已遇到并修复的问题

- Windows3306绑定失败：Compose本机端口改为13306。
- MinIO镜像拉取受阻：选择Milvus官方单机支持的内嵌etcd/本地存储配置，并用真实检索测试验证。
- PyMilvus2.6.2搜索返回自定义主键`chunk_id`，分析器结果为`AnalyzeResult.tokens`：已按真实API返回修正。
- 候选中的datetime不能直接落JSON：统一序列化UTC时间。
- 百炼Rerank最初端点缺末尾路径：修正为 `/services/rerank/text-rerank/text-rerank`，真实调用通过。
- 教学文本“耐心程度没有说明”不能加偏好分：否定/资料不足语句不计入肯定证据。
- 委派说明不能替换原始检索词：原始用户query通道固定保留。
- 原文引用不能冒充报价/区域来源：模型评价工具会拒绝使用教学文档支持结构化事实的理由，结构化条件另列来源。
- 重启恢复曾干扰CLI验收：增加执行范围，API恢复仅处理API任务。

## 实际限制与风险

- 用户中转站发生过连接/TLS失败；已实现有限重试，超过限制明确标为失败。一次独立预算场景复测的前置路由也遇到网络失败，因此该次不能完成后续预算保留；最终复测通过不等于网络稳定性保证。
- 模型输出仍可能需要纠正工具参数，系统会拒绝不符合约束的工具，不静默切换mock。达到最大步骤后明确失败。
- `quote`逐字、ID归属和当前版本可以自动校验；模型理由的所有语义推论无法仅靠这些结构校验完全证明，应通过更多真实资料评估与人工复核检查。
- 当前无联网研究、预约、付款、权限认证、生产高可用和跨进程任务队列；限定本机单worker运行。API重启时正在执行任务标为失败需重发，人工等待状态保留。
- 用户知识库不支持：扫描件/图片型 PDF（无文本层）、OCR、Word/Excel/PPT、网页抓取；单文件 10 MB、单文档 20 万字符上限。
- 预登记不是已达成的预约：不含支付、教练侧确认、改期或取消；“已登记 · 待教练确认”只表示按教练开放时段提交了时间，等待教练回应。
- 跨会话画像为软背景，不参与硬条件；若历史偏好与本次明确条件冲突，以本次条件为准。
- 全部教练/案例仍为虚构样例。后续真实或更丰富的生成数据由用户提供，按 `docs/DATA_GUIDE.md` 导入；不把模型真实调用等同于资料真实。

页面截图：`docs/ui-live.png`。启动与数据替换见根目录README及DATA_GUIDE。
