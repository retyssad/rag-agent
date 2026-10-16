# 合练 · 教练选择助手

按 `PROJECT_HANDOFF.md` 实现的本地可运行项目：FastAPI + Vue 3/TypeScript/Pinia，MySQL 持久化业务事实与会话，Redis 缓存和事件通知，Milvus 中文 BM25 与稠密检索。对话、路由、改写和 Agent 工具决策统一调用阿里百炼的 Qwen（`qwen3.8-flash`）；Embedding 和独立 Reranker 同样使用阿里百炼。

教练资料使用 **90 位虚构教练、161 篇明确标为演示的文档**（当前库中数据）。真实模式表示模型及存储服务是真实调用，**不表示演示教练资料是真实的**。生成与导入见 [导入指南](docs/DATA_GUIDE.md)。

- 首批生成：`python -m scripts.generate_catalog --count 50 --output data/demo/catalog-50.json`（确定性，可复现；ID 为 `coach-01..coach-50`）。
- 追加一批（不覆盖已有）：`python -m scripts.generate_catalog --count 40 --offset 50 --id-start 51 --price-shift 5 --no-platform --output data/demo/catalog-40-add.json`，再 `python -m scripts.import_data data/demo/catalog-40-add.json`（**追加时不要加 `--replace`**）。
- `data/demo/dataset.json`（16 位）保留为**测试与固定评估的 fixture**，不要用于替换业务库；所有文件共用 ID 前缀，混用会互相覆盖。

## 账号与登录

- 界面需注册/登录后使用；用户名 3–32 字符不含空格，密码至少 8 位。聊天记录按账号存储在 MySQL/SQLite，支持历史列表、切换和删除。
- 登录态用服务端会话 Cookie（HttpOnly、SameSite=Lax、30 天）：服务端只存 token 的 SHA-256，密码用 stdlib `hashlib.scrypt` 加盐哈希，不引入新依赖。SSE 由浏览器自动携带 Cookie。
- 端点：`POST /api/auth/register`、`POST /api/auth/login`、`POST /api/auth/logout`、`GET /api/auth/me`；除 `/api/health` 与注册/登录外所有 API 都要求登录，访问他人会话一律 404。
- 迁移 `0002` 会清理登录功能之前的匿名会话；无改密码、找回密码、限流等能力。
- 账号下还保存收藏教练、预登记时间安排与跨会话画像（见下方“决策报告、预登记与收藏”）。预登记必须登录后使用。

## 运行前提

- Windows PowerShell，Python **3.11** 或 3.12，Node.js 22+，npm。
- 真实存储需要 Docker Desktop：Linux containers + WSL2；建议预留至少 6 GB 内存。
- 执行命令的工作目录统一是 `D:\help-RAG`。不要在 `backend` 目录执行根目录路径命令。
- Python 完整依赖在 `backend/requirements.lock.txt`，前端有 `package-lock.json`；首次安装需要网络。

## 最快体验：演示模式

无需模型密钥与 Docker，使用 SQLite 和明确标记的模拟适配器。Redis 可选，未连接时返回缓存降级提示，不影响 SQL 会话持久化。

```powershell
cd D:\help-RAG
powershell -ExecutionPolicy Bypass -File scripts\start-demo.ps1 -Install
```

