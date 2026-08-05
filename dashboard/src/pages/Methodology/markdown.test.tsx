/**
 * The two failure modes this page's plumbing has, and both of them are about
 * text going missing rather than text looking wrong:
 *
 *  1. a markdown construct outside the supported subset being SWALLOWED, and
 *  2. a locale file whose section markers no longer match the contract being
 *     rendered as whatever the parser managed to make of it.
 *
 * The copy is authored by a different model in a later slice, so both are
 * things that will actually be attempted.
 */

import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { Markdown, parseBlocks } from "./markdown";
import { METHODOLOGY, parseMethodology, SECTION_IDS } from "./sections";

const renderMd = (markdown: string) =>
  render(
    <MemoryRouter>
      <Markdown blocks={parseBlocks(markdown)} />
    </MemoryRouter>,
  );

/** A file that satisfies the contract, so each test can break ONE thing. */
const good = SECTION_IDS.map((id) => `<!-- section:${id} -->\n## ${id} heading\n\nprose.`).join(
  "\n\n",
);

describe("methodology markdown", () => {
  it("renders the supported subset", () => {
    renderMd(
      [
        "## Heading",
        "A paragraph with **bold**, *italic*, `code` and [a link](https://example.org).",
        "- first\n- second",
        "1. step one\n2. step two",
      ].join("\n\n"),
    );

    expect(screen.getByRole("heading", { name: "Heading" })).toBeInTheDocument();
    expect(screen.getByText("bold").tagName).toBe("STRONG");
    expect(screen.getByText("italic").tagName).toBe("EM");
    expect(screen.getByText("code").tagName).toBe("CODE");
    const link = screen.getByRole("link", { name: "a link" });
    expect(link).toHaveAttribute("href", "https://example.org");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "first",
      "second",
      "step one",
      "step two",
    ]);
  });

  it("keeps in-app links client-side and same-page anchors bare", () => {
    renderMd("See [the caveats](#caveats) and [the detectors](/detectors).");
    expect(screen.getByRole("link", { name: "the caveats" })).not.toHaveAttribute("target");
    expect(screen.getByRole("link", { name: "the detectors" })).toHaveAttribute(
      "href",
      "/detectors",
    );
  });

  it("renders unsupported constructs verbatim rather than dropping them", () => {
    // A table, a block quote and a partly-bulleted block: none of them are in
    // the subset, and every word of all three has to survive to the screen.
    const text = "| a | b |\n| - | - |";
    renderMd([text, "> quoted methodology text", "- bulleted\nnot bulleted"].join("\n\n"));

    expect(screen.getByText("| a | b | | - | - |")).toBeInTheDocument();
    expect(screen.getByText("> quoted methodology text")).toBeInTheDocument();
    expect(screen.getByText("- bulleted not bulleted")).toBeInTheDocument();
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
  });

  it("drops author comments, which are not prose", () => {
    const blocks = parseBlocks("<!-- a note to the translator -->\n\nvisible.");
    expect(blocks).toEqual([{ kind: "p", text: "visible." }]);
  });
});

describe("methodology section guard", () => {
  it("accepts the shipped files", () => {
    for (const locale of ["pt-BR", "en"] as const)
      expect(METHODOLOGY[locale].map((section) => section.id)).toEqual([...SECTION_IDS]);
  });

  it("rejects a missing section", () => {
    const missing = good.replace("<!-- section:caveats -->\n## caveats heading\n\nprose.", "");
    expect(() => parseMethodology(missing, "test.md")).toThrow(/caveats/);
  });

  it("rejects sections out of order", () => {
    const swapped = SECTION_IDS.map((id) => `<!-- section:${id} -->\n## ${id}\n\nprose.`);
    const reordered = [swapped[1], swapped[0], ...swapped.slice(2)].join("\n\n");
    expect(() => parseMethodology(reordered, "test.md")).toThrow(/in that order/);
  });

  it("rejects an unknown section id", () => {
    expect(() => parseMethodology(`${good}\n\n<!-- section:opinion -->\n## op\n\np.`, "t.md")).toThrow(
      /unknown section marker "opinion"/,
    );
  });

  it("rejects a section without a heading, since the ToC links its text", () => {
    expect(() =>
      parseMethodology(good.replace("## sources heading", "sources heading"), "test.md"),
    ).toThrow(/must open with a "## " heading/);
  });

  it("rejects prose that would render nowhere", () => {
    expect(() => parseMethodology(`stray sentence\n\n${good}`, "test.md")).toThrow(
      /before the first section marker/,
    );
  });
});
