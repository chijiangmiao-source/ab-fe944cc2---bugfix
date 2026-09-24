import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import RecordPanel from "./RecordPanel.jsx";

/** 与真实 API 在同代价四点环场景下返回结构一致的记录。 */
const TIE_CYCLE_RECORD = {
  key_scheme: "按 (本层修正代价, 规范优先标记, 通道标识) 逐分量取最小；规范树通道标记为 0，其余为 1",
  levels: [
    {
      depth: 0,
      rule: "按 (本层修正代价, 规范优先标记, 通道标识) 逐分量取最小；规范树通道标记为 0，其余为 1",
      nodes: ["a", "b", "c", "r"],
      chosen: [
        { node: "a", channel: "e00", cost: 0, key: [0, 0, "e00"] },
        { node: "b", channel: "e01", cost: 0, key: [0, 0, "e01"] },
        { node: "c", channel: "e07", cost: 0, key: [0, 0, "e07"] },
      ],
      candidates: [
        {
          node: "a",
          inlets: [
            { channel: "e00", from: "b", to: "a", adjusted_cost: 0, canonical: true, key: [0, 0, "e00"], selected: true },
          ],
        },
        {
          node: "b",
          inlets: [
            { channel: "e01", from: "c", to: "b", adjusted_cost: 0, canonical: true, key: [0, 0, "e01"], selected: true },
          ],
        },
        {
          node: "c",
          inlets: [
            { channel: "e07", from: "r", to: "c", adjusted_cost: 0, canonical: true, key: [0, 0, "e07"], selected: true },
            { channel: "e06", from: "a", to: "c", adjusted_cost: 0, canonical: false, key: [0, 1, "e06"], selected: false },
          ],
        },
      ],
      cycle: null,
    },
  ],
  expansions: [],
  canonical_decisions: [
    { channel: "e00", forced_with: [], min_cost_if_forced: 0, optimal_cost: 0, accepted: true },
    { channel: "e01", forced_with: ["e00"], min_cost_if_forced: 0, optimal_cost: 0, accepted: true },
    { channel: "e06", forced_with: ["e00", "e01"], min_cost_if_forced: null, optimal_cost: 0, accepted: false },
    { channel: "e07", forced_with: ["e00", "e01"], min_cost_if_forced: 0, optimal_cost: 0, accepted: true },
  ],
  contractions: 0,
};

describe("<RecordPanel /> 同代价环规范裁决", () => {
  it("公开选择规则、候选比较键与规范裁决依据", () => {
    const html = renderToString(
      <RecordPanel record={TIE_CYCLE_RECORD} selectedItem={null} onSelect={() => {}} />
    );
    // 选择规则公开
    expect(html).toContain("规范优先标记");
    // c 的同价候选 e06/e07 都列出，e07 规范优先
    expect(html).toContain("e07");
    expect(html).toContain("e06");
    expect(html).toContain("规范优先");
    // 规范裁决：e06 被放弃的原因（强制后不可行）可见
    expect(html).toContain("规范裁决");
    expect(html).toContain("不可行");
    // 不再笼统宣称“各选最低入口”
    expect(html).not.toContain("各选最低入口");
  });
});
