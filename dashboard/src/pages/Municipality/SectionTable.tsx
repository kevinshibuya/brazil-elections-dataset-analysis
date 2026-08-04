import { useI18n } from "../../i18n";
import { formatCount, formatShare } from "./format";
import type { CandidateView, SectionRow } from "./model";
import ScoreBadge from "./ScoreBadge";

/** What the table can be ordered by. `order` is the ballot order (zone, section). */
export type SortKey = "order" | "turnout" | "score";

export interface SortState {
  readonly key: SortKey;
  readonly dir: "asc" | "desc";
}

/** Rows per page. Large enough that most municipalities fit in one. */
export const PAGE_SIZE = 50;

const HEAD_CLASS = "px-2 py-2 text-left text-xs font-medium uppercase tracking-wide text-slate-400";
const CELL_CLASS = "px-2 py-1.5 tabular-nums";

function SortableHead({
  label,
  sortKey,
  sort,
  onSort,
  align = "left",
}: {
  label: string;
  sortKey: SortKey;
  sort: SortState;
  onSort: (key: SortKey) => void;
  align?: "left" | "right";
}) {
  const active = sort.key === sortKey;
  return (
    <th
      scope="col"
      className={`${HEAD_CLASS} ${align === "right" ? "text-right" : ""}`}
      aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
    >
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={`inline-flex items-center gap-1 uppercase transition-colors hover:text-slate-200 ${
          active ? "text-slate-200" : ""
        }`}
      >
        {label}
        <span aria-hidden="true" className={active ? "opacity-100" : "opacity-0"}>
          {sort.dir === "asc" ? "▲" : "▼"}
        </span>
      </button>
    </th>
  );
}

/**
 * The section table: one row per section, paginated.
 *
 * Rows are keyed and identified by the shard's dense section index, never by
 * the section number — two zones of the same municipality can both have a
 * section 1, and the panel joins back on this index.
 */
export default function SectionTable({
  rows,
  candidates,
  sort,
  onSort,
  selected,
  onSelect,
  page,
  pageCount,
  onPage,
  total,
}: {
  rows: readonly SectionRow[];
  candidates: readonly CandidateView[];
  sort: SortState;
  onSort: (key: SortKey) => void;
  selected: number | null;
  onSelect: (index: number) => void;
  page: number;
  pageCount: number;
  onPage: (page: number) => void;
  total: number;
}) {
  const { t, locale } = useI18n();
  const first = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const last = page * PAGE_SIZE + rows.length;

  return (
    <div className="min-w-0">
      <div className="overflow-x-auto rounded-lg border border-slate-800">
        <table className="w-full border-collapse text-sm">
          <caption className="sr-only">{t("municipality.table.caption")}</caption>
          <thead className="border-b border-slate-800 bg-slate-900/60">
            <tr>
              <SortableHead
                label={t("municipality.table.section")}
                sortKey="order"
                sort={sort}
                onSort={onSort}
              />
              <SortableHead
                label={t("municipality.table.turnout")}
                sortKey="turnout"
                sort={sort}
                onSort={onSort}
                align="right"
              />
              {candidates.map((candidate) => (
                <th
                  key={candidate.number}
                  scope="col"
                  className={`${HEAD_CLASS} text-right`}
                  title={candidate.name}
                >
                  <span className="block max-w-28 truncate">{candidate.name}</span>
                </th>
              ))}
              <th scope="col" className={`${HEAD_CLASS} text-right`}>
                {t("municipality.table.nominal")}
              </th>
              <SortableHead
                label={t("municipality.table.score")}
                sortKey="score"
                sort={sort}
                onSort={onSort}
                align="right"
              />
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.index}
                data-testid="section-row"
                data-max-score={row.maxScore ?? ""}
                aria-current={selected === row.index ? "true" : undefined}
                tabIndex={0}
                onClick={() => onSelect(row.index)}
                onKeyDown={(event) => {
                  if (event.key !== "Enter" && event.key !== " ") return;
                  event.preventDefault();
                  onSelect(row.index);
                }}
                className={`cursor-pointer border-b border-slate-800/60 outline-none transition-colors last:border-b-0 focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-slate-400 ${
                  selected === row.index ? "bg-slate-800/80" : "hover:bg-slate-900"
                }`}
              >
                <td className={`${CELL_CLASS} whitespace-nowrap text-slate-200`}>
                  <span className="text-slate-500">
                    {t("municipality.table.zone-prefix", { zone: row.zone })}
                  </span>{" "}
                  {row.section}
                </td>
                <td className={`${CELL_CLASS} text-right text-slate-300`}>
                  {formatShare(locale, row.turnout)}
                </td>
                {row.shares.map((share, i) => (
                  <td
                    key={candidates[i]?.number ?? i}
                    className={`${CELL_CLASS} text-right text-slate-300`}
                  >
                    {formatShare(locale, share)}
                  </td>
                ))}
                <td className={`${CELL_CLASS} text-right text-slate-500`}>
                  {formatCount(locale, row.nominal)}
                </td>
                <td className={`${CELL_CLASS} text-right`}>
                  <ScoreBadge value={row.maxScore} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {rows.length === 0 ? (
          <p className="px-4 py-8 text-center text-sm text-slate-400">
            {t("municipality.table.empty")}
          </p>
        ) : null}
      </div>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-xs text-slate-400">
        <p>{t("municipality.table.showing", { first, last, total })}</p>
        {pageCount > 1 ? (
          <nav className="flex items-center gap-2" aria-label={t("municipality.pagination.label")}>
            <button
              type="button"
              className="rounded border border-slate-700 px-2 py-1 disabled:opacity-40"
              disabled={page === 0}
              onClick={() => onPage(page - 1)}
            >
              {t("municipality.pagination.previous")}
            </button>
            <span>{t("municipality.pagination.page", { page: page + 1, pages: pageCount })}</span>
            <button
              type="button"
              className="rounded border border-slate-700 px-2 py-1 disabled:opacity-40"
              disabled={page >= pageCount - 1}
              onClick={() => onPage(page + 1)}
            >
              {t("municipality.pagination.next")}
            </button>
          </nav>
        ) : null}
      </div>
    </div>
  );
}
