/** 收缩 / 展开记录的联动面板。
 *
 * 记录条目：
 *   - level 选择记录：高亮该层选中的全部通道，并公开该层每个非根（超）点的
 *     全部候选入边及其比较键（修正代价, 规范优先标记, 标识）
 *   - contraction 环收缩：高亮环节点与环边，并列出代价修正明细
 *   - expansion 展开替换：高亮进入通道与保留环边，标出被替换的环边
 *   - 规范裁决：按标识升序公开每条通道强制入选的裁决与代价依据
 */

function KeyBadge({ k }) {
  return (
    <code className="key-badge">
      ({k[0]}, {k[1]}, {k[2]})
    </code>
  );
}

export default function RecordPanel({ record, selectedItem, onSelect }) {
  if (!record) return null;
  const { levels, expansions, canonical_decisions: decisions, key_scheme: keyScheme } = record;

  const items = [];
  levels.forEach((lv) => {
    items.push({ kind: "level", depth: lv.depth, data: lv });
    if (lv.cycle) items.push({ kind: "contraction", depth: lv.depth, data: lv.cycle });
  });
  expansions.forEach((e, i) => {
    items.push({ kind: "expansion", depth: -(i + 1), data: e });
  });

  const keyOf = (it) => `${it.kind}:${it.depth}`;

  return (
    <div className="record-panel">
      <h3>可复算记录（收缩 → 展开）</h3>
      {keyScheme && (
        <p className="hint">
          逐层选择规则：<code>{keyScheme}</code>。每个点的全部候选入边及其比较键
          随记录公开，取键最小者即为选中通道，可逐步复算。
        </p>
      )}
      <ol className="record-list">
        {items.map((it) => {
          const active = selectedItem && keyOf(selectedItem) === keyOf(it);
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
                  为 {lv.chosen.length} 个非根点各选比较键最小的入口：
                  <code>{lv.chosen.map((c) => c.channel).join(", ")}</code>
                </span>
                {lv.candidates && (
                  <ul className="rewire-list">
                    {lv.candidates.map((c) => (
                      <li key={c.node}>
                        点 <code>{c.node}</code> 候选：
                        {c.inlets.map((inl) => (
                          <span key={inl.channel} className={inl.selected ? "" : "muted"}>
                            {" "}
                            <code>{inl.channel}</code>
                            <KeyBadge k={inl.key} />
                            {inl.canonical ? "（规范优先）" : ""}
                            {inl.selected ? " ✓" : ""}
                          </span>
                        ))}
                      </li>
                    ))}
                  </ul>
                )}
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
                      进入候选 <code>{r.channel}</code>（{r.from}→{r.to}）
                      代价 {r.original_cost} − {r.original_cost - r.adjusted_cost}
                      （{r.enters} 的当前入口）= 修正代价 <strong>{r.adjusted_cost}</strong>
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
      {decisions && decisions.length > 0 && (
        <div className="canon-decisions">
          <h4>规范裁决（同优树取升序标识字典序最小）</h4>
          <p className="hint">
            按标识升序逐条试探强制入选：强制后仍能达到最优代价则接受，否则放弃。
            每条的代价依据公开如下，可独立核验。
          </p>
          <ul className="rewire-list">
            {decisions.map((d) => (
              <li key={d.channel}>
                <code>{d.channel}</code>：
                {d.accepted ? (
                  <>
                    接受 —— 在已接受 [{d.forced_with.join(", ") || "无"}] 基础上强制入选，
                    最小总代价仍为 <strong>{d.min_cost_if_forced}</strong>（= 最优 {d.optimal_cost}）
                  </>
                ) : (
                  <>
                    放弃 —— 在已接受 [{d.forced_with.join(", ") || "无"}] 基础上强制入选，
                    {d.min_cost_if_forced === null
                      ? "不可行（与已接受通道成环 / 冲突）"
                      : `最小总代价升至 ${d.min_cost_if_forced}`}
                    （最优 {d.optimal_cost}）
                  </>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
