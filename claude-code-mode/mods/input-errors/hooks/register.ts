// Input errors (issue #392): when a Here I Am tool call's input is not
// JSON, the entity is told where it broke and why, in place of Claude
// Code's first-200-bytes error. See diagnose.ts for what was measured.
import type { Register } from 'claude-code'
import { explain, unparsed } from './diagnose'

const SERVER = 'mcp__here-i-am__'

export const register: Register = on => {
  on('tool.call', async ($, e, next) => {
    if (!e.tool.startsWith(SERVER)) return next(e)
    const input = unparsed(e as Record<string, unknown>)
    if (input === null) return next(e)
    // Core still decides: a harness that learns to repair the input runs
    // the call, and its result stands. Only its refusal is replaced.
    const ran = await next(e)
    if (ran.isError !== true) return ran
    return { deny: explain(e.tool, input.raw, input.len) }
  })
}
