import { Fragment, type ReactNode } from "react";

// Render common Markdown as React nodes; model output never becomes raw HTML.
function inlineMarkdown(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^\s)]+\))/g).map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={index}>{part.slice(1, -1)}</code>;
    const link = part.match(/^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/);
    if (link) return <a key={index} href={link[2]} target="_blank" rel="noreferrer noopener">{link[1]}</a>;
    return <Fragment key={index}>{part}</Fragment>;
  });
}

export function AssistantMarkdown({ content }: { content: string }) {
  const lines = content.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    if (!line.trim()) continue;
    if (line.startsWith("```")) {
      const start = index;
      const code: string[] = [];
      while (++index < lines.length && !lines[index].startsWith("```")) code.push(lines[index]);
      blocks.push(<pre key={start}><code>{code.join("\n")}</code></pre>);
    } else if (/^#{1,6}\s/.test(line)) {
      blocks.push(<h4 key={index}>{inlineMarkdown(line.replace(/^#{1,6}\s+/, ""))}</h4>);
    } else if (/^\s*([-*]|\d+[.)])\s+/.test(line)) {
      const start = index;
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const items: ReactNode[] = [];
      const pattern = ordered ? /^\s*\d+[.)]\s+/ : /^\s*[-*]\s+/;
      do {
        items.push(<li key={index}>{inlineMarkdown(lines[index].replace(pattern, ""))}</li>);
        index++;
      } while (index < lines.length && pattern.test(lines[index]));
      index--;
      blocks.push(ordered ? <ol key={start}>{items}</ol> : <ul key={start}>{items}</ul>);
    } else if (line.includes("|") && index + 1 < lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1])) {
      const start = index;
      const cells = (row: string) => row.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map((cell) => cell.trim());
      const heading = cells(line);
      const rows: string[][] = [];
      index += 2;
      while (index < lines.length && lines[index].includes("|")) rows.push(cells(lines[index++]));
      index--;
      blocks.push(<div key={start} className="pa-markdown-table"><table>
        <thead><tr>{heading.map((cell, cellIndex) => <th key={cellIndex} scope="col">{inlineMarkdown(cell)}</th>)}</tr></thead>
        <tbody>{rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{inlineMarkdown(cell)}</td>)}</tr>)}</tbody>
      </table></div>);
    } else {
      const start = index;
      const paragraph = [line];
      while (index + 1 < lines.length && lines[index + 1].trim() && !/^(#{1,6}\s|```|\s*([-*]|\d+[.)])\s)/.test(lines[index + 1]) && !(lines[index + 1].includes("|") && /^\s*\|?\s*:?-{3,}/.test(lines[index + 2] ?? ""))) paragraph.push(lines[++index]);
      blocks.push(<p key={start}>{inlineMarkdown(paragraph.join("\n"))}</p>);
    }
  }
  return <div className="pa-markdown">{blocks}</div>;
}
