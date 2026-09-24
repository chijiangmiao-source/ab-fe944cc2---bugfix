"""最小汇流树（最小树形图）求解器 —— Chu–Liu/Edmonds 算法的手工实现。

不依赖任何现成图优化库。给定注入根与带非负整数代价的有向通道，
求一棵以根为源、覆盖全部采样点、总代价最小的汇流树（每个非根点
恰有一条入选通道且自根可达）。同优解中按"升序通道标识序列"取字典序
最小者（规范树）。

除规范树外，本模块还产出可复算的有向环收缩 / 展开记录：
  * 每一层为每个非根点列出全部同（有效）价最低入口候选及其规范惩罚，
    并记录最终选入通道与裁决依据（唯一项 / 规范惩罚最小 / 标识兜底），
    因此每个选择仅凭输入代价、标识与随结果公开的规范裁决即可逐步复算；
  * 每次有向环收缩（环节点、环边、超点编号、入边代价修正、被丢弃的环内边）；
  * 每次展开替换（进入通道、进入点、被替换的环边、保留的环边）。

规范树的求得分两步：
  1. 用 Edmonds 求出最小总代价 C*；
  2. 按通道标识升序逐个尝试"强制入选"：若强制后仍存在代价为 C* 的
     汇流树，则强制之。可证明最终强制集本身就是字典序最小的最优树。
     每次接受 / 拒绝都记录为公开的「规范裁决」，其可行性均以 C* 为据。
最后用 (代价, 是否规范边, 标识) 作为字典序边键再跑一次 Edmonds，
保证产出的收缩记录恰好对应规范树；该运行中每个点的选入通道都能用
"候选清单 + 规范裁决"公开复算，无需获知任何内部边键。
"""

from __future__ import annotations

from dataclasses import dataclass


