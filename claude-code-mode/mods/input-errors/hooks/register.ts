// Input errors (issue #392): when a Here I Am tool call's input is not
// JSON, the entity is told where it broke and why, in place of Claude
// Code's first-200-bytes error. See diagnose.ts for what was measured.
import type { Register } from 'claude-code'
import { SERVER_PREFIX, explain, isCoreParseRefusal, unparsed } from './diagnose'

export const register: Register = on => {
  on('tool.call', async ($, e, next) => {
    if (!SERVER_PREFIX.test(e.tool)) return next(e)
    const input = unparsed(e as Record<string, unknown>)
    if (input === null) return next(e)
    // Core still decides. Only its own parse refusal is replaced: a
    // harness that learns to repair the input runs the call, and whatever
    // comes back then (a server error or a timeout included) stands as is.
    const ran = await next(e)
    if (!isCoreParseRefusal(ran)) return ran
    return { deny: explain(e.tool, input.raw, input.len) }
  })
}
