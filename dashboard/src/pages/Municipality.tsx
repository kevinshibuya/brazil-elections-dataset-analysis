import { useParams } from "react-router";

import SchemaMismatch from "../components/SchemaMismatch";
import { isSchemaVersionError, useShard } from "../data/hooks";
import { useT } from "../i18n";

/**
 * Placeholder — the section table, filters and anomaly panel land in the
 * drill-down slice.
 *
 * It does load the shard, though: this is the route that proves the split-aware
 * loader against the real export in a browser. Whether the municipality was
 * exported as one file or as per-zone subshards is invisible here by design.
 */
export default function Municipality() {
  const { code } = useParams<{ code: string }>();
  const t = useT();
  const shard = useShard(code);

  if (isSchemaVersionError(shard.error)) return <SchemaMismatch found={shard.error.found} />;

  return (
    <section>
      <h2 className="text-xl font-semibold tracking-tight">
        {shard.data ? shard.data.name : t("page.municipality.title")}
      </h2>
      <p className="mt-1 text-sm text-slate-400">{t("municipality.code", { code: code ?? "" })}</p>

      <p className="mt-4 text-sm text-slate-300" data-testid="shard-status">
        {shard.isPending
          ? t("status.loading")
          : shard.isError
            ? t("status.unavailable")
            : t("municipality.sections-loaded", {
                sections: shard.data.section.length,
                races: shard.data.races.length,
              })}
      </p>

      <p className="mt-6 text-sm text-slate-500">{t("page.municipality.placeholder")}</p>
    </section>
  );
}
