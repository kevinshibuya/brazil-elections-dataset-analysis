/**
 * How the ranked scores are spread — and where the flag threshold falls in
 * that spread.
 *
 * The honest caption matters more than the chart: this is a histogram of the
 * exported top-N rows, NOT of every scored row, so it is the shape of the
 * tail and nothing else. Saying that in words is the difference between a
 * useful picture and a claim about the whole election.
 *
 * The threshold is encoded by splitting the bars into two steps of the same
 * hue rather than by a second colour — above and below a cut are two ends of
 * one measure, and the legend names both.
 */

import { useMemo } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { useI18n } from "../../i18n";
import { AXIS, ChartLegend, Figure, HIGH, LOW, TOOLTIP } from "./charts";
import { BlockState, Empty, Panel, queryState } from "./chrome";
import { formatCount, formatScore } from "./format";
import { useAnomalies } from "../../data/hooks";
import type { DetectorDefinition } from "./registry";
import { anomalyRows, type AnomaliesFile } from "./types";

/** Enough bins to show a shape, few enough that each one holds something. */
const BINS = 16;

interface Bin {
  readonly label: string;
  readonly mid: number;
  readonly count: number;
}

function histogram(scores: readonly number[], format: (value: number) => string): Bin[] {
  const min = Math.min(...scores);
  const max = Math.max(...scores);
  const width = (max - min) / BINS;
  return Array.from({ length: BINS }, (_, index) => {
    const from = min + index * width;
    const to = from + width;
    // The last bin owns its upper edge, or the maximum falls out of the chart.
    const count = scores.filter((score) =>
      index === BINS - 1 ? score >= from && score <= to : score >= from && score < to,
    ).length;
    return { label: `${format(from)} – ${format(to)}`, mid: from + width / 2, count };
  });
}

export default function ScoreDistribution({ definition }: { definition: DetectorDefinition }) {
  const { t, locale } = useI18n();
  const query = useAnomalies<AnomaliesFile>(definition.scoreKey);
  const scores = useMemo(() => anomalyRows(query.data).map((row) => row.score), [query.data]);

  const threshold =
    typeof query.data?.flag_threshold === "number" ? query.data.flag_threshold : null;
  const min = scores.length > 0 ? Math.min(...scores) : 0;
  const max = scores.length > 0 ? Math.max(...scores) : 0;
  const degenerate = scores.length > 0 && max === min;

  const bins = useMemo(
    () => (scores.length === 0 || max === min ? [] : histogram(scores, (v) => formatScore(locale, v))),
    [scores, max, min, locale],
  );

  const state = queryState(query);

  return (
    <Panel
      title={t("detector.distribution.title")}
      caption={t("detector.distribution.caption", { shown: formatCount(locale, scores.length) })}
    >
      {state !== "ready" ? (
        <BlockState state={state} />
      ) : scores.length === 0 ? (
        <Empty>{t("detector.ranking.empty")}</Empty>
      ) : degenerate ? (
        // Every ranked row carries the same score — the usual reason is that the
        // detector found nothing at all. A 16-bin chart of one value would be a
        // picture of nothing pretending to be a distribution.
        <Empty>
          {t("detector.distribution.single-value", { value: formatScore(locale, max) })}
        </Empty>
      ) : (
        <>
          <Figure label={t("detector.distribution.title")}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={bins} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
                <CartesianGrid stroke={AXIS.stroke} strokeDasharray="2 4" vertical={false} />
                <XAxis
                  dataKey="label"
                  interval={Math.ceil(BINS / 5)}
                  tick={AXIS.tick}
                  tickLine={AXIS.tickLine}
                  axisLine={AXIS.axisLine}
                  stroke={AXIS.stroke}
                />
                <YAxis
                  allowDecimals={false}
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
                />
                <Bar
                  dataKey="count"
                  name={t("detector.distribution.axis-count")}
                  radius={[4, 4, 0, 0]}
                  isAnimationActive={false}
                >
                  {bins.map((bin) => (
                    <Cell
                      key={bin.label}
                      fill={threshold !== null && bin.mid >= threshold ? HIGH : LOW}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </Figure>
          {threshold === null ? null : (
            <ChartLegend
              items={[
                { color: HIGH, label: t("detector.distribution.above", { threshold: formatScore(locale, threshold) }) },
                { color: LOW, label: t("detector.distribution.below", { threshold: formatScore(locale, threshold) }) },
              ]}
            />
          )}
        </>
      )}
    </Panel>
  );
}
