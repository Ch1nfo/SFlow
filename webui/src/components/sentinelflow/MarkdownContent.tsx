type MarkdownContentProps = {
  content: string
  inverted?: boolean
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function applyInlineMarkdown(text: string): string {
  const BR_PLACEHOLDER = '\x00BR\x00'
  let output = text.replace(/<br\s*\/?>/gi, BR_PLACEHOLDER)
  output = escapeHtml(output)
  output = output.replace(/\x00BR\x00/g, '<br>')
  output = output.replace(/`([^`]+)`/g, '<code>$1</code>')
  output = output.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  output = output.replace(/(^|[^\*])\*([^*]+)\*(?!\*)/g, '$1<em>$2</em>')
  output = output.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
  return output
}

function isTableRow(line: string): boolean {
  const trimmed = line.trim()
  return trimmed.startsWith('|') && trimmed.endsWith('|') && trimmed.split('|').length >= 4
}

function isTableDelimiter(line: string): boolean {
  if (!isTableRow(line)) return false
  return splitTableRow(line).every((cell) => /^:?-{3,}:?$/.test(cell.trim()))
}

function splitTableRow(line: string): string[] {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim())
}

function renderTable(lines: string[]): string {
  const headerCells = splitTableRow(lines[0])
  const bodyRows = lines.slice(2).map(splitTableRow)
  const columnCount = headerCells.length
  const header = headerCells.map((cell) => `<th>${applyInlineMarkdown(cell)}</th>`).join('')
  const body = bodyRows.map((row) => {
    const cells = Array.from({ length: columnCount }, (_, index) => row[index] ?? '')
    return `<tr>${cells.map((cell) => `<td>${applyInlineMarkdown(cell)}</td>`).join('')}</tr>`
  }).join('')
  return `<div class="sentinelflow-markdown-table-wrap"><table><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table></div>`
}

function renderMarkdownToHtml(markdown: string): string {
  const normalized = markdown
    .replace(/\r\n/g, '\n')
    .trim()
    // Merge definition-list lines: "Term\n: Value" or "Term\n：Value" → "Term: Value"
    .replace(/([^\n]+)\n[：:]\s*([^\n]+)/g, '$1: $2')
    // Merge lines where the next line starts with Chinese sentence-continuation punctuation (run twice for chained cases)
    .replace(/([^\n]+)\n([，。、；！？…）」』〕】])/g, '$1$2')
    .replace(/([^\n]+)\n([，。、；！？…）」』〕】])/g, '$1$2')
  if (!normalized) return ''

  const lines = normalized.split('\n')
  const html: string[] = []
  let index = 0

  while (index < lines.length) {
    const line = lines[index]

    if (!line.trim()) {
      index += 1
      continue
    }

    const codeFence = line.match(/^```([\w-]+)?\s*$/)
    if (codeFence) {
      const language = codeFence[1] ? ` class="language-${escapeHtml(codeFence[1])}"` : ''
      index += 1
      const block: string[] = []
      while (index < lines.length && !lines[index].startsWith('```')) {
        block.push(lines[index])
        index += 1
      }
      if (index < lines.length) {
        index += 1
      }
      html.push(`<pre><code${language}>${escapeHtml(block.join('\n'))}</code></pre>`)
      continue
    }

    const heading = line.match(/^(#{1,4})\s+(.+)$/)
    if (heading) {
      const level = heading[1].length
      html.push(`<h${level}>${applyInlineMarkdown(heading[2].trim())}</h${level}>`)
      index += 1
      continue
    }

    const blockquote = line.match(/^>\s?(.*)$/)
    if (blockquote) {
      const block: string[] = [blockquote[1]]
      index += 1
      while (index < lines.length) {
        const next = lines[index].match(/^>\s?(.*)$/)
        if (!next) break
        block.push(next[1])
        index += 1
      }
      html.push(`<blockquote>${block.map((item) => `<p>${applyInlineMarkdown(item)}</p>`).join('')}</blockquote>`)
      continue
    }

    if (isTableRow(line) && index + 1 < lines.length && isTableDelimiter(lines[index + 1])) {
      const tableLines = [line, lines[index + 1]]
      index += 2
      while (index < lines.length && isTableRow(lines[index])) {
        tableLines.push(lines[index])
        index += 1
      }
      html.push(renderTable(tableLines))
      continue
    }

    const unordered = line.match(/^[-*]\s+(.+)$/)
    if (unordered) {
      const items: string[] = []
      while (index < lines.length) {
        const match = lines[index].match(/^[-*]\s+(.+)$/)
        if (!match) break
        items.push(`<li>${applyInlineMarkdown(match[1])}</li>`)
        index += 1
      }
      html.push(`<ul>${items.join('')}</ul>`)
      continue
    }

    const ordered = line.match(/^\d+\.\s+(.+)$/)
    if (ordered) {
      const items: string[] = []
      while (index < lines.length) {
        const match = lines[index].match(/^\d+\.\s+(.+)$/)
        if (!match) break
        items.push(`<li>${applyInlineMarkdown(match[1])}</li>`)
        index += 1
      }
      html.push(`<ol>${items.join('')}</ol>`)
      continue
    }

    const paragraph: string[] = [line]
    index += 1
    while (index < lines.length) {
      const next = lines[index]
      if (
        !next.trim() ||
        /^```/.test(next) ||
        /^(#{1,4})\s+/.test(next) ||
        /^>\s?/.test(next) ||
        (isTableRow(next) && index + 1 < lines.length && isTableDelimiter(lines[index + 1])) ||
        /^[-*]\s+/.test(next) ||
        /^\d+\.\s+/.test(next)
      ) {
        break
      }
      paragraph.push(next)
      index += 1
    }
    html.push(`<p>${applyInlineMarkdown(paragraph.join('<br>'))}</p>`)
  }

  return html.join('')
}

export default function MarkdownContent({ content, inverted = false }: MarkdownContentProps) {
  const html = renderMarkdownToHtml(content)
  return (
    <div
      className={inverted ? 'sentinelflow-markdown sentinelflow-markdown-invert max-w-none' : 'sentinelflow-markdown max-w-none'}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}
