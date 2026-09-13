/**
 * 零依赖 markdown 解析器（UI v3）。
 *
 * 设计取舍：
 * - 只解析 LLM 回答里真实出现的块级语法（标题/列表/代码/引用/表格/分隔线/段落），
 *   不为罕见语法付出复杂度；不引入 react-markdown 等依赖（项目哲学：零新依赖）。
 * - 输出 AST 交给 React 元素渲染，**不产生 HTML 字符串** → 天然免疫 XSS；
 *   行内解析只识别受控语法，链接协议白名单（http/https/mailto）。
 * - 流式友好：未闭合的 ``` 围栏当作代码块处理（打字机过程中不会闪段落）。
 */

export type InlineToken =
  | { type: 'text'; text: string }
  | { type: 'code'; text: string }
  | { type: 'strong'; text: string }
  | { type: 'em'; text: string }
  | { type: 'strike'; text: string }
  | { type: 'link'; text: string; href: string }

export type MarkdownNode =
  | { type: 'paragraph'; tokens: InlineToken[] }
  | { type: 'heading'; level: number; tokens: InlineToken[] }
  | { type: 'code'; lang: string; code: string; closed: boolean }
  | { type: 'list'; ordered: boolean; items: { tokens: InlineToken[]; depth: number }[] }
  | { type: 'quote'; tokens: InlineToken[]; variant?: string }
  | { type: 'table'; header: InlineToken[][]; rows: InlineToken[][][] }
  | { type: 'hr' }

const SAFE_PROTOCOLS = ['http:', 'https:', 'mailto:']

export function isSafeHref(href: string): boolean {
  const trimmed = String(href ?? '').trim()
  if (!trimmed) {
    return false
  }
  if (/^(#|\/(?!\/))/i.test(trimmed)) {
    return true
  }
  try {
    const url = new URL(trimmed, 'https://placeholder.invalid')
    if (url.origin === 'https://placeholder.invalid') {
      return false
    }
    return SAFE_PROTOCOLS.includes(url.protocol)
  } catch {
    return false
  }
}

/** 行内解析：`code` **strong** *em* ~~strike~~ [text](href)。 */
export function parseInline(input: string): InlineToken[] {
  const tokens: InlineToken[] = []
  const pattern = /(`[^`]+`)|(\*\*[^*]+\*\*)|(__[^_]+__)|(\*[^*\n]+\*)|(_[^_\n]+_)|(~~[^~]+~~)|(\[[^\]\n]*\]\([^)\s]*\))/g
  let lastIndex = 0
  let match: RegExpExecArray | null

  const pushText = (text: string): void => {
    if (!text) {
      return
    }
    const previous = tokens[tokens.length - 1]
    if (previous && previous.type === 'text') {
      previous.text += text
      return
    }
    tokens.push({ type: 'text', text })
  }

  while ((match = pattern.exec(input)) !== null) {
    pushText(input.slice(lastIndex, match.index))
    lastIndex = match.index + match[0].length
    const raw = match[0]

    if (raw.startsWith('`')) {
      tokens.push({ type: 'code', text: raw.slice(1, -1) })
    } else if (raw.startsWith('**') || raw.startsWith('__')) {
      tokens.push({ type: 'strong', text: raw.slice(2, -2) })
    } else if (raw.startsWith('~~')) {
      tokens.push({ type: 'strike', text: raw.slice(2, -2) })
    } else if (raw.startsWith('*') || raw.startsWith('_')) {
      tokens.push({ type: 'em', text: raw.slice(1, -1) })
    } else {
      const linkMatch = /^\[([^\]]*)\]\(([^)\s]*)\)$/.exec(raw)
      if (linkMatch && isSafeHref(linkMatch[2])) {
        tokens.push({ type: 'link', text: linkMatch[1] || linkMatch[2], href: linkMatch[2] })
      } else {
        pushText(raw)
      }
    }
  }
  pushText(input.slice(lastIndex))
  return tokens.length > 0 ? tokens : [{ type: 'text', text: '' }]
}

function splitTableRow(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, '').replace(/\|$/, '')
  return trimmed.split('|').map((cell) => cell.trim())
}

function isTableSeparator(line: string): boolean {
  return /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(line) && line.includes('-')
}

