import { describe, expect, it } from "vitest";

import type { Topology } from "../../data/geo";
import { projectTopology } from "./projection";

/**
 * Two unit squares sharing their middle edge, encoded the way mapshaper writes
 * the real asset: quantized deltas plus a transform, with the shared border
 * stored ONCE and referenced backwards by the right-hand square.
 *
 *   arc 0: the left square's outline, minus the shared edge
 *   arc 1: the shared edge, bottom → top
 *   arc 2: the right square's outline, minus the shared edge
 */
const topology: Topology = {
  type: "Topology",
  transform: { scale: [0.01, 0.01], translate: [-50, -10] },
  arcs: [
    // (0,0) → (0,100) → (100,100): up the left side and across the top.
    [
      [0, 0],
      [0, 100],
      [100, 0],
    ],
    // The shared edge, top → bottom, then back along the bottom to the start.
    [
      [100, 100],
      [0, -100],
      [-100, 0],
    ],
    // (100,0) → (200,0) → (200,100) → (100,100): around the right square.
    [
      [100, 0],
      [100, 0],
      [0, 100],
      [-100, 0],
    ],
  ],
  objects: {
    municipalities: {
      type: "GeometryCollection",
      geometries: [
        { type: "Polygon", arcs: [[0, 1]], properties: { code: "00001", uf: "AA" } },
        { type: "Polygon", arcs: [[2, -2]], properties: { code: "00002", uf: "AA" } },
      ],
    },
    states: {
      type: "GeometryCollection",
      geometries: [{ type: "MultiPolygon", arcs: [[[0, 1]], [[2, -2]]], properties: { uf: "AA" } }],
    },
  },
};

describe("projectTopology", () => {
  it("decodes quantized deltas and fits the topology to the requested width", () => {
    const map = projectTopology(topology, 1000);
    expect(map.width).toBe(1000);
    // Two degrees wide by one degree tall, near the equator where Mercator is
    // ~conformal, so the box is about twice as wide as it is tall.
    expect(map.height).toBeGreaterThan(480);
    expect(map.height).toBeLessThan(520);
    expect(map.municipalities.map((shape) => shape.id)).toEqual(["00001", "00002"]);
  });

  it("closes every ring and starts it with a move", () => {
    const map = projectTopology(topology, 1000);
    for (const shape of [...map.municipalities, ...map.states]) {
      expect(shape.d.startsWith("M")).toBe(true);
      expect(shape.d.endsWith("Z")).toBe(true);
    }
  });

  it("gives the shared border the same coordinates from both sides", () => {
    const map = projectTopology(topology, 1000);
    const [left, right] = map.municipalities;
    // The shared edge is arc 1 forwards for the left square and ~1 backwards
    // for the right one; if the reversal or the rounding differed, the two
    // polygons would not meet and the map would show a hairline gap.
    const points = (d: string): Set<string> => new Set(d.slice(1, -1).split(/[ML]/).filter(Boolean));
    const shared = [...points(left?.d ?? "")].filter((point) => points(right?.d ?? "").has(point));
    expect(shared.length).toBeGreaterThanOrEqual(2);
  });

  it("draws only what the topology contains — a code with no geometry is absent", () => {
    const map = projectTopology(topology, 1000);
    expect(map.municipalities.some((shape) => shape.id === "30066")).toBe(false);
  });
});
