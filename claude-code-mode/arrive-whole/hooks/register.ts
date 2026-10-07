// Arrive whole (issue #384): puts what the Here I Am hooks filed in place
// of their pointers, and drops the harness's framing from their rows,
// before each row is stored. See whole.ts for what was measured.
import type { Register } from 'claude-code'
import { EVENTS, arrive } from './whole'

async function readFile($: any, path: string): Promise<string> {
  return $.fs.read(path)
}

export const register: Register = (on) => {
  on('session.append', { door: 'hook-context' }, async ($, e, next) => {
    if (e.origin.kind !== 'hook' || !EVENTS.has(e.origin.event)) return next(e)
    const [block, ...rest] = e.message.content
    if (block?.type !== 'text' || rest.length) return next(e)
    const text = await arrive(String(block.text), path => readFile($, path))
    if (text === null) return next(e)
    return next({ ...e, message: { ...e.message, content: [{ type: 'text', text }] } })
  })
}