function listItemMatch(line: string): { ordered: boolean; depth: number; text: string } | null {
  const match = /^(\s*)(?:([-*+])|(\d+)[.)])\s+(.*)$/.exec(line)
  if (!match) {
    return null
  }
  const indent = match[1].replace(/\t/g, '  ').length
  return {
    ordered: Boolean(match[3]),
    depth: Math.min(2, Math.floor(indent / 2)),
    text: match[4],
  }
}

/** 解析 markdown 文本为块级 AST。 */
export function parseMarkdown(input: string): MarkdownNode[] {
  const lines = String(input ?? '').replace(/\r\n?/g, '\n').split('\n')
  const nodes: MarkdownNode[] = []
  let index = 0

  const flushParagraph = (buffer: string[]): void => {
    if (buffer.length === 0) {
      return
    }
    const text = buffer.join('\n').trim()
    if (text) {
      nodes.push({ type: 'paragraph', tokens: parseInline(text) })
    }
    buffer.length = 0
  }

  const paragraph: string[] = []

  while (index < lines.length) {
    const line = lines[index]

    // 围栏代码块（未闭合也当代码块——流式友好）
    const fence = /^\s*```(.*)$/.exec(line)
    if (fence) {
      flushParagraph(paragraph)
      const lang = fence[1].trim()
      const body: string[] = []
      index += 1
      let closed = false
      while (index < lines.length) {
        if (/^\s*```\s*$/.test(lines[index])) {
          closed = true
          index += 1
          break
        }
        body.push(lines[index])
        index += 1
      }
      nodes.push({ type: 'code', lang, code: body.join('\n'), closed })
      continue
    }

    // 分隔线
    if (/^\s*([-*_])\s*\1\s*\1[\s\-*_]*$/.test(line)) {
      flushParagraph(paragraph)
      nodes.push({ type: 'hr' })
      index += 1
      continue
    }

    // 标题
    const heading = /^(#{1,6})\s+(.*)$/.exec(line)
    if (heading) {
      flushParagraph(paragraph)
      nodes.push({ type: 'heading', level: heading[1].length, tokens: parseInline(heading[2].trim()) })
      index += 1
      continue
    }

    // 表格
    if (line.includes('|') && index + 1 < lines.length && isTableSeparator(lines[index + 1])) {
      flushParagraph(paragraph)
      const header = splitTableRow(line).map(parseInline)
      index += 2
      const rows: InlineToken[][][] = []
      while (index < lines.length && lines[index].includes('|') && lines[index].trim()) {
        rows.push(splitTableRow(lines[index]).map(parseInline))
        index += 1
      }
      nodes.push({ type: 'table', header, rows })
      continue
    }

    // 引用（支持 > [!NOTE] 形式）
    if (/^\s*>\s?/.test(line)) {
      flushParagraph(paragraph)
      const body: string[] = []
      let variant: string | undefined
      while (index < lines.length && /^\s*>\s?/.test(lines[index])) {
        const stripped = lines[index].replace(/^\s*>\s?/, '')
        const callout = /^\[!(\w+)\]\s*(.*)$/.exec(stripped.trim())
        if (callout && body.length === 0) {
          variant = callout[1].toUpperCase()
          if (callout[2]) {
            body.push(callout[2])
          }
        } else {
          body.push(stripped)
        }
        index += 1
      }
      const text = body.join('\n').trim()
      if (text) {
        nodes.push({ type: 'quote', tokens: parseInline(text), variant })
      }
      continue
    }

    // 列表（支持一层缩进子项）
    const listMatch = listItemMatch(line)
    if (listMatch) {
      flushParagraph(paragraph)
      const items: { tokens: InlineToken[]; depth: number }[] = []
      const ordered = listMatch.ordered
      while (index < lines.length) {
        const current = listItemMatch(lines[index])
        if (!current) {
          // 列表项内的续行（缩进且非空）合并进上一项
          if (items.length > 0 && /^\s+\S/.test(lines[index])) {
            const previous = items[items.length - 1]
            const extra = parseInline(lines[index].trim())
            previous.tokens = [...previous.tokens, { type: 'text', text: ' ' }, ...extra]
            index += 1
            continue
          }
          break
        }
        if (current.ordered !== ordered && current.depth === 0) {
          break
        }
        items.push({ tokens: parseInline(current.text), depth: current.depth })
        index += 1
      }
      nodes.push({ type: 'list', ordered, items })
      continue
    }

    if (!line.trim()) {
      flushParagraph(paragraph)
      index += 1
      continue
    }

    paragraph.push(line)
    index += 1
  }

  flushParagraph(paragraph)
  return nodes
}