打开 <http://127.0.0.1:5173>。停止由脚本启动的服务：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop-demo.ps1
```

也可分终端手动启动：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r backend\requirements.lock.txt
$env:PYTHONPATH='backend'
$env:PYTHONIOENCODING='utf-8'
$env:APP_MODE='demo'
$env:DATABASE_URL='sqlite:///./.local/coach.db'
.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
.venv\Scripts\python.exe -m scripts.import_data data/demo/dataset.json
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

另开终端：

```powershell
cd D:\help-RAG\frontend
npm ci
npm run dev -- --port 5173 --strictPort
```

环境变量优先于 `.env`，真实运行前在新终端执行，或清除演示覆盖：`Remove-Item Env:APP_MODE,Env:DATABASE_URL -ErrorAction SilentlyContinue`。

## 真实模式：模型 + MySQL + Redis + Milvus

1. 安装上述依赖。运行 `Copy-Item .env.example .env` **仅适用于没有 `.env` 的首次配置**；已有文件请直接编辑，避免覆盖自己的密钥。
2. 在 `.env` 填入下列配置。密钥只存本地 `.env`，该文件已加入 `.gitignore` 和 `.dockerignore`。

```dotenv
APP_MODE=live
DATABASE_URL=mysql+pymysql://coach:local-demo-coach@127.0.0.1:13306/coach_guide?charset=utf8mb4
REDIS_URL=redis://127.0.0.1:6379/0
MILVUS_URI=http://127.0.0.1:19530
CHAT_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
CHAT_MODEL=qwen3.8-flash
CHAT_API_KEY=你的阿里百炼密钥
DASHSCOPE_API_KEY=你的百炼密钥
BAILIAN_EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIMENSION=1024
BAILIAN_RERANK_MODEL=gte-rerank-v2
BAILIAN_RERANK_URL=https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank
LOG_LEVEL=INFO
LOG_FORMAT=json
```

对话、路由、改写和工具决策统一调用阿里百炼的 OpenAI 兼容接口 `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`，模型为 `qwen3.8-flash`（已验证支持 function calling）。对话与 Embedding/Rerank 可用同一个百炼密钥。如账户开放其他模型，只需修改 `CHAT_MODEL`。结构化输出通过函数参数提交并校验。阿里百炼默认中国内地站；其他站点需要改为对应的 Embedding/Rerank URL 与同站点密钥。

3. 启动并检查服务，再迁移和导入：

```powershell
docker compose up -d mysql redis milvus
docker compose ps
$env:PYTHONPATH='backend'
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
.venv\Scripts\python.exe -m scripts.import_data data/demo/dataset.json
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

完成首次安装、迁移和导入后，也可用 `powershell -ExecutionPolicy Bypass -File scripts\start-live.ps1 -SkipIndex` 启动；省略 `-SkipIndex` 会重新调用 Embedding 同步索引。停止本机启动脚本使用 `scripts\stop-demo.ps1`，脚本同时适用于记录过的真实服务进程，Docker 基础服务独立管理。

服务全为 healthy 后导入；首次拉取镜像和 Milvus 启动需要时间。前端启动与演示模式相同。数据库端口特意使用 **13306**，避免 Windows 本机 3306 冲突。Compose 密码为本地开发默认值，可通过 `MYSQL_PASSWORD`/`MYSQL_ROOT_PASSWORD` 修改；相应修改 DATABASE_URL。

## 全 Docker 启动

先准备 `.env`，前端使用容器内 nginx 代理后端，无需本机 Node/Python：

```powershell
docker compose --profile app up -d --build
docker compose exec backend python -m scripts.import_data data/demo/dataset.json
```

容器内 `.env` 的数据库/Redis/Milvus 地址由 Compose 覆盖为服务名。后端镜像启动时自动执行 Alembic，数据导入需要上述单独命令，避免每次重启重复模型索引费用。访问 <http://127.0.0.1:5173> 和 <http://127.0.0.1:8000/docs>。本机服务与容器服务使用相同端口，启动前停止另一种运行方式。

Milvus 固定 **服务端 2.6.2 / PyMilvus 2.6.2**，使用完整 Milvus standalone 服务，内嵌 etcd + 本地持久化对象存储；开发模式无需外部 etcd/MinIO。`infra/embedEtcd.yaml` 与 `milvus-data` 卷提供持久化。已经实际验证 `analyzer_params={"type":"chinese"}`、BM25 Function、稀疏索引、稠密索引和模板过滤。此配置面向单机演示，不是生产高可用部署。

## 可以演示的闭环

