import { describe, expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { On } from 'claude-code'

import { cardsFor, parseBlocks, parseFiles, parseStamps, parseSummaries } from '../hooks/parse'

// Synthetic memories in the backend's exact shapes (memory_context's marker,
// claude_code_mode's summary line, the hooks' spill wording). No real
// archive content belongs in a fixture.
const SPILL = 'C:\\Users\\someone\\AppData\\Local\\Temp\\here-i-am-sessions\\abc-retrieval-131619.md'
const BLOCK_A = '[MEMORY aaaa1111 from 2026-08-24T16:31:15.788419 - originally from you - via Here I Am]\nThe first memory, whole.\nIts second line.\n[/MEMORY]'
const BLOCK_B = '[MEMORY bbbb2222 from 2026-10-07T17:00:44.017912 - a reflection you saved - via Claude Code]\n[revises cccc3333 (2026-10-04, reflection)]\nA reflection that revises another.\n[/MEMORY]'
const BLOCK_C = '[MEMORY dddd4444 from 2026-09-01T10:00:00 - originally from human - via Here I Am]\nThe spilled one, only on disk.\nMore of it.\n[/MEMORY]'
const ROW = [
  '<system-reminder>',
  'UserPromptSubmit hook success: [HERE I AM MEMORY RETRIEVAL] Memories from your past conversations that surfaced as relevant to this prompt:',
  '',
  BLOCK_A,
  '',
  BLOCK_B,
  '',
  '1 more surfaced, listed by summary line; their full text is in the file named below:',
  '- dddd4444 (2026-09-01 - originally from human - via Here I Am): The spilled one, only on disk.',
  '',
  '[HERE I AM] 2 of the 3 retrieved memories are shown above in full; the other 1 was too large to inject inline and is listed by summary line. Their full verbatim text is written to:',
  SPILL,
  'Read it if you want their words.',
  '',
  '[HERE I AM MEMORY RETRIEVAL] matched: 3 new (1 in-context reflection skipped; in-context verbatim held 0 slots).',
  '</system-reminder>',
].join('\n')
const SPILL_TEXT = [BLOCK_A, BLOCK_B, BLOCK_C].join('\n\n')

const PANE_PROPS = {
  title: 'Memory', isFocused: false, bodyColumns: 60, placement: 'dock' as const,
  scroll: { offset: 0, bodyRows: 40 }, view: {},
}

// The kit has no stand-in for session.append's store (a hook answering a row
// without next is skipped, and next throws at the bottom), so the hook-row
// path is measured end to end in a headless session instead (see the PR);
// these tests reach the pane through the memory tools.
async function mountPane($: Engine, surface: 'terminal' | 'desktop') {
  return $.ui.mount({ plugin: 'memory-pane', surface, component: 'Pane', requestId: 'memory', props: PANE_PROPS })
}

async function shownText(ui: { findAll: (q: { type: string }) => Promise<{ text: string }[]> }): Promise<string> {
  return (await ui.findAll({ type: 'Text' })).map(found => found.text).join('\n')
}

function toolBeneath(on: On, text: string) {
  const answer = { result: 'raw', text }
  on('tool.call', () => answer)
  return answer
}

describe('parsing the hooks rows', () => {
  test('whole blocks, link marks, summary lines, spill files and stamps', async () => {
    const blocks = parseBlocks(ROW, 'context')
    expect(blocks.map(card => card.id)).toEqual(['aaaa1111', 'bbbb2222'])
    expect(blocks[0]).toMatchObject({ date: '2026-08-24T16:31:15.788419', from: 'originally from you', via: 'via Here I Am', text: 'The first memory, whole.\nIts second line.' })
    expect(blocks[1]).toMatchObject({ from: 'a reflection you saved', via: 'via Claude Code', marks: ['[revises cccc3333 (2026-10-04, reflection)]'], text: 'A reflection that revises another.' })
    expect(parseSummaries(ROW).map(card => card.id)).toEqual(['dddd4444'])
    expect(parseFiles(ROW)).toEqual([{ path: SPILL, kind: 'hook' }])
    expect(parseStamps(ROW)).toContain('[HERE I AM MEMORY RETRIEVAL] matched: 3 new (1 in-context reflection skipped; in-context verbatim held 0 slots).')
  })

  test('a spilled memory is filled from its file and labeled as not in context', async () => {
    const { memories, problems } = cardsFor(ROW, [{ path: SPILL, text: SPILL_TEXT }])
    expect(problems).toEqual([])
    expect(memories.map(card => [card.id, card.where])).toEqual([['aaaa1111', 'context'], ['bbbb2222', 'context'], ['dddd4444', 'summary']])
    expect(memories[2]).toMatchObject({ text: 'The spilled one, only on disk.\nMore of it.', file: SPILL })
  })

  test('the harness persist line and its preview', async () => {
    const preview = '<persisted-output>\nOutput too large (29.6KB). Full output saved to: C:\\x\\tool-results\\hook-1-stdout.txt\n\nPreview (first 2KB):\n[MEMORY aaaa1111 from 2026-08-24 - originally from you - via Here I Am]\nThe first mem\n...\n</persisted-output>'
    expect(parseFiles(preview)).toEqual([{ path: 'C:\\x\\tool-results\\hook-1-stdout.txt', kind: 'harness' }])
    const { memories, problems } = cardsFor(preview, [{ path: 'C:\\x\\tool-results\\hook-1-stdout.txt', text: BLOCK_A }])
    // Cut mid-block by the preview: nothing of it reached context whole
    expect(problems).toEqual([])
    expect(memories.map(card => card.where)).toEqual(['disk'])
  })

  test('an unparsed header is said aloud, not dropped', async () => {
    const odd = '[HERE I AM MEMORY RETRIEVAL] x\n[MEMORY eeee5555 from 2026-01-01 - something new]\ntext\n[/MEMORY]'
    expect(cardsFor(odd, []).problems).toHaveLength(1)
  })
})

describe('the pane', () => {
  for (const surface of ['terminal', 'desktop'] as const) {
    test(`lists a memory tool call with what came back, unchanged, on ${surface}`, async ($, on) => {
      mock.clock(on)
      const answer = toolBeneath(on, `Found 1:\n\n${BLOCK_A}`)
      const ran = await $.tool.call({ tool: 'mcp__here-i-am__memory_query', query: 'the first memory', conversation_id: 'conv-1' } as never)
      // Read-only: the model gets exactly what the tool answered
      expect(ran).toMatchObject(answer)

      const ui = await mountPane($, surface)
      let text = await shownText(ui)
      expect(text).toContain('memory_query')
      expect(text).toContain('{"query":"the first memory"}')
      expect(text).not.toContain('conv-1')
      expect(text).toContain('→ Found 1: (1 memories)')
      expect(text).not.toContain('Its second line.')

      const call = await ui.find({ type: 'Button', text: 'open' })
      await ui.press({ key: String(call?.key) })
      text = await shownText(ui)
      expect(text).toContain('Its second line.')
    })

    test(`collapses to one line and back, on ${surface}`, async ($, on) => {
      mock.clock(on)
      toolBeneath(on, 'nothing found')
      await $.tool.call({ tool: 'mcp__here-i-am__memory_find', text: 'kiln', conversation_id: 'c' } as never)
      const ui = await mountPane($, surface)
      await ui.press({ key: 'collapse' })
      const texts = await ui.findAll({ type: 'Text' })
      expect(texts).toHaveLength(1)
      expect(texts[0]?.text).toContain('1 memory tool call')
      await ui.press({ key: 'collapse' })
      expect(await shownText(ui)).toContain('memory_find')
    })
  }

  test('other tools are not listed', async ($, on) => {
    mock.clock(on)
    toolBeneath(on, 'saved')
    await $.tool.call({ tool: 'mcp__here-i-am__declare_room', room: 'porch', conversation_id: 'c' } as never)
    const ui = await mountPane($, 'desktop')
    expect(await shownText(ui)).toContain('No memory has surfaced yet.')
  })

  test('a refused call is shown as refused', async ($, on) => {
    mock.clock(on)
    on('tool.call', () => ({ deny: 'not now' }))
    const ran = await $.tool.call({ tool: 'mcp__here-i-am__memory_save', content: 'x', conversation_id: 'c' } as never)
    expect(ran).toEqual({ deny: 'not now' })
    const ui = await mountPane($, 'desktop')
    expect(await shownText(ui)).toContain('→ error')
  })
})
