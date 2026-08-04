import { useSummary } from "../data/hooks";
import { useI18n } from "../i18n";

function formatTimestamp(iso: string, locale: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(
    parsed,
  );
}

/**
 * Shell chrome, not page content: which export the site is currently reading.
 *
 * It is also the shortest possible end-to-end proof that the data layer works —
 * manifest fetched, logical name resolved to its hashed path, JSON version-gated.
 */
export default function DataStatus({ generated }: { generated: string | undefined }) {
  const { t, locale } = useI18n();
  const summary = useSummary();

  if (summary.isPending) return <p className="text-xs text-slate-500">{t("status.loading")}</p>;
  if (summary.isError)
    return <p className="text-xs text-slate-500">{t("data.export.unavailable")}</p>;

  return (
    <p className="text-xs text-slate-500" data-testid="data-status">
      <span className="text-slate-400">{t("data.export.label")}</span>{" "}
      {t("data.export.counts", {
        municipalities: summary.data.municipalities,
        sections: summary.data.sections,
      })}
      {generated
        ? ` · ${t("data.export.generated", { generated: formatTimestamp(generated, locale) })}`
        : ""}
    </p>
  );
}
