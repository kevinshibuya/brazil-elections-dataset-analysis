import type { JsonValue, ScoreDetail, ScoreRaceId, Shard } from "../../data/types";
import { useI18n } from "../../i18n";
import {
  detailFieldLabel,
  detectorLabel,
  formatCount,
  formatDetailValue,
  formatShare,
  raceLabel,
  type Translate,
} from "./format";
import {
  detectorEntries,
  shareBaseline,
  turnoutBaseline,
  type DetectorEntry,
  type LocalBaseline,
  type RaceLens,
  type SampleSize,
} from "./model";
import ScoreBadge from "./ScoreBadge";

/** Known detail fields first, in reading order; anything newer keeps its own. */
const DETAIL_ORDER = ["cand", "k", "n", "p_hat", "rho_hat", "p_hi", "p_lo", "baseline"];

function orderedDetail(detail: ScoreDetail, sample: SampleSize): [string, JsonValue][] {
  const fields = Object.keys(detail).sort((a, b) => {
    const ra = DETAIL_ORDER.indexOf(a);
    const rb = DETAIL_ORDER.indexOf(b);
    return (ra === -1 ? DETAIL_ORDER.length : ra) - (rb === -1 ? DETAIL_ORDER.length : rb);
  });
  return fields
    // The sample size already has its own line above; repeating it as a field
    // just makes the reader check whether the two numbers agree.
    .filter((field) => !(field === "n" && detail[field] === sample.n))
    .map((field): [string, JsonValue] => [field, detail[field] ?? null]);
}

function candidateName(shard: Shard, raceId: ScoreRaceId, number: number): string | null {
  for (const entry of shard.cand[raceId] ?? []) if (entry[0] === number) return entry[1];
  return null;
}

function SampleLine({ sample, t }: { sample: SampleSize; t: Translate }) {
  return (
    <span className="whitespace-nowrap font-mono text-xs text-slate-400">
      {t("municipality.panel.sample-size", { n: sample.n })}{" "}
      <span className="font-sans text-slate-500">{t(`municipality.sample.${sample.kind}`)}</span>
    </span>
  );
}

/** A section value beside the same quantity over the rest of the municipality. */
function BaselineRow({ label, local }: { label: string; local: LocalBaseline }) {
  const { t, locale } = useI18n();
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-3 border-b border-slate-800/60 py-1.5 last:border-b-0">
      <dt className="min-w-0 truncate text-slate-300" title={label}>
        {label}
      </dt>
      <dd className="flex items-baseline gap-2 tabular-nums">
        <span className="font-medium text-slate-100">{formatShare(locale, local.value)}</span>
        <span className="text-xs text-slate-500">
          {t("municipality.panel.vs-baseline", {
            baseline: formatShare(locale, local.baseline),
          })}
        </span>
        <SampleLine sample={local.sample} t={t} />
      </dd>
    </div>
  );
}

function DetectorCard({ shard, entry }: { shard: Shard; entry: DetectorEntry }) {
  const { t, locale } = useI18n();

  return (
    <li
      data-testid="detector-score"
      data-detector={entry.scoreKey}
      data-race={entry.raceId}
      data-score={entry.score}
      className="rounded border border-slate-800 bg-slate-900/50 px-3 py-2"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm text-slate-200">{detectorLabel(t, entry.scoreKey)}</p>
          <p className="truncate text-xs text-slate-500">{raceLabel(t, entry.raceId)}</p>
        </div>
        <ScoreBadge value={entry.score} size="lg" />
      </div>

      <p className="mt-1.5">
        <SampleLine sample={entry.sample} t={t} />
      </p>

      {entry.detail ? (
        <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
          {orderedDetail(entry.detail, entry.sample).map(([field, value]) => {
            const named =
              field === "cand" && typeof value === "number"
                ? candidateName(shard, entry.raceId, value)
                : null;
            return (
              <div key={field} className="col-span-2 flex justify-between gap-3">
                <dt className="text-slate-500">{detailFieldLabel(t, field)}</dt>
                <dd className="truncate text-right text-slate-300" title={named ?? undefined}>
                  {field === "p_hat" && typeof value === "number"
                    ? formatShare(locale, value)
                    : formatDetailValue(t, locale, value)}
                  {named ? <span className="text-slate-400"> · {named}</span> : null}
                </dd>
              </div>
            );
          })}
        </dl>
      ) : (
        <p className="mt-2 text-xs text-slate-500">{t("municipality.panel.no-detail")}</p>
      )}
    </li>
  );
}

/**
 * Everything recorded about ONE section — the page's neutral-stance surface.
 *
 * The rule it exists to enforce: no score is ever shown on its own. Each one
 * carries the sample size it was computed over and, where the detector flagged
 * the section, the local baseline it deviated from. A reader who disagrees with
 * a flag can see exactly what it was measured against, and a section with a
 * high score and a sample size of forty votes reads as what it is.
 *
 * Scores for the reserved cross-race ids (`ALL_1` / `ALL_2`) are listed
 * alongside the per-race ones; they are the whole output of the cross-race
 * detectors and dropping them would under-report the section.
 */
export default function AnomalyPanel({
  shard,
  lens,
  index,
}: {
  shard: Shard;
  lens: RaceLens;
  index: number;
}) {
  const { t, locale } = useI18n();
  const row = lens.rows[index];
  if (!row) return null;

  const entries = detectorEntries(shard, index);
  const nominal = lens.rows.map((entry) => entry.nominal);

  return (
    <aside
      data-testid="anomaly-panel"
      aria-label={t("municipality.panel.title", { zone: row.zone, section: row.section })}
      className="min-w-0 rounded-lg border border-slate-800 bg-slate-900/40 p-4 lg:sticky lg:top-6"
    >
      <header className="border-b border-slate-800 pb-3">
        <h3 className="text-sm font-semibold tracking-tight text-slate-100">
          {t("municipality.panel.title", { zone: row.zone, section: row.section })}
        </h3>
        <p className="mt-0.5 text-xs text-slate-500">
          {t("municipality.panel.electorate", {
            aptos: formatCount(locale, row.aptos),
            comp: formatCount(locale, row.comp),
          })}
        </p>
      </header>

      <section className="mt-3">
        <h4 className="text-xs font-medium uppercase tracking-wide text-slate-400">
          {t("municipality.panel.context", { race: raceLabel(t, lens.raceId) })}
        </h4>
        <dl className="mt-1 text-sm">
          <BaselineRow
            label={t("municipality.panel.turnout")}
            local={turnoutBaseline(shard, lens.raceId, index)}
          />
          {lens.candidates.map((candidate) => (
            <BaselineRow
              key={candidate.number}
              label={candidate.name}
              local={shareBaseline(candidate, nominal, index)}
            />
          ))}
        </dl>
      </section>

      <section className="mt-4">
        <h4 className="text-xs font-medium uppercase tracking-wide text-slate-400">
          {t("municipality.panel.detectors")}
        </h4>
        {entries.length > 0 ? (
          <ul className="mt-2 space-y-2">
            {entries.map((entry) => (
              <DetectorCard key={`${entry.raceId}:${entry.scoreKey}`} shard={shard} entry={entry} />
            ))}
          </ul>
        ) : (
          <p className="mt-2 text-xs text-slate-500">{t("municipality.panel.no-scores")}</p>
        )}
      </section>

      <p className="mt-4 border-t border-slate-800 pt-3 text-xs leading-relaxed text-slate-500">
        {t("municipality.panel.stance")}
      </p>
    </aside>
  );
}
