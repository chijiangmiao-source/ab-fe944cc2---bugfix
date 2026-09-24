import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import RecordPanel from "./components/RecordPanel.jsx";

/** 四点零代价环场景的公开记录（与后端 /api/solve 响应结构一致）。 */
const ZERO_CYCLE_RECORD = {
  levels: [
    {
      depth: 0,
      nodes: ["a", "b", "c", "r"],
      chosen: [
        { node: "a", channel: "e00", cost: 0, penalty: 0, reason: "unique" },
        { node: "b", channel: "e01", cost: 0, penalty: 0, reason: "unique" },
        {
          node: "c",
          channel: "e07",
          cost: 0,
          penalty: 0,
          reason: "canonical_ruling",
          candidates: [
            { channel: "e07", cost: 0, penalty: 0 },
            { channel: "e06", cost: 0, penalty: 1 },
          ],
        },
      ],
      cycle: null,
    },
  ],
  expansions: [],
  rulings: [
    {
      channel: "e00", from: "b", to: "a", cost: 0,
      decision: "accepted", forced_cost: 0, optimal_cost: 0, basis: "强制 e00 入选后最小总代价仍为 0",
    },
    {
      channel: "e01", from: "c", to: "b", cost: 0,
      decision: "accepted", forced_cost: 0, optimal_cost: 0, basis: "强制 e01 入选后最小总代价仍为 0",
    },
    {
      channel: "e06", from: "a", to: "c", cost: 0,
      decision: "rejected", forced_cost: null, optimal_cost: 0, basis: "强制 e06 入选后无解（会闭合或冲突）",
    },
    {
      channel: "e07", from: "r", to: "c", cost: 0,
      decision: "accepted", forced_cost: 0, optimal_cost: 0, basis: "强制 e07 入选后最小总代价仍为 0",
    },
  ],
  contractions: 0,
};

describe("<RecordPanel /> 零代价环规范裁决", () => {
  it("公开 c 的全部同价候选、规范惩罚与裁决依据，而不只是零次收缩", () => {
    const raw = renderToString(
      <RecordPanel record={ZERO_CYCLE_RECORD} selectedItem={null} onSelect={() => {}} />
    );
    // SSR 会在文本与表达式之间插入注释占位，比较前统一去除
    const html = raw.replaceAll("<!-- -->", "");
    // 规范裁决区块
    expect(html).toContain("规范裁决");
    expect(html).toContain("e06");
    expect(html).toContain("e07");
    expect(html).toContain("拒绝");
    // c 的两个同价最低入口均公开，且标注规范惩罚
    expect(html).toContain("同价最低入口候选");
    expect(html).toContain("规范惩罚 0");
    expect(html).toContain("规范惩罚 1");
    // 选入 e07 及其裁决依据（枚举映射为中文说明，不直接暴露内部字符串）
    expect(html).toContain("规范惩罚最低且唯一");
    expect(html).toContain("e07");
    expect(html).toMatch(/e07[\s\S]*← 选入/);
    // 不再出现旧的「各选最低入口」笼统说法
    expect(html).not.toContain("各选最低入口");
  });
});
