/**
 * The urna-model contrast — the one block on this site whose entire job is to
 * show a difference between two numbers rather than the numbers themselves.
 *
 * Older voting machines and newer ones report different vote shares. Taken
 * nationally that difference (`raw_gap`) is mostly **geography**: the old
 * machines are not spread evenly, they sit where they sit, and the places they
 * sit voted the way those places vote. Compare the two machine generations
 * only WITHIN a municipality, where the electorate is held fixed, and what is
 * left (`within_mun_gap`) is the part the machines could plausibly account
 * for.
 *
 * So the two are always drawn side by side, at the same scale, with the raw
 * gap deliberately recessive and the within-municipality estimate carrying its
 * 95 % interval. Publishing the raw gap alone would be the single most
 * misleading number this project could produce; publishing only the adjusted
 * one would hide the correction that makes the point.
 */

import { useStateAggregate } from "../../data/hooks";
import { useI18n } from "../../i18n";
import { ACCENT, AXIS, AXIS_INK, Figure, MUTED, REFERENCE, TOOLTIP } from "./charts";
import { BlockState, NotComputed, Panel, Segmented, Stat, queryState } from "./chrome";
import { formatCount, formatPercentPoints, formatShare, formatSignedShare } from "./format";
import { useUfSelection } from "./state";
import { urnaModelPayload, type StateAggregate } from "./types";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ErrorBar,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

/** Model keys the exporter uses for the two machine generations. */
const MODELS = ["ue2020", "pre2020", "unknown"] as const;

