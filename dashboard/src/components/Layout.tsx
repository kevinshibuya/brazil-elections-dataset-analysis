import { NavLink, Outlet } from "react-router";

import { isSchemaVersionError, useManifest } from "../data/hooks";
import { useT } from "../i18n";
import DataStatus from "./DataStatus";
import LocaleToggle from "./LocaleToggle";
import SchemaMismatch from "./SchemaMismatch";

const NAV = [
  { to: "/", key: "nav.overview", end: true },
  { to: "/detectors", key: "nav.detectors", end: false },
  { to: "/methodology", key: "nav.methodology", end: false },
] as const;

/**
 * The shell every route renders inside: title, navigation, locale toggle, and
 * the export status strip. It also owns the one global failure mode — a
 * schema-version mismatch replaces the page rather than decorating it.
 */
export default function Layout() {
  const t = useT();
  const manifest = useManifest();
  const schemaError = isSchemaVersionError(manifest.error) ? manifest.error : null;

  return (
    <div className="flex min-h-screen flex-col bg-slate-950 text-slate-100">
      <a
        href="#content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:rounded focus:bg-slate-100 focus:px-3 focus:py-2 focus:text-slate-900"
      >
        {t("app.skip-to-content")}
      </a>

      <header className="border-b border-slate-800">
        <div className="mx-auto flex max-w-5xl flex-wrap items-baseline justify-between gap-4 px-6 py-6">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">{t("app.title")}</h1>
            <p className="mt-1 text-sm text-slate-400">{t("app.tagline")}</p>
          </div>
          <LocaleToggle />
        </div>
        <nav aria-label={t("nav.label")} className="mx-auto max-w-5xl px-6">
          <ul className="flex gap-6 text-sm">
            {NAV.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) =>
                    `-mb-px inline-block border-b-2 pb-3 transition-colors ${
                      isActive
                        ? "border-slate-100 text-slate-100"
                        : "border-transparent text-slate-400 hover:text-slate-200"
                    }`
                  }
                >
                  {t(item.key)}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </header>

      <main id="content" className="mx-auto w-full max-w-5xl flex-1 px-6 py-10">
        {schemaError ? <SchemaMismatch found={schemaError.found} /> : <Outlet />}
      </main>

      <footer className="border-t border-slate-800">
        <div className="mx-auto flex max-w-5xl flex-col gap-2 px-6 py-6 text-xs text-slate-500">
          <DataStatus generated={manifest.data?.generated} />
          <p>{t("footer.source")}</p>
          <p>{t("footer.stance")}</p>
        </div>
      </footer>
    </div>
  );
}
