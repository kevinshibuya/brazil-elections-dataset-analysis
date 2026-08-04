import { useMemo, useState } from "react";

import { compareRaceIds, type Shard } from "../../data/types";
import { useT } from "../../i18n";
import AnomalyPanel from "./AnomalyPanel";
import { buildLens, type SectionRow } from "./model";
import SectionFilters, { type FilterState } from "./SectionFilters";
import SectionTable, { PAGE_SIZE, type SortKey, type SortState } from "./SectionTable";

/** Ties are broken by ballot order so a re-sort never shuffles equal rows. */
function ballotOrder(a: SectionRow, b: SectionRow): number {
  return a.zone - b.zone || a.section - b.section;
}

function comparator(sort: SortState): (a: SectionRow, b: SectionRow) => number {
  const direction = sort.dir === "asc" ? 1 : -1;
  if (sort.key === "order") return (a, b) => ballotOrder(a, b) * direction;
  const read = (row: SectionRow) => (sort.key === "turnout" ? row.turnout : row.maxScore);
  return (a, b) => {
    const left = read(a);
    const right = read(b);
    // Unscored and voter-less sections sink to the bottom in BOTH directions:
    // they are missing, not extreme, and floating them to the top of an
    // ascending sort would read as "lowest signal".
    if (left === null || right === null) {
      if (left === right) return ballotOrder(a, b);
      return left === null ? 1 : -1;
    }
    return (left - right) * direction || ballotOrder(a, b);
  };
}

/**
 * The stateful half of the drill-down: lens, filters, sort, page, selection.
 *
 * Mounted with `key={code}` by the page, so every bit of this state is scoped
 * to one municipality and switching municipalities cannot leave a zone filter
 * or a selected section index behind pointing at the wrong shard.
 */
export default function SectionExplorer({ shard }: { shard: Shard }) {
  const t = useT();
  const races = useMemo(() => [...shard.races].sort(compareRaceIds), [shard.races]);
  const zones = useMemo(() => [...new Set(shard.zone)].sort((a, b) => a - b), [shard.zone]);

  const [filters, setFilters] = useState<FilterState>(() => ({
    race: races.find((race) => race === "PRES_1") ?? races[0] ?? "",
    zone: null,
    minScore: 0,
  }));
  const [sort, setSort] = useState<SortState>({ key: "score", dir: "desc" });
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<number | null>(null);

  const lens = useMemo(() => buildLens(shard, filters.race), [shard, filters.race]);

  const visible = useMemo(() => {
    const matching = lens.rows.filter(
      (row) =>
        (filters.zone === null || row.zone === filters.zone) &&
        (filters.minScore === 0 || (row.maxScore !== null && row.maxScore >= filters.minScore)),
    );
    return matching.sort(comparator(sort));
  }, [lens.rows, filters.zone, filters.minScore, sort]);

  const pageCount = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
  const current = Math.min(page, pageCount - 1);
  const rows = visible.slice(current * PAGE_SIZE, current * PAGE_SIZE + PAGE_SIZE);

  function changeFilters(next: FilterState): void {
    setFilters(next);
    setPage(0);
  }

  function changeSort(key: SortKey): void {
    setSort((previous) =>
      previous.key === key
        ? { key, dir: previous.dir === "asc" ? "desc" : "asc" }
        : { key, dir: key === "order" ? "asc" : "desc" },
    );
    setPage(0);
  }

  return (
    <div className="mt-6 space-y-4">
      <SectionFilters
        races={races}
        zones={zones}
        value={filters}
        onChange={changeFilters}
        matched={visible.length}
        total={lens.rows.length}
      />

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <SectionTable
          rows={rows}
          candidates={lens.candidates}
          sort={sort}
          onSort={changeSort}
          selected={selected}
          onSelect={setSelected}
          page={current}
          pageCount={pageCount}
          onPage={setPage}
          total={visible.length}
        />

        {selected === null ? (
          <p className="rounded-lg border border-dashed border-slate-800 px-4 py-8 text-center text-sm text-slate-500">
            {t("municipality.panel.empty")}
          </p>
        ) : (
          <AnomalyPanel shard={shard} lens={lens} index={selected} />
        )}
      </div>
    </div>
  );
}
