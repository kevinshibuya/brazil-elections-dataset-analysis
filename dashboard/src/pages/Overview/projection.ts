/**
 * TopoJSON → SVG path data, with no mapping library.
 *
 * A choropleth of fixed, pre-simplified polygons needs exactly three things:
 * decode the delta-encoded arcs, put lon/lat on a plane, and emit `d` strings.
 * That is ~100 lines, so this project does it here rather than taking on
 * d3-geo + topojson-client (and their bundle) for the 5 % of them it would use.
 *
 * The one design decision worth stating: **arcs are projected once, not once
 * per polygon**. Roughly a third of the arcs in the national mesh are shared
 * between two municipalities, and every state outline is made of arcs the
 * municipality layer already owns — so projecting per-geometry would do the
 * trigonometry twice and, worse, could round two copies of the same border
 * differently and leave a hairline gap between neighbours.
 */

import {
  MUNICIPALITIES_OBJECT,
  STATES_OBJECT,
  TopologyShapeError,
  type TopoGeometry,
  type Topology,
} from "../../data/geo";

/** An axis-aligned box in viewBox units. */
export interface Box {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

/** One drawable shape: an SVG path plus the keys the page joins data on. */
export interface MapShape {
  /** TSE municipality code, or the UF for a state outline. */
  readonly id: string;
  readonly uf: string;
  readonly d: string;
  /** Extent in viewBox units — what lets the page frame a subset of the map. */
  readonly box: Box;
}

/** Everything the map needs, in one coordinate system. */
export interface ProjectedMap {
  /** viewBox width — the caller's `width` argument, by construction. */
  readonly width: number;
  /** viewBox height, from the projected aspect ratio. */
  readonly height: number;
  readonly municipalities: readonly MapShape[];
  readonly states: readonly MapShape[];
}

const DEGREES_TO_RADIANS = Math.PI / 180;

/** Web-Mercator latitude clamp; Brazil is nowhere near it, but NaN is worse. */
const MAX_LATITUDE = 85;

/**
 * Spherical Mercator.
 *
 * Brazil spans ~39° of latitude, where Mercator's area exaggeration reaches
 * ~1.5× between Chuí and Oiapoque. That is a real distortion, but this map
 * encodes its quantity in COLOUR, never in area, so the exaggeration cannot
 * mislead the reading — and Mercator is the projection every reader has already
 * seen this country in.
 */
function mercatorX(longitude: number): number {
  return longitude * DEGREES_TO_RADIANS;
}

function mercatorY(latitude: number): number {
  const phi = Math.min(MAX_LATITUDE, Math.max(-MAX_LATITUDE, latitude)) * DEGREES_TO_RADIANS;
  return Math.log(Math.tan(Math.PI / 4 + phi / 2));
}

/** A projected arc: `[x0, y0, x1, y1, …]` in Mercator units. */
type ProjectedArc = Float64Array;

interface Bounds {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

function projectArcs(topology: Topology): { arcs: ProjectedArc[]; bounds: Bounds } {
  const transform = topology.transform;
  const [scaleX, scaleY] = transform ? transform.scale : [1, 1];
  const [translateX, translateY] = transform ? transform.translate : [0, 0];
  const bounds: Bounds = {
    minX: Number.POSITIVE_INFINITY,
    minY: Number.POSITIVE_INFINITY,
    maxX: Number.NEGATIVE_INFINITY,
    maxY: Number.NEGATIVE_INFINITY,
  };

  const arcs = topology.arcs.map((arc) => {
    const projected = new Float64Array(arc.length * 2);
    // Quantized arcs are deltas against a running position; unquantized ones
    // are absolute. `transform` is what tells the two apart.
    let cursorX = 0;
    let cursorY = 0;
    for (let i = 0; i < arc.length; i += 1) {
      const point = arc[i] as readonly number[];
      const rawX = point[0] ?? 0;
      const rawY = point[1] ?? 0;
      let longitude: number;
      let latitude: number;
      if (transform) {
        cursorX += rawX;
        cursorY += rawY;
        longitude = cursorX * scaleX + translateX;
        latitude = cursorY * scaleY + translateY;
      } else {
        longitude = rawX;
        latitude = rawY;
      }
      const x = mercatorX(longitude);
      const y = mercatorY(latitude);
      projected[i * 2] = x;
      projected[i * 2 + 1] = y;
      if (x < bounds.minX) bounds.minX = x;
      if (x > bounds.maxX) bounds.maxX = x;
      if (y < bounds.minY) bounds.minY = y;
      if (y > bounds.maxY) bounds.maxY = y;
    }
    return projected;
  });

  return { arcs, bounds };
}

/** `arcs` of a Polygon are rings; of a MultiPolygon, polygons of rings. */
function ringsOf(geometry: TopoGeometry): readonly (readonly number[])[] {
  const arcs = geometry.arcs;
  if (!Array.isArray(arcs)) return [];
  if (geometry.type === "MultiPolygon")
    return (arcs as readonly (readonly (readonly number[])[])[]).flat();
  return arcs as readonly (readonly number[])[];
}

/**
 * Emit one ring as `M … L … Z`.
 *
 * Coordinates are rounded to one unit of the 1 000-wide viewBox — a tenth of a
 * CSS pixel at the widest the map is ever laid out — and consecutive points
 * that round to the same place are dropped. On the national mesh that removes
 * roughly a fifth of the points from the DOM at no visible cost.
 */
function ringPath(
  ring: readonly number[],
  arcs: readonly ProjectedArc[],
  toX: (x: number) => number,
  toY: (y: number) => number,
  extent: Bounds,
): string {
  let path = "";
  let lastX = Number.NaN;
  let lastY = Number.NaN;

  for (const index of ring) {
    const reversed = index < 0;
    const arc = arcs[reversed ? ~index : index];
    if (!arc) continue;
    const points = arc.length / 2;
    for (let step = 0; step < points; step += 1) {
      // A ring continues where the previous arc ended, so the shared endpoint
      // is emitted once.
      const i = reversed ? points - 1 - step : step;
      const x = Math.round(toX(arc[i * 2] as number) * 10) / 10;
      const y = Math.round(toY(arc[i * 2 + 1] as number) * 10) / 10;
      if (x < extent.minX) extent.minX = x;
      if (x > extent.maxX) extent.maxX = x;
      if (y < extent.minY) extent.minY = y;
      if (y > extent.maxY) extent.maxY = y;
      if (x === lastX && y === lastY) continue;
      path += path === "" ? `M${x} ${y}` : `L${x} ${y}`;
      lastX = x;
      lastY = y;
    }
  }

  return path === "" ? "" : `${path}Z`;
}

function emptyBounds(): Bounds {
  return {
    minX: Number.POSITIVE_INFINITY,
    minY: Number.POSITIVE_INFINITY,
    maxX: Number.NEGATIVE_INFINITY,
    maxY: Number.NEGATIVE_INFINITY,
  };
}

function boxOf(bounds: Bounds): Box {
  return {
    x: bounds.minX,
    y: bounds.minY,
    width: bounds.maxX - bounds.minX,
    height: bounds.maxY - bounds.minY,
  };
}

/** The smallest box containing every one of `boxes`, or `null` if there are none. */
export function unionBox(boxes: readonly Box[]): Box | null {
  if (boxes.length === 0) return null;
  const bounds = emptyBounds();
  for (const box of boxes) {
    if (box.x < bounds.minX) bounds.minX = box.x;
    if (box.y < bounds.minY) bounds.minY = box.y;
    if (box.x + box.width > bounds.maxX) bounds.maxX = box.x + box.width;
    if (box.y + box.height > bounds.maxY) bounds.maxY = box.y + box.height;
  }
  return boxOf(bounds);
}

/**
 * Pad a box and stretch it to `aspect`, growing whichever axis is short.
 *
 * Deliberately NOT clamped to the map's own extent: the page is wider than it
 * is tall, and letting the frame run past the coastline costs nothing (the
 * surface out there is the page background) while clamping would squash the
 * aspect ratio back and put the subject off-centre.
 */
export function frameBox(box: Box, aspect: number, padRatio: number): Box {
  const pad = Math.max(box.width, box.height) * padRatio;
  let x = box.x - pad;
  let y = box.y - pad;
  let width = box.width + pad * 2;
  let height = box.height + pad * 2;

  if (width / height < aspect) {
    const grown = height * aspect;
    x -= (grown - width) / 2;
    width = grown;
  } else {
    const grown = width / aspect;
    y -= (grown - height) / 2;
    height = grown;
  }

  return { x, y, width, height };
}

function shapesOf(
  topology: Topology,
  objectName: string,
  key: (geometry: TopoGeometry) => string,
  arcs: readonly ProjectedArc[],
  toX: (x: number) => number,
  toY: (y: number) => number,
): MapShape[] {
  const object = topology.objects[objectName];
  if (!object) throw new TopologyShapeError(`topology has no object "${objectName}"`);
  const shapes: MapShape[] = [];
  for (const geometry of object.geometries) {
    const extent = emptyBounds();
    let d = "";
    for (const ring of ringsOf(geometry)) d += ringPath(ring, arcs, toX, toY, extent);
    if (d === "") continue;
    shapes.push({
      id: key(geometry),
      uf: geometry.properties?.uf ?? "",
      d,
      box: boxOf(extent),
    });
  }
  return shapes;
}

/**
 * Project a whole topology into one `viewBox`, fitted to `width`.
 *
 * Pure and synchronous: the caller memoises the result against the fetched
 * topology, so this runs once per page load and never during an interaction.
 */
export function projectTopology(topology: Topology, width = 1000): ProjectedMap {
  const { arcs, bounds } = projectArcs(topology);
  const spanX = bounds.maxX - bounds.minX;
  const spanY = bounds.maxY - bounds.minY;
  if (!(spanX > 0) || !(spanY > 0))
    throw new TopologyShapeError("topology has no extent — every arc projected to a point");

  const k = width / spanX;
  const height = spanY * k;
  const toX = (x: number): number => (x - bounds.minX) * k;
  // SVG's y axis points down; Mercator's points up.
  const toY = (y: number): number => (bounds.maxY - y) * k;

  return {
    width,
    height: Math.round(height * 10) / 10,
    municipalities: shapesOf(
      topology,
      MUNICIPALITIES_OBJECT,
      (geometry) => geometry.properties?.code ?? "",
      arcs,
      toX,
      toY,
    ),
    states: shapesOf(
      topology,
      STATES_OBJECT,
      (geometry) => geometry.properties?.uf ?? "",
      arcs,
      toX,
      toY,
    ),
  };
}