export default function UrnaModelPanel() {
  const { t, locale } = useI18n();
  const { ufs, uf, setUf, summaryFailed } = useUfSelection();
  const query = useStateAggregate<StateAggregate>(uf);
  const payload = urnaModelPayload(query.data);
  const state = summaryFailed ? "error" : queryState(query);

  const rawLabel = t("detector.urna.raw-gap");
  const withinLabel = t("detector.urna.within-gap");

  const [ciLow, ciHigh] = payload?.ci95 ?? [0, 0];
  const data = payload
    ? [
        { name: rawLabel, value: payload.raw_gap, fill: MUTED, error: null },
        {
          name: withinLabel,
          value: payload.within_mun_gap,
          fill: ACCENT,
          error: [payload.within_mun_gap - ciLow, ciHigh - payload.within_mun_gap] as [
            number,
            number,
          ],
        },
      ]
    : [];

  // A symmetric domain rounded up to whole 5-point steps: the two gaps are only
  // comparable if they share a scale, and an axis reading "-20,8" invites the
  // reader to take a rounding artefact for a measurement.
  const STEP = 0.05;
  const reach = payload
    ? Math.max(Math.abs(payload.raw_gap), Math.abs(ciLow), Math.abs(ciHigh), STEP)
    : STEP;
  const span = Math.ceil((reach * 1.1) / STEP) * STEP;
  const ticks = [-span, -span / 2, 0, span / 2, span];

  return (
    <Panel
      title={t("detector.urna.title")}
      caption={t("detector.urna.caption")}
      actions={
        <Segmented
          label={t("detector.uf.label")}
          value={uf ?? ""}
          options={ufs.map((entry) => ({ value: entry, label: entry }))}
          onChange={setUf}
        />
      }
    >
      {state !== "ready" ? (
        <BlockState state={state} />
      ) : !payload ? (
        <NotComputed>{t("detector.urna.not-computed", { uf: uf ?? "" })}</NotComputed>
      ) : (
        <div className="space-y-6">
          {/* The contrast, as two numbers first — the chart is the same claim,
              drawn to scale, for a reader who prefers to see it. */}
          <dl className="grid gap-4 sm:grid-cols-2" data-testid="urna-contrast">
            <div className="rounded-md border border-slate-800 bg-slate-950/40 p-4">
              <Stat
                label={rawLabel}
                value={formatSignedShare(locale, payload.raw_gap)}
                hint={t("detector.urna.raw-gap.hint")}
              />
            </div>
            <div className="rounded-md border border-sky-400/30 bg-sky-400/5 p-4">
              <Stat
                label={withinLabel}
                value={formatSignedShare(locale, payload.within_mun_gap)}
                hint={t("detector.urna.ci95", {
                  low: formatSignedShare(locale, ciLow),
                  high: formatSignedShare(locale, ciHigh),
                })}
              />
            </div>
          </dl>

          <Figure height={140} label={t("detector.urna.chart-label")}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={data}
                layout="vertical"
                margin={{ top: 4, right: 16, bottom: 0, left: 8 }}
                barCategoryGap="30%"
              >
                <CartesianGrid stroke={AXIS.stroke} strokeDasharray="2 4" horizontal={false} />
                <XAxis
                  type="number"
                  domain={[-span, span]}
                  ticks={ticks}
                  tick={AXIS.tick}
                  tickLine={AXIS.tickLine}
                  axisLine={AXIS.axisLine}
                  stroke={AXIS.stroke}
                  tickFormatter={(value: number) => `${formatPercentPoints(locale, value)} %`}
                />
                <YAxis
                  type="category"
                  dataKey="name"
                  width={140}
                  tick={AXIS.tick}
                  tickLine={AXIS.tickLine}
                  axisLine={AXIS.axisLine}
                  stroke={AXIS.stroke}
                />
                <Tooltip
                  contentStyle={TOOLTIP.contentStyle}
                  labelStyle={TOOLTIP.labelStyle}
                  itemStyle={TOOLTIP.itemStyle}
                  cursor={TOOLTIP.cursor}
                  formatter={(value) =>
                    typeof value === "number" ? formatSignedShare(locale, value) : ""
                  }
                />
                <ReferenceLine x={0} stroke={AXIS_INK} />
                <Bar
                  dataKey="value"
                  name={t("detector.urna.gap")}
                  radius={4}
                  isAnimationActive={false}
                >
                  {data.map((entry) => (
                    <Cell key={entry.name} fill={entry.fill} />
                  ))}
                  {/* Neutral, not the bar's own colour: half of a 95 % interval lies
                      INSIDE the bar, and an accent-on-accent whisker is invisible
                      exactly where the reader needs to see it. */}
                  <ErrorBar
                    dataKey="error"
                    direction="x"
                    width={6}
                    strokeWidth={2}
                    stroke={REFERENCE}
                  />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </Figure>

          <dl className="grid grid-cols-2 gap-x-6 gap-y-4 border-t border-slate-800 pt-5 sm:grid-cols-4">
            <Stat label={t("detector.uf.label")} value={payload.uf} />
            <Stat
              label={t("detector.urna.municipalities")}
              value={formatCount(locale, payload.n_municipalities)}
              hint={t("detector.urna.municipalities.hint")}
            />
            <Stat
              label={t("detector.urna.mixed-votes")}
              value={formatCount(locale, payload.mixed_votes)}
            />
            <Stat label={t("detector.urna.se")} value={formatShare(locale, payload.se)} />
          </dl>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[24rem] text-sm">
              <caption className="sr-only">{t("detector.urna.models-caption")}</caption>
              <thead>
                <tr className="border-b border-slate-800 text-left text-xs uppercase tracking-wide text-slate-500">
                  <th scope="col" className="py-2 pr-3 font-medium">
                    {t("detector.urna.model")}
                  </th>
                  <th scope="col" className="py-2 pr-3 text-right font-medium">
                    {t("detector.urna.model-sections")}
                  </th>
                  <th scope="col" className="py-2 pr-3 text-right font-medium">
                    {t("detector.urna.model-share")}
                  </th>
                  <th scope="col" className="py-2 text-right font-medium">
                    {t("detector.urna.model-valid")}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70 tabular-nums">
                {MODELS.filter((model) => model in payload.model_sections).map((model) => (
                  <tr key={model}>
                    <td className="py-2 pr-3 text-slate-300">{t(`detector.urna.model.${model}`)}</td>
                    <td className="py-2 pr-3 text-right text-slate-400">
                      {formatCount(locale, payload.model_sections[model] ?? 0)}
                    </td>
                    <td className="py-2 pr-3 text-right text-slate-400">
                      {formatShare(locale, payload.shares[model] ?? null)}
                    </td>
                    <td className="py-2 text-right text-slate-400">
                      {formatCount(locale, payload.votes[model]?.valid ?? 0)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Panel>
  );
}