1. 输入“我想减脂，预算每节课300元以内，南山区，工作日晚上，膝盖不舒服，希望教练耐心”。硬条件过滤后返回候选表及资料原文。
2. 输入“预算改成400，其他条件不变”。区域、时间、目标和偏好保留，筛选、检索、评估重新执行。
3. 勾选两位教练并比较，或输入“比较前两位教练”。
4. 开新对话，输入“帮我推荐教练”。系统暂停，填写目标/区域后恢复同一次运行。
5. 输入“找减脂教练，南山区，预算100”。系统说明实际条件诊断，等待选择。只有选择“不限预算”等才清除该硬条件。
6. “什么是渐进超负荷？”直接由 Qwen 回答；“平台减脂课程怎么安排？”按平台文档检索后回答；混合问题由主 Agent 分别委派。

### 决策报告、预登记与收藏（新增）

7. **导出决策报告**：候选表工具栏点“导出决策报告 PDF”，打开报告视图（账号、需求摘要、需求/知识版本、候选对比、逐条依据含引文与来源、待确认项、证据引用列表、免责声明），点“打印 / 另存为 PDF”即用浏览器打印为 PDF。数据来自 `GET /api/runs/{id}/report`，无需重新推理；中文由浏览器字体渲染，**不新增任何依赖**。
8. **预登记子 Agent**：在对话里说“帮我给林知夏预登记周三晚上”，主 Agent 识别 `coach_preregistration` 意图并委派预登记子 Agent，按教练真实档期校验后落库，UI 显示“**已登记 · 待教练确认**”。可选时段只能是未来 4 周内、教练开放时段中的整点窗口。
9. **未来 4 周预登记表**：候选行点“📅 预登记”或点教练名/头像打开教练详情，显示未来 4 周（明天起 28 天，Asia/Hong_Kong）逐日可约网格；可约格子按小时点选后提交。已登记格子显示“已登记 · 待教练确认”徽标，并可在“我的账号 → 预登记”查看。
10. **收藏教练**：候选行或教练详情点“☆ 收藏教练 / ★ 已收藏”切换；“我的账号 → 收藏教练”可查看并点开。
11. **跨会话用户画像**：点顶栏用户名打开“我的账号 → 跨会话画像”，汇总该账号全部会话的目标分布、区域偏好、预算区间、教学偏好、自述情况、会话/需求版本统计，以及收藏与预登记教练。画像作为**软背景**注入意图路由与知识回答，**绝不作为硬条件、不自动放宽、不自动过滤候选、不跳过澄清**；无账号（CLI/评估）时画像为空，行为与之前一致。
12. **删除入口**：“我的账号”里三块都可清理——**收藏**每条可“移除”；**预登记表**每条右侧 `×` 可单删，也有“清空全部预登记”；**跨会话画像**有“清空画像”。清空画像会重置累积：**聊天记录保留**，画像只统计清空时间之后的新会话（面板会提示“已清空过，画像只统计 YYYY-MM-DD 之后的新会话”），因此不会从历史会话自动重算回来。

预登记追问也走同一 HITL：要求的时间不在教练可约时段、或没给出具体时间时，运行暂停并给出该教练可约的星期选项，用户补充后从原运行恢复；不代替用户选择时间，也不登记过去的时间。

### 我的知识库（用户专属，与通用库隔离）

13. **入口**：侧边栏点“📚 我的知识库”，进入独立界面（不是弹窗，是整页视图）。
14. **上传入库**：支持 **PDF**（`.pdf`，用 `pypdf` 抽取文本）与 **Markdown/纯文本**（`.md/.markdown/.txt`）。上传后自动切父子块并重建**该账号自己**的向量索引。单个文件上限 10 MB，单文档上限 20 万字符（超出截断并在响应中标注 `truncated`）。
15. **基于文档问答**：左侧文档列表 + 右侧提问框。回答**只使用该账号上传的文档**，并给出可点开的原文引用；资料不足时明确说明“没有找到能支持这个问题的内容”，不编造。
16. **检索方式与主界面一致**：原始/改写 query → Milvus 稠密 + BM25 双路召回 → 按 chunk_id 去重后 RRF 融合 → 独立百炼 Reranker 精排 → 按 `parent_id` 回填父块。
17. **账号隔离**：所有操作以登录账号为边界。SQL 侧按 `owner_id` 过滤，Milvus 用**独立集合 `user_kb_v1`** + `owner_id` 表达式过滤；跨账号读取原文一律 404。与通用教练/平台资料完全不通。
18. **示例文件**：`data/kb-samples/` 提供 5 份可直接上传的虚构科普资料——蛋白粉摄入、肌酸与补剂、力量训练基础、减脂营养、以及一份带文本层的 PDF（训练补水与电解质）。

