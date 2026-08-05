/**
 * The methodology page's skeleton: five sections with stable ids, parsed out
 * of the per-locale markdown at module load.
 *
 * WHY THE IDS COME FROM MARKER COMMENTS AND NOT FROM THE HEADINGS. The copy
 * pass rewrites every heading on this page, in two languages. An id slugged
 * from heading text would move the moment that happens — silently breaking the
 * table of contents and the `#caveats` fragment the detector pages link to.
 * The marker is the contract; the visible heading is free.
 *
 * And when a file breaks that contract this module THROWS, at import time,
 * rather than rendering what it managed to understand. A methodology page that
 * quietly dropped its caveats section would be worse than one that fails
 * loudly: the missing section is exactly the one whose absence flatters the
 * findings.
 */

import type { Locale } from "../../i18n";
import ptMarkdown from "../../i18n/methodology.pt.md?raw";
import enMarkdown from "../../i18n/methodology.en.md?raw";
import { parseBlocks, stripComments, type Block } from "./markdown";

/**
 * Binding, and in this order. Shared with the table of contents, the fragment
 * links on the detector pages, and the acceptance test.
 */
export const SECTION_IDS = ["sources", "detectors", "caveats", "reproduce", "citations"] as const;

export type SectionId = (typeof SECTION_IDS)[number];

export interface MethodologySection {
  readonly id: SectionId;
  /** The section's own `##` heading, as authored — the ToC's link text. */
  readonly heading: string;
  /** Everything under that heading. */
  readonly blocks: readonly Block[];
}

const MARKER = /<!--\s*section:([a-z0-9-]+)\s*-->/g;

function isSectionId(value: string): value is SectionId {
  return (SECTION_IDS as readonly string[]).includes(value);
}

function fail(source: string, detail: string): never {
  throw new Error(`${source}: ${detail}`);
}

/**
 * Split a methodology markdown file into its five sections.
 *
 * Exported for the guard's own test: every failure mode below is a way the
 * page could go out wrong, so each one has to be reachable from a test.
 */
export function parseMethodology(markdown: string, source: string): readonly MethodologySection[] {
  const markers = [...markdown.matchAll(MARKER)];
  const found = markers.map((match) => match[1] ?? "");

  for (const id of found)
    if (!isSectionId(id))
      fail(source, `unknown section marker "${id}" — expected one of ${SECTION_IDS.join(", ")}`);

  if (found.join(",") !== SECTION_IDS.join(","))
    fail(
      source,
      `sections must be exactly ${SECTION_IDS.join(", ")} in that order, found ${
        found.length > 0 ? found.join(", ") : "none"
      }`,
    );

  const first = markers[0];
  if (first?.index !== undefined && stripComments(markdown.slice(0, first.index)).trim() !== "")
    fail(source, "text before the first section marker would not be rendered — move it into a section");

  return markers.map((match, i) => {
    const id = found[i];
    const start = (match.index ?? 0) + match[0].length;
    const next = markers[i + 1]?.index ?? markdown.length;
    const blocks = parseBlocks(markdown.slice(start, next));
    const heading = blocks[0];
    if (id === undefined || !isSectionId(id)) fail(source, "unreadable section marker");
    if (heading?.kind !== "heading" || heading.level !== 2)
      fail(source, `section "${id}" must open with a "## " heading — it is the ToC's link text`);
    return { id, heading: heading.text, blocks: blocks.slice(1) };
  });
}

/** Both locales, parsed once. A bad file fails the import, not a render. */
export const METHODOLOGY: Record<Locale, readonly MethodologySection[]> = {
  "pt-BR": parseMethodology(ptMarkdown, "i18n/methodology.pt.md"),
  en: parseMethodology(enMarkdown, "i18n/methodology.en.md"),
};
