# 冰川洞穴染料示踪 · 全局最小汇流树

逐点选择最低误差入口可能闭合成局部循环；本系统用 **Chu–Liu / Edmonds 最小树形图算法**
在全局范围内求一棵以注入根为源、覆盖全部采样点、总代价精确最小的有向汇流树，
并在同优树中输出规范解。

## 功能

- **精确最小化**：手工实现 Edmonds 算法（`api/app/arborescence.py`），不调用任何现成图优化库；
  含 500 组随机图与暴力枚举对拍的单元测试。
- **字典序规范树**：同优树中按「升序通道标识序列」取字典序最小者
  （按标识升序逐个贪心强制入选，验证强制后最优代价不变）。
- **可复算记录**（只凭输入与随结果公开的证据即可逐步重放）：
  - `record.rulings`：按标识升序逐条给出规范裁决——试探强制该通道后的
    最小总代价（`forced_cost`，无解为 `null`）与接受/拒绝结论，
    接受恰当 `forced_cost == C*`；接受集就是最终规范树；
  - 每一收缩层各点的选入通道 `reason`（`unique` 唯一最低有效代价 /
    `canonical_ruling` 同价候选中规范惩罚最低且唯一 /
    `tie_break_id` 同价同惩罚取最小标识），并在同价时公开全部候选
    及其本层有效代价与规范惩罚；
  - 每次有向环收缩（环节点/环边、超点、入边有效代价 `w − w*(v)` 与
    规范惩罚 `pen(e) − pen*(v)` 的修正、丢弃的环内边）；
  - 每次展开替换（进入通道、进入点、被替换环边、保留环边）。
  - `replay_record()` 以独立消费者视角从输入重算上述全部中间量，
    逐层核对候选、裁决、收缩修正与展开替换，再做结构校验并重算出同一棵树；
    伪造任意选择、候选或裁决都会被拒绝。
- **失败诊断**：输入错误（标识、数量、自环、重复、负代价等）返回逐条原因；
  无解时返回全部**不可达点**与说明。页面在失败后**保留全部输入**。
- **联动页面**：React + SVG 网络图，规范树高亮、逐边代价、树边证据标签；
  点击收缩 / 展开记录即在图上联动高亮环节点、进入通道与被替换环边。
- **容器化**：Web 与 API 各自独立 Dockerfile，Docker Compose 编排，
  宿主端口可经 `WEB_PORT` / `API_PORT` 配置，两个服务均带健康检查；
  `verify` 一次性服务运行测试、前后端构建与真实 API/页面核对后以退出码报告。

## 目录

```
api/                 FastAPI 后端
  app/arborescence.py  Edmonds 求解 + 校验 + 记录复算（无第三方图库）
  app/main.py          /api/health、/api/solve
  tests/               单元测试（含暴力对拍）+ API 测试
  Dockerfile
web/                 React + Vite 前端
  src/lib/             输入解析、确定性布局（纯函数，均带测试）
  src/components/      网络图、记录面板、树表面板、输入面板
  Dockerfile           多阶段构建，nginx 同源反代 /api
verify/              一次性核对服务
  verify.py            真实 API 与页面结果核对 + HTTP/页面冒烟
  run.sh               后端测试 → 前端测试 → 前端构建 → API/页面核对
  Dockerfile
docker-compose.yml
```

## 运行

```bash
# 常驻服务（默认页面 http://localhost:8080 ，API http://localhost:8000 ）
docker compose up --build

# 自定义宿主端口
WEB_PORT=18080 API_PORT=18000 docker compose up --build

# 一次性核对（单元测试、前端测试与构建、真实 API/页面样例核对与冒烟）
docker compose run --build --rm verify
echo $?   # 0 表示全部通过
```

页面与 API 同源：nginx 把 `/api/*` 反代到 `api:8000`，浏览器不跨域。

## 接口

`POST /api/solve`

```json
{
  "points": ["r", "a", "b"],
  "root": "r",
  "channels": [
    {"id": "e1", "from": "r", "to": "a", "cost": 5},
    {"id": "e2", "from": "a", "to": "b", "cost": 1}
  ]
}
```

约束：2–40 个唯一可打印 ASCII 点；根必须属于点集；至多 160 条通道，
标识唯一、代价为非负整数、允许平行通道、禁止自环。

成功响应含 `total_cost`、`tree`（逐边代价）、`canonical_ids` 与
`record.levels` / `record.expansions` / `record.rulings`；无解返回
`status: "unsolvable"` 与 `unreachable`；输入错误返回 HTTP 422、
`status: "invalid"` 与 `errors`。

## 算法要点

1. 每个非根点取有效代价最小的入边（同价时由规范惩罚与标识兜底，见第 5 点）；
   若所选边不成环，即为最优树。
2. 出现有向环时收缩为超点，进入环的边有效代价修正为 `w(e) − w*(enters)`，
   递归求解。
3. 自叶向上展开：以下层解中进入超点的通道进入环内对应节点，断开该节点原环边，
   保留其余环边——代价变化与收缩修正严格抵消。
4. 嵌套环由递归自然处理；每次收缩与展开都写入记录。
5. 规范解：先求最优代价 C*，再按标识升序逐条试探强制入选，
   仅当强制后最优代价仍为 C* 时接受（每步接受/拒绝连同 `forced_cost`
   作为 `record.rulings` 公开）；最后以 `(代价, 规范惩罚, 标识)`
   为边键重跑 Edmonds，其中规范惩罚完全由公开裁决接受集决定
   （接受 0 / 拒绝 1），收缩层做 `pen(e) − pen*(v)` 修正。
   因此最终运行中每个点的选入通道都能用「有效代价 → 规范惩罚 → 标识」
   的公开定夺规则复算，无需获知任何内部边键。
