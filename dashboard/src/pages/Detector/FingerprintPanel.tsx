/**
 * The Klimek fingerprint: turnout on x, the leader's share of valid votes on
 * y, one cell per (1 %, 1 %) bin, shaded by how many sections landed there.
 *
 * Hand-rolled SVG rather than Recharts: a 2-D histogram is a heatmap, Recharts
 * has no heatmap, and faking one out of a scatter with square dots would cost
 * more code than the 30 lines below and read worse. Only the non-empty cells
 * are drawn — a 100 × 100 grid is 10 000 rects and typically fewer than 2 000
 * of them carry anything.
 *
 * What the picture is for: a clean election is one blob, because turnout
 * varies for local reasons and share varies for political ones and the two are
 * close to independent. The top-right corner — high turnout AND a near-total
 * share — is the shape ballot-stuffing makes, so the corner mass is printed
 * next to the chart as a number instead of left to the eye.
 *
 * `corner_mass = 0` and "no fingerprint in this export" are different things
 * and are rendered differently.
 */

import { useMemo, useState } from "react";

import { useStateAggregate } from "../../data/hooks";
import { useI18n } from "../../i18n";
import { ChartLegend, GRID, RAMP, rampStep } from "./charts";
import { BlockState, Empty, NotComputed, Panel, Segmented, Stat, queryState } from "./chrome";
import { formatCount, formatShare } from "./format";
import { useUfSelection } from "./state";
import { fingerprintPayload, type FingerprintRound, type StateAggregate } from "./types";

/** Both axes are per cent, and the corner is the top-right 10 %. */
const CORNER_PCT = 90;
/** Plot box, plus room for tick numbers and one rotated axis label each side. */
const PLOT = 300;
const PAD_L = 42;
const PAD_T = 8;
const PAD_B = 34;
const PAD_R = 10;
const TICKS = [0, 25, 50, 75, 100];

interface Cell {
  readonly x: number;
  readonly y: number;
  readonly count: number;
}

function cellsOf(round: FingerprintRound): Cell[] {
  const cells: Cell[] = [];
  round.grid.forEach((column, x) =>
    column.forEach((count, y) => {
      if (count > 0) cells.push({ x, y, count });
    }),
  );
  return cells;
}

function Heatmap({
  round,
  bins,
  label,
  xLabel,
  yLabel,
}: {
  round: FingerprintRound;
  bins: number;
  label: string;
  xLabel: string;
  yLabel: string;
}) {
  const cells = useMemo(() => cellsOf(round), [round]);
  const max = cells.reduce((peak, cell) => Math.max(peak, cell.count), 0);
  const scale = PLOT / bins;
  const corner = (CORNER_PCT / 100) * PLOT;

  return (
    <svg
      viewBox={`0 0 ${PAD_L + PLOT + PAD_R} ${PAD_T + PLOT + PAD_B}`}
      role="img"
      aria-label={label}
      className="h-auto w-full max-w-md"
    >
      {/* Plot area: x = turnout %, y = share %, origin bottom-left. */}
      <g transform={`translate(${PAD_L}, ${PAD_T})`}>
        <rect x={0} y={0} width={PLOT} height={PLOT} fill="#0b1220" />
        {TICKS.map((tick) => (
          <g key={tick}>
            <line
              x1={(tick / 100) * PLOT}
              x2={(tick / 100) * PLOT}
              y1={0}
              y2={PLOT}
              stroke={GRID}
              strokeWidth={0.5}
            />
            <line
              x1={0}
              x2={PLOT}
              y1={(1 - tick / 100) * PLOT}
              y2={(1 - tick / 100) * PLOT}
              stroke={GRID}
              strokeWidth={0.5}
            />
            <text
              x={(tick / 100) * PLOT}
              y={PLOT + 13}
              textAnchor="middle"
              fontSize={9}
              fill="#64748b"
            >
              {tick}
            </text>
            <text
              x={-6}
              y={(1 - tick / 100) * PLOT + 3}
              textAnchor="end"
              fontSize={9}
              fill="#64748b"
            >
              {tick}
            </text>
          </g>
        ))}

        {cells.map((cell) => (
          <rect
            key={`${cell.x}-${cell.y}`}
            x={cell.x * scale}
            y={PLOT - (cell.y + 1) * scale}
            width={scale}
            height={scale}
            fill={rampStep(cell.count, max)}
          />
        ))}

        {/* The corner the detector summarises as `corner_mass`. */}
        <rect
          x={corner}
          y={0}
          width={PLOT - corner}
          height={PLOT - corner}
          fill="none"
          stroke="#94a3b8"
          strokeDasharray="3 3"
          strokeWidth={1}
        />

        {/* Each axis is labelled on its own axis — a caption under the figure
            saying "turnout · share" leaves the reader to guess which is which. */}
        <text x={PLOT / 2} y={PLOT + 29} textAnchor="middle" fontSize={10} fill="#94a3b8">
          {xLabel}
        </text>
        <text
          transform={`translate(${-PAD_L + 11}, ${PLOT / 2}) rotate(-90)`}
          textAnchor="middle"
          fontSize={10}
          fill="#94a3b8"
        >
          {yLabel}
        </text>
      </g>
    </svg>
  );
}

