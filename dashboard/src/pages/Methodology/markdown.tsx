/**
 * A deliberately small markdown renderer — just enough for the methodology
 * copy, and no more.
 *
 * Why not a library: the only markdown this app renders is two files it ships
 * itself, and the alternative (`react-markdown`, or `dangerouslySetInnerHTML`
 * over a parser) buys a dependency and an HTML-injection surface to render
 * five constructs. The supported subset is documented at the top of both
 * `src/i18n/methodology.*.md` so the copy pass stays inside it:
 *
 *     ## / ### headings · paragraphs · `- ` lists · `1. ` lists
 *     [text](url) · `code` · **bold** · *italic*
 *
 * THE ONE RULE THAT MATTERS: anything outside that subset renders as a
 * verbatim paragraph. It must never vanish. The copy is authored by a
 * different model and it will eventually reach for a table or a block quote;
 * a renderer that silently swallowed that would drop a paragraph of
 * methodology off a page whose entire claim is methodological care. Ugly text
 * on screen is a bug someone fixes; missing text is one nobody sees.
 */

import { type ReactNode } from "react";
import { Link } from "react-router";

export type Block =
  | { readonly kind: "heading"; readonly level: 2 | 3; readonly text: string }
  | { readonly kind: "ul"; readonly items: readonly string[] }
  | { readonly kind: "ol"; readonly items: readonly string[] }
  | { readonly kind: "p"; readonly text: string };

/** HTML comments are author notes: invisible by markdown convention. */
export function stripComments(markdown: string): string {
  return markdown.replace(/<!--[\s\S]*?-->/g, "");
}

const HEADING = /^(#{2,3})\s+(.+)$/;
const UL_ITEM = /^-\s+(\S.*)$/;
const OL_ITEM = /^\d+\.\s+(\S.*)$/;

function pushBlock(out: Block[], chunk: string): void {
  const lines = chunk
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "");
  const first = lines[0];
  if (first === undefined) return;

  const heading = HEADING.exec(first);
  const hashes = heading?.[1];
  const headingText = heading?.[2];
  if (hashes !== undefined && headingText !== undefined) {
    out.push({ kind: "heading", level: hashes.length === 2 ? 2 : 3, text: headingText.trim() });
    // A heading with its prose glued to it (no blank line) is still two blocks.
    if (lines.length > 1) pushBlock(out, lines.slice(1).join("\n"));
    return;
  }

  // A list is a block where EVERY line is an item — one line per item. A block
  // that is only partly a list falls through to the verbatim paragraph rather
  // than losing the lines that did not match.
  const bullets = lines.map((line) => UL_ITEM.exec(line)?.[1]);
  if (bullets.every((item) => item !== undefined)) {
    out.push({ kind: "ul", items: bullets as string[] });
    return;
  }
  const numbered = lines.map((line) => OL_ITEM.exec(line)?.[1]);
  if (numbered.every((item) => item !== undefined)) {
    out.push({ kind: "ol", items: numbered as string[] });
    return;
  }

  out.push({ kind: "p", text: lines.join(" ") });
}

/** Blank-line-separated blocks, in file order. */
export function parseBlocks(markdown: string): readonly Block[] {
  const out: Block[] = [];
  for (const chunk of stripComments(markdown).split(/\n[ \t]*\n/)) pushBlock(out, chunk);
  return out;
}

/**
 * Inline spans, matched in one pass so that `**bold**` wins over `*italic*`
 * and a code span's contents stay literal. No nesting: a link's text is text.
 */
const INLINE = /`([^`]+)`|\[([^\]]+)\]\(([^\s)]+)\)|\*\*([^*]+)\*\*|\*([^*]+)\*/g;

const LINK_CLASS =
  "underline decoration-slate-600 underline-offset-2 transition-colors hover:text-slate-100";

function MdLink({ href, children }: { href: string; children: ReactNode }) {
  // In-app destinations stay client-side; `#fragment` links are same-page.
  if (href.startsWith("/"))
    return (
      <Link to={href} className={LINK_CLASS}>
        {children}
      </Link>
    );
  if (href.startsWith("#"))
    return (
      <a href={href} className={LINK_CLASS}>
        {children}
      </a>
    );
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className={LINK_CLASS}>
      {children}
    </a>
  );
}

export function renderInline(text: string): readonly ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let key = 0;
  for (const match of text.matchAll(INLINE)) {
    const at = match.index;
    const whole = match[0];
    if (at === undefined) continue;
    if (at > last) out.push(text.slice(last, at));
    const [, code, linkText, href, bold, italic] = match;
    if (code !== undefined)
      out.push(
        <code key={key++} className="rounded bg-slate-800/70 px-1 py-0.5 text-[0.85em] text-slate-200">
          {code}
        </code>,
      );
    else if (linkText !== undefined && href !== undefined)
      out.push(
        <MdLink key={key++} href={href}>
          {linkText}
        </MdLink>,
      );
    else if (bold !== undefined)
      out.push(
        <strong key={key++} className="font-semibold text-slate-100">
          {bold}
        </strong>,
      );
    else if (italic !== undefined) out.push(<em key={key++}>{italic}</em>);
    else out.push(whole);
    last = at + whole.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** The rendered blocks of one section's body, in file order. */
export function Markdown({ blocks }: { blocks: readonly Block[] }) {
  return (
    <div className="space-y-3 text-sm leading-relaxed text-slate-300">
      {blocks.map((block, i) => {
        const key = `${block.kind}-${i}`;
        if (block.kind === "heading")
          return block.level === 2 ? (
            <h3 key={key} className="pt-2 text-sm font-medium text-slate-200">
              {renderInline(block.text)}
            </h3>
          ) : (
            <h4 key={key} className="pt-2 text-xs font-medium uppercase tracking-wide text-slate-400">
              {renderInline(block.text)}
            </h4>
          );
        if (block.kind === "ul")
          return (
            <ul key={key} className="list-disc space-y-1.5 pl-5 marker:text-slate-600">
              {block.items.map((item, j) => (
                <li key={j}>{renderInline(item)}</li>
              ))}
            </ul>
          );
        if (block.kind === "ol")
          return (
            <ol key={key} className="list-decimal space-y-1.5 pl-5 marker:text-slate-600">
              {block.items.map((item, j) => (
                <li key={j}>{renderInline(item)}</li>
              ))}
            </ol>
          );
        return (
          <p key={key} className="max-w-2xl">
            {renderInline(block.text)}
          </p>
        );
      })}
    </div>
  );
}
