import { useT } from "../i18n";

/** Placeholder — the per-detector pages land in the Detectors slice. */
export default function Detectors() {
  const t = useT();
  return (
    <section>
      <h2 className="text-xl font-semibold tracking-tight">{t("page.detectors.title")}</h2>
      <p className="mt-2 text-sm text-slate-400">{t("page.detectors.placeholder")}</p>
    </section>
  );
}
