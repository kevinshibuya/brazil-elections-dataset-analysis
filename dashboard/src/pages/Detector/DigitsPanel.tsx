/**
 * The digit screen: observed digit frequencies against the law they would
 * follow if nobody had touched them.
 *
 * One stratum = one state × municipality-size bucket × test. Each card draws
 * the ten observed frequencies as bars and the expected distribution — Benford
 * for the second digit, uniform for the last — as a dashed neutral line. The
 * expectation is the null hypothesis, not a rival series, so it is grey and it
 * is a different mark: the eye should read "how far off the line are the bars",
 * which is exactly what the chi-square underneath reports.
 *
 * **A skipped stratum shows no p-value, because it has none.** The exporter
 * writes `{skipped: true, reason}` with the `p` key ABSENT when a stratum is
 * too sparse for a chi-square to mean anything, and the one thing this panel
 * must never do is fill that hole with a zero, a dash that looks like a
 * result, or a card that looks like every other card.
 */

import { useMemo } from "react";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { useStateAggregate } from "../../data/hooks";
import { useI18n } from "../../i18n";
import { ACCENT, AXIS, ChartLegend, Figure, REFERENCE, TOOLTIP } from "./charts";
import { BlockState, Empty, NotComputed, Panel, Segmented, queryState } from "./chrome";
import { formatCount, formatP, formatPercentPoints, formatScore } from "./format";
import { useUfSelection } from "./state";
import { digitsPayload, type DigitStratum, type StateAggregate } from "./types";

/** Reading order: biggest municipalities first, then the two tests. */
const BUCKET_ORDER = ["large", "mid", "small"];
const TEST_ORDER = ["second_digit", "last_digit"];

function rank(order: readonly string[], value: string): number {
  const index = order.indexOf(value);
  return index === -1 ? order.length : index;
}

function slug(value: string): string {
  return value.replace(/_/g, "-");
}

/**
 * A label for a bucket or a test — its own wire name when this build has no
 * copy for it. The exporter may add a size bucket or a third digit test before
 * the dashboard learns about it, and a stratum that rendered as a raw message
 * key would look like a bug rather than like a new test.
 */
function labelOf(
  t: (key: string, params?: Record<string, string | number>) => string,
  known: ReadonlySet<string>,
  namespace: string,
  value: string,
): string {
  return known.has(value) ? t(`${namespace}.${slug(value)}`) : value;
}

