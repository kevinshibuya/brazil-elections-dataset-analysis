/**
 * Acceptance test for the methodology page's STRUCTURE (Task 21).
 *
 * The prose is Task 22's and is written by a different model; what is pinned
 * here is the skeleton that prose lands in — the five sections, their order,
 * their stable ids, the table of contents that links to them, and the citation
 * list. Task 22 rewrites every word in the markdown files and must not be able
 * to move a section id or drop a citation without this test going red.
 *
 * Read-only to the implementer.
 */

import { cleanup, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderWithProviders } from "../../test/renderWithProviders";
import MethodologyPage from "./MethodologyPage";

/**
 * Binding, and in this order. These ids are a contract with three other
 * things: the table-of-contents anchors, the `#fragment` links the detector
 * pages point at, and Task 22's copy test.
 */
const SECTIONS = ["sources", "detectors", "caveats", "reproduce", "citations"] as const;

/** The works the page must cite, by the surname the entry is known under. */
const CITED = ["Klimek", "Deckert", "Ridout"] as const;

describe("methodology page", () => {
  it("renders all skeleton sections and the citation list in both locales", async () => {
    for (const loc of ["pt-BR", "en"] as const) {
      renderWithProviders(<MethodologyPage />, { locale: loc });
      for (const id of SECTIONS)
        expect(await screen.findByTestId(`section-${id}`), `${loc}/${id}`).toBeInTheDocument();
      expect(screen.getAllByTestId("citation").length).toBeGreaterThanOrEqual(3);
      cleanup();
    }
  });

  it("keeps the skeleton sections in their binding order", async () => {
    renderWithProviders(<MethodologyPage />);
    await screen.findByTestId(`section-${SECTIONS[0]}`);
    const rendered = screen.getAllByTestId(/^section-/).map((el) => el.dataset.testid);
    expect(rendered).toEqual(SECTIONS.map((id) => `section-${id}`));
  });

  it("links every section from the table of contents by a working anchor", async () => {
    renderWithProviders(<MethodologyPage />);
    const toc = await screen.findByTestId("methodology-toc");
    const targets = within(toc)
      .getAllByRole("link")
      .map((a) => a.getAttribute("href"));
    expect(targets).toEqual(SECTIONS.map((id) => `#${id}`));
    // An anchor that points at nothing is not a table of contents.
    for (const id of SECTIONS) expect(screen.getByTestId(`section-${id}`)).toHaveAttribute("id", id);
  });

  it("switching locale swaps the markdown, it does not re-render the same file", async () => {
    const proseOf = async (locale: "pt-BR" | "en") => {
      renderWithProviders(<MethodologyPage />, { locale });
      await screen.findByTestId("section-sources");
      // Only the sections that carry prose: the citation list is bibliographic
      // and reads the same in both languages by design.
      const text = SECTIONS.filter((id) => id !== "citations")
        .map((id) => screen.getByTestId(`section-${id}`).textContent ?? "")
        .join("\n");
      cleanup();
      return text;
    };

    const pt = await proseOf("pt-BR");
    const en = await proseOf("en");
    expect(pt.trim().length).toBeGreaterThan(0);
    expect(en).not.toEqual(pt);
  });

  it("renders each citation as a structured entry, not a bare string", async () => {
    renderWithProviders(<MethodologyPage />);
    const citations = await screen.findAllByTestId("citation");
    const text = citations.map((c) => c.textContent ?? "");
    for (const surname of CITED)
      expect(text.some((entry) => entry.includes(surname)), surname).toBe(true);
    // Every entry carries all four structured fields; the year is the one that
    // is machine-checkable without pinning a title.
    for (const entry of citations) {
      expect(entry.textContent).toMatch(/\b(19|20)\d{2}\b/);
      for (const field of ["authors", "year", "title", "venue"])
        expect(within(entry).getByTestId(`citation-${field}`).textContent?.trim()).toBeTruthy();
    }
  });
});
