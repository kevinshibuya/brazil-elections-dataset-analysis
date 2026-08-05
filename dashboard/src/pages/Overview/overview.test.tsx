import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { fixtureIndex, renderWithProviders } from "../../test/renderWithProviders";
import OverviewPage from "./OverviewPage";

describe("overview", () => {
  it("renders headline cards from summary.json with links to detector pages", async () => {
    renderWithProviders(<OverviewPage />);
    const cards = await screen.findAllByTestId("headline-card");
    expect(cards.length).toBeGreaterThanOrEqual(3);
    expect(cards[0]!.querySelector("a")).toHaveAttribute(
      "href",
      expect.stringContaining("/detectors/"),
    );
  });

  it("choropleth binds a data value to every index municipality that has geometry", async () => {
    renderWithProviders(<OverviewPage />);
    const map = await screen.findByTestId("choropleth");
    const bound = map.querySelectorAll("path[data-mun][data-value]").length;
    // ZZ (exterior) has no geometry and stays off the map.
    const expected = fixtureIndex.filter((m) => m.lat !== null).length;
    expect(expected).toBeGreaterThan(0); // a fixture with no geometry would prove nothing
    expect(bound).toBe(expected); // fixture-size independent by construction
  });
});
