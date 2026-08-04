import { useParams } from "react-router";

import SchemaMismatch from "../../components/SchemaMismatch";
import { isSchemaVersionError, useShard } from "../../data/hooks";
import { useI18n } from "../../i18n";
import { formatCount } from "./format";
import MunicipalitySearch from "./MunicipalitySearch";
import SectionExplorer from "./SectionExplorer";

/**
 * Municipality drill-down: every section of one municipality, with the
 * detector output recorded against it.
 *
 * `code` normally comes from the route; the prop exists so the page can be
 * mounted directly (tests, and any future embed) without a route around it.
 *
 * Whether the export wrote this municipality as one shard or as per-zone
 * subshards is `useShard`'s business and shows up nowhere below — a split
 * municipality renders exactly like an unsplit one, zones and all.
 */
export default function MunicipalityPage({ code: override }: { code?: string }) {
  const params = useParams<{ code: string }>();
  const code = override ?? params.code;
  const { t, locale } = useI18n();
  const shard = useShard(code);

  if (isSchemaVersionError(shard.error)) return <SchemaMismatch found={shard.error.found} />;

  const data = shard.data;
  const zones = data ? new Set(data.zone).size : 0;

  return (
    <section>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="text-xl font-semibold tracking-tight">
            {data ? data.name : t("page.municipality.title")}
          </h2>
          <p className="mt-1 text-sm text-slate-400">
            {t("municipality.code", { code: code ?? "" })}
            {data ? ` · ${data.uf}` : ""}
            {data
              ? ` · ${t("municipality.sections-loaded", {
                  sections: formatCount(locale, data.section.length),
                  races: data.races.length,
                })}`
              : ""}
            {zones > 1 ? ` · ${t("municipality.zones", { zones })}` : ""}
          </p>
        </div>
        <MunicipalitySearch currentCode={code} />
      </div>

      {code === undefined ? (
        <p className="mt-6 text-sm text-slate-400">{t("municipality.no-selection")}</p>
      ) : shard.isPending ? (
        <p className="mt-6 text-sm text-slate-400" data-testid="shard-status">
          {t("status.loading")}
        </p>
      ) : shard.isError ? (
        <p className="mt-6 text-sm text-slate-400" data-testid="shard-status">
          {t("status.unavailable")}
        </p>
      ) : (
        <SectionExplorer key={code} shard={shard.data} />
      )}
    </section>
  );
}
