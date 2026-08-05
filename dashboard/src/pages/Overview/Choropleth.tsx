import { useQuery } from "@tanstack/react-query";
import { useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";

import { fetchTopology } from "../../data/geo";
import type { MunicipalitiesIndex } from "../../data/types";
import { formatCount, formatShare } from "../../format";
import { useI18n } from "../../i18n";
import Legend from "./Legend";
import { fillOf, NO_VALUE_FILL, OUT_OF_SCOPE_FILL, type Layer } from "./metrics";
import { frameBox, projectTopology, unionBox, type MapShape } from "./projection";

/**
 * Frame shape. The map sits in a wide column, so the viewport is stretched to
 * 1.35 : 1 and padded by a third of the subject's longer side — enough
 * surrounding country to say "this is where in Brazil you are looking".
 */
const FRAME_ASPECT = 1.35;
const FRAME_PADDING = 0.35;

/** Stroke widths in pixels at the map's natural scale, held constant on zoom. */
const BORDER_STROKE = 0.35;
const STATE_STROKE = 0.9;
const HIGHLIGHT_STROKE = 1.6;

/** Where the pointer is, in container pixels, and what is under it. */
interface Hover {
  readonly shape: MapShape;
  readonly x: number;
  readonly y: number;
}

/** One municipality, as the tooltip describes it. */
interface Described {
  readonly name: string;
  readonly uf: string;
  readonly sections: number;
  readonly value: number | null;
  readonly flagged: number | null;
}

function useGeography() {
  return useQuery({
    queryKey: ["geo", "br-municipalities"],
    queryFn: fetchTopology,
    staleTime: Number.POSITIVE_INFINITY,
  });
}

/**
 * The national municipality choropleth.
 *
 * Three things are deliberate:
 *
 * - **Every geometry is drawn, but only municipalities the export covers carry
 *   `data-mun` / `data-value`.** A single-state export then renders as Brazil
 *   with one state coloured in, which is an honest picture of the export's
 *   scope; the alternative — drawing only what is covered — makes a partial
 *   export look like a whole country.
 * - **Pointer handlers live on the group, not on 5 570 paths.** The paths carry
 *   attributes only, so a hover neither re-renders them nor allocates a closure
 *   per polygon; the highlight is a single overlay path drawn on top.
 * - **Exterior ("ZZ") municipalities are absent from the topology**, so they
 *   are off the map by construction rather than by a filter that could rot.
 */
export default function Choropleth({
  index,
  layer,
}: {
  index: MunicipalitiesIndex;
  layer: Layer;
}) {
  const { t, locale } = useI18n();
  const navigate = useNavigate();
  const geography = useGeography();
  const containerRef = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<Hover | null>(null);

  const map = useMemo(
    () => (geography.data ? projectTopology(geography.data) : null),
    [geography.data],
  );

  const described = useMemo(() => {
    const byCode = new Map<string, Described>();
    for (let i = 0; i < index.code.length; i += 1) {
      const code = index.code[i] as string;
      byCode.set(code, {
        name: index.name[i] ?? code,
        uf: index.uf[i] ?? "",
        sections: index.sections[i] ?? 0,
        value: layer.values.get(code) ?? null,
        flagged: index.flag_density[i] ?? null,
      });
    }
    return byCode;
  }, [index, layer]);

  const shapesByCode = useMemo(() => {
    const byCode = new Map<string, MapShape>();
    for (const shape of map?.municipalities ?? []) byCode.set(shape.id, shape);
    return byCode;
  }, [map]);

  /**
   * Frame the map on what this export actually covers.
   *
   * A single-state export drawn at national scale is 92 % empty page, so the
   * viewport is fitted to the covered municipalities — the rest of the country
   * still renders around it, greyed, which is what makes the framing legible
   * as "one state of Brazil" rather than as a map of a state.
   */
  const view = useMemo(() => {
    if (!map) return null;
    const covered = map.municipalities.filter((shape) => described.has(shape.id));
    const box = unionBox(covered.map((shape) => shape.box));
    return box
      ? frameBox(box, FRAME_ASPECT, FRAME_PADDING)
      : { x: 0, y: 0, width: map.width, height: map.height };
  }, [map, described]);

  // Strokes are authored at the map's natural scale and divided back out, so a
  // hairline border stays a hairline however far the frame is zoomed in.
  const strokeScale = view && map ? view.width / map.width : 1;

  function locate(event: { clientX: number; clientY: number; target: EventTarget }): Hover | null {
    const element = event.target as Element | null;
    const code = element instanceof Element ? (element.getAttribute("data-mun") ?? "") : "";
    const shape = shapesByCode.get(code);
    const rect = containerRef.current?.getBoundingClientRect();
    if (!shape || !rect) return null;
    return { shape, x: event.clientX - rect.left, y: event.clientY - rect.top };
  }

  if (geography.isPending)
    return (
      <p className="py-16 text-center text-sm text-slate-500" data-testid="map-status">
        {t("status.loading")}
      </p>
    );

  if (geography.isError || !map || !view)
    return (
      <p className="py-16 text-center text-sm text-slate-500" data-testid="map-status">
        {t("overview.map.unavailable")}
      </p>
    );

  const hovered = hover ? described.get(hover.shape.id) : undefined;

  return (
    <div ref={containerRef} className="relative">
      <svg
        viewBox={`${view.x} ${view.y} ${view.width} ${view.height}`}
        className="h-auto w-full [touch-action:pan-y]"
        role="img"
        aria-label={t("overview.map.label")}
        data-testid="choropleth"
        onMouseMove={(event) => setHover(locate(event))}
        onMouseLeave={() => setHover(null)}
        onClick={(event) => {
          const target = locate(event);
          if (target) void navigate(`/municipality/${target.shape.id}`);
        }}
      >
        <g strokeLinejoin="round">
          {map.municipalities.map((shape) => {
            const entry = described.get(shape.id);
            const value = entry?.value ?? undefined;
            return (
              <path
                key={shape.id}
                d={shape.d}
                {...(entry ? { "data-mun": shape.id } : {})}
                {...(value === undefined ? {} : { "data-value": value })}
                fill={
                  entry === undefined
                    ? OUT_OF_SCOPE_FILL
                    : value === undefined
                      ? NO_VALUE_FILL
                      : fillOf(layer, value)
                }
                stroke="#020617"
                strokeWidth={BORDER_STROKE * strokeScale}
                className={entry ? "cursor-pointer" : undefined}
              />
            );
          })}
        </g>

        {/* State outlines: drawn from the same arcs as the borders above, so
            they land exactly on top of them instead of a pixel beside. */}
        <g
          fill="none"
          stroke="#64748b"
          strokeOpacity={0.55}
          strokeWidth={STATE_STROKE * strokeScale}
          pointerEvents="none"
        >
          {map.states.map((shape) => (
            <path key={shape.id} d={shape.d} />
          ))}
        </g>

        {hover ? (
          <path
            d={hover.shape.d}
            fill="none"
            stroke="#f8fafc"
            strokeWidth={HIGHLIGHT_STROKE * strokeScale}
            pointerEvents="none"
          />
        ) : null}
      </svg>

      {hover && hovered ? (
        <div
          className="pointer-events-none absolute z-10 max-w-64 -translate-x-1/2 -translate-y-full rounded-md border border-slate-700 bg-slate-900/95 px-3 py-2 text-xs shadow-xl backdrop-blur"
          style={{ left: hover.x, top: hover.y - 12 }}
          role="status"
        >
          <p className="font-medium text-slate-100">
            {hovered.name}
            <span className="text-slate-500"> · {hovered.uf}</span>
          </p>
          <p className="mt-1 text-slate-300 tabular-nums">
            {layer.metric.id === "flag-density"
              ? t("overview.tooltip.flagged", {
                  flagged: formatCount(locale, hovered.flagged ?? 0),
                  sections: formatCount(locale, hovered.sections),
                })
              : t(`overview.tooltip.${layer.metric.id === "turnout" ? "turnout" : "share"}`, {
                  share: formatShare(locale, hovered.value),
                })}
          </p>
          <p className="mt-1 text-slate-500">
            {hovered.value === null
              ? t("overview.tooltip.no-value")
              : t("overview.tooltip.open", {
                  sections: formatCount(locale, hovered.sections),
                })}
          </p>
        </div>
      ) : null}

      <Legend layer={layer} />
    </div>
  );
}
