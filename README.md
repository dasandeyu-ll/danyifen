# 单易分 · 基于目标识别的智能工单分拣台

一个"客服工单驾驶舱"：左边是待处理工单列表，点进去右边是 Agent 的分析卡片 ——
识别出的**多标签意图**、抽出的**槽位**、带阈值刻度的**置信度条**；
置信度低的标黄并转人工，人工确认后工单一键分拣。
顶部是数据看板：意图分布饼图、分拣准确率仪表盘、缺槽位榜单。

## 架构一览

```
前端（React 18 + Vite + ECharts）
  │  REST /api/*（开发由 Vite 反代，生产由 nginx 反代 —— 同一套相对路径）
  ▼
后端（FastAPI）
  ├── GET/POST /tickets            工单 CRUD（SQLite，Goal 存 JSON 快照 + 索引列冗余）
  ├── POST /tickets/analyze        两阶段：多标签意图分类 → 结构化槽位抽取
  ├── POST /tickets/confirm        人工确认：approve / correct / reject
  ├── POST /tickets/advance        状态机推进一步（非法迁移返回 409）
  ├── GET  /api/meta               意图词典 / 槽位 schema / 色板 / 阈值 / 状态机图 —— 前端零硬编码
  ├── GET  /stats                  意图分布 + 质检准确率（供看板）
  ├── POST /seed                   灌入 14 条演示工单
  └── POST /eval/run               跑 200 条评测集，返回意图准确率 / 槽位 F1
            │
            ▼
   LangGraph 状态机
   split（单句多目标拆分）→ classify（多标签分类）→ extract（槽位抽取）
        → verify（校验 + 双引擎交叉验证 + 置信度校准）
        → ask（追问）/ ready（就绪）/ human（转人工）
```

### 目标生命周期状态机（核心领域模型）

一条客服留言不是"一个意图"，而是可以拆成 **N 个 Goal**，每个 Goal 独立走状态机：

```
pending ──> asking ──> ready ──> done
   │           ▲         │
   └───────────┴─────────┘   低置信度挂 need_human 标记（状态不变，UI 标黄等人工）
```

- **pending**：刚进来 / 槽位齐但置信度不够（不能直接 `pending → done`，状态机层面禁掉）
- **asking**：缺必需槽位，Agent 自动生成追问话术（追问话术模板由槽位名驱动，可配置）
- **ready**：槽位齐 + 置信度够，一键分拣到 **done**
- **done**：允许 `done → ready` 撤销分拣

全部由 Pydantic 建模（`backend/app/schemas.py`），迁移合法性写成数据表。工单的状态永远由它的目标**推导**出来，杜绝"工单说 done、目标还没执行"的脏数据。

## 3 分钟跑通

### 方式 A：Docker 一键启动（推荐演示路径）

```bash
docker compose up --build
# 打开 http://localhost:5173
# 后端文档：http://localhost:8000/docs
```

### 方式 B：本地开发（前后端分离）

```bash
# 后端（需 Python 3.10+）
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8000

# 前端（另开一个终端，需 Node 18+）
cd frontend
npm install
npm run dev
# 打开 http://127.0.0.1:5173
```

> 不需要配任何 API Key 也能完整运行：默认 `ANALYZER_MODE=auto`，没有 `DEEPSEEK_API_KEY`
> 时自动降级到内置规则引擎。填上 key 后系统会用 DeepSeek 做两阶段识别，
> 并把"LLM 和规则是否一致"作为置信度证据展示在分析卡片上。

### 演示剧本（3 分钟）

1. 点 **灌入演示数据** → 14 条中文工单自动被分析（单目标、缺槽位、多目标、模糊表达各有代表）。
2. 点一条**标黄的"需确认"工单**（如"你们家的物流速度很快…点个赞"）：
   看到意图打架（物流查询 58% vs 建议 46%）、红色置信度条、追问话术、完整执行链路。
3. 点 **补齐信息并确认** → 修正意图为"建议"提交 → 目标 `asking → ready`。
4. 点 **分拣执行** → `ready → done`，看板上的"工单状态分布"与"分拣准确率"同步变化。
5. 右下角点 **运行评测** → 看到分层指标：调优集 ~100%、改写留出集 ~83%。

## 自建评测集与指标

`backend/app/eval/`：

