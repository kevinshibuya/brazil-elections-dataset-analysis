import { useT } from "../i18n";

/** Placeholder — the methodology write-up lands in its own slice. */
export default function Methodology() {
  const t = useT();
  return (
    <section>
      <h2 className="text-xl font-semibold tracking-tight">{t("page.methodology.title")}</h2>
      <p className="mt-2 text-sm text-slate-400">{t("page.methodology.placeholder")}</p>
    </section>
  );
}
