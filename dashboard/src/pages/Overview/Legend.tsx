import { formatShare } from "../../format";
import { useI18n } from "../../i18n";
import { NO_VALUE_FILL, OUT_OF_SCOPE_FILL, RAMP, type Layer } from "./metrics";

function Swatch({ fill, label }: { fill: string; label: string }) {
  return (
    <li className="flex items-center gap-1.5">
      <span
        aria-hidden
        className="h-3 w-3 shrink-0 rounded-sm ring-1 ring-inset ring-white/10"
        style={{ background: fill }}
      />
      <span className="tabular-nums">{label}</span>
    </li>
  );
}

/**
 * The key to the ramp — and the one place the page says, in words, which of the
 * two greys means "we have no number for this municipality" and which means
 * "this municipality is not in this export at all". Two greys with no legend
 * would be indistinguishable from a rendering bug.
 */
export default function Legend({ layer }: { layer: Layer }) {
  const { t, locale } = useI18n();

  if (layer.state === "not-computed")
    return (
      <p
        className="mt-4 rounded-md border border-dashed border-slate-700 bg-slate-900/40 px-4 py-3 text-sm text-slate-400"
        data-testid="metric-not-computed"
      >
        {t("overview.metric.not-computed")}
      </p>
    );

  const edges = layer.thresholds;
  const bins = Math.min(edges.length + 1, RAMP.length);

  function labelFor(bin: number): string {
    const share = (value: number): string => formatShare(locale, value);
    if (bin === 0)
      return layer.metric.zeroIsOwnBin
        ? t("overview.legend.none")
        : t("overview.legend.below", { to: share(edges[0] as number) });
    if (bin === bins - 1) return t("overview.legend.from", { from: share(edges[bin - 1] as number) });
    return t("overview.legend.range", {
      from: share(edges[bin - 1] as number),
      to: share(edges[bin] as number),
    });
  }

  return (
    <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-slate-400">
      <ul className="flex flex-wrap items-center gap-x-3 gap-y-2">
        {Array.from({ length: bins }, (_, bin) => (
          <Swatch key={bin} fill={RAMP[bin] as string} label={labelFor(bin)} />
        ))}
      </ul>
      <ul className="flex flex-wrap items-center gap-x-3 gap-y-2 text-slate-500">
        <Swatch fill={NO_VALUE_FILL} label={t("overview.legend.no-value")} />
        <Swatch fill={OUT_OF_SCOPE_FILL} label={t("overview.legend.out-of-scope")} />
      </ul>
    </div>
  );
}
