import type { RaceId } from "../../data/types";
import { useT } from "../../i18n";
import { raceLabel } from "./format";

/** Preset thresholds. `0` keeps every scored and unscored section. */
export const MIN_SCORE_PRESETS = [0, 1, 2, 3] as const;

/** The threshold a single click on the preset control has to reach. */
export const FLAGGED_PRESET = 2;

const SELECT_CLASS =
  "rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm text-slate-100 " +
  "focus:border-slate-400 focus:outline-none focus:ring-1 focus:ring-slate-400";

export interface FilterState {
  readonly race: RaceId;
  readonly zone: number | null;
  readonly minScore: number;
}

/**
 * Zone, race and minimum-signal controls.
 *
 * The race select is a lens, not just a filter: it decides which candidates the
 * table shows shares for AND which scores the signal column reads, so the two
 * can never disagree about what a row's number means.
 */
export default function SectionFilters({
  races,
  zones,
  value,
  onChange,
  matched,
  total,
}: {
  races: readonly RaceId[];
  zones: readonly number[];
  value: FilterState;
  onChange: (next: FilterState) => void;
  matched: number;
  total: number;
}) {
  const t = useT();

  return (
    <div className="flex flex-wrap items-end gap-x-6 gap-y-4 rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-3">
      <label className="flex flex-col gap-1">
        <span className="text-xs font-medium uppercase tracking-wide text-slate-400">
          {t("municipality.filter.race")}
        </span>
        <select
          className={SELECT_CLASS}
          value={value.race}
          onChange={(event) => onChange({ ...value, race: event.target.value })}
          data-testid="filter-race"
        >
          {races.map((race) => (
            <option key={race} value={race}>
              {raceLabel(t, race)}
            </option>
          ))}
        </select>
      </label>

      {zones.length > 1 ? (
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium uppercase tracking-wide text-slate-400">
            {t("municipality.filter.zone")}
          </span>
          <select
            className={SELECT_CLASS}
            value={value.zone === null ? "" : String(value.zone)}
            onChange={(event) =>
              onChange({ ...value, zone: event.target.value === "" ? null : Number(event.target.value) })
            }
            data-testid="filter-zone"
          >
            <option value="">{t("municipality.filter.zone.all")}</option>
            {zones.map((zone) => (
              <option key={zone} value={String(zone)}>
                {t("municipality.filter.zone.option", { zone })}
              </option>
            ))}
          </select>
        </label>
      ) : null}

      <div className="flex flex-col gap-1">
        <span className="text-xs font-medium uppercase tracking-wide text-slate-400">
          {t("municipality.filter.min-score")}
        </span>
        <div className="flex overflow-hidden rounded border border-slate-700" role="group">
          {MIN_SCORE_PRESETS.map((preset) => {
            const active = value.minScore === preset;
            return (
              <button
                key={preset}
                type="button"
                aria-pressed={active}
                data-testid={preset === FLAGGED_PRESET ? "filter-min-score" : undefined}
                onClick={() => onChange({ ...value, minScore: preset })}
                className={`border-r border-slate-700 px-2.5 py-1.5 text-sm transition-colors last:border-r-0 ${
                  active
                    ? "bg-slate-100 font-medium text-slate-900"
                    : "bg-slate-900 text-slate-300 hover:bg-slate-800"
                }`}
              >
                {preset === 0
                  ? t("municipality.filter.min-score.all")
                  : t("municipality.filter.min-score.preset", { min: preset })}
              </button>
            );
          })}
        </div>
      </div>

      <p className="ml-auto text-xs text-slate-400" aria-live="polite">
        {t("municipality.filter.matched", { matched, total })}
      </p>
    </div>
  );
}
