import { LOCALES, translate, useI18n } from "../i18n";

/**
 * What the site shows when the published export announces a schema version
 * this build does not read.
 *
 * Deliberately BILINGUAL regardless of the current locale: a version mismatch
 * usually means the visitor is running a stale bundle, and the message has to
 * land whichever language they read. Both strings still come from the
 * dictionaries — no hardcoded copy.
 */
export default function SchemaMismatch({ found }: { found?: unknown }) {
  const { t } = useI18n();

  return (
    <section
      className="mx-auto max-w-2xl rounded-lg border border-amber-500/40 bg-amber-500/5 p-6"
      data-testid="schema-mismatch"
      role="alert"
    >
      {LOCALES.map((locale) => (
        <div key={locale} className="mb-4 last:mb-0" lang={locale}>
          <h2 className="text-lg font-semibold text-amber-200">
            {translate(locale, "error.schema-mismatch.title")}
          </h2>
          <p className="mt-1 text-sm text-slate-300">
            {translate(locale, "error.schema-mismatch.body")}
          </p>
        </div>
      ))}
      {found === undefined ? null : (
        <p className="mt-2 font-mono text-xs text-slate-500">
          {t("error.schema-mismatch.found", { found: String(found) })}
        </p>
      )}
      <button
        type="button"
        onClick={() => window.location.reload()}
        className="mt-4 rounded-md bg-amber-400 px-4 py-2 text-sm font-medium text-slate-900 hover:bg-amber-300"
      >
        {t("error.schema-mismatch.action")}
      </button>
    </section>
  );
}
