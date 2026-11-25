# 多维指标分析与实验评估平台

面向业务指标口径管理、多维分析查询、AB实验评估、分群圈选和数据服务发布的分析应用平台

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8012/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5176/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8012`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 指标口径 | `metric_def` | 指标定义 | 指标标识、指标名称、计算公式 |
| 多维查询 | `dim_query` | 查询方案 | 方案标识、查询指标、维度行 |
| 实验管理 | `experiment` | 实验方案 | 方案标识、实验名称、对照组 |
| 显著性检验 | `significance` | 检验任务 | 任务标识、关联实验、检验方法 |
| 人群圈选 | `cohort` | 人群分群 | 分群标识、分群名称、圈选条件 |
| 数据服务 | `data_service` | 服务接口 | 接口标识、关联指标、接口路径 |
| 异常检测 | `anomaly_detect` | 检测规则 | 规则标识、关联指标、检测算法 |
| 分析图表 | `analysis_chart` | 图表配置 | 图表标识、数据来源、图表类型 |
| 预聚合任务 | `aggregate_job` | 聚合任务 | 任务标识、目标指标、聚合维度 |
| 下钻分析 | `drill_down` | 下钻路径 | 路径标识、起始指标、下钻维度 |
| 数据导出 | `data_export` | 导出任务 | 任务标识、查询方案、导出格式 |
| 数据时效 | `data_freshness` | 时效检查 | 检查标识、数据来源、期望时效 |
| 查询缓存 | `query_cache` | 缓存策略 | 策略标识、关联查询、缓存键 |
| 分群对比 | `segment_compare` | 对比方案 | 方案标识、分群A、分群B |
| 指标校准 | `metric_calibrate` | 校准记录 | 记录标识、校准指标、原口径值 |
| 查询日志 | `query_log` | 查询记录 | 记录标识、查询内容、查询用户 |
| 数据采样 | `data_sample` | 采样配置 | 配置标识、数据来源、采样方法 |
| 报告调度 | `report_schedule` | 报告任务 | 任务标识、报告内容、接收人 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 实验名称关联迁移演练（独立情景，不影响18个业务模块）

解决「存量显著性检验只按实验显示名关联、两个同名实验结果会混淆」的上线问题：
新版只认稳定实验标识 `exp_code` 与组别标识 `group_code`，由**组别版本映射表**
登记新版评估依赖的实验/组别/版本；存量能唯一证明归属的才迁移，无法证明的
保留旧记录待人工核查，绝不猜组。

### 演练情景

两个同名实验（显示名均为「首页推荐流改版实验」）：

| 版本 | 实验标识 | 存量关联 | 迁移判定 |
| --- | --- | --- | --- |
| v1（A） | `EXP-HOME-001` | 带可核对组别号 G01 | 组别证据底账可核实，登记映射 |
| v2（B） | `EXP-HOME-002` | 只写显示名、无组别号 | 保留旧记录待核查，补齐证据后再迁移 |

### 关键规则与接口（前缀 `/api/migration`）

- `POST /run`：执行/重试迁移。迁移台账按存量检验唯一、样本按作用域唯一，
  同一批次重试幂等，换批次也不会把一条历史检验或一个样本记到两个新版批次。
- `POST /links/{id}/complete-group`：人工核查补齐 B 的组别证据，回填稳定标识。
- `POST /evaluation/submit`：新版评估先查组别版本映射表决定能否领取样本；
  映射缺失时返回明确的未受理原因；幂等键防重复受理，样本被其他批次领取时逐条退回。
- `POST /evaluation/{batch_no}/verify`：回滚后按原凭据复核，仍能查到所用组别和样本。
- `POST /experiment/rename`：实验改名只改显示名，稳定标识与映射不变，
  旧名登记别名，`GET /legacy/search` 按旧名仍能查历史检验。
- `POST /legacy/evaluate`：旧版应急入口，同名实验必须显式指定版本，
  不指定版本直接拒绝（`AMBIGUOUS_NAME`），结果按版本隔离不混淆。
- `POST /entry-mode`：值班员在新版/旧版入口间应急切换并留痕；
  回滚到旧版后新版评估暂停受理（`ENTRY_LEGACY_MODE`）。

前端入口：侧边栏「实验关联迁移演练」（路由 `/migration-drill`）。
命令行一键演练：`cd backend && .venv/bin/python -I scripts/migration_drill.py`。
相关代码：`backend/app/migration_seed.py`、`migration_store.py`、
`services/migration.py`、`routers/migration.py`、`tests/test_migration.py`。

领域：数据工程
子方向：数据分析与数据应用
