// Reading the Here I Am hooks' rows back into cards (issue #385).
//
// The pane shows the page the entity was handed, so it reads the row as it
// reached context, not a structured copy from the backend: what is parsed
// here is the same text the model read. Whatever this can't parse stays
// visible through the entry's raw view, and an unparsed [MEMORY] header is
// reported as a problem rather than dropped.

import type { Entry, MemoryCard, MemoryWhere, SpillFile } from '../types'

// [MEMORY <id> from <created_at> - <role label> - via <origin>]
// (memory_context.build_memory_message). The role label can hold " - " in a
// multi-entity room, so the origin is anchored at the end instead.
const BLOCK = /\[MEMORY ([0-9a-f]{6,}) from (\S+) - (.+?) - (via [^\]\r\n]+)\]\r?\n([\s\S]*?)\r?\n?\[\/MEMORY\]/g
const HEADER = /\[MEMORY [0-9a-f]{6,} from /g

// - <id> (<date> - <role label> - via <origin>): <first line>
// (claude_code_mode.render_retrieval_summary_line)
const SUMMARY = /^- ([0-9a-f]{8}) \((\d{4}-\d{2}-\d{2}) - (.+?) - (via [^)]+)\): (.*?)\r?$/gm

// Link marker lines sit directly under a header (memory_context.format_memory_link_lines)
const MARK = /^\[(?:revises|sources:|later |cited by|source withdrawn)[^\]]*\]$/

// Our hooks name a spill file on a line of its own, optionally sized:
//   C:\...\here-i-am-sessions\<session>-retrieval-131619.md (79 KB)
const HOOK_FILE = /^\s*((?:[A-Za-z]:[\\/]|\/)[^\r\n]*?here-i-am-sessions[\\/][^\r\n]*?\.md)(?: \(([\d.]+ ?[KMG]?B)\))?\s*$/gm
// The harness's persist line: "Full output saved to: <path>"
const HARNESS_FILE = /Full output saved to: ([^\r\n]+?)\s*$/gm

// The hooks' own lines worth showing: stamps, notices, failures. The long
// identity paragraphs are left to the raw view.
const STAMP = /^\[(HERE I AM[^\]]*|MEMORY (?:STATUS|ARCHIVE) NOTICE)\] ?(.*)$/

// The memory tools head each result "--- Memory xxxxxxxx (" (memory_neighbors
// marks its target "--- >> Memory"), the shape memory_tools' reload parser keys on
const TOOL_HEADER = /^--- (?:>> )?Memory ([0-9a-f]{8}) \(/gm

// What a surface draws in one text child: at most 10,000 characters, and no
// control characters but tab and newline. Kept under the line with room
const TEXT_CHILD_MAX = 8000
const CONTROL = /[\u0000-\u0008\u000B-\u001F\u007F]/g

const IDENTITY_TAGS = new Set(['HERE I AM MEMORY TOOLS', 'ROOMS REGISTRY', 'YOUR NOTES', 'GIT IDENTITY'])

export function normPath(path: string): string {
  return path.trim().replace(/\\/g, '/').toLowerCase()
}

// A capture group of a match, '' when it didn't take part
function g(m: RegExpMatchArray, i: number): string {
  return m[i] ?? ''
}

function splitMarks(body: string): { marks: string[]; text: string } {
  const lines = body.split(/\r?\n/)
  const marks: string[] = []
  while (lines.length > 0 && MARK.test((lines[0] ?? '').trim())) marks.push((lines.shift() ?? '').trim())
  return { marks, text: lines.join('\n') }
}

/** Every whole [MEMORY] block in `text`, in order. */
export function parseBlocks(text: string, where: MemoryWhere, file?: string): MemoryCard[] {
  const cards: MemoryCard[] = []
  for (const m of text.matchAll(BLOCK)) {
    const { marks, text: body } = splitMarks(g(m, 5))
    cards.push({ id: g(m, 1), date: g(m, 2), from: g(m, 3), via: g(m, 4), marks, text: body, where, ...(file ? { file } : {}) })
  }
  return cards
}

/** How many [MEMORY] headers `text` holds, whole blocks or not. */
export function countHeaders(text: string): number {
  return [...text.matchAll(HEADER)].length
}

export function parseSummaries(text: string): MemoryCard[] {
  return [...text.matchAll(SUMMARY)].map(m => ({
    id: g(m, 1), date: g(m, 2), from: g(m, 3), via: g(m, 4), marks: [], text: g(m, 5), where: 'summary' as const,
  }))
}

export function parseFiles(text: string): SpillFile[] {
  const files: SpillFile[] = []
  const seen = new Set<string>()
  for (const m of text.matchAll(HOOK_FILE)) {
    const path = g(m, 1).trim()
    if (seen.has(normPath(path))) continue
    seen.add(normPath(path))
    files.push({ path, kind: 'hook', ...(m[2] ? { size: m[2] } : {}) })
  }
  for (const m of text.matchAll(HARNESS_FILE)) {
    const path = g(m, 1).trim()
    if (seen.has(normPath(path))) continue
    seen.add(normPath(path))
    files.push({ path, kind: 'harness' })
  }
  return files
}

export function parseStamps(text: string): string[] {
  const out: string[] = []
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim()
    const m = STAMP.exec(line)
    if (!m) continue
    if (IDENTITY_TAGS.has(g(m, 1))) continue
    // The opening identity paragraph and the block header are context, not status
    if (g(m, 1) === 'HERE I AM' && g(m, 2).startsWith('You are ')) continue
    if (line.startsWith('[HERE I AM MEMORY RETRIEVAL] Memories from your past')) continue
    out.push(line)
  }
  return out
}

