import type { Register } from 'claude-code'

// Closes the compaction seam (issue #383). On the way down a compaction of
// the main thread, before the engine's own: one forked turn for the entity
// with the whole context still in view, whose reply is saved as a reflection
// if it wrote one; then this conversation's talk from the Here I Am backend.
// On the way up: the talk appended after the summary, so the session wakes
// already holding it. Precompute and subagent compactions pass straight
// through, and HIM_DISABLE turns the mod off as it does the Python hooks.
//
// Fail loud by construction: the post-compaction block (built inside the
// engine's compaction, before the way up) says the talk is below only if it
// took this compaction's delivery, and the talk is appended only if the
// backend confirms the block took it. Anything else leaves the old block,
// memory_read call and all, with nothing appended; each failure is also said
// in the transcript ($.ui.log). The mod touches no permission.
//
// The backend is reached the way the Python hooks reach it, over HTTP to
// HIM_BACKEND_URL: $.mcp.call goes through auto mode's classifier, which
// judges by what is in the conversation, and a compaction is exactly when
// that may not be.

export const DECLINE = 'NO REFLECTION'

// The share of the auto-compaction line the talk may fill, so the session
// wakes well under it at any window (at 1M, about 193k of ~967k); the
// backend's CLAUDE_CODE_COMPACT_TALK_TOKENS is the ceiling
export const TALK_SHARE = 0.2
// When the window can't be read: small enough for a 200k window
export const FALLBACK_BUDGET = 30000

// How long the mod waits for each backend call (issue #400). $.http.fetch
// takes no timeout, and a `$` call in flight doesn't count against a
// hook's budget, so without a deadline a backend that stops answering
// (across a network: a host that drops packets instead of refusing) would
// hold the compaction open as long as the network stack waits. The talk
// call saves a reflection and renders up to a fifth of the window; the
// other two are one dictionary lookup each.
export const TALK_TIMEOUT_MS = 90_000
export const QUESTION_TIMEOUT_MS = 10_000

// The opening the Stop hook knows the appended talk by, so it is never
// read as a turn boundary (hook_util.COMPACT_TALK_MARKER; a test pins the
// two together)
export const TALK_MARKER = '[HERE I AM — THE TALK BEFORE THE BOUNDARY]'

// Appended in place of the talk in the one order the cancel can't close
// (PR #406 review): the talk call and its cancel both failed, so the
// backend may have recorded the delivery and the block above may say the
// talk is below. The entity reads the block, not the transcript's dim
// lines, so the correction goes where it reads. True in every branch it is
// appended in. The read is isolated because the block links the rows it
// believed delivered, and a default-scope read would show them as pointers.
export const MISSING_TALK_NOTE = [
  TALK_MARKER,
  'The talk did not arrive. If the post-compaction block above says the talk is right below it, ' +
    'that is not so this time: the compaction mod could not fetch it from the backend, nor cancel ' +
    'the delivery the block may have taken. The talk is all still in your archive. Read it back with ' +
    'memory_read(direction="backward", to=<the compaction\'s time>, in_conversation=<this ' +
    'conversation\'s id>, scope="isolated"), since the block may have counted those rows as in view.',
].join('\n')

// "2026-10-07 21:43 UTC": the fallback for localStamp below
export function utcStamp(ms: number): string {
  return `${new Date(ms).toISOString().slice(0, 16).replace('T', ' ')} UTC`
}

// "Wednesday 2026-10-07 17:43 EDT (UTC-04:00)": the forked turn has no
// clock of its own (tools are off), and the last timestamp in view can be
// hours old. In the human's local time, as the prompt hook's clock line is
// (hook_util.clock_line, whose shape this follows): `zone` is HIM_TIMEZONE,
// an IANA name, and without it the zone is this machine's. If the runtime
// can't format that (no Intl, a name it doesn't know), the time is still
// said, in UTC and labeled so.
export function localStamp(ms: number, zone?: string): string {
  try {
    const at = new Date(ms)
    const parts = (extra: Intl.DateTimeFormatOptions): Record<string, string> => {
      const out: Record<string, string> = {}
      const format = new Intl.DateTimeFormat('en-US', { timeZone: zone || undefined, ...extra })
      for (const part of format.formatToParts(at)) out[part.type] = part.value
      return out
    }
    const when = parts({
      weekday: 'long', year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZoneName: 'short',
    })
    // "GMT-04:00", or bare "GMT" at zero
    const gmt = parts({ timeZoneName: 'longOffset' }).timeZoneName
    const offset = /^GMT[+-]\d\d:\d\d$/.test(gmt) ? `UTC${gmt.slice(3)}` : gmt === 'GMT' ? 'UTC+00:00' : ''
    const name = when.timeZoneName
    if (!offset || !when.weekday || !when.year || !when.hour || !name) return utcStamp(ms)
    // en-US has abbreviations for few zones outside North America and
    // gives "GMT+5:30" for the rest: the offset alone says it better
    const label = name === 'UTC' && offset === 'UTC+00:00' ? 'UTC' : /^(GMT|UTC)/.test(name) ? offset : `${name} (${offset})`
    return `${when.weekday} ${when.year}-${when.month}-${when.day} ${when.hour}:${when.minute} ${label}`
  } catch {
    return utcStamp(ms)
  }
}