class ProblemError(Exception):
    """输入不合法时抛出。errors 为逐条原因列表。"""

    def __init__(self, reason: str, errors: list[str] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.errors = errors or [reason]


@dataclass(frozen=True)
class Channel:
    id: str
    u: str  # 上游点（from）
    v: str  # 下游点（to）
    cost: int


@dataclass
class _Edge:
    """某一收缩层级上的边。orig 始终指向原始通道。"""

    orig: Channel
    u: str  # 当前层级的尾点（可能是超点）
    v: str  # 当前层级的头点（可能是超点）
    key: tuple  # 字典序键 (代价, 惩罚, 标识)
    enters: str  # 在当前层级上本边进入的节点（== v，展开时用来定位环边）
    lower: "_Edge | None"  # 收缩前一层对应的边；原始层为 None


# ---------------------------------------------------------------------------
# 输入校验
# ---------------------------------------------------------------------------

_MAX_POINTS = 40
_MIN_POINTS = 2
_MAX_CHANNELS = 160
_MAX_ID_LEN = 32


def _is_ascii_id(value: str) -> bool:
    if not isinstance(value, str) or not (1 <= len(value) <= _MAX_ID_LEN):
        return False
    # 可打印 ASCII（不含空白），便于排序与展示
    return all(0x21 <= ord(ch) <= 0x7E for ch in value)


def validate_problem(
    points: list[str], root: str, channels: list[Channel]
) -> None:
    """收集全部输入错误；有任何错误即抛 ProblemError。"""
    errors: list[str] = []

    if not (_MIN_POINTS <= len(points) <= _MAX_POINTS):
        errors.append(
            f"采样点数量须为 {_MIN_POINTS}..{_MAX_POINTS}，实际为 {len(points)}"
        )
    seen: set[str] = set()
    for p in points:
        if not _is_ascii_id(p):
            errors.append(f"采样点标识非法（须为 1..{_MAX_ID_LEN} 个可打印 ASCII 字符）: {p!r}")
        elif p in seen:
            errors.append(f"采样点标识重复: {p!r}")
        seen.add(p)

    if not _is_ascii_id(root):
        errors.append(f"注入根标识非法: {root!r}")
    elif root not in seen:
        errors.append(f"注入根 {root!r} 不在采样点集合中")

    if len(channels) > _MAX_CHANNELS:
        errors.append(f"通道数量至多为 {_MAX_CHANNELS}，实际为 {len(channels)}")

    chan_ids: set[str] = set()
    for c in channels:
        if not _is_ascii_id(c.id):
            errors.append(f"通道标识非法: {c.id!r}")
        elif c.id in chan_ids:
            errors.append(f"通道标识重复: {c.id!r}")
        chan_ids.add(c.id)
        if c.u not in seen:
            errors.append(f"通道 {c.id!r} 的起点 {c.u!r} 不是已知采样点")
        if c.v not in seen:
            errors.append(f"通道 {c.id!r} 的终点 {c.v!r} 不是已知采样点")
        if c.u == c.v:
            errors.append(f"通道 {c.id!r} 是自环（{c.u!r} -> {c.v!r}），不被允许")
        if not isinstance(c.cost, int) or isinstance(c.cost, bool) or c.cost < 0:
            errors.append(f"通道 {c.id!r} 的代价须为非负整数，实际为 {c.cost!r}")

    if errors:
        raise ProblemError("输入不合法", errors)


# ---------------------------------------------------------------------------
# Edmonds 核心（带收缩 / 展开记录）
# ---------------------------------------------------------------------------


def _fresh_supernode(idx: int, used: set[str]) -> str:
    name = f"S{idx}"
    n = idx
    while name in used:
        n += 1
        name = f"S{n}"
    return name


def _min_incoming(edges: list[_Edge], v: str) -> list[_Edge]:
    """头点为 v、在本层有效代价（key[0]）上同为最低的全部入边。

    同价候选按完整键排序（规范运行中即 (有效代价, 规范惩罚, 标识)），
    因此首个元素就是算法实际选入的边；其余元素是仅凭公开代价
    无法区分、需要规范裁决信息的同价候选。无入边返回空表。
    """
    best_cost = None
    winners: list[_Edge] = []
    for e in edges:
        if e.v != v:
            continue
        if best_cost is None or e.key[0] < best_cost:
            best_cost = e.key[0]
            winners = [e]
        elif e.key[0] == best_cost:
            winners.append(e)
    winners.sort(key=lambda e: e.key)
    return winners


def _selection_outcome(cands: list[_Edge]) -> tuple[str, _Edge]:
    """在同（有效）价最低入口候选中，依据公开的 (规范惩罚, 标识) 定夺选入边。

    规范惩罚完全由公开的裁决接受集决定（接受 0 / 拒绝 1，收缩层做
    pen(e) − pen*(v) 修正），因此该定夺无需任何内部信息：

      unique            —— 唯一最低有效代价入口；
      canonical_ruling  —— 同价候选中规范惩罚最小者唯一（即取裁决接受通道，
                           或取替换后净增被裁决接受环边最少的入口）；
      tie_break_id      —— 同价同惩罚者多个，按标识升序取最小。
    """
    if len(cands) == 1:
        return "unique", cands[0]
    min_pen = min(e.key[1] for e in cands)
    pen_winners = sorted((e for e in cands if e.key[1] == min_pen), key=lambda e: e.key)
    if len(pen_winners) == 1:
        return "canonical_ruling", pen_winners[0]
    return "tie_break_id", pen_winners[0]


def _find_cycle(nodes: list[str], root: str, in_edge: dict[str, _Edge]) -> list[str] | None:
    """在每个非根点恰有一条选中入边的函数图上找一个有向环。

    返回环节点列表（沿选中入边逆向行走的顺序），无环返回 None。
    """
    for start in sorted(nodes):
        if start == root:
            continue
        seen: dict[str, int] = {}
        order: list[str] = []
        cur = start
        while cur != root and cur not in seen:
            seen[cur] = len(order)
            order.append(cur)
            cur = in_edge[cur].u
        if cur != root:
            return order[seen[cur]:]
    return None


def _cycle_forward(cycle: list[str], in_edge: dict[str, _Edge]) -> tuple[list[str], list[str]]:
    """把逆向行走得到的环转换为沿通道方向的展示顺序。

    返回 (节点序列, 通道标识序列)，其中通道 i 从节点 i 指向节点 i+1（末位回绕）。
    """
    # cycle 中 in_edge[cycle[i]].u == cycle[(i+1) % k]
    k = len(cycle)
    nodes_fwd = [cycle[0]] + [cycle[k - 1 - i] for i in range(k - 1)]
    channels_fwd = [in_edge[nodes_fwd[(i + 1) % k]].orig.id for i in range(k)]
    return nodes_fwd, channels_fwd


def _solve_level(
    nodes: list[str],
    root: str,
    edges: list[_Edge],
    depth: int,
    levels: list[dict],
    expansions: list[dict],
    sup_counter: list[int],
) -> list[_Edge] | None:
    """在当前层级上求解；返回以本层 _Edge 表示的入选边，无解返回 None。

    levels / expansions 收集可复算记录。最终规范运行中边键已含
    (有效代价, 规范惩罚, 标识)，每个选择都能凭公开信息复算。
    """
    in_edge: dict[str, _Edge] = {}
    chosen_rec: list[dict] = []
    for n in sorted(nodes):
        if n == root:
            continue
        cands = _min_incoming(edges, n)
        if not cands:
            return None  # 某点无入边：本层不可解
        reason, chosen = _selection_outcome(cands)
        in_edge[n] = chosen
        rec = {
            "node": n,
            "channel": chosen.orig.id,
            "cost": chosen.key[0],
            "penalty": chosen.key[1],
            "reason": reason,
        }
        if len(cands) > 1:
            # 公开本层全部同（有效）价最低入口及其规范惩罚分量。
            # 有效代价 = 本层边键的代价分量（收缩层为 w − w* 修正代价，
            # 可由上一层 rewired_in 复算）；规范惩罚 = 同层惩罚分量
            # （0 裁决接受 / 1 裁决拒绝，收缩层为 pen(e) − pen*(v)）。
            # 定夺规则：先取有效代价最小，再取规范惩罚最小且唯一，
            # 仍并列时取标识最小——全部可由输入与公开裁决复算。
            rec["candidates"] = sorted(
                (
                    {
                        "channel": e.orig.id,
                        "cost": e.key[0],
                        "penalty": e.key[1],
                    }
                    for e in cands
                ),
                key=lambda x: (x["cost"], x["penalty"], x["channel"]),
            )
        chosen_rec.append(rec)

    level_rec = {
        "depth": depth,
        "nodes": sorted(nodes),
        "chosen": chosen_rec,
        "cycle": None,
    }
    levels.append(level_rec)

    cycle = _find_cycle(nodes, root, in_edge)
    if cycle is None:
        return [in_edge[n] for n in sorted(nodes) if n != root]

    sup_counter[0] += 1
    used = set(nodes) | {e.orig.id for e in edges}
    sname = _fresh_supernode(sup_counter[0], used)
    cyc = set(cycle)

    nodes_fwd, channels_fwd = _cycle_forward(cycle, in_edge)
    new_edges: list[_Edge] = []
    rewired_in: list[dict] = []
    rewired_out: list[dict] = []
    dropped: list[str] = []
    cycle_edge_ids = {id(in_edge[v]) for v in cycle}

    for e in edges:
        u_in, v_in = e.u in cyc, e.v in cyc
        if u_in and v_in:
            if id(e) not in cycle_edge_ids:
                dropped.append(e.orig.id)
            continue
        if v_in:
            base = in_edge[e.v].key
            nkey = (e.key[0] - base[0], e.key[1] - base[1], e.key[2])
            new_edges.append(_Edge(e.orig, e.u, sname, nkey, enters=e.v, lower=e))
            rewired_in.append(
                {
                    "channel": e.orig.id,
                    "from": e.orig.u,
                    "to": e.orig.v,
                    "original_cost": e.orig.cost,
                    "level_cost": e.key[0],
                    "adjusted_cost": nkey[0],
                    "level_penalty": e.key[1],
                    "adjusted_penalty": nkey[1],
                    "enters": e.v,
                }
            )
        elif u_in:
            new_edges.append(_Edge(e.orig, sname, e.v, e.key, enters=e.v, lower=e))
            rewired_out.append(
                {
                    "channel": e.orig.id,
                    "from": e.orig.u,
                    "to": e.orig.v,
                    "cost": e.key[0],
                    "penalty": e.key[1],
                }
            )
        else:
            # 未受影响的边也必须包一层，保证每条新层级的边恰有一级 lower
            # 指向本层——否则展开时会越过本层直接解包到外层。
            new_edges.append(_Edge(e.orig, e.u, e.v, e.key, enters=e.v, lower=e))

    level_rec["cycle"] = {
        "nodes": nodes_fwd,
        "channels": channels_fwd,
        "supernode": sname,
        "rewired_in": rewired_in,
        "rewired_out": rewired_out,
        "dropped_internal": sorted(dropped),
    }

    new_nodes = [n for n in nodes if n not in cyc] + [sname]
    sub = _solve_level(
        new_nodes, root, new_edges, depth + 1, levels, expansions, sup_counter
    )
    if sub is None:
        return None

    entering = [e for e in sub if e.v == sname]
    # 超点非根，下一层解中恰有一条边进入它
    assert len(entering) == 1, "收缩层解中超点入边数量不为 1"
    entering_edge = entering[0]
    removed = in_edge[entering_edge.enters]
    kept = [in_edge[v] for v in cycle if v != entering_edge.enters]

    expansions.append(
        {
            "supernode": sname,
            "entering_channel": entering_edge.orig.id,
            "enters_node": entering_edge.enters,
            "removed_cycle_channel": removed.orig.id,
            "kept_cycle_channels": [e.orig.id for e in kept],
        }
    )

    result: list[_Edge] = []
    for e in sub:
        result.append(e.lower if e.lower is not None else e)
    result.extend(kept)
    return result


def _edmonds(
    nodes: list[str], root: str, edges: list[_Edge]
) -> tuple[list[_Edge], list[dict], list[dict]] | None:
    """完整 Edmonds 运行；返回 (入选边, 层级记录, 展开记录) 或 None。"""
    levels: list[dict] = []
    expansions: list[dict] = []
    picked = _solve_level(list(nodes), root, edges, 0, levels, expansions, [0])
    if picked is None:
        return None
    return picked, levels, expansions


# ---------------------------------------------------------------------------
# 强制入选约束下的最小代价（用于规范树的字典序贪心）
# ---------------------------------------------------------------------------


class _DSU:
    def __init__(self, items):
        self.parent = {x: x for x in items}

    def find(self, x):
        p = self.parent
        while p[x] != x:
            p[x] = p[p[x]]
            x = p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _min_cost_with_forced(
    points: list[str], root: str, channels: list[Channel], forced: list[Channel]
) -> int | None:
    """在必须包含 forced 中全部通道的前提下求最小总代价；不可行返回 None。"""
    parent: dict[str, str] = {}
    for f in forced:
        if f.v == root or f.v in parent:
            return None  # 根不能有入边；每点至多一条入边
        parent[f.v] = f.u
    # 强制边内部不得成环（函数图：每点至多一个父指针，沿链走访判环）
    for start in parent:
        seen: set[str] = set()
        cur = start
        while cur in parent:
            if cur in seen:
                return None
            seen.add(cur)
            cur = parent[cur]

    dsu = _DSU(points)
    for f in forced:
        dsu.union(f.u, f.v)

    comp = {p: dsu.find(p) for p in points}
    head: dict[str, str] = {}
    for p in points:
        if p not in parent:  # 每个连通块恰有一个无强制入边的点（头）
            head[comp[p]] = p

    forced_ids = {f.id for f in forced}
    red_edges: list[_Edge] = []
    for c in channels:
        if c.id in forced_ids:
            continue
        cu, cv = comp[c.u], comp[c.v]
        if cu == cv:
            continue  # 块内边：成环或重复入边
        if head[cv] != c.v:
            continue  # 终点已有强制入边
        red_edges.append(_Edge(c, cu, cv, (c.cost, 0, c.id), enters=cv, lower=None))

    nodes = sorted(set(comp.values()))
    root_comp = comp[root]
    sub = _edmonds(nodes, root_comp, red_edges)
    if sub is None:
        return None
    picked = sub[0]
    return sum(f.cost for f in forced) + sum(e.orig.cost for e in picked)


# ---------------------------------------------------------------------------
# 顶层求解
# ---------------------------------------------------------------------------


def reachable_from(root: str, channels: list[Channel]) -> set[str]:
    adj: dict[str, list[str]] = {}
    for c in channels:
        adj.setdefault(c.u, []).append(c.v)
    seen = {root}
    stack = [root]
    while stack:
        u = stack.pop()
        for v in adj.get(u, ()):
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return seen


def solve(points: list[str], root: str, channels: list[Channel]) -> dict:
    """求解并组装 API 结果。输入须已通过 validate_problem。"""
    reach = reachable_from(root, channels)
    unreachable = sorted(p for p in points if p not in reach)
    if unreachable:
        return {
            "status": "unsolvable",
            "reason": "存在无法自注入根经有向通道到达的采样点，"
            "无法满足“每个非根点恰有一条入选通道并从根可达”。",
            "unreachable": unreachable,
        }

    # 1) 最小总代价
    base_edges = [
        _Edge(c, c.u, c.v, (c.cost, 0, c.id), enters=c.v, lower=None) for c in channels
    ]
    run = _edmonds(list(points), root, base_edges)
    assert run is not None, "全部可达但 Edmonds 无解，内部不一致"
    best_cost = sum(e.orig.cost for e in run[0])

    # 2) 规范树：按标识升序贪心强制入选，并公开每一步裁决
    #    （以最优总代价 best_cost 为唯一判据：试探代价 == best_cost 即接受）。
    forced: list[Channel] = []
    rulings: list[dict] = []
    for c in sorted(channels, key=lambda x: x.id):
        trial_cost = _min_cost_with_forced(points, root, channels, forced + [c])
        accepted = trial_cost == best_cost
        rulings.append(
            {
                "channel": c.id,
                "from": c.u,
                "to": c.v,
                "cost": c.cost,
                "decision": "accepted" if accepted else "rejected",
                "forced_cost": trial_cost,
                "optimal_cost": best_cost,
                "basis": (
                    f"强制 {c.id} 入选后最小总代价仍为 {best_cost}"
                    if accepted
                    else (
                        f"强制 {c.id} 入选后无解（会闭合或冲突）"
                        if trial_cost is None
                        else f"强制 {c.id} 入选后最小总代价升为 {trial_cost}"
                    )
                ),
            }
        )
        if accepted:
            forced.append(c)
    canonical_ids = sorted(c.id for c in forced)

    # 3) 以 (代价, 规范惩罚, 标识) 为键重跑，产出对应规范树的收缩记录。
    #    该内部键不直接公开；其代价与惩罚分量（及收缩修正）全部写入记录，
    #    每个选择改以「有效代价 → 规范惩罚 → 标识」的公开定夺规则复算。
    canon = set(canonical_ids)
    keyed_edges = [
        _Edge(c, c.u, c.v, (c.cost, 0 if c.id in canon else 1, c.id), enters=c.v, lower=None)
        for c in channels
    ]
    final_run = _edmonds(list(points), root, keyed_edges)
    assert final_run is not None
    picked, levels, expansions = final_run
    final_ids = sorted(e.orig.id for e in picked)
    assert final_ids == canonical_ids, "规范树重跑结果与强制集不一致"

    by_id = {c.id: c for c in channels}
    tree = [
        {"id": cid, "from": by_id[cid].u, "to": by_id[cid].v, "cost": by_id[cid].cost}
        for cid in final_ids
    ]
    return {
        "status": "ok",
        "total_cost": best_cost,
        "tree": tree,
        "canonical_ids": final_ids,
        "record": {
            "levels": levels,
            "expansions": expansions,
            "rulings": rulings,
            "contractions": sum(1 for lv in levels if lv["cycle"]),
        },
    }


# ---------------------------------------------------------------------------
# 记录复算（供测试与 verify 服务核对证据链）
# ---------------------------------------------------------------------------


def _detect_cycle(nodes: list[str], root: str, parent: dict[str, str]) -> set[str] | None:
    """与求解器无关地在「每点一条父指针」的函数图上找一个环节点集合。"""
    for start in sorted(nodes):
        if start == root:
            continue
        seen: dict[str, int] = {}
        order: list[str] = []
        cur = start
        while cur != root and cur not in seen:
            seen[cur] = len(order)
            order.append(cur)
            cur = parent[cur]
        if cur != root:
            return set(order[seen[cur]:])
    return None


def replay_record(
    points: list[str], root: str, channels: list[Channel], record: dict
) -> list[str]:
    """仅凭输入与随结果公开的记录，逐步重放全部选择并复算最终入选通道。

    本函数是"独立消费者"视角的审计：除输入代价与标识外只允许使用记录中
    公开的内容（每层同价候选、裁决依据、规范裁决、收缩 / 展开明细），
    不读取求解器任何内部状态。逐步校验：

      1. 规范裁决覆盖全部通道且逐条自洽（accepted ⇔ forced_cost == C*），
         其中 C* 取已接受裁决的强制代价，最终入选集恰为 accepted 集；
      2. 每一层每个点的同（有效）价最低入口候选及其规范惩罚，由本层有效
         代价与由裁决接受集导出的惩罚独立算出，与记录公开的候选清单逐一
         相符；选入通道必能由「唯一最低有效代价 / 规范惩罚最小且唯一 /
         仍并列则标识最小」的公开定夺规则推出；
      3. 环的节点与环边由各点选入通道独立检出，前向顺序逐边邻接；
         收缩后的有效代价 w − w*(v) 与规范惩罚 pen(e) − pen*(v) 均独立
         重算，与 rewired_in / rewired_out 相符，丢弃的环内边同样核对；
      4. 展开替换的进入点、被替换环边、保留环边与收缩层选择一致；
      5. 最终每个非根点恰一条入边、无环、自根可达，总代价等于 C*。

    任何一项不符即抛 AssertionError；通过则返回入选通道标识升序列表。
    """
    levels: list[dict] = record.get("levels", [])
    expansions: list[dict] = record.get("expansions", [])
    rulings: list[dict] = record.get("rulings", [])
    if not levels:
        raise AssertionError("记录缺少层级信息")

    by_id = {c.id: c for c in channels}

    # --- 1) 规范裁决的完整性与逐条自洽 ---
    if len(rulings) != len(channels):
        raise AssertionError(
            f"规范裁决 {len(rulings)} 条，与通道数 {len(channels)} 不符"
        )
    ruling_ids = [r["channel"] for r in rulings]
    if ruling_ids != sorted(by_id):
        raise AssertionError("规范裁决未按标识升序逐条覆盖全部通道")
    accepted: set[str] = set()
    optimal: int | None = None
    for r in rulings:
        cid = r["channel"]
        c = by_id[cid]
        if r.get("from") != c.u or r.get("to") != c.v or r.get("cost") != c.cost:
            raise AssertionError(f"裁决 {cid} 的通道描述与输入不一致")
        if r.get("decision") not in ("accepted", "rejected") or not r.get("basis"):
            raise AssertionError(f"裁决 {cid} 缺少结论或依据")
        fc = r.get("forced_cost")
        if fc is not None and (not isinstance(fc, int) or isinstance(fc, bool)):
            raise AssertionError(f"裁决 {cid} 的 forced_cost 非法")
        if r["decision"] == "accepted":
            accepted.add(cid)
            if fc is None:
                raise AssertionError(f"裁决 {cid} 被接受却缺少强制代价")
            optimal = fc if optimal is None else min(optimal, fc)
        elif fc is not None and fc < 0:
            raise AssertionError(f"裁决 {cid} 的强制代价为负")
    if optimal is None:
        raise AssertionError("没有任何被接受的规范裁决")
    for r in rulings:
        fc = r.get("forced_cost")
        is_accepted = r["decision"] == "accepted"
        if is_accepted != (fc == optimal):
            raise AssertionError(
                f"裁决 {r['channel']} 的结论与强制代价不一致"
                f"（accepted={is_accepted}, forced_cost={fc}, C*={optimal}）"
            )
        if not is_accepted and fc is not None and fc < optimal:
            # 任何可行汇流树的代价都不低于 C*，更小的强制代价不可能成立
            raise AssertionError(
                f"裁决 {r['channel']} 声称强制代价 {fc} < C*={optimal}，与最优代价矛盾"
            )

    # --- 2/3) 逐层重放选择与收缩 ---
    # 活动边：标识 -> [尾点, 头点, 本层有效代价, 本层规范惩罚]；活动点随收缩演进。
    # 初始规范惩罚完全由公开裁决决定：接受 0 / 拒绝 1。
    active: dict[str, list] = {
        c.id: [c.u, c.v, c.cost, 0 if c.id in accepted else 1] for c in channels
    }
    active_nodes: set[str] = set(points)
    chosen_by_level: list[dict[str, tuple[str, int, int]]] = []
    cycle_by_level: list[dict | None] = []
    leaf_selected: set[str] | None = None

    for depth, lv in enumerate(levels):
        if lv.get("depth") != depth:
            raise AssertionError(f"第 {depth} 层 depth 字段不连续")
        if set(lv.get("nodes", [])) != active_nodes:
            raise AssertionError(f"第 {depth} 层节点集合与收缩演进不符")

        chosen_map: dict[str, tuple[str, int, int]] = {}
        for rec in lv.get("chosen", []):
            n = rec.get("node")
            if n not in active_nodes or n == root:
                raise AssertionError(f"第 {depth} 层选择记录了非法节点 {n!r}")
            incoming = [(eid, e) for eid, e in active.items() if e[1] == n]
            if not incoming:
                raise AssertionError(f"第 {depth} 层点 {n} 无入口，记录却给出选择")
            min_eff = min(e[2] for _, e in incoming)
            tie = sorted(eid for eid, e in incoming if e[2] == min_eff)
            min_pen = min(active[cid][3] for cid in tie)
            pen_winners = sorted(cid for cid in tie if active[cid][3] == min_pen)

            if len(tie) == 1:
                expect_reason = "unique"
            elif len(pen_winners) == 1:
                expect_reason = "canonical_ruling"
            else:
                expect_reason = "tie_break_id"
            if rec.get("reason") != expect_reason:
                raise AssertionError(
                    f"第 {depth} 层点 {n} 的裁决依据 {rec.get('reason')!r} 与公开证据不符"
                    f"（应为 {expect_reason!r}）"
                )
            if rec.get("cost") != min_eff:
                raise AssertionError(f"第 {depth} 层点 {n} 的最低有效代价无法复算")

            chosen = rec.get("channel")
            if chosen not in tie:
                raise AssertionError(
                    f"第 {depth} 层点 {n} 的选入通道 {chosen} 不在最低入口候选中"
                )
            if rec.get("penalty") != active[chosen][3]:
                raise AssertionError(
                    f"第 {depth} 层点 {n} 的选入通道规范惩罚无法复算"
                )

            pub_cands = rec.get("candidates")
            if len(tie) == 1:
                if pub_cands is not None:
                    raise AssertionError(f"第 {depth} 层点 {n} 唯一入口却给出候选清单")
            else:
                expect_pub = sorted(
                    (
                        {"channel": cid, "cost": active[cid][2], "penalty": active[cid][3]}
                        for cid in tie
                    ),
                    key=lambda x: (x["cost"], x["penalty"], x["channel"]),
                )
                if pub_cands != expect_pub:
                    raise AssertionError(
                        f"第 {depth} 层点 {n} 的同价候选清单（含规范惩罚）无法由输入复算"
                    )

            if expect_reason == "canonical_ruling" and chosen != pen_winners[0]:
                raise AssertionError(
                    f"第 {depth} 层点 {n} 的选择无法由规范裁决推出"
                )
            if expect_reason == "tie_break_id" and chosen != pen_winners[0]:
                raise AssertionError(
                    f"第 {depth} 层点 {n} 同价同惩罚下未取标识最小者 {pen_winners[0]}"
                )
            chosen_map[n] = (chosen, min_eff, active[chosen][3])

        if set(chosen_map) != active_nodes - {root}:
            raise AssertionError(f"第 {depth} 层未覆盖全部非根点的选择")
        chosen_by_level.append(chosen_map)

        # 独立检出环（沿选入通道逆向行走）
        parent = {n: active[cid][0] for n, (cid, _, _) in chosen_map.items()}
        cyc_nodes = _detect_cycle(list(active_nodes), root, parent)
        pub = lv.get("cycle")
        cycle_by_level.append(pub)

        if cyc_nodes is None:
            if pub is not None:
                raise AssertionError(f"第 {depth} 层无环，记录却报告收缩")
            if depth != len(levels) - 1:
                raise AssertionError("无环层之后仍有更深层记录")
            leaf_selected = {cid for cid, _, _ in chosen_map.values()}
            break
        if pub is None:
            raise AssertionError(f"第 {depth} 层检出环，记录却缺少收缩明细")

        sname = pub.get("supernode")
        if not sname or sname in active_nodes:
            raise AssertionError(f"第 {depth} 层超点命名非法: {sname!r}")
        fwd = pub.get("nodes", [])
        if len(fwd) != len(cyc_nodes) or set(fwd) != cyc_nodes:
            raise AssertionError(f"第 {depth} 层环节点无法由选入通道复算")
        pub_cycle_ch = pub.get("channels", [])
        cyc_ch = {chosen_map[n][0] for n in cyc_nodes}
        if len(pub_cycle_ch) != len(cyc_nodes) or set(pub_cycle_ch) != cyc_ch:
            raise AssertionError(f"第 {depth} 层环边集合无法复算")
        # 前向顺序：通道 i 从节点 i 指向节点 i+1（末位回绕）
        k = len(fwd)
        for i in range(k):
            eid = pub_cycle_ch[i]
            u, v, _, _ = active[eid]
            if u != fwd[i] or v != fwd[(i + 1) % k]:
                raise AssertionError(f"第 {depth} 层环的前向邻接顺序不成立")

        # 独立构造下一层活动边，并核对全部改写明细
        wstar = {n: chosen_map[n][1] for n in cyc_nodes}
        pstar = {n: chosen_map[n][2] for n in cyc_nodes}
        next_active: dict[str, list] = {}
        expect_rew_in: dict[str, tuple[int, int, int, int, str]] = {}
        expect_rew_out: dict[str, tuple[str, str, int, int]] = {}
        expect_dropped: set[str] = set()
        for eid, (u, v, eff, pen) in active.items():
            u_in, v_in = u in cyc_nodes, v in cyc_nodes
            if u_in and v_in:
                if eid not in cyc_ch:
                    expect_dropped.add(eid)
                continue
            if v_in:
                adjusted_cost = eff - wstar[v]
                adjusted_pen = pen - pstar[v]
                next_active[eid] = [u, sname, adjusted_cost, adjusted_pen]
                expect_rew_in[eid] = (eff, adjusted_cost, pen, adjusted_pen, v)
            elif u_in:
                next_active[eid] = [sname, v, eff, pen]
                expect_rew_out[eid] = (sname, v, eff, pen)
            else:
                next_active[eid] = [u, v, eff, pen]

        pub_rew_in = {r["channel"]: r for r in pub.get("rewired_in", [])}
        if set(pub_rew_in) != set(expect_rew_in):
            raise AssertionError(f"第 {depth} 层进入边改写集合无法复算")
        for eid, r in pub_rew_in.items():
            eff_before, adjusted_cost, pen_before, adjusted_pen, enters = expect_rew_in[eid]
            c = by_id[eid]
            if r.get("level_cost") != eff_before:
                raise AssertionError(
                    f"第 {depth} 层 {eid} 收缩前的层级有效代价无法复算"
                    f"（记录 {r.get('level_cost')}，应为 {eff_before}）"
                )
            if r.get("adjusted_cost") != adjusted_cost:
                raise AssertionError(
                    f"第 {depth} 层 {eid} 的修正代价 {r.get('adjusted_cost')} "
                    f"≠ {eff_before} − {wstar[enters]} = {adjusted_cost}"
                )
            if r.get("level_penalty") != pen_before:
                raise AssertionError(
                    f"第 {depth} 层 {eid} 收缩前的规范惩罚无法复算"
                )
            if r.get("adjusted_penalty") != adjusted_pen:
                raise AssertionError(
                    f"第 {depth} 层 {eid} 的修正规范惩罚 {r.get('adjusted_penalty')} "
                    f"≠ {pen_before} − {pstar[enters]} = {adjusted_pen}"
                )
            if r.get("enters") != enters:
                raise AssertionError(f"第 {depth} 层 {eid} 的进入点无法复算")
            if (
                r.get("from") != c.u or r.get("to") != c.v
                or r.get("original_cost") != c.cost
            ):
                raise AssertionError(f"第 {depth} 层 {eid} 的原始通道描述与输入不符")
        pub_rew_out = {r["channel"]: r for r in pub.get("rewired_out", [])}
        if set(pub_rew_out) != set(expect_rew_out):
            raise AssertionError(f"第 {depth} 层外出边改写集合无法复算")
        for eid, r in pub_rew_out.items():
            _, v, eff, pen = expect_rew_out[eid]
            c = by_id[eid]
            if (
                r.get("from") != c.u or r.get("to") != c.v
                or r.get("cost") != eff or r.get("penalty") != pen
            ):
                raise AssertionError(f"第 {depth} 层 {eid} 的外出边改写无法复算")
        if set(pub.get("dropped_internal", [])) != expect_dropped:
            raise AssertionError(f"第 {depth} 层丢弃环内边集合无法复算")

        active = next_active
        active_nodes = {n for n in active_nodes if n not in cyc_nodes} | {sname}

    assert leaf_selected is not None

    # --- 4) 展开替换自叶向上重放 ---
    selected: set[str] = set(leaf_selected)
    cycles_by_super: dict[str, tuple[dict, dict[str, tuple[str, int, int]]]] = {}
    for lv, cyc in zip(levels, cycle_by_level):
        if cyc is not None:
            depth = lv["depth"]
            cycles_by_super[cyc["supernode"]] = (cyc, chosen_by_level[depth])

    for exp in expansions:
        sname = exp.get("supernode")
        if sname not in cycles_by_super:
            raise AssertionError(f"展开记录引用了未知超点 {sname}")
        cyc, chosen_map = cycles_by_super[sname]
        entering = exp.get("entering_channel")
        enters_node = exp.get("enters_node")
        removed = exp.get("removed_cycle_channel")
        kept = exp.get("kept_cycle_channels", [])
        if entering not in selected:
            raise AssertionError(f"展开 {sname} 的进入通道 {entering} 不在当前选中集")
        if enters_node not in chosen_map:
            raise AssertionError(f"展开 {sname} 的进入点 {enters_node} 不属于该环")
        if removed != chosen_map[enters_node][0]:
            raise AssertionError(
                f"展开 {sname} 被替换环边应为 {chosen_map[enters_node][0]}，记录为 {removed}"
            )
        expect_kept = cyc["channels"].copy()
        expect_kept.remove(removed)
        if sorted(kept) != sorted(expect_kept):
            raise AssertionError(f"展开 {sname} 的保留环边集合无法复算")
        selected.update(kept)

    # --- 5) 最终结构、裁决一致性与总代价 ---
    if selected != accepted:
        raise AssertionError(
            f"规范裁决接受集 {sorted(accepted)} 与复算树 {sorted(selected)} 不一致"
        )
    for cid in selected:
        if cid not in by_id:
            raise AssertionError(f"选中通道 {cid} 不在输入中")
    indeg: dict[str, int] = {}
    for cid in selected:
        indeg[by_id[cid].v] = indeg.get(by_id[cid].v, 0) + 1
    for p in points:
        want = 0 if p == root else 1
        if indeg.get(p, 0) != want:
            raise AssertionError(f"点 {p} 的入选入边数为 {indeg.get(p, 0)}，应为 {want}")
    sub_channels = [by_id[cid] for cid in selected]
    if reachable_from(root, sub_channels) != set(points):
        raise AssertionError("复算结果不能自根到达全部点")
    total = sum(c.cost for c in sub_channels)
    if total != optimal:
        raise AssertionError(f"复算总代价 {total} ≠ 裁决依据中的最优代价 {optimal}")
    return sorted(selected)
