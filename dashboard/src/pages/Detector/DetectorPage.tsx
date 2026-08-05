/**
 * One detector, explained and then shown.
 *
 * Every page on this route is the same shell over a registry entry — what the
 * test is, what it found, what it cannot tell you, and where to look next —
 * and the shell is what guarantees the last two are never quietly dropped.
 *
 * The one structural rule worth stating out loud: on the digit screen the
 * caveat renders BEFORE the results, not under them. Digit tests are the most
 * over-read screen in this whole field — a chi-square on the second digit is a
 * screening prompt, not evidence — so the limits are placed where they are
 * read first. `caveatFirst` in the registry is that rule, and this component
 * is the only place it is honoured.
 *
 * (The caveat and explainer TEXT is deliberately placeholder: the copy slice
 * owns the words, this slice owns the slot they land in.)
 */

import type { ReactNode } from "react";
import { Link, useParams } from "react-router";

import SchemaMismatch from "../../components/SchemaMismatch";
import { isSchemaVersionError, useSummary } from "../../data/hooks";
import type { DetectorSummary } from "../../data/types";
import { useI18n } from "../../i18n";
import ClusteringPanel from "./ClusteringPanel";
import { Stat } from "./chrome";
import DigitsPanel from "./DigitsPanel";
import FingerprintPanel from "./FingerprintPanel";
import { formatCount } from "./format";
import RankingTable from "./RankingTable";
import { detectorFor, nameKey, type BlockId, type DetectorDefinition } from "./registry";
import ScoreDistribution from "./ScoreDistribution";
import UrnaModelPanel from "./UrnaModelPanel";

function Block({ id, definition }: { id: BlockId; definition: DetectorDefinition }) {
  switch (id) {
    case "ranking":
      return <RankingTable definition={definition} />;
    case "distribution":
      return <ScoreDistribution definition={definition} />;
    case "clustering":
      return <ClusteringPanel />;
    case "fingerprint":
      return <FingerprintPanel />;
    case "digits":
      return <DigitsPanel />;
    case "urna-model":
      return <UrnaModelPanel />;
  }
}

/**
 * What this detector found, in one line.
 *
 * A `distribution` detector scores no sections at all, so its `sections_scored
 * = 0` is a property of the method and not a finding — printing "0 of 0
 * flagged" for it would be a clean bill of health this page has no business
 * issuing. Hence the split on `kind` rather than on the numbers.
 */
function Finding({ definition }: { definition: DetectorDefinition }) {
  const { t, locale } = useI18n();
  const summary = useSummary();
  const entry: DetectorSummary | undefined = summary.data?.detectors[definition.id];

  if (definition.kind === "distribution")
    return (
      <p className="text-sm text-slate-400" data-testid="detector-finding">
        {t("detector.kind.distribution.finding")}
      </p>
    );

  if (!entry || entry.sections_scored === 0)
    return (
      <p className="text-sm text-slate-400" data-testid="detector-finding">
        {t("detector.state.not-computed")}
      </p>
    );

  return (
    <dl className="grid grid-cols-2 gap-6 sm:grid-cols-4" data-testid="detector-finding">
      <Stat
        label={t("detector.stat.scored")}
        value={formatCount(locale, entry.sections_scored)}
      />
      <Stat
        label={t("detector.stat.flagged")}
        value={formatCount(locale, entry.sections_flagged)}
        hint={
          entry.sections_flagged === 0 ? t("detector.state.clean") : t("detector.state.flagged")
        }
      />
    </dl>
  );
}

/** The limits of this test. Its position on the page is the point. */
function Caveat({ definition }: { definition: DetectorDefinition }) {
  const { t } = useI18n();
  return (
    <aside
      data-testid="methodology-caveat"
      className="rounded-lg border border-dashed border-slate-700 bg-slate-900/40 p-4 sm:p-5"
    >
      <h3 className="text-sm font-medium text-slate-200">{t("detector.caveat.title")}</h3>
      <p className="mt-2 max-w-2xl text-sm text-slate-400">
        {t(`detector.${definition.slug}.caveat`)}
      </p>
      <p className="mt-2 max-w-2xl text-xs text-slate-500">{t("detector.caveat.stance")}</p>
      <Link
        to="/methodology"
        className="mt-3 inline-block text-xs text-slate-300 underline underline-offset-2 transition-colors hover:text-white"
      >
        {t("detector.caveat.methodology")}
      </Link>
    </aside>
  );
}

function Unknown({ id }: { id: string | undefined }) {
  const { t } = useI18n();
  return (
    <section>
      <h2 className="text-xl font-semibold tracking-tight">{t("detector.unknown.title")}</h2>
      <p className="mt-2 text-sm text-slate-400">{t("detector.unknown.body", { id: id ?? "" })}</p>
      <Link
        to="/detectors"
        className="mt-4 inline-block text-sm text-slate-300 underline underline-offset-2 transition-colors hover:text-white"
      >
        {t("detector.all")}
      </Link>
    </section>
  );
}

/**
 * `id` normally comes from the route; the prop exists so the page can be
 * mounted directly (tests, and any future embed) without a route around it —
 * the same contract the municipality drill-down uses.
 */
export default function DetectorPage({ id: override }: { id?: string }) {
  const { t } = useI18n();
  const params = useParams<{ id: string }>();
  const summary = useSummary();
  const definition = detectorFor(override ?? params.id);

  if (isSchemaVersionError(summary.error)) return <SchemaMismatch found={summary.error.found} />;
  if (!definition) return <Unknown id={override ?? params.id} />;

  const caveat: ReactNode = <Caveat definition={definition} />;

  return (
    <section className="space-y-8" key={definition.id}>
      <header>
        <p className="text-xs uppercase tracking-wide text-slate-500">
          {t(`detector.kind.${definition.kind}.label`)}
        </p>
        <h2 className="mt-1 text-xl font-semibold tracking-tight">{t(nameKey(definition))}</h2>
        <p className="mt-2 max-w-2xl text-sm text-slate-400">
          {t(`detector.${definition.slug}.lede`)}
        </p>
      </header>

      <div className="border-y border-slate-800 py-5">
        <Finding definition={definition} />
      </div>

      {definition.caveatFirst ? caveat : null}

      <div className="space-y-6" data-testid="detector-results">
        {definition.blocks.map((block) => (
          <Block key={block} id={block} definition={definition} />
        ))}
      </div>

      {definition.caveatFirst ? null : caveat}

      <footer className="flex flex-wrap gap-x-6 gap-y-2 border-t border-slate-800 pt-5 text-sm">
        <Link
          to="/detectors"
          className="text-slate-400 underline-offset-2 transition-colors hover:text-slate-100 hover:underline"
        >
          {t("detector.all")}
        </Link>
        <Link
          to="/methodology"
          className="text-slate-400 underline-offset-2 transition-colors hover:text-slate-100 hover:underline"
        >
          {t("detector.caveat.methodology")}
        </Link>
      </footer>
    </section>
  );
}
