import { useId, useMemo, useState } from "react";
import { Link } from "react-router";

import { useMunicipalitiesIndex } from "../../data/hooks";
import { useI18n } from "../../i18n";
import { formatCount } from "./format";

const MAX_RESULTS = 8;

interface Entry {
  readonly code: string;
  readonly name: string;
  readonly uf: string;
  readonly sections: number;
  /** Accent-folded haystack, precomputed once for the whole index. */
  readonly haystack: string;
}

/** Accent- and case-insensitive: "caracarai" has to find CARACARAÍ. */
function fold(value: string): string {
  return value
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase();
}

/**
 * Jump to another municipality without leaving the page.
 *
 * Backed by `municipalities-index.json` — the one file that lists every
 * municipality — so it works before any shard has been fetched. Results are
 * links, not click handlers: a drill-down URL is worth sharing.
 */
export default function MunicipalitySearch({ currentCode }: { currentCode: string | undefined }) {
  const { t, locale } = useI18n();
  const index = useMunicipalitiesIndex();
  const [query, setQuery] = useState("");
  const inputId = useId();

  const entries = useMemo<Entry[]>(() => {
    const data = index.data;
    if (!data) return [];
    return data.code.map((code, i) => {
      const name = data.name[i] ?? code;
      const uf = data.uf[i] ?? "";
      return {
        code,
        name,
        uf,
        sections: data.sections[i] ?? 0,
        haystack: fold(`${name} ${uf} ${code}`),
      };
    });
  }, [index.data]);

  const trimmed = query.trim();
  const matches = useMemo(() => {
    if (trimmed === "") return [];
    const needle = fold(trimmed);
    return entries.filter((entry) => entry.haystack.includes(needle)).slice(0, MAX_RESULTS);
  }, [entries, trimmed]);

  return (
    <div className="relative">
      <label htmlFor={inputId} className="sr-only">
        {t("municipality.search.label")}
      </label>
      <input
        id={inputId}
        type="search"
        autoComplete="off"
        value={query}
        disabled={index.isError}
        onChange={(event) => setQuery(event.target.value)}
        placeholder={
          index.isError ? t("municipality.search.unavailable") : t("municipality.search.placeholder")
        }
        data-testid="municipality-search"
        className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-slate-400 focus:outline-none focus:ring-1 focus:ring-slate-400 disabled:opacity-50 sm:w-72"
      />

      {trimmed !== "" ? (
        <div className="absolute z-10 mt-1 w-full overflow-hidden rounded border border-slate-700 bg-slate-900 shadow-xl sm:w-72">
          {matches.length === 0 ? (
            <p className="px-3 py-2 text-sm text-slate-500">
              {t("municipality.search.empty", { query: trimmed })}
            </p>
          ) : (
            <ul>
              {matches.map((entry) => (
                <li key={entry.code}>
                  <Link
                    to={`/municipality/${entry.code}`}
                    onClick={() => setQuery("")}
                    aria-current={entry.code === currentCode ? "page" : undefined}
                    className="flex items-baseline justify-between gap-3 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800 aria-[current]:text-slate-400"
                  >
                    <span className="truncate">
                      {entry.name}
                      <span className="text-slate-500"> · {entry.uf}</span>
                    </span>
                    <span className="whitespace-nowrap text-xs text-slate-500">
                      {t("municipality.search.sections", {
                        sections: formatCount(locale, entry.sections),
                      })}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}
