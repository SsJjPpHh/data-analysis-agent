(function exposeResearchMarkdown(root, factory) {
  const renderer = factory();
  if (typeof module === "object" && module.exports) module.exports = renderer;
  if (root) root.ResearchMarkdown = renderer;
}(typeof globalThis !== "undefined" ? globalThis : this, function createResearchMarkdown() {
  "use strict";

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function normalize(value) {
    return String(value ?? "")
      .replace(/\r\n?/g, "\n")
      .replace(/\s*\|\|\s*/g, "|\n|")
      .replace(/([：:。；;])\s+(\|[^\n]+\|\n\|)/g, "$1\n\n$2")
      .replace(/[ \t]+(#{1,6}\s+)/g, "\n\n$1")
      .trim();
  }

  function safeHref(value) {
    const href = String(value || "").trim();
    const compact = href.replace(/[\u0000-\u0020\u007f]+/g, "");
    if (!compact) return "";
    if (/^(https?:|mailto:)/i.test(compact)) return href;
    if (/^(#|\/|\.\/|\.\.\/)/.test(compact) && !/^\/\//.test(compact)) return href;
    if (!/^[a-z][a-z0-9+.-]*:/i.test(compact) && !/^\/\//.test(compact)) return href;
    return "";
  }

  function renderInline(value) {
    const tokens = [];
    const stash = (html) => {
      const token = `\u0000RSW${tokens.length}\u0000`;
      tokens.push(html);
      return token;
    };
    let source = String(value ?? "");
    source = source.replace(/`([^`\n]+)`/g, (_, code) => stash(`<code>${escapeHtml(code)}</code>`));
    source = source.replace(/\[([^\]\n]+)\]\(([^\s)]+)(?:\s+"[^"]*")?\)/g, (_, label, href) => {
      const safe = safeHref(href);
      if (!safe) return stash(escapeHtml(label));
      return stash(`<a href="${escapeHtml(safe)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`);
    });
    let html = escapeHtml(source);
    html = html.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    html = html.replace(
      /(^|[\s([{>，。；：！？])__([^_\n]+)__(?=$|[\s)\]}<，。；：！？.,!?])/g,
      "$1<strong>$2</strong>"
    );
    html = html.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
    html = html.replace(
      /(^|[\s([{>，。；：！？])_([^_\n]+)_(?=$|[\s)\]}<，。；：！？.,!?])/g,
      "$1<em>$2</em>"
    );
    tokens.forEach((token, index) => {
      html = html.replace(`\u0000RSW${index}\u0000`, token);
    });
    return html;
  }

  function splitTableRow(line) {
    const source = String(line || "").trim().replace(/^\|/, "").replace(/\|$/, "");
    const cells = [];
    let cell = "";
    let escaped = false;
    let inCode = false;
    for (const char of source) {
      if (escaped) {
        cell += char;
        escaped = false;
      } else if (char === "\\") {
        escaped = true;
      } else if (char === "`") {
        inCode = !inCode;
        cell += char;
      } else if (char === "|" && !inCode) {
        cells.push(cell.trim());
        cell = "";
      } else {
        cell += char;
      }
    }
    if (escaped) cell += "\\";
    cells.push(cell.trim());
    return cells;
  }

  function isTableSeparator(line) {
    return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(String(line || ""));
  }

  function renderTable(headers, rows, label = "数据表") {
    const columnCount = headers.length;
    return `
      <div class="rsw-md-table-wrap" role="region" aria-label="${escapeHtml(label)}" tabindex="0">
        <table class="rsw-md-table">
          <thead><tr>${headers.map((cell) => `<th>${renderInline(cell)}</th>`).join("")}</tr></thead>
          <tbody>${rows.map((row) => `<tr>${Array.from({ length: columnCount }, (_, index) => `<td>${renderInline(row[index] || "")}</td>`).join("")}</tr>`).join("")}</tbody>
        </table>
      </div>`;
  }

  function tableAt(lines, header, startIndex, label) {
    const headers = splitTableRow(header);
    let index = startIndex;
    if (isTableSeparator(lines[index] || "")) index += 1;
    const rows = [];
    while (index < lines.length) {
      const line = lines[index] || "";
      if (!line.trim() || !line.includes("|") || isTableSeparator(line)) break;
      const row = splitTableRow(line);
      if (row.length < 2) break;
      rows.push(row);
      index += 1;
    }
    if (!rows.length || headers.length < 2) return null;
    return { html: renderTable(headers, rows, label), next: index };
  }

  function leadingWidth(value) {
    const whitespace = String(value || "").match(/^\s*/)?.[0] || "";
    return whitespace.replace(/\t/g, "    ").length;
  }

  function listMatch(line) {
    return /^(\s*)([-+*]|\d+[.)])\s+(.+)$/.exec(String(line || ""));
  }

  function renderList(lines, startIndex) {
    const first = listMatch(lines[startIndex]);
    if (!first) return null;
    const baseIndent = leadingWidth(first[1]);
    const ordered = /^\d/.test(first[2]);
    const tag = ordered ? "ol" : "ul";
    const items = [];
    let index = startIndex;

    while (index < lines.length) {
      const match = listMatch(lines[index]);
      if (!match || leadingWidth(match[1]) !== baseIndent || /^\d/.test(match[2]) !== ordered) break;
      let item = renderInline(match[3].trim());
      index += 1;

      while (index < lines.length && lines[index].trim()) {
        const nestedMatch = listMatch(lines[index]);
        if (nestedMatch && leadingWidth(nestedMatch[1]) > baseIndent) {
          const nested = renderList(lines, index);
          if (!nested) break;
          item += nested.html;
          index = nested.next;
          continue;
        }
        if (nestedMatch) break;
        if (leadingWidth(lines[index]) > baseIndent) {
          item += `<br>${renderInline(lines[index].trim())}`;
          index += 1;
          continue;
        }
        break;
      }
      items.push(`<li>${item}</li>`);
    }
    return { html: `<${tag}>${items.join("")}</${tag}>`, next: index };
  }

  function sectionMatch(line) {
    return /^\s*【([^】]+)】\s*(.*)$/.exec(String(line || ""));
  }

  function isBlockStart(lines, index) {
    const raw = lines[index] || "";
    const line = raw.trim();
    if (!line) return true;
    if (/^```/.test(line) || /^(#{1,6})\s+/.test(line) || /^>\s?/.test(line)) return true;
    if (/^(?:-{3,}|\*{3,}|_{3,})$/.test(line) || listMatch(raw) || sectionMatch(raw)) return true;
    return line.includes("|") && isTableSeparator(lines[index + 1] || "");
  }

  function render(value) {
    const source = normalize(value);
    if (!source) return "<p></p>";
    const lines = source.split("\n");
    const output = [];
    let index = 0;

    while (index < lines.length) {
      const rawLine = lines[index] || "";
      const line = rawLine.trim();
      if (!line) {
        index += 1;
        continue;
      }

      const fence = /^```\s*([\w+-]*)/.exec(line);
      if (fence) {
        const code = [];
        index += 1;
        while (index < lines.length && !/^```\s*$/.test((lines[index] || "").trim())) {
          code.push(lines[index]);
          index += 1;
        }
        if (index < lines.length) index += 1;
        const language = fence[1] ? ` class="language-${escapeHtml(fence[1])}"` : "";
        output.push(`<pre><code${language}>${escapeHtml(code.join("\n"))}</code></pre>`);
        continue;
      }

      const section = sectionMatch(rawLine);
      if (section) {
        const title = section[1].trim();
        const remainder = section[2].trim();
        output.push(`<h3>${renderInline(title)}</h3>`);
        if (remainder.includes("|") && (lines[index + 1] || "").includes("|")) {
          const table = tableAt(lines, remainder, index + 1, title);
          if (table) {
            output.push(table.html);
            index = table.next;
            continue;
          }
        }
        if (remainder) output.push(`<p class="rsw-md-section-lead">${renderInline(remainder)}</p>`);
        index += 1;
        continue;
      }

      if (line.includes("|") && isTableSeparator(lines[index + 1] || "")) {
        const table = tableAt(lines, line, index + 1, "Markdown 数据表");
        if (table) {
          output.push(table.html);
          index = table.next;
          continue;
        }
      }

      const heading = /^(#{1,6})\s+(.+)$/.exec(line);
      if (heading) {
        const level = heading[1].length;
        output.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
        index += 1;
        continue;
      }

      if (/^(?:-{3,}|\*{3,}|_{3,})$/.test(line)) {
        output.push("<hr>");
        index += 1;
        continue;
      }

      if (/^>\s?/.test(line)) {
        const quote = [];
        while (index < lines.length && /^>\s?/.test((lines[index] || "").trim())) {
          quote.push((lines[index] || "").trim().replace(/^>\s?/, ""));
          index += 1;
        }
        output.push(`<blockquote>${render(quote.join("\n"))}</blockquote>`);
        continue;
      }

      const list = renderList(lines, index);
      if (list) {
        output.push(list.html);
        index = list.next;
        continue;
      }

      if (/^\s{2,}\S/.test(rawLine)) {
        const items = [];
        while (index < lines.length && /^\s{2,}\S/.test(lines[index] || "") && !listMatch(lines[index])) {
          items.push(`<li>${renderInline((lines[index] || "").trim())}</li>`);
          index += 1;
        }
        output.push(`<ul class="rsw-md-plain-list">${items.join("")}</ul>`);
        continue;
      }

      const paragraph = [];
      while (index < lines.length && !isBlockStart(lines, index)) {
        paragraph.push((lines[index] || "").trim());
        index += 1;
      }
      if (paragraph.length) {
        output.push(`<p>${paragraph.map(renderInline).join("<br>")}</p>`);
      } else {
        output.push(`<p>${renderInline(line)}</p>`);
        index += 1;
      }
    }

    return output.join("");
  }

  return {
    isTableSeparator,
    normalize,
    render,
    renderInline,
    splitTableRow,
  };
}));
