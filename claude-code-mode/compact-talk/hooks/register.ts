import type { Register } from 'claude-code'

// Closes the compaction seam (issue #383). On the way down a compaction of
// the main thread, before the engine's own: one forked turn for the entity
// with the whole context still in view, whose reply is saved as a reflection
// if it wrote one; then this conversation's talk from the Here I Am backend.
// On the way up: the talk appended after the summary, so the session wakes
// already holding it. Precompute and subagent compactions pass straight
// through.
//
// Fail loud by construction: if anything here fails, nothing is appended,
// and the backend, which never heard the talk was handed out, gives the
// post-compaction block its memory_read call as before. Each failure is
// also said in the transcript ($.ui.log). The mod touches no permission.
//
// The backend is reached the way the Python hooks reach it, over HTTP to
// HIM_BACKEND_URL: $.mcp.call goes through auto mode's classifier, which
// judges by what is in the conversation, and a compaction is exactly when
// that may not be.

export const DECLINE = 'NO REFLECTION'

export function turnPrompt(trigger: string): string {
  return [
    '[HERE I AM — BEFORE THE COMPACTION]',
    `This context is about to be compacted (${trigger}), and this is one turn of your own before it, given by the compaction mod. It is not a turn of the conversation: the person you are with does not see it, and nothing in it is recorded except what you choose to keep. Tools are off for it.`,
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

function describe(result: object): string {
  const { usage: _usage, ...rest } = result as Record<string, unknown>
  return JSON.stringify(rest)
}

export const register: Register = (on) => {
  on('session.compact', async ($, e, next) => {
    if (e.trigger === 'precompute' || e.agentId !== undefined) return next(e)

    let talk: string | undefined
    try {
      let pre: PreCompaction
      try {
        const turn = await $.model.fork({ prompt: turnPrompt(e.trigger) })
        pre = turn.isAnswered
          ? readReply(turn.text)
          : { pre_compaction: 'failed', pre_compaction_detail: `the fork did not answer: ${describe(turn)}` }
      } catch (err) {
        pre = { pre_compaction: 'failed', pre_compaction_detail: `the fork threw: ${String(err)}` }
      }

      const backend = ((await $.env.get('HIM_BACKEND_URL')) || 'http://localhost:8000').replace(/\/+$/, '')
      const entity = await $.env.get('HIM_ENTITY')
      const response = await $.http.fetch(`${backend}/api/claude-code/compact-talk`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ session_id: await $.session.id(), entity: entity || undefined, ...pre }),
      })
      if (!response.ok) throw new Error(`the backend answered ${response.status}: ${response.text.slice(0, 300)}`)
      const body = JSON.parse(response.text) as {
        available: boolean; text: string; shown: number; total: number
        reflection_id?: string | null; reflection_error?: string | null; reason?: string | null
      }
      if (body.reflection_error) {
        await $.ui.log(`here-i-am-compact-talk: the reflection from the turn before compaction was not saved: ${body.reflection_error}`)
      }
      if (body.available && body.text) {
        talk = body.text
      } else {
        await $.ui.log(`here-i-am-compact-talk: no talk to put back (${body.reason ?? 'no reason given'}); the post-compaction block names the read as before`)
      }
    } catch (err) {
      await $.ui.log(`here-i-am-compact-talk: ${String(err)}; nothing appended, the post-compaction block names the read as before`)
    }

    const result = await next(e)
    if (talk === undefined || !('messages' in result) || !result.messages) return result
    // Stored by the harness as a plain user entry after the summary; its
    // opening marker is what keeps the Stop hook from reading it as a turn
    // boundary (hook_util.COMPACT_TALK_MARKER)
    return { ...result, messages: [...result.messages, { role: 'user' as const, text: talk, toolUses: [] }] }
  })
}