支持刷新恢复、SSE 断线重连与轮询降级、取消运行、新消息替代旧运行、人工回复幂等。等待请求持久化到 SQL，重启后可恢复；正在计算的任务在重启后明确标为失败，不伪装成已经完成，需要重发。

## 实现细节

- **Agent**：主 Agent 和候选、检索、评估子 Agent 分别建立独立消息上下文；真实模式通过模型 `tool_calls` 选择动作，观察工具结果，再调用下一步或 finish。每个 Agent 默认最多10步，单步最多5个工具，重复同工具同参数被拒绝。工具参数 Pydantic 校验，SQL 查询参数化，不执行模型生成 SQL。
- **依赖**：候选筛选与独立知识回答允许 asyncio 并行；候选证据依赖筛选结果，评估依赖候选和证据。数据库工作放到线程执行；HTTP 模型调用异步，限时，网络/429/5xx 最多3次尝试。
- **RAG**：标题/段落父块≤1800字，子块400字、60字重叠；原始和改写 query 保留，分别召回 dense/BM25，按 chunk_id RRF 融合，独立百炼 Reranker 精排，再父块去重回填。默认15路候选/每通道，RRF k=60，精排8条，最多6父块/9000字符。
- **事实与证据**：价格、档期、区域、方式来自 SQL。评价理由须附本教练的父块 ID 和逐字 quote；引用越界、跨教练、非原文片段会被拒绝。检索分数不作为匹配概率；100分排序参考由公开规则计算，偏好资料中的否定描述不加分。
- **时间**：星期0=周一，6=周日；“工作日晚上”默认18–22，“周末”默认9–18，时区 Asia/Hong_Kong。可用时段为可选范围，至少与一个教练时段有连续一小时交集才满足。更多时间表达由模型结构化提取，不够明确时可进一步补充。
- **版本**：每条新消息分配需求版本；人工补充属于同一运行，保存新的快照。新消息取消旧运行和旧问题，SQL 会话行锁防止旧结果覆盖新需求。文档内容变更须增加 version；重建索引前标 importing，未完成时阻止真实检索；SQL 再校验父块当前版本。
- **Redis**：查询缓存含原始/改写 query、候选范围、全部需求与版本、知识版本、模型/维度和检索配置；运行状态与事件通知也写 Redis。SQL 是持久状态和 SSE 重放的事实源，Redis 故障明确降级。
- **运行方式**：当前支持单后端 worker（`--workers 1`）；不提供跨进程后台队列。SQL 行锁保证人工输入与提交一致性，Redis 临时锁能力有真实集成测试。不要自行提高 worker 数量，否则重启恢复逻辑会干扰其他进程。
- **候选范围**：单次评估最多比较 `MAX_CANDIDATES`（默认30）位 SQL 筛选候选（按价格、ID）。**超过上限时结果里会明确写出“符合硬条件的共 N 位，本次仅评估前 30 位”，不会静默丢弃**（可用环境变量 `MAX_CANDIDATES` 调整，5–200）。目标与教学偏好用于后续排序，不是未经确认的新增硬条件。
- **账号扩展（迁移 `0004`、`0005`）**：新增 `favorites`（收藏，账号+教练唯一）、`preregistrations`（预登记，slots 存具体日期与整点窗口）、`user_profiles`（跨会话画像缓存，`0005` 加 `reset_at` 支持清空后重置累积）三表。迁移沿用 `0002/0003` 的 inspector 幂等写法：新库由 `0001` 的 `create_all` 建出，旧库补齐缺失表/列。端点：`GET /api/runs/{id}/report`、`GET /api/me/profile`、`DELETE /api/me/profile`（清空画像并重置累积）、`GET/POST /api/me/favorites`、`DELETE /api/me/favorites/{id}`、`GET/POST /api/me/preregistrations`、`DELETE /api/me/preregistrations`（清空，可带 `?coach_id=`）、`DELETE /api/me/preregistrations/{id}`、`GET /api/coaches/{id}/schedule`；全部要求登录，越权一律 404。预登记时段非法返回 400（`ValidationFailed`）。
- **预登记边界**：只登记“教练开放时段内、未来 4 周内”的具体整点窗口，不是已达成的预约；状态固定为“已登记 · 待教练确认”。不实现支付、教练侧确认、取消或改期。
- **用户专属知识库（迁移 `0006`）**：新增 `user_documents`、`user_parent_chunks`、`user_knowledge_state` 三表（均以 `owner_id`/`user_id` 归属），以及独立 Milvus 集合 `user_kb_v1`（含 `owner_id` 字段与中文 BM25）。端点：`GET/POST /api/kb/documents`（上传用 `multipart/form-data`）、`DELETE /api/kb/documents/{id}`、`POST /api/kb/reindex`、`POST /api/kb/query`、`GET /api/kb/evidence/{parent_id}`；全部要求登录，越权/跨账号一律 404。需要新增依赖 `pypdf==6.20.1`（PDF 文本抽取）与 `python-multipart==0.0.20`（文件上传），已重新生成 `requirements.lock.txt`。
- **知识库边界**：只抽取 PDF 文本层——扫描件/图片型 PDF 会明确报“没有可提取的文本层”，不会静默入库空内容；不支持 Word/Excel/PPT、OCR、网页抓取。索引失败时文档仍保存在 SQL，状态保持“未同步”并返回 503 提示执行“重建索引”，不会假装成功。
- **边界**：无支付、预约、改密/找回密码、联网研究、医疗诊断和生产发布。HTTP/SSE 默认绑定 localhost。页面渲染模型/资料为纯文本，避免执行返回 HTML。开发调试视图只记录工具/阶段摘要，无内部思维链。

