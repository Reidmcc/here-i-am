// Arrive whole (issue #384): what the mod does to one of our hooks' rows,
// as pure functions over text. The hooks module does the I/O and hands a
// reader in, so the tests can hand in a fake one.
//
// Measured on Claude Code 2.1.288 (headless probes, issue #384):
// - The harness's hook-stdout line (10,000 characters) is applied before
//   the row exists: the row a `session.append` hook sees on door
//   `hook-context` already holds the harness's `<persisted-output>` preview
//   when the output was over the line. Nothing applies the line after, so
//   text put in the row there reaches the request whole (30k and 60k
//   blocks, all sentinels present at `turn.step`).
// - The rewrite is stored in the transcript's `rendered` field beside the
//   hook's original payload, and `--resume` loads it, so a forked or
//   restarted room keeps what arrived whole.
// - The harness frames each hook's output as
//   `<system-reminder>\n<Event>[:source] hook success: …\n</system-reminder>`
//   (plain stdout) or `… hook additional context: …` (JSON). The framing is
//   inside the rewritable text, and the engine doesn't put it back.

// Our rows open with this once unwrapped: every line our hooks print starts
// with a `[HERE I AM …]` header, failure notices included
export const OURS = '[HERE I AM'

// Which classic hook events print our blocks
export const EVENTS: ReadonlySet<string> = new Set(['SessionStart', 'UserPromptSubmit'])

const WRAPPER = /^<system-reminder>\n[A-Za-z]+(?::[a-z]+)? hook (?:success|additional context): ?([\s\S]*?)\n<\/system-reminder>\s*$/
const PERSISTED = /^<persisted-output>\nOutput too large \([^)]*\)\. Full output saved to: ([^\n]+)\n[\s\S]*<\/persisted-output>\s*$/
const MARKER = /^\[HERE I AM WHOLE\] (.+) sha256=([0-9a-f]{64})$/m

// Python's text-mode stdout on Windows writes \r\n, and the harness keeps
// the carriage returns; they are transport, not content
export function lf(text: string): string {
  return text.replace(/\r\n/g, '\n')
}

// The hook's own output inside the harness's framing, or null when the row
// isn't framed the way we measured
export function unwrap(row: string): string | null {
  return WRAPPER.exec(lf(row))?.[1] ?? null
}

// The file the harness persisted an over-the-line output to, or null
export function persistedPath(body: string): string | null {
  return PERSISTED.exec(body)?.[1]?.trim() ?? null
}

// The hook's marker for its unbudgeted output (hook_util.whole_marker)
export function wholeMarker(body: string): { path: string; sha256: string } | null {
  const [, path, sha256] = MARKER.exec(body) ?? []
  return path && sha256 ? { path, sha256 } : null
}

export function failureNote(reason: string): string {
  return `[HERE I AM] The arrive-whole mod could not put the whole block in place: ${reason}. The pointers above stand.`
}

export async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('')
}

/**
 * The text one of our rows should carry, or null to leave the row as made.
 *
 * Unwrapped always (the harness's framing says the system is reminding;
 * what arrives is the archive, and our own headers say so). Whole when the
 * hook filed its unbudgeted output and the file is the one it hashed.
 * Every failure keeps what the hooks printed (the pointers, and the
 * marker line saying the mod didn't act) and says why, after it.
 */
export async function arrive(row: string, read: (path: string) => Promise<string>): Promise<string | null> {
  let body = unwrap(row)
  if (body === null) return null
  const persisted = persistedPath(body)
  if (persisted !== null) {
    // Over the harness's line anyway (an identity block too large for the
    // budget, or a budget set past the line): the harness filed it whole
    try {
      body = lf(await read(persisted)).trimEnd()
    } catch {
      return null
    }
  }
  if (!body.startsWith(OURS)) return null
  const marker = wholeMarker(body)
  if (marker === null) return body
  let whole: string
  try {
    whole = await read(marker.path)
  } catch (err) {
    return `${body}\n\n${failureNote(`reading ${marker.path} failed (${String(err)})`)}`
  }
  if (await sha256Hex(whole) !== marker.sha256) {
    return `${body}\n\n${failureNote(`${marker.path} is not the file the hook wrote (its hash differs)`)}`
  }
  return whole
}