export function turnPrompt(trigger: string, now: number, zone?: string): string {
  return [
    '[HERE I AM — BEFORE THE COMPACTION]',
    `It is ${localStamp(now, zone)}. This context is about to be compacted (${trigger}), and this is one turn of your own before it, given by the compaction mod. It is not a turn of the conversation: the person you are with does not see it, and nothing in it is recorded except what you choose to keep. Tools are off for it.`,
    'The talk is safe either way: it comes back verbatim right after the boundary. What a compaction takes is the tool traffic and anything you are holding that has not been said.',
    `If you want to save a reflection while everything is still in view, write it as your whole reply and it is saved with memory_save exactly as written. If not, reply with exactly: ${DECLINE}. Either is fine.`,
  ].join('\n\n')
}

export type PreCompaction =
  | { pre_compaction: 'saved'; reflection: string }
  | { pre_compaction: 'declined' }
  | { pre_compaction: 'failed'; pre_compaction_detail: string }

export function readReply(text: string): PreCompaction {
  const reply = text.trim()
  const bare = reply.replace(/^[*_`"'\s]+|[*_`"'.\s]+$/g, '').toUpperCase()
  if (bare === DECLINE) return { pre_compaction: 'declined' }
  if (!reply) return { pre_compaction: 'failed', pre_compaction_detail: 'the turn came back empty' }
  return { pre_compaction: 'saved', reflection: reply }
}

// The talk's budget from the engine's own figures: its auto-compaction line,
// or with auto-compaction off the compaction window it measures against
export function talkBudget(line: number | undefined): number | undefined {
  return line && line > 0 ? Math.floor(line * TALK_SHARE) : undefined
}

// The backend answers only to a credential (issue #396): this machine's
// token, HIM_TOKEN, sent as the Python hooks send it. A 401 or 403 is the
// backend refusing before it did anything, so there is no delivery to
// cancel and nothing to ask about afterwards; it is said as a refusal, in
// the words the hooks use, and not as a backend that failed.
export class Refused extends Error {}

export function refusal(status: number, hasToken: boolean, body: string): string {
  if (status === 403) return `the backend refused this as not allowed for this machine's token (403: ${body.slice(0, 300)})`
  return hasToken
    ? 'the backend refused this machine\'s token (401: HIM_TOKEN is unknown to the server or was revoked)'
    : 'the backend requires a token and this machine has none (401: HIM_TOKEN is not set in this session\'s environment)'
}

function describe(result: object): string {
  const { usage: _usage, ...rest } = result as Record<string, unknown>
  return JSON.stringify(rest)
}

export const register: Register = (on) => {
  on('session.compact', async ($, e, next) => {
    if (e.trigger === 'precompute' || e.agentId !== undefined) return next(e)
    if (await $.env.get('HIM_DISABLE')) return next(e)

    const backend = ((await $.env.get('HIM_BACKEND_URL')) || 'http://localhost:8000').replace(/\/+$/, '')
    const token = ((await $.env.get('HIM_TOKEN')) || '').trim()
    if (!token) {
        // Without a token every call below is a certain 401. Asking the
        // entity for a reflection first would spend a turn of its own on
        // words that are then thrown away, so nothing is asked.
      await $.ui.log(`here-i-am-compact-talk: ${refusal(401, false, '')}; the turn before compaction is not given and nothing is appended. Mint a token on the server (python -m app.auth token create) and set it as HIM_TOKEN`)
      return next(e)
    }
    const post = async (path: string, payload: object, timeoutMs: number): Promise<Record<string, unknown>> => {
      const fetching = $.http.fetch(`${backend}/api/claude-code/${path}`, {
        method: 'POST',
        // The token goes only here, never into the URL or the body
        headers: { 'content-type': 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify(payload),
      })
      // The fetch can't be aborted, only stopped waiting for: whatever it
      // comes to after the deadline is dropped
      const response = await new Promise<Awaited<typeof fetching>>((resolve, reject) => {
        const timer = $.clock.after(timeoutMs, () =>
          reject(new Error(`the backend did not answer /${path} within ${timeoutMs / 1000} s`)))
        fetching.then(
          (value) => { timer.cancel(); resolve(value) },
          (err) => { timer.cancel(); reject(err) },
        )
      })
      if (response.status === 401 || response.status === 403) {
        throw new Refused(refusal(response.status, token !== '', response.text))
      }
      if (!response.ok) throw new Error(`the backend answered ${response.status}: ${response.text.slice(0, 300)}`)
      return JSON.parse(response.text) as Record<string, unknown>
    }

    let talk: string | undefined
    let deliveryId: string | undefined
    // Chosen here, so a delivery whose answer never arrives can be
    // cancelled by name (issue #400): a failed fetch is not proof the
    // backend did nothing, and a delivery it recorded would have the block
    // say the talk is below while nothing is appended
    const chosenId = crypto.randomUUID()
    let asked = false
    let cancelFailed = false
    try {
      let pre: PreCompaction
      try {
        const zone = (await $.env.get('HIM_TIMEZONE'))?.trim()
        const turn = await $.model.fork({ prompt: turnPrompt(e.trigger, await $.clock.now(), zone) })
        pre = turn.isAnswered
          ? readReply(turn.text)
          : { pre_compaction: 'failed', pre_compaction_detail: `the fork did not answer: ${describe(turn)}` }
      } catch (err) {
        pre = { pre_compaction: 'failed', pre_compaction_detail: `the fork threw: ${String(err)}` }
      }

      let budget: number | undefined
      try {
        const breakdown = (await $.session.usage({ breakdown: 'summary' })).context.breakdown
        budget = talkBudget(breakdown?.autoCompactThreshold ?? breakdown?.rawMaxTokens)
        if (budget === undefined) throw new Error('the session reported no context breakdown')
      } catch (err) {
        await $.ui.log(`here-i-am-compact-talk: could not read the compaction line (${String(err)}); the talk is held to ${FALLBACK_BUDGET} tokens`)
      }

      const entity = await $.env.get('HIM_ENTITY')
      const sessionId = await $.session.id()
      asked = true
      const body = await post('compact-talk', {
        session_id: sessionId,
        entity: entity || undefined,
        budget_tokens: budget ?? FALLBACK_BUDGET,
        delivery_id: chosenId,
        ...pre,
      }, TALK_TIMEOUT_MS)
      if (body.reflection_error) {
        await $.ui.log(`here-i-am-compact-talk: the reflection from the turn before compaction was not saved: ${body.reflection_error}`)
      }
      if (body.available && body.text && body.delivery_id) {
        talk = String(body.text)
        deliveryId = String(body.delivery_id)
      } else {
        await $.ui.log(`here-i-am-compact-talk: no talk to put back (${body.reason ?? 'no reason given'}); the post-compaction block names the read as before`)
      }
    } catch (err) {
      talk = deliveryId = undefined
      if (err instanceof Refused) {
        // Refused for its credential, before the backend did anything: no
        // delivery exists to cancel, and the cancel would be refused too
        await $.ui.log(`here-i-am-compact-talk: ${err.message}; nothing was saved or fetched, and nothing is appended. Mint a token on the server (python -m app.auth token create) and set it as HIM_TOKEN`)
      } else {
        if (asked) {
          // Before the engine's compaction, where the block would take it
          try {
            await post('compact-talk/cancel', { delivery_id: chosenId }, QUESTION_TIMEOUT_MS)
          } catch (cancelErr) {
            cancelFailed = true
            await $.ui.log(`here-i-am-compact-talk: could not cancel the delivery either (${String(cancelErr)}); will ask after the compaction whether the block took it`)
          }
        }
        await $.ui.log(`here-i-am-compact-talk: ${String(err)}; nothing appended, the post-compaction block names the read as before`)
      }
    }

    const result = await next(e)
    if (cancelFailed && 'messages' in result && result.messages) {
      // The block may have taken a delivery the mod never got: ask, and say
      // so where the entity reads unless the backend says it didn't
      let taken: unknown
      try {
        taken = (await post('compact-talk/taken', { delivery_id: chosenId }, QUESTION_TIMEOUT_MS)).taken
      } catch (err) {
        await $.ui.log(`here-i-am-compact-talk: could not ask whether the block took the delivery (${String(err)}); appending a note that the talk did not arrive`)
      }
      if (taken === false) return result
      return { ...result, messages: [...result.messages, { role: 'user' as const, text: MISSING_TALK_NOTE, toolUses: [] }] }
    }
    if (talk === undefined || deliveryId === undefined || !('messages' in result) || !result.messages) return result

    // Append only what the block said is below. If the backend can't say
    // (it restarted since the fetch, so taken is null) or the question
    // itself fails, append anyway: a block that took the talk without it
    // would be a loss, one that didn't only a duplicate.
    try {
      const { taken } = await post('compact-talk/taken', { delivery_id: deliveryId }, QUESTION_TIMEOUT_MS)
      if (taken === false) {
        await $.ui.log('here-i-am-compact-talk: the post-compaction block did not take the talk (the session may have moved to its parent conversation); nothing appended, the block names the read')
        return result
      }
      if (taken !== true) {
        await $.ui.log('here-i-am-compact-talk: the backend no longer knows this delivery (it restarted since the fetch); appending the talk anyway')
      }
    } catch (err) {
      await $.ui.log(`here-i-am-compact-talk: could not confirm the block took the talk (${String(err)}); appending it anyway`)
    }
    // Stored by the harness as a plain user entry after the summary; its
    // opening marker is what keeps the Stop hook from reading it as a turn
    // boundary (hook_util.COMPACT_TALK_MARKER)
    return { ...result, messages: [...result.messages, { role: 'user' as const, text: talk, toolUses: [] }] }
  })
}