function StratumCard({ stratum }: { stratum: DigitStratum }) {
  const { t, locale } = useI18n();

  const observed = stratum.digit_freqs;
  const expected = stratum.expected_freqs;
  const counts = stratum.digit_counts;
  const data = useMemo(
    () =>
      observed
        ? observed.map((value, digit) => ({
            digit: String(digit),
            observed: value,
            expected: expected?.[digit] ?? null,
            count: counts?.[digit] ?? null,
          }))
        : [],
    [observed, expected, counts],
  );

  const title = t("detector.digits.stratum", {
    bucket: labelOf(t, new Set(BUCKET_ORDER), "detector.digits.bucket", stratum.bucket),
    test: labelOf(t, new Set(TEST_ORDER), "detector.digits.test", stratum.test),
  });

  return (
    <div className="rounded-md border border-slate-800 bg-slate-950/40 p-4" data-testid="digit-stratum">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h4 className="text-sm font-medium text-slate-200">{title}</h4>
        <p className="text-xs tabular-nums text-slate-500">
          {t("detector.digits.n", { n: formatCount(locale, stratum.n) })}
        </p>
      </div>

      {stratum.skipped ? (
        <div className="mt-3">
          <NotComputed>
            {t("detector.digits.skipped", { reason: stratum.reason ?? "" })}
          </NotComputed>
        </div>
      ) : (
        <>
          <dl className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs tabular-nums text-slate-400">
            <div className="flex gap-1.5">
              <dt className="text-slate-500">{t("detector.digits.chi2")}</dt>
              <dd>{formatScore(locale, stratum.chi2 ?? null)}</dd>
            </div>
            <div className="flex gap-1.5">
              <dt className="text-slate-500">{t("detector.digits.dof")}</dt>
              <dd>{stratum.dof ?? "—"}</dd>
            </div>
            <div className="flex gap-1.5">
              <dt className="text-slate-500">{t("detector.digits.p")}</dt>
              <dd>{formatP(locale, stratum.p)}</dd>
            </div>
          </dl>

          {data.length === 0 ? (
            <Empty>{t("detector.digits.no-frequencies")}</Empty>
          ) : (
            <>
              <Figure height={180} label={title}>
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={data} margin={{ top: 8, right: 4, bottom: 0, left: -20 }}>
                    <CartesianGrid stroke={AXIS.stroke} strokeDasharray="2 4" vertical={false} />
                    <XAxis
                      dataKey="digit"
                      tick={AXIS.tick}
                      tickLine={AXIS.tickLine}
                      axisLine={AXIS.axisLine}
                      stroke={AXIS.stroke}
                    />
                    <YAxis
                      tick={AXIS.tick}
                      tickLine={AXIS.tickLine}
                      axisLine={AXIS.axisLine}
                      stroke={AXIS.stroke}
                      tickFormatter={(value: number) => `${formatPercentPoints(locale, value)} %`}
                    />
                    <Tooltip
                      contentStyle={TOOLTIP.contentStyle}
                      labelStyle={TOOLTIP.labelStyle}
                      itemStyle={TOOLTIP.itemStyle}
                      cursor={TOOLTIP.cursor}
                      formatter={(value) =>
                        typeof value === "number" ? formatPercentPoints(locale, value) : ""
                      }
                      labelFormatter={(label) =>
                        t("detector.digits.digit", { digit: String(label ?? "") })
                      }
                    />
                    <Bar
                      dataKey="observed"
                      name={t("detector.digits.observed")}
                      fill={ACCENT}
                      radius={[4, 4, 0, 0]}
                      isAnimationActive={false}
                    />
                    <Line
                      dataKey="expected"
                      name={t("detector.digits.expected")}
                      type="linear"
                      stroke={REFERENCE}
                      strokeWidth={2}
                      strokeDasharray="4 3"
                      dot={{ r: 2.5, fill: REFERENCE, stroke: "none" }}
                      isAnimationActive={false}
                    />
                  </ComposedChart>
                </ResponsiveContainer>
              </Figure>
              <ChartLegend
                items={[
                  { color: ACCENT, label: t("detector.digits.observed") },
                  { color: REFERENCE, label: t("detector.digits.expected") },
                ]}
              />
            </>
          )}
        </>
      )}
    </div>
  );
}

export default function DigitsPanel() {
  const { t, locale } = useI18n();
  const { ufs, uf, setUf, summaryFailed } = useUfSelection();
  const query = useStateAggregate<StateAggregate>(uf);
  const payload = digitsPayload(query.data);

  const strata = useMemo(
    () =>
      [...(payload?.strata ?? [])].sort(
        (a, b) =>
          rank(BUCKET_ORDER, a.bucket) - rank(BUCKET_ORDER, b.bucket) ||
          rank(TEST_ORDER, a.test) - rank(TEST_ORDER, b.test) ||
          a.test.localeCompare(b.test),
      ),
    [payload],
  );

  const state = summaryFailed ? "error" : queryState(query);

  return (
    <Panel
      title={t("detector.digits.title")}
      caption={
        payload
          ? t("detector.digits.caption", {
              uf: payload.uf,
              counts: formatCount(locale, payload.n_counts),
            })
          : undefined
      }
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
        <NotComputed>{t("detector.digits.not-computed", { uf: uf ?? "" })}</NotComputed>
      ) : strata.length === 0 ? (
        <Empty>{t("detector.digits.no-strata")}</Empty>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {strata.map((stratum) => (
            <StratumCard key={`${stratum.bucket}-${stratum.test}`} stratum={stratum} />
          ))}
        </div>
      )}
    </Panel>
  );
}
