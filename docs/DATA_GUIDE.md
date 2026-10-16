# 给数据生成 AI 的导入契约

后续教练数据由你另外生成。让其他 AI 输出 UTF-8 JSON，顶层仅 `coaches` 和 `documents`，参考 `data/demo/dataset.json`。以下内容可以直接交给数据生成 AI：

> 为健身教练选择助手生成虚构演示资料。不要使用真实客户信息，不捏造真实机构核验、医疗诊断或有效医疗资质。输出符合下方格式的 UTF-8 JSON，不输出 Markdown 围栏。每位教练的 ID 稳定唯一；价格、区域、授课方式、课程和档期放在 coaches，教学理念/案例/平台知识写在 documents.content。每份文档只能属于一个 coach_id，平台资料用 null。演示数据 is_demo=true。各教练的价格、时段、区域、目标和资料充分程度应存在差异；部分偏好缺资料时明确说明未核实，而非编造。所有案例标为虚构，案例不能推导医疗资质或成功概率。预算等数据库事实不可在文档中当旧版本报价返回。

```json
{
  "coaches": [
    {
      "id": "coach-101",
      "name": "虚构教练甲",
      "price": 280,
      "districts": ["南山区"],
      "delivery_modes": ["线下"],
      "specialties": ["减脂", "新手入门"],
      "qualifications": [{"title":"演示训练指导证书（虚构）","verified":false,"scope":"非医疗资质"}],
      "courses": [{"name":"基础私教课","duration_minutes":60,"price":280}],
      "availability": [{"days":[0,2,4],"start":18,"end":22}],
      "bio": "用于流程演示的虚构教练。",
      "is_demo": true
    }
  ],
  "documents": [
    {
      "id": "coach-101-teaching",
      "coach_id": "coach-101",
      "title": "虚构教练甲教学资料",
      "source": "demo://coaches/coach-101/teaching",
      "document_type": "teaching",
      "version": "1",
      "content": "# 教学方式\n\n面向新手提供动作讲解，逐步示范并记录训练反馈。以上为虚构教学样例。",
      "is_demo": true
    },
    {
      "id": "platform-course-101",
      "coach_id": null,
      "title": "平台基础课程（演示）",
      "source": "demo://platform/course-101",
      "document_type": "platform",
      "version": "1",
      "content": "# 课程安排\n\n这是虚构平台课程样例。每周安排两次基础力量训练，由教练根据训练反馈调整强度。",
      "is_demo": true
    }
  ]
}
```

- ID 只允许字母/数字/下划线/短横线，长度≤64；文档 ID、教练 ID 均不得重复。
- `price` 每节课人民币单价，必须非负；地区须规范化，例如“南山区”，不要混用“南山”。
- 时区 Asia/Hong_Kong，`days` 是周一0到周日6，`start/end` 是整点24小时制，`start < end`。
- `delivery_modes` 只允许“线上”“线下”；线上教练也需 `districts` 非空，可写“线上”并说明服务方式。
- `content` 使用 `#`/`##` 标题和空行分段；建议每个章节一个主题。导入自动切父子块，不需要生成向量/父子 ID。
- `source` 要能辨认实际原始资料，可用内部路径/URL/`demo://`。UI 不会自动下载第三方来源，查看原文直接读取 SQL 父块。
- 推荐每位教练至少一篇教学方式和一篇案例，主题类型使用 `teaching`、`case`；平台资料 `platform`。
- `qualifications` 与 `courses` 是结构化字段，不把虚构证书设为 verified=true。上线前应额外建立真实审核流程，目前不实现认证核验。
- 同一文档内容/标题/归属变化必须将 `version` 增为新值；同 ID 同内容重复导入不会重复插入，旧父块下架，新子块重新索引。

## 导入命令

```powershell
cd D:\help-RAG
$env:PYTHONPATH='backend'
$env:PYTHONIOENCODING='utf-8'
# 先只校验格式
.venv\Scripts\python.exe -m scripts.import_data data\your-dataset.json --validate-only
# 合并导入，已有同ID更新，其他数据保留
.venv\Scripts\python.exe -m scripts.import_data data\your-dataset.json
# 仅写SQL，之后再统一重建真实索引
.venv\Scripts\python.exe -m scripts.import_data data\your-dataset.json --sql-only
.venv\Scripts\python.exe -m scripts.import_data --reindex
```

## 内置生成器（大批量虚构数据）

按数量生成互不相同的虚构教练，确定性可复现（同一参数结果一致）。

```powershell
# 首批：生成 50 位到 data/demo/catalog-50.json（ID 为 coach-01..coach-50）
.venv\Scripts\python.exe -m scripts.generate_catalog --count 50 --output data\demo\catalog-50.json
# 覆盖导入并重建真实索引
.venv\Scripts\python.exe -m scripts.import_data data\demo\catalog-50.json --replace
```

**追加一批（不覆盖已有数据）**：用 `--offset`（姓名池下标 = 已有教练数）、`--id-start`（新编号起点）、`--price-shift`（价格网格错位，避免与既有教练撞价）、`--no-platform`（不重复输出平台文档）。**追加时不要加 `--replace`**，否则会把文件外的既有文档下架。

```powershell
# 已有 50 位时，再追加 40 位 → coach-51..coach-90，并与既有价格错开
.venv\Scripts\python.exe -m scripts.generate_catalog --count 40 --offset 50 --id-start 51 --price-shift 5 --no-platform --output data\demo\catalog-40-add.json
.venv\Scripts\python.exe -m scripts.import_data data\demo\catalog-40-add.json
```

- 维度差异：价格、区域（8 个）、专长目标（16 个）、授课方式、档期（16 种窗口）两两组合互不相同。每批内价格唯一，`--price-shift` 让不同批次之间也不撞价。
- 资料充分度：每批都刻意留出 **无任何文档** 与 **仅有教学资料** 的教练，用于验证“资料不足如实标注”。首批沿用固定集合以保证 `catalog-50.json` 可复现；追加批次按比例生成。
- 文档 `version` 为 `2`，用于**原地替换**同 ID 的旧演示文档；平台文档沿用 `platform-*` ID 以便评估集继续引用，且只在首批输出一次。
- **注意**：`data/demo/dataset.json`（16 位）是测试与固定评估的 fixture，与目录文件共用 ID 前缀，**混用会互相覆盖**；替换业务库请只用目录文件。


`--replace` 会将文件中未出现的全部知识文档下架，但保留教练记录；只在你确实要用完整文件替换当前知识库时使用。默认不会删除未出现的教练。索引同步会重建当前有效子块，知识导入期间真实查询返回“正在同步”，不会返回部分旧索引。

如要移除教练，需要先确认没有现行文档和历史会话引用，另行做业务数据维护；当前 CLI 不直接删除教练。演示数据可在生成自己的同 ID 数据时覆盖，也可在独立数据库中导入新数据。

更改模型/维度时：设置新的 `MILVUS_COLLECTION`，然后 `--reindex`。旧集合保留供人工管理，不偷偷删除其他集合。
