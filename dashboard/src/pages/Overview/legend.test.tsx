import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { MunicipalitiesIndex } from "../../data/types";
import { renderWithProviders } from "../../test/renderWithProviders";
import Legend from "./Legend";
import { buildLayer, METRICS } from "./metrics";

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

describe("overview legend", () => {
  it("says a null column is not computed instead of drawing a scale", () => {
    const layer = buildLayer(METRICS["flag-density"], indexWith([null, null]));
    renderWithProviders(<Legend layer={layer} />);
    expect(screen.getByTestId("metric-not-computed")).toBeInTheDocument();
    expect(screen.queryByText(/nenhuma/)).not.toBeInTheDocument();
  });

  it("draws the scale — with an explicit zero band — once the column has values", () => {
    const layer = buildLayer(METRICS["flag-density"], indexWith([0, 0, 3, 11]));
    renderWithProviders(<Legend layer={layer} />);
    expect(screen.queryByTestId("metric-not-computed")).not.toBeInTheDocument();
    // "none" is its own swatch: a municipality the detectors cleared must not
    // read as one they never looked at.
    expect(screen.getByText("nenhuma")).toBeInTheDocument();
    expect(screen.getByText("sem valor")).toBeInTheDocument();
  });
});
