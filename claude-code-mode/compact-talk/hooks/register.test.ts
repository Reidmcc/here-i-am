import { describe, expect, test } from 'claude-code/testing'
import { DECLINE, readReply } from './register.ts'

const SUMMARY = { role: 'user' as const, text: 'This session is being continued...', toolUses: [] }
const OLD = { role: 'assistant' as const, text: 'the talk before', toolUses: [] }
const TALK = '[HERE I AM — THE TALK BEFORE THE BOUNDARY]\nthe talk'

type Seen = { forks: string[]; posts: Record<string, unknown>[]; logs: string[] }

// The engine beneath the mod: a fork that answers `reply` (or the result
// given), a backend that answers `backend`, and a compaction that returns
// the summary
function engine(on: any, opts: { reply?: string; fork?: object; backend?: object; status?: number; env?: Record<string, string> }): Seen {
  const seen: Seen = { forks: [], posts: [], logs: [] }
  on('model.fork', async (_$: any, e: any) => {
    seen.forks.push(e.prompt)
    return { value: opts.fork ?? { isAnswered: true, text: opts.reply ?? DECLINE, usage: {} } }
  })
  on('env.get', async (_$: any, e: any) => ({ value: (opts.env ?? {})[e.name] }))
  on('session.id', async () => ({ value: 'session-1' }))
  on('http.fetch', async (_$: any, e: any) => {
    seen.posts.push({ url: e.url, ...JSON.parse(e.init.body) })
    const status = opts.status ?? 200
    return { value: {
      status, ok: status < 400, headers: {},
      text: JSON.stringify(opts.backend ?? { available: true, text: TALK, shown: 2, total: 2 }),
    } }
  })
  on('ui.log', async (_$: any, e: any) => { seen.logs.push(JSON.stringify(e)); return { value: undefined } })
  on('session.compact', async () => ({ messages: [SUMMARY], tokensBefore: 100, tokensAfter: 10 }))
  return seen
}

describe('the talk', () => {
  test('is appended after the summary', async ($, on) => {
    const seen = engine(on, {})
    const result: any = await $.session.compact({ trigger: 'manual', messages: [OLD] })
    expect(result.messages.length).toBe(2)
    expect(result.messages[0].text).toBe(SUMMARY.text)
    expect(result.messages[1]).toEqual({ role: 'user', text: TALK, toolUses: [] })
    expect(seen.posts.length).toBe(1)
    expect(seen.posts[0].url).toBe('http://localhost:8000/api/claude-code/compact-talk')
    expect(seen.posts[0].session_id).toBe('session-1')
  })

  test('goes to the configured backend and entity', async ($, on) => {
    const seen = engine(on, { env: { HIM_BACKEND_URL: 'http://127.0.0.1:9000/', HIM_ENTITY: 'Kira' } })
    await $.session.compact({ trigger: 'auto', messages: [OLD] })
    expect(seen.posts[0].url).toBe('http://127.0.0.1:9000/api/claude-code/compact-talk')
    expect(seen.posts[0].entity).toBe('Kira')
  })

  test('is not appended when there is none, and that is said', async ($, on) => {
    const seen = engine(on, { backend: { available: false, text: '', reason: 'no conversation is recorded for this session' } })
    const result: any = await $.session.compact({ trigger: 'manual', messages: [OLD] })
    expect(result.messages.length).toBe(1)
    expect(seen.logs.join('\n')).toContain('no conversation is recorded')
  })

  test('is not appended when the backend fails, and that is said', async ($, on) => {
    const seen = engine(on, { status: 500, backend: { detail: 'boom' } })
    const result: any = await $.session.compact({ trigger: 'manual', messages: [OLD] })
    expect(result.messages.length).toBe(1)
    expect(seen.logs.join('\n')).toContain('the backend answered 500')
  })
})

describe('the turn before', () => {
  test('a reflection is sent to be saved as written', async ($, on) => {
    const seen = engine(on, { reply: '  What this stretch was.  ' })
    await $.session.compact({ trigger: 'auto', messages: [OLD] })
    expect(seen.forks.length).toBe(1)
    expect(seen.forks[0]).toContain('(auto)')
    expect(seen.posts[0].pre_compaction).toBe('saved')
    expect(seen.posts[0].reflection).toBe('What this stretch was.')
  })

  test('declining saves nothing', async ($, on) => {
    const seen = engine(on, { reply: `**${DECLINE}.**` })
    await $.session.compact({ trigger: 'manual', messages: [OLD] })
    expect(seen.posts[0].pre_compaction).toBe('declined')
    expect(seen.posts[0].reflection).toBe(undefined)
  })

  test('a fork that fails is reported and the talk still comes', async ($, on) => {
    const seen = engine(on, { fork: { isAnswered: false, reason: 'api-error', status: 413, usage: {} } })
    const result: any = await $.session.compact({ trigger: 'auto', messages: [OLD] })
    expect(seen.posts[0].pre_compaction).toBe('failed')
    expect(String(seen.posts[0].pre_compaction_detail)).toContain('413')
    expect(result.messages.length).toBe(2)
  })
})

describe('passes through', () => {
  test('a precompute', async ($, on) => {
    const seen = engine(on, {})
    const result: any = await $.session.compact({ trigger: 'precompute', messages: [OLD] })
    expect(result.messages.length).toBe(1)
    expect(seen.forks.length + seen.posts.length).toBe(0)
  })

  test("a subagent's compaction", async ($, on) => {
    const seen = engine(on, {})
    const result: any = await $.session.compact({ trigger: 'auto', agentId: 'a1', messages: [OLD] })
    expect(result.messages.length).toBe(1)
    expect(seen.forks.length + seen.posts.length).toBe(0)
  })
})

describe('readReply', () => {
  test('reads the decline loosely and anything else as the reflection', () => {
    expect(readReply('NO REFLECTION')).toEqual({ pre_compaction: 'declined' })
    expect(readReply('no reflection.')).toEqual({ pre_compaction: 'declined' })
    expect(readReply('No reflection needed, but here is one anyway.').pre_compaction).toBe('saved')
    expect(readReply('   ').pre_compaction).toBe('failed')
  })
})