## 测试与实际验收

```powershell
# 本地业务、API、版本、引用、暂停恢复、模型协议等测试
.venv\Scripts\python.exe -m pytest -c backend\pytest.ini -q
# 真实存储（先启动Compose，按真实模式执行MySQL迁移）
$env:RUN_INTEGRATION='1'
.venv\Scripts\python.exe -m pytest -c backend\pytest.ini -q
# 真实供应商 + 真实索引 + 多Agent完整推荐；会产生API费用
$env:PYTHONPATH='backend'
.venv\Scripts\python.exe -m scripts.verify_live
# 小型固定演示评估，生成实际指标
.venv\Scripts\python.exe -m scripts.evaluate
# 同样评估集调用真实模型，结果写到另一个报告
.venv\Scripts\python.exe -m scripts.evaluate --live --output docs/evaluation-live.json
```

前端检查：`cd frontend; npm run build`。验收事实与限制见 [VALIDATION.md](docs/VALIDATION.md)，演示评估指标见 `docs/evaluation-demo.json`。真实 Milvus 集成测试使用固定测试向量，单独验证服务器 BM25/稠密检索与更新/删除；不能将其当作百炼模型质量验收。

## 日志与追踪

后端用 stdlib `logging` + 自写 JSON formatter（`backend/app/observability.py`），**不引入 structlog 等新依赖**，因此 `requirements.lock.txt` 无需重新生成。默认单行 JSON 到 stdout；`LOG_FORMAT=text` 可切人类可读格式，`LOG_LEVEL` 控制级别。启动时 `configure_logging` 会把 uvicorn 的 logger 一并接过来，**服务端日志和业务日志是同一种 JSON**；uvicorn 内置 access log 被压到 WARNING，由中间件统一输出。

