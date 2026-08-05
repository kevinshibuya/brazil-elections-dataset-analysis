import { useMemo, useState } from "react";
import { Link } from "react-router";

import SchemaMismatch from "../../components/SchemaMismatch";
import { isSchemaVersionError, useMunicipalitiesIndex, useSummary } from "../../data/hooks";
import { formatCount, formatShare } from "../../format";
import { useI18n } from "../../i18n";
import Choropleth from "./Choropleth";
import HeadlineCards from "./HeadlineCards";
import { buildLayer, METRIC_IDS, METRICS, type MetricId } from "./metrics";

/** How many municipalities the "where the signal is" list names. */
const TOP_N = 8;

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-1 text-lg font-semibold tabular-nums text-slate-100">{value}</dd>
    </div>
  );
}

/**
 * The front page: what this export contains, where it is, and what the
 * detectors made of it.
 *
 * The reading order is deliberate — scope first (how much data is behind this),
 * then the map (where), then the detector findings (what), then the specific
 * municipalities to look at. A reader who stops after the first screen should
 * still have been told the size of the thing they are looking at.
 */
export default function OverviewPage() {
  const { t, locale } = useI18n();
  const summary = useSummary();
  const index = useMunicipalitiesIndex();
  const [metricId, setMetricId] = useState<MetricId>("leading-share");

  const layer = useMemo(
    () => (index.data ? buildLayer(METRICS[metricId], index.data) : null),
    [index.data, metricId],
  );

  const top = useMemo(() => {
    if (!layer || !index.data || layer.state !== "ready") return [];
    const data = index.data;
    return data.code
      .map((code, i) => ({
        code,
        name: data.name[i] ?? code,
        uf: data.uf[i] ?? "",
        value: layer.values.get(code) ?? null,
      }))
      .filter((entry): entry is typeof entry & { value: number } => entry.value !== null)
      .sort((a, b) => b.value - a.value)
      .slice(0, TOP_N);
  }, [index.data, layer]);

  const schemaError =
    (isSchemaVersionError(summary.error) ? summary.error : null) ??
    (isSchemaVersionError(index.error) ? index.error : null);
  if (schemaError) return <SchemaMismatch found={schemaError.found} />;

  const headline = index.data?.headline;

  return (
    <section className="space-y-10">
      <header>
        <h2 className="text-xl font-semibold tracking-tight">{t("page.overview.title")}</h2>
        <p className="mt-2 max-w-2xl text-sm text-slate-400">{t("overview.lede")}</p>
      </header>

      {summary.data ? (
        <dl className="grid grid-cols-2 gap-6 border-y border-slate-800 py-5 sm:grid-cols-4">
          <Stat
            label={t("overview.stat.ufs")}
            value={formatCount(locale, summary.data.ufs.length)}
          />
          <Stat
            label={t("overview.stat.municipalities")}
            value={formatCount(locale, summary.data.municipalities)}
          />
          <Stat
            label={t("overview.stat.sections")}
            value={formatCount(locale, summary.data.sections)}
          />
          <Stat
            label={t("overview.stat.annulled")}
            value={formatCount(locale, summary.data.annulled.votes)}
          />
        </dl>
      ) : null}

      <div>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="min-w-0">
            <h3 className="text-sm font-medium text-slate-200">{t("overview.map.title")}</h3>
            <p className="mt-1 text-xs text-slate-500">
              {metricId === "leading-share" && headline && headline.candidates.length > 0
                ? headline.candidates.map((candidate) => candidate.name).join(" · ")
                : t(`overview.metric.${metricId}.caption`)}
            </p>
          </div>

          <div
            role="group"
            aria-label={t("overview.metric.label")}
            className="flex rounded-md border border-slate-800 bg-slate-900/60 p-0.5 text-xs"
          >
            {METRIC_IDS.map((id) => (
              <button
                key={id}
                type="button"
                aria-pressed={id === metricId}
                onClick={() => setMetricId(id)}
                className={`rounded px-3 py-1.5 transition-colors ${
                  id === metricId
                    ? "bg-slate-100 font-medium text-slate-900"
                    : "text-slate-400 hover:text-slate-100"
                }`}
              >
                {t(`overview.metric.${id}`)}
              </button>
            ))}
          </div>
        </div>

        <div className="mt-4 overflow-hidden rounded-lg border border-slate-800 bg-slate-900/30 p-3 sm:p-4">
          {index.isPending ? (
            <p className="py-16 text-center text-sm text-slate-500" data-testid="map-status">
              {t("status.loading")}
            </p>
          ) : index.isError || !index.data || !layer ? (
            <p className="py-16 text-center text-sm text-slate-500" data-testid="map-status">
              {t("status.unavailable")}
            </p>
          ) : (
            <Choropleth index={index.data} layer={layer} />
          )}
        </div>
      </div>

      <div>
        <h3 className="text-sm font-medium text-slate-200">{t("overview.headlines.title")}</h3>
        <p className="mt-1 max-w-2xl text-xs text-slate-500">{t("overview.headlines.caption")}</p>
        <div className="mt-4">
          {summary.isPending ? (
            <p className="text-sm text-slate-500">{t("status.loading")}</p>
          ) : summary.isError || !summary.data ? (
            <p className="text-sm text-slate-500">{t("status.unavailable")}</p>
          ) : (
            <HeadlineCards summary={summary.data} />
          )}
        </div>
      </div>

      {top.length > 0 ? (
        <div>
          <h3 className="text-sm font-medium text-slate-200">{t("overview.top.title")}</h3>
          <p className="mt-1 max-w-2xl text-xs text-slate-500">{t("overview.top.caption")}</p>
          {/* Also the map's keyboard-reachable equivalent: 5 570 focusable
              polygons would be a worse experience than a ranked list of links. */}
          <ul className="mt-4 divide-y divide-slate-800 border-y border-slate-800">
            {top.map((entry) => (
              <li key={entry.code}>
                <Link
                  to={`/municipality/${entry.code}`}
                  className="flex items-baseline justify-between gap-4 py-2.5 text-sm text-slate-300 transition-colors hover:text-white"
                >
                  <span className="truncate">
                    {entry.name}
                    <span className="text-slate-500"> · {entry.uf}</span>
                  </span>
                  <span className="whitespace-nowrap tabular-nums text-slate-400">
                    {formatShare(locale, entry.value)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