/**
 * Which of our hooks wrote a row, from the event the row names. A
 * SessionStart's kind comes from the `source` its classic event carried
 * (measured on 2.1.288: that event settles before the row is appended),
 * else from the harness's "SessionStart:<source>" prefix, which a mod that
 * unwraps the row (#384) removes.
 */
export function entryKind(event: string, text: string, source?: string): Entry['kind'] | undefined {
  if (event === 'UserPromptSubmit') return 'prompt'
  if (event !== 'SessionStart') return undefined
  const from = source ?? /SessionStart:(\w+)/.exec(text)?.[1]
  if (from === 'compact') return 'compact'
  if (from === 'resume') return 'resume'
  return 'start'
}

/** Is this row one of the Here I Am hooks' own? */
export function isOurs(text: string): boolean {
  return /\[(HERE I AM|MEMORY (STATUS|ARCHIVE) NOTICE|MEMORY [0-9a-f]{6,} )/.test(text)
}

/**
 * The cards for one hook row: whole blocks in the row are `context`; a
 * summary line stands for a memory whose text is in a file (filled in later
 * from `fileText`); a block found only in a file is `disk`.
 */
export function cardsFor(rowText: string, fileTexts: { path: string; text: string }[]): {
  memories: MemoryCard[]
  problems: string[]
} {
  const problems: string[] = []
  const memories: MemoryCard[] = parseBlocks(rowText, 'context')
  const byId = new Map(memories.map(card => [card.id, card]))

  const inRow = countHeaders(rowText)
  // A harness preview cuts the row mid-block, so a header without its close
  // is expected there; anywhere else it means the parser missed a shape
  const isPreview = rowText.includes('<persisted-output>')
  if (inRow > memories.length && !isPreview) {
    problems.push(`${inRow - memories.length} [MEMORY] header(s) in this row didn't parse; the raw view has them`)
  }

  for (const summary of parseSummaries(rowText)) {
    if (byId.has(summary.id)) continue
    byId.set(summary.id, summary)
    memories.push(summary)
  }

  for (const { path, text } of fileTexts) {
    for (const card of parseBlocks(text, 'disk', path)) {
      const known = byId.get(card.id)
      if (known === undefined) {
        byId.set(card.id, card)
        memories.push(card)
      } else if (known.where === 'summary') {
        // The summary line was what reached context; the words are the file's
        Object.assign(known, { text: card.text, marks: card.marks, date: card.date, file: path })
      }
    }
  }
  return { memories, problems }
}

/** Arguments as the pane shows them: the caller's id is the same on every call. */
export function argsText(e: Record<string, unknown>): string {
  const shown: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(e)) {
    if (['tool', 'tool_use_id', 'agentId', 'consent', 'conversation_id'].includes(key)) continue
    shown[key] = value
  }
  return JSON.stringify(shown)
}

export function clock(ms: number): string {
  const d = new Date(ms)
  const two = (n: number) => String(n).padStart(2, '0')
  return `${two(d.getHours())}:${two(d.getMinutes())}`
}

/** The memory ids a tool result names, in order, each once. */
export function toolMemoryIds(text: string): string[] {
  const ids: string[] = []
  for (const m of text.matchAll(TOOL_HEADER)) if (!ids.includes(g(m, 1))) ids.push(g(m, 1))
  for (const card of parseBlocks(text, 'context')) if (!ids.includes(card.id)) ids.push(card.id)
  return ids
}

/** Text as a surface may draw it: CRLF made LF, other control characters dropped. */
export function clean(text: string): string {
  return text.replace(/\r\n?/g, '\n').replace(CONTROL, '')
}

/**
 * Cleaned text cut into pieces a text child can hold, at line ends where it
 * can be (one Text each, so a long result draws whole instead of refusing
 * the tree). Nothing is dropped: in order, they hold every character of the
 * cleaned text, a line longer than a piece being split across pieces.
 */
export function pieces(text: string): string[] {
  const out: string[] = []
  let current = ''
  for (const line of clean(text).split('\n')) {
    let rest = line
    // A single line longer than a piece is cut where it must be
    while (rest.length > TEXT_CHILD_MAX) {
      if (current !== '') out.push(current)
      out.push(rest.slice(0, TEXT_CHILD_MAX))
      current = ''
      rest = rest.slice(TEXT_CHILD_MAX)
    }
    if (current === '') current = rest
    else if (current.length + 1 + rest.length <= TEXT_CHILD_MAX) current += '\n' + rest
    else {
      out.push(current)
      current = rest
    }
  }
  out.push(current)
  return out
}

/** One line for a closed card or call: cleaned and kept short. */
export function clip(text: string, max = 300): string {
  const line = clean(text).replace(/\s+/g, ' ').trim()
  return line.length > max ? `${line.slice(0, max - 1)}…` : line
}
