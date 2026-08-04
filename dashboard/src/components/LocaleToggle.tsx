import { LOCALES, useI18n } from "../i18n";

/** Persisted pt-BR / EN switch. Lives in the header on every page. */
export default function LocaleToggle() {
  const { locale, setLocale, t } = useI18n();

  return (
    <div
      className="flex items-center gap-1 rounded-full border border-slate-700 p-0.5"
      role="group"
      aria-label={t("locale.label")}
    >
      {LOCALES.map((candidate) => {
        const active = candidate === locale;
        return (
          <button
            key={candidate}
            type="button"
            onClick={() => setLocale(candidate)}
            aria-pressed={active}
            className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
              active
                ? "bg-slate-100 text-slate-900"
                : "text-slate-400 hover:text-slate-100"
            }`}
          >
            {t(`locale.${candidate}`)}
          </button>
        );
      })}
    </div>
  );
}
