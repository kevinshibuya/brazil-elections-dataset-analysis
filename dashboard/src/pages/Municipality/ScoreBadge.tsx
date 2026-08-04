import { useI18n } from "../../i18n";
import { formatScore, scoreBand, type ScoreBand } from "./format";

const BAND_CLASS: Record<ScoreBand, string> = {
  none: "border-slate-800 bg-slate-900 text-slate-500",
  low: "border-slate-700 bg-slate-800/60 text-slate-300",
  elevated: "border-amber-500/40 bg-amber-500/10 text-amber-200",
  high: "border-rose-500/40 bg-rose-500/10 text-rose-200",
};

/**
 * A score, in a band-coloured pill.
 *
 * The colour is the only editorial thing on this page, so it stops at three
 * bands and never says "suspicious" — the number is a statistical distance and
 * the panel next to it is where the baseline that produced it lives.
 */
export default function ScoreBadge({
  value,
  size = "sm",
}: {
  value: number | null;
  size?: "sm" | "lg";
}) {
  const { t, locale } = useI18n();
  const band = scoreBand(value);

  return (
    <span
      className={`inline-flex items-center justify-end rounded border px-1.5 font-mono tabular-nums ${
        size === "lg" ? "py-0.5 text-sm" : "text-xs"
      } ${BAND_CLASS[band]}`}
      title={t("municipality.score.label")}
    >
      {formatScore(locale, value)}
    </span>
  );
}
