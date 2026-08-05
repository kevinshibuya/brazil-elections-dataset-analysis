import { describe, expect, it } from "vitest";

import type { MunicipalitiesIndex } from "../../data/types";
import { binOf, buildLayer, fillOf, METRICS, NO_VALUE_FILL, RAMP } from "./metrics";

function indexWith(flagged: readonly (number | null)[]): MunicipalitiesIndex {
  return {
    v: 1,
    code: flagged.map((_, i) => String(i).padStart(5, "0")),
    name: flagged.map((_, i) => `M${i}`),
    uf: flagged.map(() => "RR"),
    lat: flagged.map(() => 0),
    lon: flagged.map(() => 0),
    sections: flagged.map(() => 100),
    headline: { race_id: "PRES_2", candidates: [] },
    headline_shares: flagged.map(() => [0.6, 0.4]),
    flag_density: flagged,
    turnout: flagged.map(() => 0.8),
  };
}

describe("overview metrics", () => {
  it("treats an all-null flag column as not computed, not as zero", () => {
    const layer = buildLayer(METRICS["flag-density"], indexWith([null, null, null]));
    expect(layer.state).toBe("not-computed");
    expect(layer.values.size).toBe(0);
    // The distinction the whole page hangs on: nothing may be painted in the
    // lowest bin, because that reads as "checked, found nothing".
    expect(fillOf(layer, undefined)).toBe(NO_VALUE_FILL);
  });

  it("treats zeros as a real finding and keeps them out of the no-value fill", () => {
    const layer = buildLayer(METRICS["flag-density"], indexWith([0, 0, 4, 9]));
    expect(layer.state).toBe("ready");
    expect(layer.values.get("00000")).toBe(0);
    expect(fillOf(layer, 0)).toBe(RAMP[0]);
    // …and a municipality with flags is never in the same bin as one without.
    expect(fillOf(layer, 0.04)).not.toBe(RAMP[0]);
  });

  it("skips a municipality with no sections rather than dividing by zero", () => {
    const index = { ...indexWith([3]), sections: [0] };
    expect(buildLayer(METRICS["flag-density"], index).values.size).toBe(0);
  });

  it("bins leading share on anchored edges", () => {
    const edges = METRICS["leading-share"].thresholds([]);
    expect(binOf(0.49, edges)).toBe(0);
    expect(binOf(0.5, edges)).toBe(1);
    expect(binOf(0.95, edges)).toBe(5);
    expect(binOf(1, edges)).toBe(RAMP.length - 1);
  });

  it("spends the whole ramp on turnout's narrow high band", () => {
    // The real column: everything between 0.75 and 0.87. Anchored 0–100 % edges
    // would paint all of it one colour, which is the failure this scale exists
    // to avoid.
    const values = [0.751, 0.772, 0.79, 0.801, 0.822, 0.845, 0.864];
    const index = { ...indexWith(values.map(() => 0)), turnout: values };
    const layer = buildLayer(METRICS.turnout, index);
    expect(layer.state).toBe("ready");
    const bins = new Set(values.map((value) => binOf(value, layer.thresholds)));
    expect(bins.size).toBe(RAMP.length);
    expect(fillOf(layer, Math.min(...values))).toBe(RAMP[0]);
    expect(fillOf(layer, Math.max(...values))).toBe(RAMP[RAMP.length - 1]);
  });

  it("keeps a null turnout out of the map rather than binning it as zero", () => {
    const index = { ...indexWith([0, 0]), turnout: [null, 0.8] };
    const layer = buildLayer(METRICS.turnout, index);
    expect(layer.values.has("00000")).toBe(false);
    expect(fillOf(layer, layer.values.get("00000"))).toBe(NO_VALUE_FILL);
  });

  it("takes the leading candidate's share, whichever candidate leads", () => {
    const index = indexWith([0]);
    expect(
      METRICS["leading-share"].extract({ ...index, headline_shares: [[0.2, 0.8]] }, 0),
    ).toBeCloseTo(0.8);
    expect(METRICS["leading-share"].extract({ ...index, headline_shares: [null] }, 0)).toBeNull();
  });
});
