import { describe, expect, test } from 'claude-code/testing'
import { RAW_KEPT, explain, findBreak } from './diagnose'

const SAVE = 'mcp__here-i-am__memory_save'
const CORE_REFUSAL =
  `${SAVE} was called with input that could not be parsed as JSON.\nYou sent (first 200 of 9 bytes): ...`

// The shapes of the failures logged whole in the transcripts (#392)
const BARE_REVISES = '{"content": "a reflection when I didn\'t.", "conversation_id": "8db3d8a3", "revises": 72d8695e}'
const BARE_CITES = '{"content": "each in our lane.", "cites": 873c391c}'
const BARE_BOTH = '{"content": "kept out.", "revises": 50c35538, "cites": b5baca8e}'

describe('findBreak', () => {
  test('passes valid JSON', () => {
    expect(findBreak('{"content": "a \\"quoted\\" word\\n", "cites": ["873c391c"], "n": -1.5e3, "b": true}')).toBe(null)
  })

  test('finds an unquoted id, digits first or letters first, with its key', () => {
    expect(findBreak(BARE_REVISES)).toEqual({ kind: 'bare', pos: BARE_REVISES.indexOf('72d8695e'), token: '72d8695e', key: 'revises' })
    expect(findBreak(BARE_CITES)).toEqual({ kind: 'bare', pos: BARE_CITES.indexOf('873c391c'), token: '873c391c', key: 'cites' })
    expect(findBreak(BARE_BOTH)).toMatchObject({ kind: 'bare', token: '50c35538', key: 'revises' })
    expect(findBreak('{"cites": ["a1b2c3d4", e5f6a7b8]}')).toMatchObject({ kind: 'bare', token: 'e5f6a7b8', key: 'cites' })
  })

  test('finds a quote that ended a string early', () => {
    const raw = '{"content": "she said "hi" and left"}'
    expect(findBreak(raw)).toEqual({ kind: 'quote', pos: raw.indexOf('"hi') , key: 'content' })
  })

  test('finds a bad escape and a raw line break', () => {
    const escape = '{"content": "C:\\Users\\me"}'
    expect(findBreak(escape)).toEqual({ kind: 'escape', pos: escape.indexOf('\\U'), key: 'content' })
    const newline = '{"content": "two\nlines"}'
    expect(findBreak(newline)).toEqual({ kind: 'control', pos: newline.indexOf('\n'), char: '\n', key: 'content' })
  })

  test('finds a missing comma and trailing text', () => {
    expect(findBreak('{"a": 1 "b": 2}')).toMatchObject({ kind: 'unexpected', char: '"' })
    expect(findBreak('{"a": 1} x')).toMatchObject({ kind: 'unexpected', char: 'x' })
  })

  test('calls text that stops mid-value an end, not a bare word', () => {
    expect(findBreak('{"content": "unfinished')).toMatchObject({ kind: 'end' })
    expect(findBreak('{"cites": ["873c391c"], "include_released": tr')).toMatchObject({ kind: 'end' })
    expect(findBreak('{"content": "x\\u00')).toMatchObject({ kind: 'end' })
    expect(findBreak('{"content": "x", ')).toMatchObject({ kind: 'end' })
  })
})

describe('explain', () => {
  test('names the unquoted id, its key and the list it belongs in', () => {
    const text = explain(SAVE, BARE_CITES, BARE_CITES.length)
    expect(text.startsWith('[HERE I AM] memory_save was not called: its input is not valid JSON')).toBe(true)
    expect(text).toContain('Nothing was saved or changed.')
    expect(text).toContain(`character ${BARE_CITES.indexOf('873c391c') + 1} of ${BARE_CITES.length}, in "cites"`)
    expect(text).toContain('`873c391c` is not in quotes')
    expect(text).toContain('"cites": ["873c391c"]')
    expect(text).toContain('⟦here⟧873c391c}')
  })

  test('outside revises and cites, quotes the value alone', () => {
    const raw = '{"memory_id": 873c391c}'
    expect(explain('mcp__here-i-am__memory_mark', raw, raw.length)).toContain('so they need double quotes: "873c391c".')
  })

  test('says when the break is past what Claude Code kept', () => {
    const long = `{"conversation_id": "x", "content": "${'a'.repeat(RAW_KEPT)}`.slice(0, RAW_KEPT)
    const text = explain(SAVE, long, 2624)
    expect(text).toContain(`keeps only the first ${RAW_KEPT} of the input's 2624 characters`)
    expect(text).toContain('cannot be shown')
    expect(text).toContain('"cites": ["873c391c"]')
  })

  test('still shows a break that falls inside what was kept', () => {
    const raw = (BARE_CITES + ' '.repeat(RAW_KEPT)).slice(0, RAW_KEPT)
    expect(explain(SAVE, raw, 3000)).toContain('`873c391c` is not in quotes')
  })

  test('calls a whole input that stops early a cut-off call', () => {
    const raw = '{"content": "unfinished'
    expect(explain(SAVE, raw, raw.length)).toContain('cut off while it was being written')
  })

  test('names a raw line break and shows it as \\n around the break', () => {
    const raw = '{"content": "line one\nline two"}'
    const text = explain(SAVE, raw, raw.length)
    expect(text).toContain('a raw line break inside a string. Write it as \\n.')
    expect(text).toContain('line one⟦here⟧\\nline two')
  })
})

describe('the hook', () => {
  // Core beneath the mod: refuses an unparsed input as Claude Code does,
  // and records whether any call reached it
  function core(on: any, seen: string[], repaired = false) {
    on('tool.call', async (_$: any, e: any) => {
      seen.push(e.tool)
      if (e.__unparsedToolInput && !repaired) return { isError: true, result: CORE_REFUSAL, text: CORE_REFUSAL }
      return { result: 'Saved reflection as memory abcd1234.', text: 'Saved reflection as memory abcd1234.' }
    })
  }

  test('replaces the refusal of a here-i-am call with the diagnosis', async ($, on) => {
    const seen: string[] = []
    core(on, seen)
    const ran: any = await $.tool.call({ tool: SAVE, __unparsedToolInput: { raw: BARE_CITES, len: BARE_CITES.length } } as any)
    expect(seen).toEqual([SAVE])
    const text = ran.deny ?? ran.text
    expect(text).toContain('`873c391c` is not in quotes')
  })

  test('leaves a repaired call\'s result alone', async ($, on) => {
    const seen: string[] = []
    core(on, seen, true)
    const ran: any = await $.tool.call({ tool: SAVE, __unparsedToolInput: { raw: BARE_CITES, len: BARE_CITES.length } } as any)
    expect(ran.text).toBe('Saved reflection as memory abcd1234.')
  })

  test('leaves other servers\' calls and parsed calls alone', async ($, on) => {
    const seen: string[] = []
    core(on, seen)
    const other: any = await $.tool.call({ tool: 'mcp__gh__issue', __unparsedToolInput: { raw: BARE_CITES, len: 40 } } as any)
    expect(other.text).toBe(CORE_REFUSAL)
    const parsed: any = await $.tool.call({ tool: SAVE, conversation_id: 'x', content: 'fine' } as any)
    expect(parsed.text).toBe('Saved reflection as memory abcd1234.')
  })
})
