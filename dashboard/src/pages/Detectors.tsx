/**
 * `/detectors` — the index over every detector page this build ships.
 *
 * Driven by the same registry as `/detectors/:id`, so a detector cannot exist
 * as a route and be missing from the list that is supposed to reach it. The
 * two `kind`s are separated because they answer differently shaped questions:
 * a section-score detector ranks individual sections, a distribution detector
 * has nothing to rank and reports a shape instead. Presenting them as one
 * undifferentiated list would invite the reader to expect a ranking that some
 * of these tests structurally cannot produce.
 */

import { Link } from "react-router";

import { useT } from "../i18n";
import {
  DETECTOR_IDS,
  detectorFor,
  nameKey,
  type DetectorDefinition,
} from "./Detector/registry";

const GROUPS = [
  { kind: "section-score", labelKey: "page.detectors.group.section-score" },
  { kind: "distribution", labelKey: "page.detectors.group.distribution" },
] as const;

function definitions(kind: DetectorDefinition["kind"]): DetectorDefinition[] {
  return DETECTOR_IDS.map((id) => detectorFor(id)).filter(
    (d): d is DetectorDefinition => d !== null && d.kind === kind,
  );
}

export default function Detectors() {
  const t = useT();
  return (
    <section>
      <h2 className="text-xl font-semibold tracking-tight">{t("page.detectors.title")}</h2>
      <p className="mt-2 max-w-2xl text-sm text-slate-400">{t("page.detectors.lede")}</p>

      {GROUPS.map(({ kind, labelKey }) => (
        <div key={kind} className="mt-8">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            {t(labelKey)}
          </h3>
          <ul className="mt-3 grid gap-3 sm:grid-cols-2">
            {definitions(kind).map((d) => (
              <li key={d.id}>
                <Link
                  to={`/detectors/${d.id}`}
                  data-testid="detector-link"
                  className="block rounded-lg border border-slate-800 bg-slate-900/40 p-4 transition
                             hover:border-slate-600 hover:bg-slate-900
                             focus-visible:outline focus-visible:outline-2 focus-visible:outline-sky-400"
                >
                  <span className="font-medium text-slate-100">{t(nameKey(d))}</span>
                  <span className="mt-1 block text-sm text-slate-400">
                    {t(`detector.${d.slug}.lede`)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}
