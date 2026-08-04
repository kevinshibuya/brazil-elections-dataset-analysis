import { useT } from "../i18n";

/** Placeholder — the choropleth and headlines land in the Overview slice. */
export default function Overview() {
  const t = useT();
  return (
    <section>
      <h2 className="text-xl font-semibold tracking-tight">{t("page.overview.title")}</h2>
      <p className="mt-2 text-sm text-slate-400">{t("page.overview.placeholder")}</p>
    </section>
  );
}
