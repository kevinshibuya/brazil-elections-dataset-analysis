/**
 * Bilingual copy: pt-BR by default, EN behind a persisted toggle.
 *
 * KEY CONVENTION — flat, dot-separated, lowercase, hyphens inside a segment:
 *
 *     <namespace>.<subject>[.<variant>]      e.g. "page.not-found.title"
 *
 * Namespaces in use: `app` (shell chrome), `nav`, `locale`, `page.<route>`,
 * `status` (inline loading/empty), `error.<kind>`, `data` (export metadata),
 * `footer`, and one namespace per feature surface (`municipality`, and later
 * `detector`, `map`, `methodology`). Both locale files carry the SAME key set
 * — `i18n.test.ts` fails the build otherwise.
 *
 * Placeholders are `{named}` and are substituted positionally-free:
 * `t("municipality.sections-loaded", { sections: 22, races: 6 })`.
 *
 * Every user-visible string goes through `t()`. No hardcoded JSX copy, ever —
 * the pt-BR/EN values here are placeholders until the copy slice replaces them,
 * but the key set is the contract.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import en from "./en.json";
import ptBR from "./pt-BR.json";

export const LOCALES = ["pt-BR", "en"] as const;
export type Locale = (typeof LOCALES)[number];

/** pt-BR is the default: the audience is Brazilian, EN is the translation. */
export const DEFAULT_LOCALE: Locale = "pt-BR";

/** BCP-47 tags for `<html lang>` / `Intl` — same strings, named for intent. */
export const HTML_LANG: Record<Locale, string> = { "pt-BR": "pt-BR", en: "en" };

const DICTIONARIES: Record<Locale, Record<string, string>> = { "pt-BR": ptBR, en };

/** Every key present in the dictionaries. */
export type TranslationKey = keyof typeof ptBR;

/**
 * A message key: autocompletes to the known keys but accepts a computed string
 * (`detector.${key}.name`), which pages legitimately need.
 */
export type MessageKey = TranslationKey | (string & {});

export type TranslationParams = Record<string, string | number>;

/** `t` — look up `key` in `locale`, substituting `{placeholders}`. */
export function translate(
  locale: Locale,
  key: MessageKey,
  params?: TranslationParams,
): string {
  const template = DICTIONARIES[locale][key] ?? DICTIONARIES[DEFAULT_LOCALE][key];
  if (template === undefined) {
    if (import.meta.env.DEV) console.warn(`[i18n] missing key: ${key}`);
    return key;
  }
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) => {
    const value = params[name];
    return value === undefined ? match : String(value);
  });
}

export interface I18nContextValue {
  readonly locale: Locale;
  readonly setLocale: (locale: Locale) => void;
  readonly t: (key: MessageKey, params?: TranslationParams) => string;
}

const I18nContext = createContext<I18nContextValue | null>(null);

const STORAGE_KEY = "observatory.locale";

function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (LOCALES as readonly string[]).includes(value);
}

function readStoredLocale(): Locale | null {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return isLocale(stored) ? stored : null;
  } catch {
    return null; // private mode / storage disabled — fall back to the default
  }
}

export interface I18nProviderProps {
  readonly children: ReactNode;
  /**
   * Locale to start in, overriding whatever is persisted. The app never passes
   * it — this is how a test (or, later, a locale-prefixed URL) can mount a page
   * in a known language without reaching into `localStorage`.
   */
  readonly initialLocale?: Locale;
}

export function I18nProvider({ children, initialLocale }: I18nProviderProps) {
  const [locale, setLocaleState] = useState<Locale>(
    () => initialLocale ?? readStoredLocale() ?? DEFAULT_LOCALE,
  );

  useEffect(() => {
    document.documentElement.lang = HTML_LANG[locale];
  }, [locale]);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // persistence is a nicety, not a requirement
    }
  }, []);

  const value = useMemo<I18nContextValue>(
    () => ({
      locale,
      setLocale,
      t: (key, params) => translate(locale, key, params),
    }),
    [locale, setLocale],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const value = useContext(I18nContext);
  if (!value) throw new Error("useI18n must be used inside <I18nProvider>");
  return value;
}

/** The common case: just the translator. */
export function useT(): (key: MessageKey, params?: TranslationParams) => string {
  return useI18n().t;
}
