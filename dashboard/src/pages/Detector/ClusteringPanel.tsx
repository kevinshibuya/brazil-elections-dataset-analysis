/**
 * Is the composite's high set CONCENTRATED, or just scattered?
 *
 * The composite marks the top 1 % of sections by combined signal. Some zone or
 * municipality always holds more of that 1 % than average — with enough groups,
 * one of them is extreme by luck alone. So each group is tested against a
 * hypergeometric null (fixed number of highs, drawn without replacement) and
 * the whole family is put through Benjamini–Hochberg at q = 0.05.
 *
 * The usual and correct outcome is that NOTHING survives, and this panel says
 * exactly that in words. "No group survived correction" is a result — it is
 * the sentence that keeps a top-500 ranking from being read as a map of fraud
 * — and it must never be rendered as an empty table or a missing block.
 */

import { useAnomalies } from "../../data/hooks";
import { useI18n } from "../../i18n";
import { BlockState, Empty, NotComputed, Panel, queryState } from "./chrome";
import { formatCount, formatScore } from "./format";
import { clusteringResult, type ClusteringTest } from "./types";

/** Grouping levels this build ships copy for. Anything else prints raw. */
const KNOWN_LEVELS = new Set(["zone", "municipality"]);

/** `municipality_round2` → a level and a round, or `null` if it is neither. */
function parseFamily(family: string): { level: string; round: string } | null {
  const match = /^(.+)_round([12])$/.exec(family);
  const level = match?.[1];
  const round = match?.[2];
  return level !== undefined && round !== undefined ? { level, round } : null;
}

export default function ClusteringPanel() {
  const { t, locale } = useI18n();
  const query = useAnomalies<unknown>("clustering");
  const result = clusteringResult(query.data);
  const state = queryState(query);

  const families: [string, ClusteringTest][] = result ? Object.entries(result.tests) : [];
  const surviving = families.reduce((total, [, test]) => total + test.surviving, 0);

  return (
    <Panel
      title={t("detector.clustering.title")}
      caption={
        result
          ? t("detector.clustering.caption", {
              q: formatScore(locale, result.q),
              model: result.null_model,
            })
          : undefined
      }
    >
      {state !== "ready" ? (
        <BlockState state={state} />
      ) : !result ? (
        // `computed: false` — the composite never ran, so there is no high set
        // to test. Not the same as "tested and nothing clustered".
        <NotComputed>{t("detector.clustering.not-computed")}</NotComputed>
      ) : families.length === 0 ? (
        <Empty>{t("detector.clustering.no-families")}</Empty>
      ) : (
        <>
          <p
            className={`mb-4 rounded-md border px-3 py-2 text-sm ${
              surviving > 0
                ? "border-sky-400/30 bg-sky-400/5 text-sky-100"
                : "border-slate-800 bg-slate-900/60 text-slate-300"
            }`}
            data-testid="clustering-finding"
          >
            {surviving > 0
              ? t("detector.clustering.survived", { groups: formatCount(locale, surviving) })
              : t("detector.clustering.none-survived")}
          </p>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[30rem] text-sm">
              <caption className="sr-only">{t("detector.clustering.table-caption")}</caption>
              <thead>
                <tr className="border-b border-slate-800 text-left text-xs uppercase tracking-wide text-slate-500">
                  <th scope="col" className="py-2 pr-3 font-medium">
                    {t("detector.clustering.family")}
                  </th>
                  <th scope="col" className="py-2 pr-3 text-right font-medium">
                    {t("detector.clustering.groups")}
                  </th>
                  <th scope="col" className="py-2 pr-3 text-right font-medium">
                    {t("detector.clustering.high")}
                  </th>
                  <th scope="col" className="py-2 pr-3 text-right font-medium">
                    {t("detector.clustering.sections")}
                  </th>
                  <th scope="col" className="py-2 text-right font-medium">
                    {t("detector.clustering.surviving")}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70 tabular-nums">
                {families.map(([family, test]) => {
                  const parsed = parseFamily(family);
                  return (
                    <tr key={family}>
                      <td className="py-2 pr-3 text-slate-300">
                        {parsed
                          ? t("detector.clustering.family.label", {
                              level: KNOWN_LEVELS.has(parsed.level)
                                ? t(`detector.clustering.level.${parsed.level}`)
                                : parsed.level,
                              round: parsed.round,
                            })
                          : family}
                      </td>
                      <td className="py-2 pr-3 text-right text-slate-400">
                        {formatCount(locale, test.groups_tested)}
                      </td>
                      <td className="py-2 pr-3 text-right text-slate-400">
                        {formatCount(locale, test.high)}
                      </td>
                      <td className="py-2 pr-3 text-right text-slate-400">
                        {formatCount(locale, test.sections)}
                      </td>
                      <td
                        className={`py-2 text-right ${
                          test.surviving > 0 ? "text-sky-200" : "text-slate-400"
                        }`}
                      >
                        {formatCount(locale, test.surviving)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Panel>
  );
}
