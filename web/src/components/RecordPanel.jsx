/** 收缩 / 展开记录的联动面板。
 *
 * 记录条目：
 *   - level 选择记录：逐点展示选入通道、裁决依据与同价候选（含规范惩罚），
 *     高亮该层选中的全部通道；
 *   - rulings 规范裁决：按标识升序逐条列出接受 / 拒绝及其代价依据；
 *   - contraction 环收缩：高亮环节点与环边，并列出代价 / 惩罚修正明细；
 *   - expansion 展开替换：高亮进入通道与保留环边，标出被替换的环边。
 */
const REASON_TEXT = {
  unique: "唯一最低有效代价入口",
  canonical_ruling: "规范裁决：同价候选中规范惩罚最低且唯一",
  tie_break_id: "同价同惩罚并列，取标识最小",
};

function LevelPicks({ lv }) {
  return (
    <ul className="rewire-list">
      {lv.chosen.map((c) => (
        <li key={c.node}>
          <code>{c.node}</code> ← <code>{c.channel}</code>
          <span className="muted">
            （有效代价 {c.cost}，依据：{REASON_TEXT[c.reason] || c.reason}）
          </span>
          {c.candidates && c.candidates.length > 1 && (
            <ul className="cand-list">
              <li className="muted">同价最低入口候选（按「代价, 规范惩罚, 标识」排序）：</li>
              {c.candidates.map((x) => (
                <li key={x.channel} className={x.channel === c.channel ? "cand-pick" : "cand-skip"}>
                  <code>{x.channel}</code>
                  <span className="muted">
                    {" "}代价 {x.cost} · 规范惩罚 {x.penalty}
                    {x.channel === c.channel ? " ← 选入" : "（未取）"}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </li>
      ))}
    </ul>
  );
}

function RulingList({ rulings }) {
  if (!rulings) return null;
  const accepted = rulings.filter((r) => r.decision === "accepted");
  return (
    <li className="record-item">
      <span className="rec-tag tag-ruling">规范裁决 · {accepted.length}/{rulings.length} 接受</span>
      <span className="rec-desc">
        按标识升序逐条试探强制入选；仅当强制后最小总代价仍为
        <code> C*={accepted[0]?.optimal_cost} </code>
        时接受。接受集 = 最终规范树，每个同价选择均据此可复算。
      </span>
      <ul className="rewire-list ruling-list">
        {rulings.map((r) => (
          <li key={r.channel} className={r.decision === "accepted" ? "ruling-ok" : "ruling-no"}>
            <code>{r.channel}</code>
            <span className="muted">
              {" "}{r.from}→{r.to}，代价 {r.cost}：
            </span>
            <strong>{r.decision === "accepted" ? "接受" : "拒绝"}</strong>
            <span className="muted"> —— {r.basis}</span>
          </li>
        ))}
      </ul>
    </li>
  );
}

export default function RecordPanel({ record, selectedItem, onSelect }) {
  if (!record) return null;
  const { levels, expansions, rulings } = record;

  const items = [];
  items.push({ kind: "rulings", depth: "rulings", data: rulings });
  levels.forEach((lv) => {
    items.push({ kind: "level", depth: lv.depth, data: lv });
    if (lv.cycle) items.push({ kind: "contraction", depth: lv.cycle.supernode, data: lv.cycle });
  });
  expansions.forEach((e, i) => {
    items.push({ kind: "expansion", depth: `exp-${i}`, data: e });
  });

  const keyOf = (it) => `${it.kind}:${it.depth}`;

  return (
    <div className="record-panel">
      <h3>可复算记录（规范裁决 → 逐层选择 → 收缩 → 展开）</h3>
      <ol className="record-list">
        {items.map((it) => {
          const active = selectedItem && keyOf(selectedItem) === keyOf(it);
          if (it.kind === "rulings") {
            return (
              <RulingList
                key={keyOf(it)}
                rulings={it.data}
              />
            );
          }
          if (it.kind === "level") {
            const lv = it.data;
            return (
              <li
                key={keyOf(it)}
                className={`record-item ${active ? "active" : ""}`}
                onClick={() => onSelect(it)}
              >
                <span className="rec-tag tag-level">第 {lv.depth} 层 · 选入通道</span>
                <span className="rec-desc">
                  为 {lv.chosen.length} 个非根点按
                  <strong>「最低有效代价 → 最低规范惩罚（规范裁决）→ 最小标识」</strong>
                  逐点定夺：
                </span>
                <LevelPicks lv={lv} />
              </li>
            );
          }
          if (it.kind === "contraction") {
            const cy = it.data;
            return (
              <li
                key={keyOf(it)}
                className={`record-item ${active ? "active" : ""}`}
                onClick={() => onSelect(it)}
              >
                <span className="rec-tag tag-contract">环收缩 → {cy.supernode}</span>
                <span className="rec-desc">
                  有向环节点 <code>{cy.nodes.join(" → ")} → {cy.nodes[0]}</code>
                  ；环边 <code>{cy.channels.join(" → ")}</code>
                </span>
                <ul className="rewire-list">
                  {cy.rewired_in.map((r) => (
                    <li key={r.channel}>
                      进入候选 <code>{r.channel}</code>（{r.from}→{r.to}，进入点 {r.enters}）
                      {" "}代价 {r.original_cost} → 本层 {r.level_cost} − {r.level_cost - r.adjusted_cost}
                      （{r.enters} 的当前入口）= 修正有效代价 <strong>{r.adjusted_cost}</strong>
                      {" "}；规范惩罚 {r.level_penalty} → <strong>{r.adjusted_penalty}</strong>
                    </li>
                  ))}
                  {cy.dropped_internal.length > 0 && (
                    <li className="muted">
                      丢弃环内非环边：<code>{cy.dropped_internal.join(", ") || "无"}</code>
                    </li>
                  )}
                </ul>
              </li>
            );
          }
          const e = it.data;
          return (
            <li
              key={keyOf(it)}
              className={`record-item ${active ? "active" : ""}`}
              onClick={() => onSelect(it)}
            >
              <span className="rec-tag tag-expand">展开 {e.supernode} · 替换</span>
              <span className="rec-desc">
                展开通道 <code>{e.entering_channel}</code> 进入点
                <code>{e.enters_node}</code>，替换掉环边
                <code className="bad">{e.removed_cycle_channel}</code>
                ，保留环边 <code>{e.kept_cycle_channels.join(", ")}</code>
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
