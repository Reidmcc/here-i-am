// Why a Here I Am tool call's input was not JSON, said so it can be fixed
// (issue #392). Claude Code refuses such a call before it reaches the
// server, and its own error shows only the first 200 bytes and a list of
// generic causes; the measured failures (every one logged whole) were a
// memory id left unquoted, `"cites": 873c391c`, which that error never
// points at. JavaScriptCore's JSON.parse gives no position, so this scans
// the input itself and stops at the first break.
//
// Claude Code keeps only the first RAW_KEPT characters of an unparsed input
// (`raw`) beside its full length (`len`): a break past that is not in the
// text at all, and the message says so instead of guessing where it was.

export const RAW_KEPT = 2048

export type Break =
  | { kind: 'end'; pos: number; key?: string }
  | { kind: 'bare'; pos: number; token: string; key?: string }
  | { kind: 'quote'; pos: number; key?: string }
  | { kind: 'escape'; pos: number; key?: string }
  | { kind: 'control'; pos: number; char: string; key?: string }
  | { kind: 'unexpected'; pos: number; char: string; key?: string }

class Stop {
  constructor(readonly found: Break) {}
}

const TOKEN = /[^\s,:[\]{}"]+/y
const NUMBER = /-?(0|[1-9]\d*)(\.\d+)?([eE][+-]?\d+)?/y
const WORD = /[\w.-]/
const ESCAPES = '"\\/bfnrt'

// The first place `raw` stops being JSON, or null when it parses whole.
export function findBreak(raw: string): Break | null {
  let i = 0
  const keys: (string | undefined)[] = []

  const fail = (found: Break): never => {
    throw new Stop({ ...found, key: keys[keys.length - 1] })
  }
  const ws = () => {
    while (i < raw.length && ' \t\n\r'.includes(raw[i])) i++
  }
  const atEnd = () => {
    if (i >= raw.length) fail({ kind: 'end', pos: i })
  }
  // A run of text where a value belongs: unquoted, so not JSON. A run that
  // reaches the end of the text may only be where the kept text stops.
  const bare = (): never => {
    TOKEN.lastIndex = i
    const m = TOKEN.exec(raw)
    if (!m) return fail({ kind: 'unexpected', pos: i, char: raw[i] })
    if (i + m[0].length >= raw.length) return fail({ kind: 'end', pos: raw.length })
    return fail({ kind: 'bare', pos: i, token: m[0] })
  }
  // After a value inside an object or array, where `,` or a close belongs.
  // Text right after a string's closing quote means the quote ended the
  // string early: an unescaped `"` inside the text.
  const separator = (close: string): boolean => {
    const valueEnd = i
    ws()
    atEnd()
    if (raw[i] === ',') {
      i++
      return true
    }
    if (raw[i] === close) {
      i++
      return false
    }
    if (raw[valueEnd - 1] === '"' && WORD.test(raw[i])) {
      return fail({ kind: 'quote', pos: valueEnd - 1 })
    }
    return fail({ kind: 'unexpected', pos: i, char: raw[i] })
  }

  const string = (): string => {
    const start = ++i
    for (;;) {
      atEnd()
      const c = raw[i]
      if (c === '"') return raw.slice(start, i++)
      if (c === '\\') {
        const n = raw[i + 1]
        if (n === undefined) fail({ kind: 'end', pos: raw.length })
        if (ESCAPES.includes(n)) {
          i += 2
          continue
        }
        if (n === 'u') {
          const hex = raw.slice(i + 2, i + 6)
          if (/^[0-9a-fA-F]{4}$/.test(hex)) {
            i += 6
            continue
          }
          if (i + 6 > raw.length && /^[0-9a-fA-F]*$/.test(hex)) fail({ kind: 'end', pos: raw.length })
        }
        fail({ kind: 'escape', pos: i })
      }
      if (c < ' ') fail({ kind: 'control', pos: i, char: c })
      i++
    }
  }

  const value = (): void => {
    ws()
    atEnd()
    const c = raw[i]
    if (c === '{') return object()
    if (c === '[') return array()
    if (c === '"') {
      string()
      return
    }
    if (c === '-' || (c >= '0' && c <= '9')) {
      NUMBER.lastIndex = i
      const m = NUMBER.exec(raw)
      // A number running straight into letters (`873c391c`) is a bare word
      if (m && m[0].length && !WORD.test(raw[i + m[0].length] ?? '')) {
        i += m[0].length
        if (i < raw.length) return
      }
      return bare()
    }
    for (const literal of ['true', 'false', 'null']) {
      if (raw.startsWith(literal, i) && !WORD.test(raw[i + literal.length] ?? '')) {
        i += literal.length
        if (i < raw.length) return
      }
    }
    return bare()
  }

  const object = (): void => {
    i++
    ws()
    atEnd()
    if (raw[i] === '}') {
      i++
      return
    }
    for (;;) {
      ws()
      atEnd()
      if (raw[i] !== '"') bare()
      const key = string()
      ws()
      atEnd()
      if (raw[i] !== ':') fail({ kind: 'unexpected', pos: i, char: raw[i] })
      i++
      // The key stays named through its separator: a stray quote that
      // ended the value early is found there
      keys.push(key)
      value()
      const more = separator('}')
      keys.pop()
      if (!more) return
    }
  }

  const array = (): void => {
    i++
    ws()
    atEnd()
    if (raw[i] === ']') {
      i++
      return
    }
    do value()
    while (separator(']'))
  }

  try {
    value()
    ws()
    if (i < raw.length) fail({ kind: 'unexpected', pos: i, char: raw[i] })
    return null
  } catch (e) {
    if (e instanceof Stop) return e.found
    throw e
  }
}

// The list parameters a memory id goes into, written as the fix
const LIST_KEYS = new Set(['revises', 'cites'])

const CHAR_NAMES: Record<string, string> = { '\n': 'line break', '\r': 'carriage return', '\t': 'tab' }
const CHAR_ESCAPES: Record<string, string> = { '\n': '\\n', '\r': '\\r', '\t': '\\t' }

function where(found: Break, len: number): string {
  const at = `character ${found.pos + 1} of ${len}`
  return found.key === undefined ? at : `${at}, in "${found.key}"`
}

// A short stretch of the input around the break, line breaks shown as \n
function around(raw: string, pos: number): string {
  const show = (s: string) => s.replace(/\r/g, '\\r').replace(/\n/g, '\\n').replace(/\t/g, '\\t')
  const before = raw.slice(Math.max(0, pos - 60), pos)
  const after = raw.slice(pos, pos + 30)
  return `${pos > 60 ? '…' : ''}${show(before)}⟦here⟧${show(after)}${pos + 30 < raw.length ? '…' : ''}`
}

function cause(found: Break, raw: string): string {
  switch (found.kind) {
    case 'bare': {
      const fix =
        found.key !== undefined && LIST_KEYS.has(found.key)
          ? `"${found.key}": ["${found.token}"]`
          : `"${found.token}"`
      return (
        `\`${found.token}\` is not in quotes. Memory ids and all other text are JSON strings, ` +
        `so they need double quotes: ${fix}.`
      )
    }
    case 'quote':
      return (
        'a double quote inside the text ended the string early. ' +
        'Write a quote inside text as \\" (or use a different quotation mark).'
      )
    case 'escape':
      return (
        `\`${raw.slice(found.pos, found.pos + 2)}\` is not a JSON escape. Inside a string only ` +
        '\\" \\\\ \\/ \\b \\f \\n \\r \\t and \\uXXXX are; a backslash itself is written \\\\, ' +
        "and an apostrophe needs no escape."
      )
    case 'control':
      return (
        `a raw ${CHAR_NAMES[found.char] ?? `control character (U+${found.char.charCodeAt(0).toString(16).padStart(4, '0')})`} ` +
        `inside a string. Write it as ${CHAR_ESCAPES[found.char] ?? '\\uXXXX'}.`
      )
    case 'unexpected':
      return `\`${found.char}\` was not expected there (a missing comma, colon or closing bracket is the usual cause).`
    case 'end':
      return 'the input ends before the JSON does.'
  }
}

// The whole message the model reads in place of Claude Code's own
export function explain(tool: string, raw: string, len: number): string {
  const name = tool.replace(/^mcp__here-i-am__/, '')
  const head =
    `[HERE I AM] ${name} was not called: its input is not valid JSON, so Claude Code ` +
    'refused it before it reached the Here I Am server. Nothing was saved or changed. ' +
    'Fix the input and call it again.'
  const kept = raw.length < len
  const found = findBreak(raw)

  if (found === null || (found.kind === 'end' && kept)) {
    return [
      head,
      `Claude Code keeps only the first ${raw.length} of the input's ${len} characters, and ` +
        'the JSON is unbroken that far, so the break comes later and cannot be shown. ' +
        'The commonest cause measured here is a memory id left out of quotes, ' +
        '`"cites": 873c391c` where `"cites": ["873c391c"]` belongs: check the values ' +
        'after the long text, ids especially.',
    ].join('\n\n')
  }
  if (found.kind === 'end') {
    return [
      head,
      `It stops at ${where(found, len)}: ${cause(found, raw)} ` +
        'The call was cut off while it was being written; write it again whole.',
    ].join('\n\n')
  }
  return [head, `It broke at ${where(found, len)}: ${cause(found, raw)}`, `Around the break: ${around(raw, found.pos)}`].join(
    '\n\n',
  )
}

// The unparsed input Claude Code hands a tool.call hook, or null
export function unparsed(e: Record<string, unknown>): { raw: string; len: number } | null {
  const u = e.__unparsedToolInput as { raw?: unknown; len?: unknown } | undefined
  if (typeof u !== 'object' || u === null) return null
  if (typeof u.raw !== 'string' || typeof u.len !== 'number') return null
  return { raw: u.raw, len: u.len }
}
