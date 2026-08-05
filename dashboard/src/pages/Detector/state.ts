/**
 * Which state's payload a distribution detector is showing.
 *
 * `fingerprint`, `digits` and `urna_model` produce no per-section score at
 * all: their output is one payload per UF inside `states/{UF}.json`. So every
 * one of those pages is really "this detector, in this state". The selector
 * itself collapses when there is only one UF to pick, so every block that uses
 * this also prints the UF it is showing as a plain stat: an export with one
 * state must not look like a national result.
 */

import { useState } from "react";

import { useSummary } from "../../data/hooks";

export interface UfSelection {
  /** Every UF in this export, in the order the exporter wrote them. */
  readonly ufs: readonly string[];
  /** The selected UF — `undefined` until the summary has been read. */
  readonly uf: string | undefined;
  readonly setUf: (uf: string) => void;
  /** True once the summary itself failed: the blocks report, not spin. */
  readonly summaryFailed: boolean;
}

export function useUfSelection(): UfSelection {
  const summary = useSummary();
  const [chosen, setChosen] = useState<string | null>(null);
  const ufs = summary.data?.ufs ?? [];
  return {
    ufs,
    uf: chosen ?? ufs[0],
    setUf: setChosen,
    summaryFailed: summary.isError,
  };
}
