import { describe, expect, test } from 'claude-code/testing'
import { DECLINE, FALLBACK_BUDGET, readReply, talkBudget, utcStamp } from './register.ts'

const SUMMARY = { role: 'user' as const, text: 'This session is being continued...', toolUses: [] }
const OLD = { role: 'assistant' as const, text: 'the talk before', toolUses: [] }
const TALK = '[HERE I AM — THE TALK BEFORE THE BOUNDARY]\nthe talk'
const NOW = Date.UTC(2026, 9, 7, 21, 43, 4)
const DELIVERED = { available: true, text: TALK, shown: 2, total: 2, delivery_id: 'd-1' }

type Seen = { forks: string[]; posts: Record<string, unknown>[]; logs: string[] }

type Opts = {
  reply?: string
  fork?: object
  backend?: object
  status?: number
  // What /compact-talk/taken answers, or the status it fails with
  taken?: boolean
  takenStatus?: number
  // The context breakdown session.usage reports, or 'throws'
  breakdown?: object | 'throws'
  env?: Record<string, string>
}

// The engine beneath the mod: a fork that answers `reply` (or the result
// given), a backend that answers `backend` and then `taken`, a session at
// a 1M window, and a compaction that returns the summary
function engine(on: any, opts: Opts): Seen {
  const seen: Seen = { forks: [], posts: [], logs: [] }
  on('model.fork', async (_$: any, e: any) => {
    seen.forks.push(e.prompt)
    return { value: opts.fork ?? { isAnswered: true, text: opts.reply ?? DECLINE, usage: {} } }
  })
  on('env.get', async (_$: any, e: any) => ({ value: (opts.env ?? {})[e.name] }))
  on('session.id', async () => ({ value: 'session-1' }))
  on('clock.now', async () => ({ value: NOW }))
  on('session.usage', async () => {
    if (opts.breakdown === 'throws') throw new Error('no session bound')
    return { value: { startedAt: 0, context: {
      window: 1000000,
      breakdown: opts.breakdown ?? { autoCompactThreshold: 967000, rawMaxTokens: 1000000, isAutoCompactEnabled: true },
    } } }
  })
  on('http.fetch', async (_$: any, e: any) => {
    seen.posts.push({ url: e.url, ...JSON.parse(e.init.body) })
    const taken = e.url.endsWith('/taken')
    const status = taken ? (opts.takenStatus ?? 200) : (opts.status ?? 200)
    const body = taken ? { taken: opts.taken ?? true } : (opts.backend ?? DELIVERED)
    return { value: { status, ok: status < 400, headers: {}, text: JSON.stringify(body) } }
  })
  on('ui.log', async (_$: any, e: any) => { seen.logs.push(JSON.stringify(e)); return { value: undefined } })
  on('session.compact', async () => ({ messages: [SUMMARY], tokensBefore: 100, tokensAfter: 10 }))
  return seen
}

describe('the talk', () => {
  test('is appended after the summary once the block took it', async ($, on) => {
    const seen = engine(on, {})
    const result: any = await $.session.compact({ trigger: 'manual', messages: [OLD] })
    expect(result.messages.length).toBe(2)
    expect(result.messages[0].text).toBe(SUMMARY.text)
    expect(result.messages[1]).toEqual({ role: 'user', text: TALK, toolUses: [] })
    expect(seen.posts.length).toBe(2)
    expect(seen.posts[0].url).toBe('http://localhost:8000/api/claude-code/compact-talk')
    expect(seen.posts[0].session_id).toBe('session-1')
    expect(seen.posts[1].url).toBe('http://localhost:8000/api/claude-code/compact-talk/taken')
    expect(seen.posts[1].delivery_id).toBe('d-1')
  })

  test('is budgeted at a fifth of the auto-compaction line', async ($, on) => {
    const seen = engine(on, {})
    await $.session.compact({ trigger: 'auto', messages: [OLD] })
    expect(seen.posts[0].budget_tokens).toBe(193400)
  })

  test('is budgeted on the compaction window when auto-compaction is off', async ($, on) => {
    const seen = engine(on, { breakdown: { rawMaxTokens: 200000, isAutoCompactEnabled: false } })
    await $.session.compact({ trigger: 'manual', messages: [OLD] })
    expect(seen.posts[0].budget_tokens).toBe(40000)
  })

  test('is held to the fallback when the line cannot be read, and that is said', async ($, on) => {
    const seen = engine(on, { breakdown: 'throws' })
    const result: any = await $.session.compact({ trigger: 'manual', messages: [OLD] })
    expect(seen.posts[0].budget_tokens).toBe(FALLBACK_BUDGET)
    expect(seen.logs.join('\n')).toContain('could not read the compaction line')
    expect(result.messages.length).toBe(2)
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
    expect(seen.posts.length).toBe(1)
  })

  test('is not appended when the backend fails, and that is said', async ($, on) => {
    const seen = engine(on, { status: 500, backend: { detail: 'boom' } })
    const result: any = await $.session.compact({ trigger: 'manual', messages: [OLD] })
    expect(result.messages.length).toBe(1)
    expect(seen.logs.join('\n')).toContain('the backend answered 500')
  })

  test('is not appended when the block did not take it, and that is said', async ($, on) => {
    const seen = engine(on, { taken: false })
    const result: any = await $.session.compact({ trigger: 'manual', messages: [OLD] })
    expect(result.messages.length).toBe(1)
    expect(seen.logs.join('\n')).toContain('did not take the talk')
  })

  test('is appended anyway when the question fails: a duplicate, never a loss', async ($, on) => {
    const seen = engine(on, { takenStatus: 503 })
    const result: any = await $.session.compact({ trigger: 'manual', messages: [OLD] })
    expect(result.messages.length).toBe(2)
    expect(seen.logs.join('\n')).toContain('could not confirm')
  })

  test('a refused reflection is said', async ($, on) => {
    const seen = engine(on, { reply: 'a reflection', backend: { ...DELIVERED, reflection_error: 'Error: too long' } })
    await $.session.compact({ trigger: 'manual', messages: [OLD] })
    expect(seen.logs.join('\n')).toContain('was not saved: Error: too long')
  })
})

describe('the turn before', () => {
  test('a reflection is sent to be saved as written', async ($, on) => {
    const seen = engine(on, { reply: '  What this stretch was.  ' })
    await $.session.compact({ trigger: 'auto', messages: [OLD] })
    expect(seen.forks.length).toBe(1)
    expect(seen.forks[0]).toContain('(auto)')
    expect(seen.forks[0]).toContain('It is 2026-10-07 21:43 UTC.')
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

  test('everything when HIM_DISABLE is set, silently', async ($, on) => {
    const seen = engine(on, { env: { HIM_DISABLE: '1' } })
    const result: any = await $.session.compact({ trigger: 'auto', messages: [OLD] })
    expect(result.messages.length).toBe(1)
    expect(seen.forks.length + seen.posts.length + seen.logs.length).toBe(0)
  })
})

describe('utcStamp', () => {
  test('reads to the minute, in UTC', () => {
    expect(utcStamp(Date.UTC(2026, 0, 2, 3, 4, 59))).toBe('2026-01-02 03:04 UTC')
  })
})

describe('talkBudget', () => {
  test('is a fifth of the line, and nothing without one', () => {
    expect(talkBudget(467000)).toBe(93400)
    expect(talkBudget(0)).toBe(undefined)
    expect(talkBudget(undefined)).toBe(undefined)
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