export default function FingerprintPanel() {
  const { t, locale } = useI18n();
  const { ufs, uf, setUf, summaryFailed } = useUfSelection();
  const query = useStateAggregate<StateAggregate>(uf);
  const payload = fingerprintPayload(query.data);
  const [round, setRound] = useState<string | null>(null);

  const rounds = payload?.rounds;
  const roundIds = rounds ? Object.keys(rounds).sort() : [];
  const selectedRound = round ?? roundIds[roundIds.length - 1];
  const shown: FingerprintRound | null =
    (rounds && selectedRound !== undefined ? rounds[selectedRound] : undefined) ?? payload ?? null;

  const state = summaryFailed ? "error" : queryState(query);

  return (
    <Panel
      title={t("detector.fingerprint.title")}
      caption={t("detector.fingerprint.caption")}
      actions={
        <div className="flex flex-wrap items-center gap-2">
          <Segmented
            label={t("detector.uf.label")}
            value={uf ?? ""}
            options={ufs.map((entry) => ({ value: entry, label: entry }))}
            onChange={setUf}
          />
          <Segmented
            label={t("detector.round.label")}
            value={selectedRound ?? ""}
            options={roundIds.map((id) => ({
              value: id,
              label: t("detector.round.option", { round: id }),
            }))}
            onChange={setRound}
          />
        </div>
      }
    >
      {state !== "ready" ? (
        <BlockState state={state} />
      ) : !payload || !shown ? (
        <NotComputed>{t("detector.fingerprint.not-computed", { uf: uf ?? "" })}</NotComputed>
      ) : shown.n_sections === 0 ? (
        <Empty>{t("detector.fingerprint.no-sections")}</Empty>
      ) : (
        <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
          <div className="min-w-0 flex-1">
            <Heatmap
              round={shown}
              bins={payload.bins}
              label={t("detector.fingerprint.chart-label", { uf: uf ?? "" })}
              xLabel={t("detector.fingerprint.axis-x")}
              yLabel={t("detector.fingerprint.axis-y")}
            />
            <ChartLegend
              items={[
                { color: RAMP[0] as string, label: t("detector.fingerprint.legend-low") },
                { color: RAMP[RAMP.length - 1] as string, label: t("detector.fingerprint.legend-high") },
              ]}
            />
          </div>

          <dl className="grid grid-cols-2 gap-x-6 gap-y-4 lg:w-56 lg:shrink-0 lg:grid-cols-1">
            <Stat label={t("detector.uf.label")} value={uf ?? ""} />
            <Stat
              label={t("detector.fingerprint.corner-mass")}
              value={formatShare(locale, shown.corner_mass)}
              hint={t("detector.fingerprint.corner-sections", {
                sections: formatCount(locale, shown.corner_sections),
              })}
            />
            <Stat
              label={t("detector.fingerprint.sections")}
              value={formatCount(locale, shown.n_sections)}
            />
            <Stat
              label={t("detector.fingerprint.candidate")}
              value={shown.candidate_name || String(shown.candidate)}
            />
          </dl>
        </div>
      )}
    </Panel>
  );
}
