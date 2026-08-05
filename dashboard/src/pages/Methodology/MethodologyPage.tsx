/**
 * The methodology page: what the data is, how each indicator works, what it
 * does not prove, how to re-run the whole thing, and what it is built on.
 *
 * The page is a skeleton over per-locale markdown. Prose lives in
 * `src/i18n/methodology.{pt,en}.md`; the ids, the order, the table of contents
 * and the reference list live here — so the copy pass can rewrite every word
 * without being able to move a section id or drop a citation.
 *
 * Both locales are bundled by static import (`?raw`). No runtime fetch: the
 * page has to render on a static host with the same guarantees as the rest of
 * the site, and prose is a few kilobytes.
 */

import { useI18n } from "../../i18n";
import { CITATIONS } from "./citations";
import { Markdown, renderInline } from "./markdown";
import { METHODOLOGY } from "./sections";

/** The reference list — bibliographic data, rendered field by field. */
function Citations() {
  return (
    <ol className="mt-4 space-y-3 border-t border-slate-800 pt-4 text-sm leading-relaxed text-slate-300">
      {CITATIONS.map((citation) => (
        <li key={`${citation.authors}-${citation.year}`} data-testid="citation" className="max-w-2xl">
          <span data-testid="citation-authors">{citation.authors}</span>{" "}
          <span data-testid="citation-year">({citation.year})</span>.{" "}
          <span data-testid="citation-title" className="text-slate-100">
            {citation.title}
          </span>
          .{" "}
          <cite data-testid="citation-venue" className="not-italic text-slate-400">
            {citation.venue}
          </cite>
          {citation.doi ? (
            <>
              {". "}
              <a
                href={`https://doi.org/${citation.doi}`}
                target="_blank"
                rel="noopener noreferrer"
                className="underline decoration-slate-600 underline-offset-2 transition-colors hover:text-slate-100"
              >
                doi:{citation.doi}
              </a>
            </>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

export default function MethodologyPage() {
  const { t, locale } = useI18n();
  const sections = METHODOLOGY[locale];

  return (
    <section className="space-y-10">
      <header>
        <h2 className="text-xl font-semibold tracking-tight">{t("page.methodology.title")}</h2>
        <p className="mt-2 max-w-2xl text-sm text-slate-400">{t("page.methodology.lede")}</p>
      </header>

      <nav
        data-testid="methodology-toc"
        aria-label={t("page.methodology.toc")}
        className="rounded-lg border border-slate-800 bg-slate-900/30 px-4 py-4 sm:px-5"
      >
        <h3 className="text-xs uppercase tracking-wide text-slate-500">
          {t("page.methodology.toc")}
        </h3>
        <ol className="mt-3 space-y-1.5 text-sm">
          {sections.map((section) => (
            <li key={section.id}>
              <a
                href={`#${section.id}`}
                className="text-slate-300 underline decoration-slate-700 underline-offset-4 transition-colors hover:text-slate-100"
              >
                {renderInline(section.heading)}
              </a>
            </li>
          ))}
        </ol>
      </nav>

      {sections.map((section) => (
        <section
          key={section.id}
          id={section.id}
          data-testid={`section-${section.id}`}
          // Anchored headings clear the page chrome instead of hiding under it.
          className="scroll-mt-8"
        >
          <h3 className="text-sm font-medium text-slate-200">{renderInline(section.heading)}</h3>
          <div className="mt-3">
            <Markdown blocks={section.blocks} />
            {/* The prose supplies the heading and the introduction; the list
                itself is data, so the page renders it. */}
            {section.id === "citations" ? <Citations /> : null}
          </div>
        </section>
      ))}
    </section>
  );
}
