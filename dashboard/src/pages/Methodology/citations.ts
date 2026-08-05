/**
 * The works this project's detectors are built on.
 *
 * NOT i18n strings, on purpose — and this is the one sanctioned exception to
 * "every user-visible string goes through `t()`". An author list, a title, a
 * journal and a volume are proper nouns: they are identical in pt-BR and in
 * English, and putting them in the locale files would invite a translated
 * paper title, which is a citation that no longer resolves to the paper.
 * Structured fields rather than one formatted string for the same reason —
 * the page decides how to present them, the data stays bibliographic.
 */

export interface Citation {
  /** As the work is cited: surnames and initials, in author order. */
  readonly authors: string;
  readonly year: number;
  readonly title: string;
  /** Journal, volume(issue), pages. */
  readonly venue: string;
  /** Stable identifier, when the work has one. */
  readonly doi?: string;
}

/** Alphabetical by first author, the way a reference list is read. */
export const CITATIONS: readonly Citation[] = [
  {
    authors: "Deckert, J., Myagkov, M., & Ordeshook, P. C.",
    year: 2011,
    title: "Benford's Law and the Detection of Election Fraud",
    venue: "Political Analysis 19(3), 245–268",
    doi: "10.1093/pan/mpr014",
  },
  {
    authors: "Klimek, P., Yegorov, Y., Hanel, R., & Thurner, S.",
    year: 2012,
    title: "Statistical detection of systematic election irregularities",
    venue: "Proceedings of the National Academy of Sciences 109(41), 16469–16473",
    doi: "10.1073/pnas.1210722109",
  },
  {
    authors: "Ridout, M. S., Demétrio, C. G. B., & Firth, D.",
    year: 1999,
    title: "Estimating Intraclass Correlation for Binary Data",
    venue: "Biometrics 55(1), 137–148",
    doi: "10.1111/j.0006-341X.1999.00137.x",
  },
];
