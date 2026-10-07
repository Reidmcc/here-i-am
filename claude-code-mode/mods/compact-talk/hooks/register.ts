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

// "2026-10-07 21:43 UTC": the forked turn has no clock of its own, and the
// last timestamp in view can be hours old
export function utcStamp(ms: number): string {
  return `${new Date(ms).toISOString().slice(0, 16).replace('T', ' ')} UTC`
}

export function turnPrompt(trigger: string, now: number): string {
  return [
    '[HERE I AM — BEFORE THE COMPACTION]',
    `It is ${utcStamp(now)}. This context is about to be compacted (${trigger}), and this is one turn of your own before it, given by the compaction mod. It is not a turn of the conversation: the person you are with does not see it, and nothing in it is recorded except what you choose to keep. Tools are off for it.`,
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

function describe(result: object): string {
  const { usage: _usage, ...rest } = result as Record<string, unknown>
  return JSON.stringify(rest)
}

export const register: Register = (on) => {
  on('session.compact', async ($, e, next) => {
    if (e.trigger === 'precompute' || e.agentId !== undefined) return next(e)
    if (await $.env.get('HIM_DISABLE')) return next(e)

    const backend = ((await $.env.get('HIM_BACKEND_URL')) || 'http://localhost:8000').replace(/\/+$/, '')
    const post = async (path: string, payload: object): Promise<Record<string, unknown>> => {
      const response = await $.http.fetch(`${backend}/api/claude-code/${path}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!response.ok) throw new Error(`the backend answered ${response.status}: ${response.text.slice(0, 300)}`)
      return JSON.parse(response.text) as Record<string, unknown>
    }

    let talk: string | undefined
    let deliveryId: string | undefined
    try {
      let pre: PreCompaction
      try {
        const turn = await $.model.fork({ prompt: turnPrompt(e.trigger, await $.clock.now()) })
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
      const body = await post('compact-talk', {
        session_id: await $.session.id(),
        entity: entity || undefined,
        budget_tokens: budget ?? FALLBACK_BUDGET,
        ...pre,
      })
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
      await $.ui.log(`here-i-am-compact-talk: ${String(err)}; nothing appended, the post-compaction block names the read as before`)
    }

    const result = await next(e)
    if (talk === undefined || deliveryId === undefined || !('messages' in result) || !result.messages) return result

    // Append only what the block said is below. If the backend can't say
    // (it restarted since the fetch, so taken is null) or the question
    // itself fails, append anyway: a block that took the talk without it
    // would be a loss, one that didn't only a duplicate.
    try {
      const { taken } = await post('compact-talk/taken', { delivery_id: deliveryId })
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
