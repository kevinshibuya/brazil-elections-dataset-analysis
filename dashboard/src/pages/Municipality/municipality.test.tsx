import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { renderWithProviders } from "../../test/renderWithProviders";
import MunicipalityPage from "./MunicipalityPage";

const user = userEvent.setup();

describe("municipality drill-down", () => {
  it("renders section rows from a decoded shard and filters by min score", async () => {
    renderWithProviders(<MunicipalityPage code="03018" />);
    expect(await screen.findAllByTestId("section-row")).not.toHaveLength(0);
    await user.click(screen.getByTestId("filter-min-score")); // preset high threshold
    const rows = screen.getAllByTestId("section-row");
    for (const r of rows) expect(Number(r.dataset.maxScore)).toBeGreaterThanOrEqual(2);
  });

  it("anomaly panel shows baseline, n and score for a flagged section", async () => {
    renderWithProviders(<MunicipalityPage code="03018" />);
    await user.click((await screen.findAllByTestId("section-row"))[0]!);
    const panel = screen.getByTestId("anomaly-panel");
    expect(panel).toHaveTextContent(/n\s*=/i);
    expect(panel.querySelectorAll("[data-testid=detector-score]").length).toBeGreaterThan(0);
  });
});
