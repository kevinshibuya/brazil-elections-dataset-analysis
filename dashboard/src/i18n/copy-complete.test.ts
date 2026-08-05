/**
 * Acceptance test for the copy pass (Task 22): the site's public text is
 * finished, in both languages, with nothing left standing in for prose.
 *
 * WHY THE PLACEHOLDER LIST IS NOT JUST /TODO|PLACEHOLDER|lorem/. That is what
 * the plan specified, and measured against the files it was written for it
 * catches 22 of the English values and only 7 of the pt-BR ones — because the
 * pt-BR placeholders were authored in Portuguese ("Espaço reservado: …"). The
 * asymmetry runs the wrong way: pt-BR is the PRIMARY authored language and the
 * one most readers will see, so the weaker half of the gate was guarding the
 * more important half of the site. Every marker idiom actually used in these
 * files is listed below, in both languages.
 *
 * Read-only to the implementer of the copy slice.
 */

import { describe, expect, it } from "vitest";

import en from "./en.json";
import ptBR from "./pt-BR.json";
import enMarkdown from "./methodology.en.md?raw";
import ptMarkdown from "./methodology.pt.md?raw";
import { METHODOLOGY, SECTION_IDS, type MethodologySection } from "../pages/Methodology/sections";
import type { Block } from "../pages/Methodology/markdown";

/**
 * Every way a value in these files has been left as a stand-in — English and
 * Portuguese. Add to this list rather than narrowing it: a marker idiom that
 * is not here is a placeholder that ships.
 */
const PLACEHOLDER = [
  // Case-SENSITIVE and anchored, unlike the /TODO/i the plan specified: the
  // Portuguese for "methodology" is "metodologia" and for "all" is "todos", so
  // a case-insensitive TODO fails the page title of the very page this slice
  // is about, and goes on failing however the copy is rewritten.
  /\bTODO\b/,
  /PLACEHOLDER/i,
  /lorem/i,
  /espaço reservado/i,
  /\bchegam? (na|em) (uma )?etapa/i, // "chega na etapa de redação/texto"
  /arrives? in a later slice/i,
  /passagem de texto/i,
  /copy pass writes/i,
  /escreve isto/i,
];

const LOCALE_FILES: ReadonlyArray<readonly [string, Record<string, string>]> = [
  ["pt-BR.json", ptBR],
  ["en.json", en],
];

/** The visible text of a parsed markdown block. */
function blockText(block: Block): string {
  return block.kind === "ul" || block.kind === "ol" ? block.items.join(" ") : block.text;
}

/** Everything under a section's heading — the prose the copy pass must supply. */
function proseOf(section: MethodologySection): string {
  return section.blocks.map(blockText).join(" ").trim();
}

describe("public copy is complete", () => {
  it("keeps the two locales key-identical", () => {
    expect(Object.keys(ptBR).sort()).toEqual(Object.keys(en).sort());
  });

  it("leaves no placeholder value in either locale", () => {
    const offenders: string[] = [];
    for (const [file, dict] of LOCALE_FILES)
      for (const [key, value] of Object.entries(dict))
        if (PLACEHOLDER.some((pattern) => pattern.test(value)))
          offenders.push(`${file} → ${key} = ${value}`);
    expect(offenders, `${offenders.length} placeholder value(s) remain`).toEqual([]);
  });

  it("leaves no empty value in either locale", () => {
    // Deleting a placeholder is not the same as replacing it.
    const empty: string[] = [];
    for (const [file, dict] of LOCALE_FILES)
      for (const [key, value] of Object.entries(dict))
        if (value.trim() === "") empty.push(`${file} → ${key}`);
    expect(empty).toEqual([]);
  });

  it("does not name the Tier-1 integrity checks after a digit test", () => {
    // `t1` is Tier 1, not "first digit". These two check arithmetic identities
    // (aptos/blank/null/nominal vs. the section total) and cross-race presence;
    // neither looks at a digit. Only `digits` is a digit test. On a site whose
    // whole claim is methodological care, this one is a credibility error.
    const misnamed = /d[ií]gito|digit|benford/i;
    for (const [file, dict] of LOCALE_FILES)
      for (const key of ["detector.t1-arithmetic.name", "detector.t1-presence.name"])
        expect(dict[key] ?? "", `${file} → ${key}`).not.toMatch(misnamed);
  });

  it("fills every methodology skeleton section with prose, in both locales", () => {
    for (const [locale, sections] of Object.entries(METHODOLOGY)) {
      expect(sections.map((section) => section.id)).toEqual([...SECTION_IDS]);
      for (const section of sections) {
        const prose = proseOf(section);
        // A heading with nothing under it is the failure this catches; the
        // floor is low enough to be a floor and high enough to mean "written".
        expect(prose.length, `${locale} → ${section.id} is empty or near-empty`).toBeGreaterThan(200);
        // And no sub-heading may be the last thing in a section, or be followed
        // straight by another heading — same failure, one level down.
        section.blocks.forEach((block, i) => {
          if (block.kind !== "heading") return;
          const next = section.blocks[i + 1];
          expect(next && next.kind !== "heading", `${locale} → ${section.id}: "${block.text}"`).toBe(
            true,
          );
        });
      }
    }
  });

  it("leaves no placeholder marker in the methodology markdown", () => {
    for (const [name, markdown] of [
      ["methodology.pt.md", ptMarkdown],
      ["methodology.en.md", enMarkdown],
    ] as const) {
      // The doc-comment at the top of each file documents the supported syntax
      // and legitimately contains the word TODO-free examples; strip comments
      // so only authored prose is judged.
      const prose = markdown.replace(/<!--[\s\S]*?-->/g, "");
      for (const pattern of PLACEHOLDER)
        expect(prose, `${name} matches ${pattern}`).not.toMatch(pattern);
    }
  });

  it("discusses the Benford caveat explicitly, by name, in both locales", () => {
    // The digit tests are the site's most misreadable output, and Deckert et
    // al. 2011 is the paper that says why. A caveats section that gestures at
    // "limitations" without naming it is not the caveat the spec asked for.
    for (const [locale, sections] of Object.entries(METHODOLOGY)) {
      const caveats = sections.find((section) => section.id === "caveats");
      expect(caveats, `${locale} has no caveats section`).toBeDefined();
      expect(proseOf(caveats!), `${locale} caveats`).toMatch(/Deckert/);
    }
  });
});
