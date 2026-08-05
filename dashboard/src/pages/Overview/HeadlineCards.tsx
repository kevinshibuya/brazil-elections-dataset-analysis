import { Link } from "react-router";

import type { DetectorSummary, Summary } from "../../data/types";
import { formatCount } from "../../format";
import { useI18n, type MessageKey, type TranslationParams } from "../../i18n";

type Translate = (key: MessageKey, params?: TranslationParams) => string;

/** Detector keys this build ships a name for. Anything else degrades to its key. */
const KNOWN_DETECTORS = new Set([
  "composite",
  "digits",
  "extreme_dominance",
  "fingerprint",
  "split_ticket",
  "t1_arithmetic",
  "t1_presence",
  "turnout",
  "urna_model",
]);

/**
 * A detector's display name.
 *
 * An export may carry a detector this build has never heard of — detectors are
 * added over time and the dashboard is deployed separately from the pipeline —
 * so an unknown key renders humanised instead of vanishing from the front page.
 */
function detectorName(t: Translate, key: string): string {
  return KNOWN_DETECTORS.has(key)
    ? t(`detector.${key.replace(/_/g, "-")}.name`)
    : key.replace(/_/g, " ");
}

/**
 * What a detector has to say, which is NOT the same question as how many
 * sections it flagged.
 *
 * `not-computed` (it never ran in this export) and `clean` (it ran over
 * thousands of sections and flagged none) are opposite findings, and a card
 * that rendered both as "0" would be the single most misleading thing on this
 * page.
 */
type Finding = "flagged" | "clean" | "not-computed";

function findingOf(detector: DetectorSummary): Finding {
  if (detector.sections_scored === 0) return "not-computed";
  return detector.sections_flagged > 0 ? "flagged" : "clean";
}

const ORDER: Record<Finding, number> = { flagged: 0, clean: 1, "not-computed": 2 };

interface Card {
  readonly key: string;
  readonly detector: DetectorSummary;
  readonly finding: Finding;
}

function cardsOf(summary: Summary): Card[] {
  return Object.entries(summary.detectors)
    .map(([key, detector]) => ({ key, detector, finding: findingOf(detector) }))
    .sort(
      (a, b) =>
        ORDER[a.finding] - ORDER[b.finding] ||
        b.detector.sections_flagged - a.detector.sections_flagged ||
        a.key.localeCompare(b.key),
    );
}

/**
 * The findings index: one card per detector, each a link into its own page.
 *
 * Every detector gets a card, not just the ones with something to report —
 * publishing only the hits would turn a set of tests into a list of
 * accusations. The ones that found nothing are the evidence that the ones that
 * did are not just noise.
 */
export default function HeadlineCards({ summary }: { summary: Summary }) {
  const { t, locale } = useI18n();
  const cards = cardsOf(summary);

  return (
    <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {cards.map(({ key, detector, finding }) => (
        <li key={key} data-testid="headline-card" data-finding={finding}>
          <Link
            to={`/detectors/${key}`}
            className="group flex h-full flex-col justify-between gap-4 rounded-lg border border-slate-800 bg-slate-900/40 p-4 transition-colors hover:border-slate-600 hover:bg-slate-900 focus-visible:border-slate-400 focus-visible:outline-none"
          >
            <h4 className="text-sm font-medium text-slate-200 group-hover:text-white">
              {detectorName(t, key)}
            </h4>

            {finding === "not-computed" ? (
              <p className="text-sm text-slate-500">{t("overview.headline.not-computed")}</p>
            ) : finding === "clean" ? (
              <div>
                <p className="text-sm text-slate-300">{t("overview.headline.clean")}</p>
                <p className="mt-1 text-xs text-slate-500 tabular-nums">
                  {t("overview.headline.scored", {
                    scored: formatCount(locale, detector.sections_scored),
                  })}
                </p>
              </div>
            ) : (
              <div>
                <p className="text-2xl font-semibold text-sky-200 tabular-nums">
                  {formatCount(locale, detector.sections_flagged)}
                </p>
                <p className="mt-1 text-xs text-slate-500 tabular-nums">
                  {t("overview.headline.flagged-of", {
                    scored: formatCount(locale, detector.sections_scored),
                  })}
                </p>
              </div>
            )}
          </Link>
        </li>
      ))}
    </ul>
  );
}
