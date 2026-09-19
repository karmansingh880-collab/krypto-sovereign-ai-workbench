import { Fragment, type ReactNode } from "react";

/**
 * A small, safe renderer for the light markdown the agent writes (headings, lists,
 * tables, code blocks, **bold**, `code`). Everything goes through React text nodes,
 * so nothing from the model is ever interpreted as HTML.
 */

const INLINE = /(`[^`\n]+`|\*\*[^*\n]+\*\*)/g;

function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let match: RegExpExecArray | null;
  INLINE.lastIndex = 0;
  while ((match = INLINE.exec(text)) !== null) {
    if (match.index > last) out.push(text.slice(last, match.index));
    const token = match[0];
    out.push(
      token.startsWith("`") ? (
        <code key={match.index}>{token.slice(1, -1)}</code>
      ) : (
        <strong key={match.index}>{token.slice(2, -2)}</strong>
      ),
    );
    last = match.index + token.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "code"; text: string }
  | { kind: "table"; header: string[]; rows: string[][] };

const BULLET = /^\s*(?:[-*•–]|\d+[.)])\s+(.*\S)\s*$/;
const ORDERED = /^\s*\d+[.)]\s+/;
const HEADING = /^\s*(#{1,4})\s+(.*\S)\s*$/;

const cells = (line: string) => line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
const isTableLine = (line: string) => line.trim().startsWith("|") && line.trim().endsWith("|");

function parse(source: string): Block[] {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  let i = 0;

  const flush = () => {
    if (paragraph.length) blocks.push({ kind: "paragraph", text: paragraph.join(" ") });
    paragraph = [];
  };

  while (i < lines.length) {
    const line = lines[i];

    if (line.trim().startsWith("```")) {
      flush();
      const code: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith("```")) code.push(lines[i++]);
      i++; // closing fence
      blocks.push({ kind: "code", text: code.join("\n") });
      continue;
    }
    if (!line.trim()) {
      flush();
      i++;
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      flush();
      blocks.push({ kind: "heading", level: heading[1].length, text: heading[2] });
      i++;
      continue;
    }
    if (isTableLine(line)) {
      flush();
      const rows: string[][] = [];
      while (i < lines.length && isTableLine(lines[i])) {
        const row = cells(lines[i++]);
        if (!row.every((c) => /^:?-{2,}:?$/.test(c))) rows.push(row);
      }
      if (rows.length) blocks.push({ kind: "table", header: rows[0], rows: rows.slice(1) });
      continue;
    }
    const bullet = BULLET.exec(line);
    if (bullet) {
      flush();
      const ordered = ORDERED.test(line);
      const items: string[] = [];
      while (i < lines.length) {
        const item = BULLET.exec(lines[i]);
        if (!item || ORDERED.test(lines[i]) !== ordered) break;
        items.push(item[1]);
        i++;
      }
      blocks.push({ kind: "list", ordered, items });
      continue;
    }
    paragraph.push(line.trim());
    i++;
  }
  flush();
  return blocks;
}

export function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown">
      {parse(text).map((block, index) => {
        switch (block.kind) {
          case "heading": {
            const Tag = (`h${Math.min(block.level + 2, 6)}`) as "h3" | "h4" | "h5" | "h6";
            return <Tag key={index}>{inline(block.text)}</Tag>;
          }
          case "paragraph":
            return <p key={index}>{inline(block.text)}</p>;
          case "list": {
            const Tag = block.ordered ? "ol" : "ul";
            return (
              <Tag key={index}>
                {block.items.map((item, n) => (
                  <li key={n}>{inline(item)}</li>
                ))}
              </Tag>
            );
          }
          case "code":
            return (
              <pre key={index}>
                <code>{block.text}</code>
              </pre>
            );
          case "table":
            return (
              <div className="table-wrap" key={index}>
                <table>
                  <thead>
                    <tr>
                      {block.header.map((h, n) => (
                        <th key={n}>{inline(h)}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {block.rows.map((row, r) => (
                      <tr key={r}>
                        {row.map((cell, c) => (
                          <Fragment key={c}>
                            <td>{inline(cell)}</td>
                          </Fragment>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
        }
      })}
    </div>
  );
}
