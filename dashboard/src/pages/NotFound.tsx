import { Link } from "react-router";

import { useT } from "../i18n";

export default function NotFound() {
  const t = useT();
  return (
    <section>
      <h2 className="text-xl font-semibold tracking-tight">{t("page.not-found.title")}</h2>
      <p className="mt-2 text-sm text-slate-400">{t("page.not-found.body")}</p>
      <Link to="/" className="mt-4 inline-block text-sm text-sky-400 hover:text-sky-300">
        {t("page.not-found.back")}
      </Link>
    </section>
  );
}
