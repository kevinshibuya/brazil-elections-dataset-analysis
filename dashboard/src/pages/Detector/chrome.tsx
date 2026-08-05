/**
 * The furniture every detector block shares: a panel, a stat, and the three
 * states a block can be in.
 *
 * The states are the point. `not-computed` (this export does not contain the
 * file) and `empty` (it does, and the test found nothing) are opposite
 * findings, and the whole page is worthless if they render alike — so they are
 * separate components with different wording, not one grey box.
 */

import type { ReactNode } from "react";

import { MissingFileError } from "../../data/manifest";
import { useT } from "../../i18n";

/** One result block: a titled, bordered region of the page. */
export function Panel({
  title,
  caption,
  actions,
  children,
}: {
  title: string;
  caption?: string | undefined;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded-lg border border-slate-800 bg-slate-900/30">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-800 px-4 py-3 sm:px-5">
        <div className="min-w-0">
          <h3 className="text-sm font-medium text-slate-200">{title}</h3>
          {caption ? <p className="mt-1 max-w-2xl text-xs text-slate-500">{caption}</p> : null}
        </div>
        {actions}
      </header>
      <div className="px-4 py-4 sm:px-5">{children}</div>
    </section>
  );
}

/** A label/value pair, in the page's tabular-numbers style. */
export function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="min-w-0">
      <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-1 break-words text-lg font-semibold tabular-nums text-slate-100">{value}</dd>
      {hint ? <p className="mt-0.5 text-xs tabular-nums text-slate-500">{hint}</p> : null}
    </div>
  );
}

/**
 * A one-row choice (state, round). Same control the overview uses for its map
 * metric — a detector page picking a different affordance for the same job
 * would just be noise.
 */
export function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: readonly { readonly value: T; readonly label: string }[];
  onChange: (value: T) => void;
}) {
  if (options.length < 2) return null;
  return (
    <div
      role="group"
      aria-label={label}
      className="flex rounded-md border border-slate-800 bg-slate-900/60 p-0.5 text-xs"
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
          className={`rounded px-2.5 py-1 transition-colors ${
            option.value === value
              ? "bg-slate-100 font-medium text-slate-900"
              : "text-slate-400 hover:text-slate-100"
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/** "We tried to read this and this export does not have it." */
export function NotComputed({ children }: { children?: ReactNode }) {
  const t = useT();
  return (
    <p
      className="rounded-md border border-dashed border-slate-700 bg-slate-900/40 px-4 py-3 text-sm text-slate-400"
      data-testid="not-computed"
    >
      {children ?? t("detector.state.not-computed")}
    </p>
  );
}

/** "It ran, and there is nothing in it." Never the same words as above. */
export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-2 text-sm text-slate-400">{children}</p>;
}

export function Loading() {
  const t = useT();
  return (
    <p className="py-2 text-sm text-slate-500" data-testid="block-status">
      {t("status.loading")}
    </p>
  );
}

export function Unavailable() {
  const t = useT();
  return (
    <p className="py-2 text-sm text-slate-500" data-testid="block-status">
      {t("status.unavailable")}
    </p>
  );
}

/**
 * The one query outcome a detector page must not blur: a file the manifest
 * has never heard of means "not computed in this export", and any other
 * failure means "we could not read it".
 */
export function queryState(query: {
  isPending: boolean;
  isError: boolean;
  error: Error | null;
}): "loading" | "not-computed" | "error" | "ready" {
  if (query.isPending) return "loading";
  if (query.error instanceof MissingFileError) return "not-computed";
  if (query.isError) return "error";
  return "ready";
}

/** Render the non-ready states, or `null` when the block should draw itself. */
export function BlockState({ state }: { state: ReturnType<typeof queryState> }) {
  if (state === "loading") return <Loading />;
  if (state === "not-computed") return <NotComputed />;
  if (state === "error") return <Unavailable />;
  return null;
}
