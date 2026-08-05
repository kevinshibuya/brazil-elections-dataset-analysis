/**
 * The choropleth's geometry — a committed bundle asset, NOT an export file.
 *
 * `public/geo/br-municipalities.topo.json` ships with the site: it is the same
 * 5 570 municipality boundaries whichever election ran, so it deliberately does
 * NOT go through `data/data-manifest.json`. The manifest resolves the files the
 * exporter writes; putting a static asset in it would make the map's
 * availability depend on an export having been generated.
 *
 * It is fetched, never imported, so 1.9 MB of coordinates stays out of the JS
 * bundle and only the page that draws a map pays for it. Nothing about it
 * touches the network beyond this origin — there is no tile server anywhere in
 * this app, by design (`public/geo/README.md`).
 */

/** TopoJSON's quantization frame: `absolute = delta * scale + translate`. */
export interface TopologyTransform {
  readonly scale: readonly [number, number];
  readonly translate: readonly [number, number];
}

/**
 * One geometry in a TopoJSON object.
 *
 * `arcs` is `number[][]` for a Polygon (rings of arc indices) and
 * `number[][][]` for a MultiPolygon. A negative index `i` means "arc `~i`,
 * traversed backwards" — that is the sharing trick that makes a shared border
 * one arc rather than two.
 */
export interface TopoGeometry {
  readonly type: string;
  readonly arcs?: readonly (readonly number[])[] | readonly (readonly (readonly number[])[])[];
  readonly properties?: { readonly code?: string; readonly uf?: string };
}

export interface TopoObject {
  readonly type: string;
  readonly geometries: readonly TopoGeometry[];
}

export interface Topology {
  readonly type: string;
  readonly transform?: TopologyTransform;
  /** Delta-encoded when `transform` is present, absolute lon/lat otherwise. */
  readonly arcs: readonly (readonly (readonly number[])[])[];
  readonly objects: { readonly [name: string]: TopoObject };
}

/** Object name holding the 5 570 municipality polygons, keyed by TSE code. */
export const MUNICIPALITIES_OBJECT = "municipalities";

/** Object name holding the 27 dissolved UF outlines. */
export const STATES_OBJECT = "states";

/** Path of the asset relative to the site root. */
export const GEO_TOPOJSON_PATH = "geo/br-municipalities.topo.json";

/** Absolute URL of the topology, honouring the Vite base path. */
export function geoUrl(): string {
  const base = import.meta.env.BASE_URL;
  return `${base.endsWith("/") ? base : `${base}/`}${GEO_TOPOJSON_PATH}`;
}

/** A topology whose shape this build cannot draw. */
export class TopologyShapeError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TopologyShapeError";
  }
}

function requireObject(topology: Topology, name: string): void {
  const object = topology.objects[name];
  if (!object || !Array.isArray(object.geometries))
    throw new TopologyShapeError(`${GEO_TOPOJSON_PATH}: missing object "${name}"`);
}

/**
 * Fetch and shape-check the topology.
 *
 * Only the parts the renderer indexes into are validated — the object names and
 * the arc array. Everything below that is coordinates, and a malformed
 * coordinate would fail the same way whether it was checked here or drawn.
 */
export async function fetchTopology(): Promise<Topology> {
  const url = geoUrl();
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status} ${response.statusText}`);
  const document = (await response.json()) as Topology;
  if (typeof document !== "object" || document === null || !Array.isArray(document.arcs))
    throw new TopologyShapeError(`${GEO_TOPOJSON_PATH}: expected a TopoJSON topology`);
  requireObject(document, MUNICIPALITIES_OBJECT);
  requireObject(document, STATES_OBJECT);
  return document;
}