| 文件              | 说明                                                                 |
|-------------------|----------------------------------------------------------------------|
| `build_dataset.py`| 确定性生成 200 条中文工单（seed 固定，可复现），按 5 组标签配比      |
| `dataset.jsonl`   | 生成产物，落盘可版本管理                                             |
| `run_eval.py`     | 算法：样本级意图准确率 / 槽位微平均 P-R-F1 / 拆分准确率 / 转人工召回  |

关键设计：**分层报告**。100 条单目标 + 40 条多目标是按表达模板生成的（规则引擎天然全对，
这组只能证明"实现没写错"）；真正的泛化能力看 **30 条刻意避开规则词典用词的改写留出集**。
当前规则引擎实测：总体意图准确率 97.2%（调优集 100.0% / 留出集 83.3%），
槽位 F1 0.977，转人工召回 100%，追问召回 100%。

> 报道数字时请同时引用两个数字。只报"97%"是在报训练集的效果；
> 83%→ 上 DeepSeek 后预期抬升 —— 这个"落差"恰好论证了大模型的必要性。

## 目录结构

```
.
├── docker-compose.yml          一键启动
├── .env.example                配置模板
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py             FastAPI 接口（CRUD / 分析 / 确认 / 统计 / 评测）
│   │   ├── schemas.py          Pydantic 领域模型：Goal 全生命周期 + 状态机迁移表
│   │   ├── db.py               SQLite 持久层（JSON 快照 + 索引列冗余 + 极简迁移）
│   │   ├── seed.py             14 条演示工单
│   │   ├── agent/
│   │   │   ├── rules.py        离线规则分析器：带权词典 + 正则槽位 + 证据量校准置信度
│   │   │   ├── llm.py          DeepSeek 客户端：JSON 模式 + Pydantic 校验 + 失败降级
│   │   │   ├── prompts.py      两阶段系统提示词（分类 / 抽槽独立维护，带版本号）
│   │   │   └── graph.py        LangGraph 状态机 + 无 LangGraph 时的等价线性执行器
│   │   └── eval/               数据集生成器 + 评测执行器 + dataset.jsonl
│   └── tests/
│       └── smoke_test.py       32 项端到端断言（识别 → 确认 → 推进 → 统计 → 评测）
└── frontend/
    ├── Dockerfile / nginx.conf 构建 + 反代
    ├── vite.config.js          /api 代理，dev 与生产同一套相对路径
    └── src/
        ├── App.jsx             数据编排（拉取 /api/meta 做配置化渲染）
        ├── api.js              统一的后端访问层
        ├── components/
        │   ├── StatsBoard.jsx  KPI + 意图饼图 + 准确率仪表盘 + 状态条 + 缺槽位榜单
        │   ├── TicketList.jsx  工单列表（状态筛选 / 搜索 / 置信度预览）
        │   ├── TicketDetail.jsx 详情容器（状态机 + 目标卡片 + 执行链路）
        │   ├── GoalCard.jsx    分析卡片（多标签意图 + 槽位 + 置信度 + 人工确认）
        │   ├── Pipeline.jsx    状态机可视化 + Agent 执行链路
        │   ├── EvalPanel.jsx   评测分层报告面板
        │   ├── NewTicketModal.jsx 新建工单 + 不落库试算
        │   ├── EChart.jsx      ECharts 极简 React 封装（实例复用 + 自适应缩放）
        │   └── atoms.jsx       通用小组件（标签 / 进度条 / 提示）
        └── styles.css          深色驾驶舱主题
```

## 环境变量

| 变量                   | 默认        | 说明                                  |
|------------------------|-------------|---------------------------------------|
| `DEEPSEEK_API_KEY`     | 空          | 为空自动降级到规则引擎，照样整站可跑  |
| `ANALYZER_MODE`        | `auto`      | `auto` / `rule` / `llm`               |
| `DEEPSEEK_BASE_URL`    | 官方地址    | 可换成兼容 OpenAI 协议的中转地址      |
| `CONFIDENCE_THRESHOLD` | `0.62`      | 低于该置信度自动转人工                |
| `MARGIN_THRESHOLD`     | `0.18`      | 头两名意图分差占比低于此值也转人工    |
| `DB_PATH`              | 数据目录    | SQLite 文件路径（Docker 里挂具名卷）  |

## 测试

```bash
cd backend
python tests/smoke_test.py   # 32 项：识别 → 确认 → 非法迁移拒绝 → 统计 → 评测
python -m app.eval.run_eval rule
```