每个请求分配一个 `request_id`：优先用客户端传的 `X-Request-ID` 头，否则服务端生成，并在响应头回显。它存在 contextvar 里，因此自动贯穿 API → orchestrator → provider 调用，**无需给任何函数加参数**；同一请求的所有日志行都带同一个 `request_id`。

```bash
# 只看某次请求：把 X-Request-ID 固定，再按它过滤
curl -H X-Request-ID: my-req-1 ...
docker compose logs backend | jq 'select(.request_id=="my-req-1")'
# 每次 run 的汇总轨迹
docker compose logs backend | jq 'select(.event=="run.trace")'
```

**每次 run 一条 trace**：存 `runs.trace` 列（迁移 `0003`），并额外打一条 `event=run.trace` 日志。内容包括阶段耗时、每次工具调用名/耗时/成败、供应商调用次数与耗时、token 用量、错误摘要。运行中会随事件一起刷新，等待用户时也能查。

```bash
curl -b cookies.txt http://127.0.0.1:8000/api/runs/{run_id}/trace
```

字段：`stages`（`route`、`delegate:candidate|retrieval|evaluation|knowledge`、`retrieval:recall|rerank`）、`tools`、`providers`、`tokens`（chat/embedding/rerank 从供应商 `usage` 累加）、`total_ms`、`request_ids`（人工暂停恢复时累计）。`GET /api/runs/{id}` 与 `/api/sessions/{id}` **不含** `trace`，避免 SSE 每 300ms 轮询反复拉全量轨迹。

不记录的内容：模型内部思维链、工具原始参数、供应商原始响应、URL 与密钥（沿用原有"不输出带 token 的异常"约定）。CLI 脚本（`import_data`/`verify_live`/`evaluate`）把 JSON 日志写到 stderr，stdout 保留脚本原有输出。

## 常见故障

- **端口冲突**：`docker compose ps`；本机 MySQL 地址使用13306。API/前端端口占用时先停止已有项目服务。
- **401/403**：检查密钥与站点/模型权限。对话模型密钥只发送到 `CHAT_BASE_URL`；百炼密钥只发送到相应百炼端点。
- **Rerank InvalidParameter / task can not be null**：路径必须包含末尾 `/text-rerank/text-rerank`。
- **模型输出校验失败**：真实模式不会切换 mock；调整用户消息或检查所选模型的 JSON/tool calling 支持后重试。
- **知识索引未同步/正在同步**：`python -m scripts.import_data --reindex`。改 Embedding 模型/维度时使用新集合名并重新索引。导入失败后旧索引不会伪装成功。
- **没有教练/平台资料**：先导入数据。来源下架/版本更新后历史引用会提示重查。
- **Redis 不可用**：SQL 状态和事件仍可用；修复 redis 地址后恢复缓存。
- **容器排查**：`docker compose logs --tail 100 milvus` 或 `backend`。`docker compose down` 只停止服务，保留命名卷；勿用 `down -v` 删除业务资料。

## 主要文件

```text
backend/app/       API、模型适配、ReAct Agent、检索、持久化、导入、用户知识库
backend/migrations/ Alembic迁移（0004 收藏/预登记/画像、0006 用户知识库）
backend/scripts/   导入、演示数据生成、固定评估、真实验收
backend/tests/     业务、用户知识库与真实存储集成测试
frontend/src/      Vue对话、需求摘要、候选比较、人工卡片、来源、账号面板、教练详情、决策报告、我的知识库
data/demo/         虚构演示数据
data/kb-samples/   可直接上传的示例资料（健身知识 / 补剂 / 带文本层 PDF）
data/evaluation/   固定回归评估集
docs/              数据生成契约、验收报告
scripts/           Windows演示启动与停止
infra/             Milvus单机配置
```
