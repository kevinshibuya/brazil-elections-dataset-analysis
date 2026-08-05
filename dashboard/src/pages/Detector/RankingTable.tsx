/**
 * The ranking: the national top-N sections for one section-scoring detector.
 *
 * Three things this table is careful about.
 *
 * **It sorts.** The exporter happens to write `anomalies/{key}.json` in
 * descending score order, but "happens to" is not a contract the reader's
 * ranking should rest on, so the page orders the rows itself and breaks ties
 * deterministically. A ranking that is silently mis-ordered is worse than no
 * ranking.
 *
 * **It says what a high row is.** Every row links straight into the
 * municipality drill-down, because a score is a place to start looking, and
 * the only useful next action is to go and look. `flag_threshold` is drawn as
 * a line through the table rather than as a verdict column.
 *
 * **It does not pretend the top of a clean list is a finding.** When no row in
 * the export reached the detector's threshold, the table says so above itself.
 * A top-500 of scores that are all zero is exactly what a detector that found
 * nothing produces, and rendering it bare would read as five hundred
 * accusations.
 */

import { useMemo, useState } from "react";
import { Link } from "react-router";

import { useAnomalies } from "../../data/hooks";
import { useI18n } from "../../i18n";
import { formatDetailValue, raceLabel } from "../Municipality/format";
import { BlockState, Empty, Panel, queryState } from "./chrome";
import { formatCount, formatScore } from "./format";
import type { DetectorDefinition } from "./registry";
import { anomalyRows, type AnomaliesFile, type AnomalyRow } from "./types";

/** Rows revealed at once. 500 rows of nine columns is not a page, it is a dump. */
const PAGE = 25;

/**
 * Score descending, then a stable geographic order.
 *
 * The tie-break is not decoration: `t1_arithmetic` scores every row 0, so
 * WITHOUT it the "ranking" would be whatever order the JSON happened to have,
 * and it would change between exports for no reason a reader could see.
 */
function compareRows(a: AnomalyRow, b: AnomalyRow): number {
  return (
    b.score - a.score ||
    a.mun_code.localeCompare(b.mun_code) ||
    a.zone - b.zone ||
    a.section - b.section ||
    a.race_id.localeCompare(b.race_id)
  );
}

export default function RankingTable({ definition }: { definition: DetectorDefinition }) {
  const { t, locale } = useI18n();
  const query = useAnomalies<AnomaliesFile>(definition.scoreKey);
  const [visible, setVisible] = useState(PAGE);

  const rows = useMemo(() => [...anomalyRows(query.data)].sort(compareRows), [query.data]);

  const state = queryState(query);
  const file = query.data;
  const threshold = typeof file?.flag_threshold === "number" ? file.flag_threshold : null;
  const top = rows[0];
  const noneReached = threshold !== null && top !== undefined && top.score < threshold;

  return (
    <Panel
      title={t("detector.ranking.title")}
      caption={
        file
          ? t("detector.ranking.caption", {
              shown: formatCount(locale, rows.length),
              scored: formatCount(locale, file.rows_scored),
            })
          : undefined
      }
    >
      {state !== "ready" ? (
        <BlockState state={state} />
      ) : rows.length === 0 ? (
        <Empty>{t("detector.ranking.empty")}</Empty>
      ) : (
        <>
          {noneReached ? (
            <p className="mb-4 rounded-md border border-slate-800 bg-slate-900/60 px-3 py-2 text-xs text-slate-400">
              {t("detector.ranking.none-reached", {
                threshold: formatScore(locale, threshold),
              })}
            </p>
          ) : null}

          <div className="-mx-4 overflow-x-auto sm:-mx-5">
            <table className="w-full min-w-[36rem] border-collapse text-sm">
              <caption className="sr-only">{t("detector.ranking.table-caption")}</caption>
              <thead>
                <tr className="border-b border-slate-800 text-left text-xs uppercase tracking-wide text-slate-500">
                  <th scope="col" className="py-2 pl-4 pr-3 font-medium sm:pl-5">
                    {t("detector.ranking.rank")}
                  </th>
                  <th scope="col" className="py-2 pr-3 font-medium">
                    {t("detector.ranking.section")}
                  </th>
                  <th scope="col" className="py-2 pr-3 font-medium">
                    {t("detector.ranking.race")}
                  </th>
                  {definition.columns.map((column) => (
                    <th
                      key={column.field}
                      scope="col"
                      className="py-2 pr-3 text-right font-medium tabular-nums"
                    >
                      {t(column.labelKey)}
                    </th>
                  ))}
                  <th scope="col" className="py-2 pl-3 pr-4 text-right font-medium sm:pr-5">
                    {t("detector.ranking.score")}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {rows.slice(0, visible).map((row, index) => {
                  const flagged = threshold !== null && row.score >= threshold;
                  return (
                    <tr
                      key={`${row.mun_code}-${row.zone}-${row.section}-${row.race_id}`}
                      data-testid="ranking-row"
                      data-score={row.score}
                      data-flagged={flagged}
                      className="align-baseline transition-colors hover:bg-slate-900/60"
                    >
                      <td className="py-2.5 pl-4 pr-3 tabular-nums text-slate-500 sm:pl-5">
                        {index + 1}
                      </td>
                      <td className="py-2.5 pr-3">
                        <Link
                          to={`/municipality/${row.mun_code}`}
                          className="text-slate-200 underline-offset-2 transition-colors hover:text-white hover:underline"
                        >
                          {row.mun_name}
                        </Link>
                        <span className="ml-2 whitespace-nowrap text-xs tabular-nums text-slate-500">
                          {row.uf} · {t("detector.ranking.zone-section", {
                            zone: row.zone,
                            section: row.section,
                          })}
                        </span>
                      </td>
                      <td className="whitespace-nowrap py-2.5 pr-3 text-xs text-slate-400">
                        {raceLabel(t, row.race_id)}
                      </td>
                      {definition.columns.map((column) => (
                        <td
                          key={column.field}
                          className="py-2.5 pr-3 text-right text-xs tabular-nums text-slate-400"
                        >
                          {formatDetailValue(t, locale, row.detail[column.field] ?? null)}
                        </td>
                      ))}
                      <td className="py-2.5 pl-3 pr-4 text-right sm:pr-5">
                        <span
                          className={`inline-flex rounded border px-1.5 font-mono text-xs tabular-nums ${
                            flagged
                              ? "border-sky-400/40 bg-sky-400/10 text-sky-100"
                              : "border-slate-800 bg-slate-900 text-slate-400"
                          }`}
                        >
                          {formatScore(locale, row.score)}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-xs text-slate-500">
            <p className="tabular-nums">
              {t("detector.ranking.showing", {
                shown: formatCount(locale, Math.min(visible, rows.length)),
                total: formatCount(locale, rows.length),
              })}
            </p>
            {visible < rows.length ? (
              <button
                type="button"
                onClick={() => setVisible((current) => current + PAGE * 3)}
                className="rounded border border-slate-700 px-3 py-1.5 text-slate-300 transition-colors hover:border-slate-500 hover:text-white"
              >
                {t("detector.ranking.more")}
              </button>
            ) : null}
          </div>
        </>
      )}
    </Panel>
  );
}
