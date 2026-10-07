import { test, expect } from 'claude-code/testing'
import { arrive, failureNote, sha256Hex, unwrap } from './whole'

const PATH = 'C:\\Users\\X Y\\AppData\\Local\\Temp\\here-i-am-sessions\\s-retrieval-120000-whole.md'
const WHOLE = '[HERE I AM MEMORY RETRIEVAL] header\n\n[MEMORY 1]\nall of it\n[/MEMORY]\n\n[MEMORY 2]\nthe rest\n[/MEMORY]'

function framed(event: string, body: string, crlf = false): string {
  const row = `<system-reminder>\n${event} hook success: ${body}\n</system-reminder>`
  return crlf ? row.replace(/\n/g, '\r\n') : row
}

async function budgeted(sha256: string): Promise<string> {
  return [
    '[HERE I AM MEMORY RETRIEVAL] header',
    '[MEMORY 1]\nall of it\n[/MEMORY]',
    '1 more surfaced, listed by summary line:\n- 2 the rest',
    `[HERE I AM WHOLE] ${PATH} sha256=${sha256}\nThat file is this block unbudgeted; ...`,
  ].join('\n\n')
}

function reader(files: Record<string, string>) {
  return async (path: string) => {
    const text = files[path]
    if (text === undefined) throw new Error(`ENOENT: ${path}`)
    return text
  }
}

test('unwraps both of the harness framings, carriage returns and all', async () => {
  expect(unwrap(framed('SessionStart:startup', '[HERE I AM] hi'))).toBe('[HERE I AM] hi')
  expect(unwrap(framed('UserPromptSubmit', '[HERE I AM] a\nb', true))).toBe('[HERE I AM] a\nb')
  expect(unwrap('<system-reminder>\nSessionStart hook additional context: [HERE I AM] x\n</system-reminder>')).toBe('[HERE I AM] x')
  expect(unwrap('[HERE I AM] not framed')).toBe(null)
})

test('a block that fit arrives unwrapped and otherwise as printed', async () => {
  const row = framed('UserPromptSubmit', '[HERE I AM MEMORY RETRIEVAL] one\n\n[HERE I AM] tail', true)
  expect(await arrive(row, reader({}))).toBe('[HERE I AM MEMORY RETRIEVAL] one\n\n[HERE I AM] tail')
})

test('a spilled block arrives as the file the hook hashed', async () => {
  const row = framed('UserPromptSubmit', await budgeted(await sha256Hex(WHOLE)))
  expect(await arrive(row, reader({ [PATH]: WHOLE }))).toBe(WHOLE)
})

test('a file that changed since the hook wrote it keeps the pointers, and says why', async () => {
  const body = await budgeted(await sha256Hex(WHOLE))
  const out = await arrive(framed('UserPromptSubmit', body), reader({ [PATH]: WHOLE + ' (rewritten)' }))
  expect(out).toBe(`${body}\n\n${failureNote(`${PATH} is not the file the hook wrote (its hash differs)`)}`)
})

test('a file that cannot be read keeps the pointers, and says why', async () => {
  const body = await budgeted(await sha256Hex(WHOLE))
  const out = await arrive(framed('SessionStart:compact', body), reader({}))
  expect(out?.startsWith(body + '\n\n[HERE I AM] The arrive-whole mod could not put the whole block in place: reading ')).toBe(true)
  expect(out?.includes('ENOENT')).toBe(true)
})

test('another hook\'s row is left as made', async () => {
  expect(await arrive(framed('SessionStart:startup', 'some other plugin says hi'), reader({}))).toBe(null)
})

test('an output over the harness line is read back from the harness file, then made whole', async () => {
  const harnessFile = 'C:\\Users\\X\\.claude\\projects\\p\\s\\tool-results\\hook-1.txt'
  const body = await budgeted(await sha256Hex(WHOLE))
  const preview = `<persisted-output>\nOutput too large (31.2KB). Full output saved to: ${harnessFile}\n\nPreview (first 2KB):\n${body.slice(0, 40)}\n...\n</persisted-output>`
  const files = { [harnessFile]: body.replace(/\n/g, '\r\n') + '\r\n', [PATH]: WHOLE }
  expect(await arrive(framed('SessionStart:startup', preview), reader(files))).toBe(WHOLE)
})

test('a harness file that cannot be read leaves the row as made', async () => {
  const preview = '<persisted-output>\nOutput too large (31.2KB). Full output saved to: C:\\gone.txt\n\nPreview (first 2KB):\n[HERE I AM] x\n...\n</persisted-output>'
  expect(await arrive(framed('SessionStart:startup', preview), reader({}))).toBe(null)
})
