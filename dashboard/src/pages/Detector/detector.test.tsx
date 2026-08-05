import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderWithProviders } from "../../test/renderWithProviders";
import DetectorPage from "./DetectorPage";

describe("detector pages", () => {
  it("renders top-N ranking from anomalies file, ranked and linked", async () => {
    renderWithProviders(<DetectorPage id="extreme_dominance" />);
    const rows = await screen.findAllByTestId("ranking-row");
    expect(rows.length).toBeGreaterThan(1); // a one-row table cannot be out of order
    const scores = rows.map((r) => Number(r.dataset.score));
    expect(scores.every((s) => Number.isFinite(s))).toBe(true);
    expect(scores).toEqual([...scores].sort((a, b) => b - a));
    expect(rows[0]!.querySelector("a")).toHaveAttribute(
      "href",
      expect.stringContaining("/municipality/"),
    );
  });

  it("digit detector page renders caveat slot before results", async () => {
    renderWithProviders(<DetectorPage id="digits" />);
    const caveat = await screen.findByTestId("methodology-caveat");
    const results = screen.getByTestId("detector-results");
    expect(
      caveat.compareDocumentPosition(results) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});
