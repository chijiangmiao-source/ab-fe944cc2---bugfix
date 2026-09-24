"""最小汇流树（最小树形图）求解器 —— Chu–Liu/Edmonds 算法的手工实现。

不依赖任何现成图优化库。给定注入根与带非负整数代价的有向通道，
求一棵以根为源、覆盖全部采样点、总代价最小的汇流树（每个非根点
恰有一条入选通道且自根可达）。同优解中按"升序通道标识序列"取字典序
最小者（规范树）。

除规范树外，本模块还产出仅凭输入与响应即可独立复算的公开决策记录。
每一步选择都按记录中公开的比较规则重放：
  * ``record["key_scheme"]`` 声明逐层选择的比较键
    ``(本层修正代价, 规范优先标记, 通道标识)``：规范树通道标记为 0，
    其余为 1；逐分量取字典序最小者。
  * 每个层级 ``record["levels"][i]`` 公开：
      - ``rule``：本层选择规则的文字说明；
      - ``chosen``：各非根（超）点选中的通道；
      - ``candidates``：各非根（超）点的**全部**候选入边及其本层比较键
        （``adjusted_cost`` / ``canonical`` / ``key``），并标注 ``selected``。
        审查者取键最小的候选即可逐步复算 ``chosen``，无需任何隐藏信息；
      - 若本层出现有向环，另公开环收缩（环节点/环边、超点、入边代价
        ``w − w*(v)`` 修正、引出边、丢弃的环内边）。
  * ``record["canonical_decisions"]`` 按标识升序公开规范树的贪心强制裁决：
    每条通道记录在"此前已接受通道 + 本条"被强制入选时可达到的最小总代价
    （不可行为 null）、全局最优代价 C* 与 accepted 与否。这些代价只由
    输入决定，任何人都可用自己的 Edmonds 实现独立核验；按序列重放即可
    重建规范边集合，进而复算各候选的规范优先标记。
  * ``record["expansions"]`` 公开每次展开替换（进入通道、进入点、
    被替换环边、保留环边）。

``replay_record()`` 只依据输入与公开记录：重放规范裁决、逐层核对候选键
与选中通道、按收缩记录核对相邻层的边变换与代价修正、重放展开替换，
逐步推出最终树并做结构校验。

规范树的求得分两步：
  1. 用 Edmonds 求出最小总代价 C*；
  2. 按通道标识升序逐个尝试"强制入选"：若强制后仍存在代价为 C* 的
     汇流树，则强制之。可证明最终强制集本身就是字典序最小的最优树。
最后用公开的 (代价, 规范优先标记, 标识) 比较键再跑一次 Edmonds，
使产出的层级记录恰好对应规范树；该运行的全部候选键都随记录公开。
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
    key_scheme: str,
    canon_ids: frozenset[str] = frozenset(),
) -> list[_Edge] | None:
    """在当前层级上求解；返回以本层 _Edge 表示的入选边，无解返回 None。

    levels / expansions 收集可复算记录；每个非根（超）点的全部候选入边
    及其本层比较键都写入记录，使选择可以仅凭公开信息逐步复算。
    canon_ids 为规范树通道标识集，用于在记录中标注候选的规范优先标记。
    """
    in_edge: dict[str, _Edge] = {}
    candidates: dict[str, list[_Edge]] = {}
    for n in sorted(nodes):
        if n == root:
            continue
        cands = [e for e in edges if e.v == n]
        if not cands:
            return None  # 某点无入边：本层不可解
        cands.sort(key=lambda e: e.key)
        candidates[n] = cands
        in_edge[n] = cands[0]

    level_rec = {
        "depth": depth,
        "rule": key_scheme,
        "nodes": sorted(nodes),
        "chosen": [
            {
                "node": n,
                "channel": in_edge[n].orig.id,
                "cost": in_edge[n].key[0],
                "key": list(in_edge[n].key),
            }
            for n in sorted(nodes)
            if n != root
        ],
        "candidates": [
            {
                "node": n,
                "inlets": [
                    {
                        "channel": e.orig.id,
                        "from": e.orig.u,
                        "to": e.orig.v,
                        "adjusted_cost": e.key[0],
                        "canonical": e.orig.id in canon_ids,
                        "key": list(e.key),
                        "selected": e is in_edge[n],
                    }
                    for e in candidates[n]
                ],
            }
            for n in sorted(nodes)
            if n != root
        ],
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
                    "adjusted_cost": nkey[0],
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
        new_nodes, root, new_edges, depth + 1, levels, expansions, sup_counter,
        key_scheme, canon_ids,
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
    nodes: list[str],
    root: str,
    edges: list[_Edge],
    key_scheme: str = "按 (本层修正代价, 规范优先标记, 通道标识) 逐分量取最小",
    canon_ids: frozenset[str] = frozenset(),
) -> tuple[list[_Edge], list[dict], list[dict]] | None:
    """完整 Edmonds 运行；返回 (入选边, 层级记录, 展开记录) 或 None。"""
    levels: list[dict] = []
    expansions: list[dict] = []
    picked = _solve_level(
        list(nodes), root, edges, 0, levels, expansions, [0], key_scheme, canon_ids
    )
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


# 逐层选择的公开比较规则（写入每层记录与 record.key_scheme）
KEY_SCHEME = "按 (本层修正代价, 规范优先标记, 通道标识) 逐分量取最小；规范树通道标记为 0，其余为 1"


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

    # 2) 规范树：按标识升序贪心强制入选；每一步裁决连同其依据一起公开，
    #    使规范边集合可以仅凭输入与记录独立重放、核验。
    forced: list[Channel] = []
    canonical_decisions: list[dict] = []
    for c in sorted(channels, key=lambda x: x.id):
        trial_cost = _min_cost_with_forced(points, root, channels, forced + [c])
        accepted = trial_cost == best_cost
        canonical_decisions.append(
            {
                "channel": c.id,
                "forced_with": [f.id for f in forced],
                "min_cost_if_forced": trial_cost,
                "optimal_cost": best_cost,
                "accepted": accepted,
            }
        )
        if accepted:
            forced.append(c)
    canonical_ids = sorted(c.id for c in forced)

    # 3) 以公开的 (代价, 规范优先标记, 标识) 比较键重跑，
    #    产出恰好对应规范树的层级记录；全部候选键随记录公开。
    canon = set(canonical_ids)
    keyed_edges = [
        _Edge(c, c.u, c.v, (c.cost, 0 if c.id in canon else 1, c.id), enters=c.v, lower=None)
        for c in channels
    ]
    final_run = _edmonds(
        list(points), root, keyed_edges, key_scheme=KEY_SCHEME, canon_ids=frozenset(canon)
    )
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
            "key_scheme": KEY_SCHEME,
            "levels": levels,
            "expansions": expansions,
            "canonical_decisions": canonical_decisions,
            "contractions": sum(1 for lv in levels if lv["cycle"]),
        },
    }


# ---------------------------------------------------------------------------
# 记录复算（供测试与 verify 服务核对证据链）
# ---------------------------------------------------------------------------


def replay_record(
    points: list[str], root: str, channels: list[Channel], record: dict
) -> list[str]:
    """仅凭输入与公开记录复算最终入选通道标识（升序）。

    复算与校验分五步，全部只依赖输入和记录本身，任何一步不符即抛
    AssertionError：
      1. 重放规范裁决：逐条核对 ``canonical_decisions`` —— 标识升序、
         ``forced_with`` 恰为此前已接受通道、``accepted`` 与
         ``min_cost_if_forced == optimal_cost`` 一致 —— 重建规范边集；
      2. 逐层核对选择：每层每个非根（超）点的选中通道须为该点全部候选中
         比较键最小者，候选键须自洽（``key == (修正代价, 规范标记, 标识)``），
         规范标记须与第 1 步重建的规范边集一致；
      3. 核对层间变换：第 0 层候选须与输入通道一一对应；更深层的候选
         须能由上一层的收缩记录（环、入边代价修正、引出边、丢弃边）推出；
      4. 重放展开替换：叶子层选中通道 ∪ 各次展开保留的环边；
      5. 结构校验：最终选中集每非根点恰一条入边、自根可达，
         且与第 1 步重建的规范边集一致。
    """
    levels: list[dict] = record["levels"]
    expansions: list[dict] = record["expansions"]
    decisions: list[dict] = record["canonical_decisions"]
    key_scheme: str = record["key_scheme"]
    if not levels:
        raise AssertionError("记录缺少层级信息")
    if record["contractions"] != sum(1 for lv in levels if lv["cycle"]):
        raise AssertionError("contractions 与层级记录中的环数量不符")

    by_id = {c.id: c for c in channels}

    # ---- 1. 重放规范裁决，重建规范边集 ----
    accepted: list[str] = []
    optimum: int | None = None
    prev_id: str | None = None
    for d in decisions:
        cid = d["channel"]
        if cid not in by_id:
            raise AssertionError(f"规范裁决引用了未知通道 {cid}")
        if prev_id is not None and cid <= prev_id:
            raise AssertionError("规范裁决未按通道标识升序给出")
        prev_id = cid
        if d["forced_with"] != accepted:
            raise AssertionError(f"裁决 {cid} 的已接受前缀与裁决序列重放不一致")
        if optimum is None:
            optimum = d["optimal_cost"]
        elif d["optimal_cost"] != optimum:
            raise AssertionError("各裁决记录的全局最优代价不一致")
        want = d["min_cost_if_forced"] == optimum
        if d["accepted"] != want:
            raise AssertionError(f"裁决 {cid} 的 accepted 与其公开的代价依据不符")
        if want:
            accepted.append(cid)
    if optimum is None:
        raise AssertionError("记录缺少规范裁决")
    canon = set(accepted)

    # ---- 2/3. 逐层核对选择与层间变换 ----
    cycles_by_super: dict[str, dict] = {}
    # rep[p]：原始点 p 在当前层级的代表（未收缩即自身；所在环收缩后为超点）
    rep: dict[str, str] = {p: p for p in points}
    prev_rep: dict[str, str] = {}
    prev_level: dict | None = None
    prev_inlet: dict[str, tuple[str, dict]] = {}
    prev_chosen: dict[str, dict] = {}
    for depth, lv in enumerate(levels):
        if lv["depth"] != depth:
            raise AssertionError("层级深度不连续")
        if lv["rule"] != key_scheme:
            raise AssertionError(f"第 {depth} 层选择规则与 key_scheme 不符")
        nonroot = sorted(n for n in lv["nodes"] if n != root)
        cand = {c["node"]: c["inlets"] for c in lv["candidates"]}
        chosen = {c["node"]: c for c in lv["chosen"]}
        if sorted(cand) != nonroot or sorted(chosen) != nonroot:
            raise AssertionError(f"第 {depth} 层的候选/选中记录与节点集不符")

        inlet_by_id: dict[str, tuple[str, dict]] = {}
        for n in nonroot:
            inlets = cand[n]
            if not inlets:
                raise AssertionError(f"第 {depth} 层点 {n} 没有任何候选入边")
            for inl in inlets:
                cid = inl["channel"]
                if cid not in by_id:
                    raise AssertionError(f"第 {depth} 层候选 {cid} 不在输入中")
                c0 = by_id[cid]
                if inl["from"] != c0.u or inl["to"] != c0.v:
                    raise AssertionError(f"第 {depth} 层候选 {cid} 的端点与输入不符")
                if rep[c0.v] != n:
                    raise AssertionError(
                        f"第 {depth} 层候选 {cid} 进入的当前节点应为 {rep[c0.v]}"
                    )
                key = inl["key"]
                if len(key) != 3 or key[0] != inl["adjusted_cost"] or key[2] != cid:
                    raise AssertionError(f"第 {depth} 层候选 {cid} 的比较键不自洽")
                if inl["canonical"] != (cid in canon):
                    raise AssertionError(
                        f"第 {depth} 层候选 {cid} 的规范标记与裁决重放结果不符"
                    )
                inlet_by_id[cid] = (n, inl)
            best = min(inlets, key=lambda x: x["key"])
            ch = chosen[n]
            if ch["channel"] != best["channel"]:
                raise AssertionError(
                    f"第 {depth} 层点 {n} 选中了 {ch['channel']}，"
                    f"但候选中比较键最小的是 {best['channel']}"
                )
            if ch["key"] != best["key"] or ch["cost"] != best["adjusted_cost"]:
                raise AssertionError(f"第 {depth} 层点 {n} 的选中记录与候选键不符")
            if [inl["selected"] for inl in inlets].count(True) != 1 or not best["selected"]:
                raise AssertionError(f"第 {depth} 层点 {n} 的 selected 标注与选中记录不符")

        if depth == 0:
            if lv["nodes"] != sorted(points):
                raise AssertionError("第 0 层节点集与输入点集不符")
            for n in nonroot:
                want = sorted(c.id for c in channels if c.v == n)
                got = sorted(inl["channel"] for inl in cand[n])
                if got != want:
                    raise AssertionError(f"第 0 层点 {n} 的候选与输入通道不一一对应")
                for inl in cand[n]:
                    c0 = by_id[inl["channel"]]
                    if inl["adjusted_cost"] != c0.cost:
                        raise AssertionError(f"第 0 层候选 {inl['channel']} 的修正代价应为原始代价")
                    if inl["key"] != [c0.cost, 0 if inl["canonical"] else 1, c0.id]:
                        raise AssertionError(
                            f"第 0 层候选 {inl['channel']} 的比较键与公开规则不符"
                        )
        else:
            assert prev_level is not None
            cyc = prev_level["cycle"]
            if cyc is None:
                raise AssertionError("上一层没有环收缩，不应存在更深层级")
            sup = cyc["supernode"]
            cyc_nodes = set(cyc["nodes"])
            want_nodes = sorted((set(prev_level["nodes"]) - cyc_nodes) | {sup})
            if lv["nodes"] != want_nodes:
                raise AssertionError(f"第 {depth} 层节点集与上一层收缩结果不符")
            internal = set(cyc["channels"]) | set(cyc["dropped_internal"])
            rewired_in = {r["channel"]: r for r in cyc["rewired_in"]}
            rewired_out = {r["channel"] for r in cyc["rewired_out"]}
            for n in nonroot:
                for inl in cand[n]:
                    cid = inl["channel"]
                    if cid in internal:
                        raise AssertionError(f"环内边 {cid} 出现在收缩后的层级")
                    prev_entry = prev_inlet.get(cid)
                    if prev_entry is None:
                        raise AssertionError(f"第 {depth} 层候选 {cid} 在上一层不存在")
                    pn, pinl = prev_entry
                    if n == sup:
                        r = rewired_in.get(cid)
                        if r is None or pn != r["enters"]:
                            raise AssertionError(
                                f"进入超点 {sup} 的候选 {cid} 缺少对应的入边修正记录"
                            )
                        base = prev_chosen[r["enters"]]
                        if inl["adjusted_cost"] != pinl["adjusted_cost"] - base["cost"]:
                            raise AssertionError(f"候选 {cid} 的修正代价与收缩记录不符")
                        if inl["key"][1] != pinl["key"][1] - base["key"][1]:
                            raise AssertionError(f"候选 {cid} 的修正规范标记与收缩记录不符")
                    else:
                        if pn != n or pinl["key"] != inl["key"]:
                            raise AssertionError(
                                f"未受收缩影响的候选 {cid} 在层间被改动"
                            )
                        if prev_rep[by_id[cid].u] in cyc_nodes and cid not in rewired_out:
                            raise AssertionError(f"引出边 {cid} 缺少 rewired_out 记录")
            for cid, (pn, _pinl) in prev_inlet.items():
                if cid not in internal and cid not in inlet_by_id:
                    raise AssertionError(f"上一层候选 {cid} 在收缩后丢失")

        cyc = lv["cycle"]
        if cyc is not None:
            nodes_fwd, chans = cyc["nodes"], cyc["channels"]
            k = len(nodes_fwd)
            if k < 2 or len(chans) != k:
                raise AssertionError(f"第 {depth} 层环记录不完整")
            if cyc["supernode"] in set(points) or cyc["supernode"] in by_id:
                raise AssertionError("超点标识与输入冲突")
            cyc_nodes = set(nodes_fwd)
            for i, nid in enumerate(nodes_fwd):
                if nid not in chosen:
                    raise AssertionError("环节点不是本层非根点")
                edge = by_id.get(chans[i])
                if edge is None:
                    raise AssertionError(f"环边 {chans[i]} 不在输入中")
                if rep[edge.u] != nid or rep[edge.v] != nodes_fwd[(i + 1) % k]:
                    raise AssertionError("环边方向与环节点序列不符")
                if chosen[nodes_fwd[(i + 1) % k]]["channel"] != chans[i]:
                    raise AssertionError("环边须为本层对应点的选中入边")
            for cid in cyc["dropped_internal"]:
                c0 = by_id.get(cid)
                if c0 is None or rep[c0.u] not in cyc_nodes or rep[c0.v] not in cyc_nodes:
                    raise AssertionError(f"丢弃的环内边 {cid} 与环节点不符")
                if cid in chans:
                    raise AssertionError(f"{cid} 既是环边又被丢弃")
            for r in cyc["rewired_in"]:
                cid = r["channel"]
                c0 = by_id.get(cid)
                if c0 is None or r["from"] != c0.u or r["to"] != c0.v:
                    raise AssertionError(f"入边修正记录 {cid} 与输入不符")
                if (
                    rep[c0.v] != r["enters"]
                    or r["enters"] not in cyc_nodes
                    or rep[c0.u] in cyc_nodes
                ):
                    raise AssertionError(f"入边修正记录 {cid} 的进入点与环不符")
                pn, pinl = inlet_by_id[cid]
                base = chosen[r["enters"]]
                if r["adjusted_cost"] != pinl["adjusted_cost"] - base["cost"]:
                    raise AssertionError(f"入边 {cid} 的代价修正与 w − w*(v) 规则不符")
                if r["original_cost"] != c0.cost:
                    raise AssertionError(f"入边 {cid} 的原始代价与输入不符")
            for r in cyc["rewired_out"]:
                c0 = by_id.get(r["channel"])
                if c0 is None or rep[c0.u] not in cyc_nodes or rep[c0.v] in cyc_nodes:
                    raise AssertionError(f"引出边记录 {r['channel']} 与环节点不符")
                if c0.v == root:
                    # 指向根的边不是任何点的候选，但其键在各层保持不变
                    if r["cost"] != c0.cost:
                        raise AssertionError(f"引出边 {r['channel']} 的代价与输入不符")
                elif r["cost"] != inlet_by_id[r["channel"]][1]["adjusted_cost"]:
                    raise AssertionError(f"引出边 {r['channel']} 的代价与本层记录不符")
            # 完备性：环节点的候选要么是环边/环内丢弃边，要么记入 rewired_in
            rin = {r["channel"] for r in cyc["rewired_in"]}
            for nid in nodes_fwd:
                for inl in cand[nid]:
                    cid = inl["channel"]
                    if cid not in chans and cid not in cyc["dropped_internal"] and cid not in rin:
                        raise AssertionError(f"进入环的候选 {cid} 缺少修正记录")
            cycles_by_super[cyc["supernode"]] = cyc
            # 环节点（含此前收缩出的超点）在当前层之后由超点代表
            prev_rep = dict(rep)
            for p in points:
                if rep[p] in cyc_nodes:
                    rep[p] = cyc["supernode"]

        prev_level = lv
        prev_inlet = inlet_by_id
        prev_chosen = chosen

    leaf = levels[-1]
    if leaf["cycle"] is not None:
        raise AssertionError("最深层仍含环，记录不完整")

    # ---- 4. 重放展开替换 ----
    selected: set[str] = {c["channel"] for c in leaf["chosen"]}
    expanded: set[str] = set()
    for exp in expansions:
        sup = exp["supernode"]
        if sup not in cycles_by_super:
            raise AssertionError(f"展开记录引用了未知超点 {sup}")
        if sup in expanded:
            raise AssertionError(f"超点 {sup} 被重复展开")
        expanded.add(sup)
        cyc = cycles_by_super[sup]
        if exp["entering_channel"] not in selected:
            raise AssertionError(
                f"展开 {sup} 的进入通道 {exp['entering_channel']} 不在当前选中集"
            )
        if exp["removed_cycle_channel"] not in cyc["channels"]:
            raise AssertionError(f"展开 {sup} 移除的 {exp['removed_cycle_channel']} 不属于该环")
        for kept in exp["kept_cycle_channels"]:
            if kept not in cyc["channels"]:
                raise AssertionError(f"展开 {sup} 保留的 {kept} 不属于该环")
            selected.add(kept)
        if {exp["removed_cycle_channel"]} | set(exp["kept_cycle_channels"]) != set(
            cyc["channels"]
        ):
            raise AssertionError(f"展开 {sup} 的替换/保留未恰好覆盖全部环边")
    if expanded != set(cycles_by_super):
        raise AssertionError("存在未展开的收缩环")

    # ---- 5. 结构校验与规范边集一致性 ----
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
    if selected != canon:
        raise AssertionError("复算得到的树与规范裁决重建的规范边集不一致")
    return sorted(selected)
