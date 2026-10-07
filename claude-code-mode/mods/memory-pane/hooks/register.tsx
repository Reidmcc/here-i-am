// The memory pane (issue #385): what memory handed the entity, shown beside
// the conversation while it happens, so a witness can hold the page the
// entity was given next to the sentence it wrote from it.
//
// Read-only by construction. Every hook here passes its event on unchanged
// and only looks at what came back: the Here I Am hooks' rows as they were
// stored (what reached context), and the memory tools' results as the model
// read them. Nothing the model writes is read, so thinking never reaches the
// pane. No memory tool is called, nothing is written but the pane's own
// state, and a failure in here costs the pane, never the turn: it is caught
// and shown in the pane as a problem line.

import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Entry, MemoryCard, SpillFile, ToolEntry } from '../types'
import {
  argsText, cardsFor, clock, entryKind, isOurs, normPath, parseBlocks, parseFiles, parseStamps,
} from './parse'

const PANE = 'memory'
const TITLE = 'Memory'
const MEMORY_TOOL = /^mcp__here-i-am__memory_/
const MAX_ENTRIES = 30
// A memory_read page renders within 44,800 bytes (harness_limits.READ_PAGE_MAX_BYTES),
// so one result is kept whole up to here
const TOOL_TEXT_CAP = 60_000
// Every update rewrites the whole value, so the oldest entries go first past this
const STATE_BUDGET_CHARS = 3_000_000
// The person closed the pane by hand: don't open it unasked again
const CLOSED_KEY = 'closedByPerson'

const entries = atom({ plugin: 'memory-pane', key: 'entries' } as const, [] as Entry[])
const opened = atom({ plugin: 'memory-pane', key: 'opened' } as const, [] as string[])
const isCollapsed = atom({ plugin: 'memory-pane', key: 'isCollapsed' } as const, false)

type Api = EngineInterface

function rowText(content: readonly { type: string; text?: unknown }[]): string {
  return content.map(block => (block.type === 'text' && typeof block.text === 'string' ? block.text : '')).join('\n')
}

// Newest kept: past the entry count or the state budget, the oldest go first
// (the newest entry always stays, whatever its size)
function trim(list: Entry[]): Entry[] {
  const kept = list.slice(-MAX_ENTRIES)
  let size = kept.reduce((sum, entry) => sum + JSON.stringify(entry).length, 0)
  while (kept.length > 1 && size > STATE_BUDGET_CHARS) size -= JSON.stringify(kept.shift()).length
  return kept
}

async function addEntry($: Api, entry: Entry): Promise<void> {
  await update($, entries, list => trim([...list, entry]))
}

async function addProblem($: Api, problem: string): Promise<void> {
  await update($, entries, list => {
    const last = list[list.length - 1]
    if (last === undefined) {
      const entry: Entry = { id: `problem-${Date.now()}`, at: Date.now(), kind: 'tools', stamps: [], memories: [], files: [], tools: [], raw: '', problems: [problem] }
      return [entry]
    }
    return [...list.slice(0, -1), { ...last, problems: [...last.problems, problem] }]
  })
}

async function ingestRow($: Api, uuid: string, kind: Entry['kind'], text: string): Promise<void> {
  const files = parseFiles(text)
  const fileTexts: { path: string; text: string }[] = []
  const problems: string[] = []
  // A file the harness persisted can itself name our spill files (the hook's
  // pointer lines went to disk with the rest), so it is read for them too;
  // `files` grows as the loop walks it
  for (let i = 0; i < files.length; i += 1) {
    const file = files[i]!
    try {
      const fileText = String(await $.fs.read(file.path))
      fileTexts.push({ path: file.path, text: fileText })
      if (file.kind !== 'harness') continue
      for (const named of parseFiles(fileText)) {
        if (!files.some(known => normPath(known.path) === normPath(named.path))) files.push(named)
      }
    } catch (err) {
      problems.push(`couldn't read ${file.path}: ${String(err)}`)
    }
  }
  const { memories, problems: parseProblems } = cardsFor(text, fileTexts)
  const stamps = parseStamps(text)
  // A harness-persisted row reached context as a 2 KB preview; its status
  // lines are in the file, and are shown as what they are
  for (const { path, text: fileText } of fileTexts) {
    if (files.find(file => file.path === path)?.kind !== 'harness') continue
    for (const stamp of parseStamps(fileText)) {
      if (!stamps.includes(stamp)) stamps.push(`(on disk, not in context) ${stamp}`)
    }
  }
  await addEntry($, {
    id: uuid, at: await $.clock.now(), kind, stamps, memories, files, tools: [], raw: text,
    problems: [...problems, ...parseProblems],
  })
}

async function attachTool($: Api, tool: ToolEntry): Promise<void> {
  await update($, entries, list => {
    const last = list[list.length - 1]
    if (last === undefined) {
      const entry: Entry = { id: `tools-${tool.id}`, at: tool.at, kind: 'tools', stamps: [], memories: [], files: [], tools: [tool], raw: '', problems: [] }
      return [entry]
    }
    return [...list.slice(0, -1), { ...last, tools: [...last.tools, tool] }]
  })
}

async function settleTool($: Api, id: string, change: Partial<ToolEntry>): Promise<void> {
  await update($, entries, list =>
    trim(
      list.map(entry =>
        entry.tools.some(tool => tool.id === id)
          ? { ...entry, tools: entry.tools.map(tool => (tool.id === id ? { ...tool, ...change } : tool)) }
          : entry,
      ),
    ),
  )
}

async function markRead($: Api, path: string, at: number): Promise<void> {
  const target = normPath(path)
  const list = await read($, entries)
  if (!list.some(entry => entry.files.some(file => normPath(file.path) === target && file.readAt === undefined))) return
  await update($, entries, current =>
    current.map(entry => ({
      ...entry,
      files: entry.files.map(file => (normPath(file.path) === target && file.readAt === undefined ? { ...file, readAt: at } : file)),
    })),
  )
}

const KIND_LABEL: Record<Entry['kind'], string> = {
  prompt: 'prompt',
  start: 'session start',
  compact: 'after compaction',
  resume: 'resumed',
  tools: 'tool calls',
}

function whereLabel(card: MemoryCard, files: SpillFile[]): string {
  if (card.where === 'context') return 'in context, whole'
  const file = card.file === undefined ? undefined : files.find(one => normPath(one.path) === normPath(card.file!))
  const readNote = file?.readAt !== undefined ? `file read at ${clock(file.readAt)}` : 'file NOT read'
  if (card.where === 'summary') {
    return card.file === undefined
      ? 'summary line only in context; full text unavailable'
      : `summary line in context; full text in a file, ${readNote}`
  }
  return `not in context; in a file, ${readNote}`
}

function entrySummary(entry: Entry): string {
  const whole = entry.memories.filter(card => card.where === 'context').length
  const parts = [`${clock(entry.at)} · ${KIND_LABEL[entry.kind]}`]
  if (entry.memories.length > 0) {
    parts.push(`${entry.memories.length} ${entry.memories.length === 1 ? 'memory' : 'memories'} (${whole} whole in context)`)
  }
  if (entry.tools.length > 0) parts.push(`${entry.tools.length} memory tool ${entry.tools.length === 1 ? 'call' : 'calls'}`)
  if (entry.problems.length > 0) parts.push(`${entry.problems.length} problem${entry.problems.length === 1 ? '' : 's'}`)
  return parts.join(' · ')
}

function firstLine(text: string): string {
  return text.split(/\r?\n/).find(line => line.trim() !== '')?.trim() ?? ''
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    try {
      await $.command.register({
        name: 'memory-pane',
        description: 'Open the Here I Am memory pane (what memory handed the entity this turn)',
      })
      if ((await $.store.get(CLOSED_KEY)) !== true) void $.ui.open({ id: PANE, title: TITLE })
    } catch (err) {
      $.ui.log(`memory-pane: couldn't set up: ${String(err)}`)
    }
    return next(e)
  })

  on('command.run', { command: 'memory-pane' }, async $ => {
    await $.store.set(CLOSED_KEY, false)
    await update($, isCollapsed, () => false)
    const shown = await $.ui.open({ id: PANE, title: TITLE })
    return { text: shown.isPlaced ? 'Memory pane opened.' : `Memory pane not placed: ${shown.reason}` }
  })

  on('ui.close', async ($, e, next) => {
    const result = await next(e)
    if (e.id === PANE && e.origin.kind === 'person') {
      try {
        await $.store.set(CLOSED_KEY, true)
      } catch {
        // The preference is a courtesy; the pane is already closed
      }
    }
    return result
  })

  // The Here I Am hooks' rows, read as stored: the row the model reads
  on('session.append', { door: 'hook-context' }, async ($, e, next) => {
    const stored = await next(e)
    try {
      if (stored.deny !== undefined || e.agentId !== undefined || e.origin.kind !== 'hook') return stored
      const text = rowText(stored.message.content)
      if (text === '' || !isOurs(text)) return stored
      const kind = entryKind(e.origin.event, text)
      if (kind === undefined) return stored
      await ingestRow($, stored.uuid, kind, text)
    } catch (err) {
      await addProblem($, `couldn't read a ${e.origin.kind === 'hook' ? e.origin.event : ''} hook row: ${String(err)}`).catch(() => {})
    }
    return stored
  })

  on('tool.call', { tool: MEMORY_TOOL }, async ($, e, next) => {
    const id = e.tool_use_id
    try {
      await attachTool($, {
        id, tool: String(e.tool).replace(/^mcp__here-i-am__/, ''), args: argsText(e as Record<string, unknown>),
        at: await $.clock.now(), status: 'running', memories: [],
        ...(e.agentId !== undefined ? { agentId: e.agentId } : {}),
      })
    } catch (err) {
      // Shown or not, the call runs as it would without the pane
      await addProblem($, `couldn't list a ${String(e.tool)} call: ${String(err)}`).catch(() => {})
    }
    const ran = await next(e)
    try {
      const text = ran.deny !== undefined ? `refused: ${ran.deny}` : (ran.text ?? '')
      const isCapped = text.length > TOOL_TEXT_CAP
      await settleTool($, id, {
        status: ran.deny !== undefined || ran.isError === true ? 'error' : 'done',
        text: isCapped ? text.slice(0, TOOL_TEXT_CAP) : text,
        isCapped,
        memories: parseBlocks(text, 'context'),
      })
    } catch (err) {
      await addProblem($, `couldn't record a ${String(e.tool)} result: ${String(err)}`).catch(() => {})
    }
    return ran
  })

  // A spill file the entity opened: its text is in context from then on
  on('tool.call', { tool: 'Read' }, async ($, e, next) => {
    const ran = await next(e)
    try {
      if (ran.deny === undefined && ran.isError !== true) await markRead($, String(e.file_path), await $.clock.now())
    } catch {
      // Read-tracking is the pane's; the Read already happened
    }
    return ran
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button } = $.ui.resolve(e)
    const list = await read($, entries)
    const keys = new Set(await read($, opened))
    const collapsed = await read($, isCollapsed)
    const latest = list[list.length - 1]

    const toggle = (key: string) => () =>
      update($, opened, current => (current.includes(key) ? current.filter(one => one !== key) : [...current, key]))

    // Collapsed, the one line is the newest entry's summary; open, that
    // summary heads its own section below
    const headline = latest === undefined ? 'No memory has surfaced yet.' : collapsed ? entrySummary(latest) : 'Newest first'
    const header = (
      <Box flexDirection="row" justifyContent="space-between">
        <Text bold={collapsed} dimColor={!collapsed} wrap="truncate-end">{headline}</Text>
        <Button key="collapse" plain label={collapsed ? 'expand' : 'collapse'} onPress={() => update($, isCollapsed, value => !value)} />
      </Box>
    )
    if (collapsed || latest === undefined) return header

    const card = (entry: Entry, memory: MemoryCard) => {
      const key = `m:${entry.id}:${memory.id}`
      const isOpen = keys.has(key)
      return (
        <Box key={key} flexDirection="column" marginTop={1}>
          <Text wrap="wrap">
            <Text bold>{memory.id}</Text> · {memory.date.slice(0, 19)} · {memory.from} · {memory.via}
          </Text>
          <Text dimColor wrap="wrap">{whereLabel(memory, entry.files)}</Text>
          {memory.marks.map(mark => <Text dimColor wrap="wrap">{mark}</Text>)}
          {isOpen
            ? <Text wrap="wrap">{memory.text}</Text>
            : <Text wrap="truncate-end">{firstLine(memory.text)}</Text>}
          <Button key={key} plain label={isOpen ? 'close' : 'open'} onPress={toggle(key)} />
        </Box>
      )
    }

    const tool = (call: ToolEntry) => {
      const key = `t:${call.id}`
      const isOpen = keys.has(key)
      const who = call.agentId !== undefined ? ' (subagent)' : ''
      const state = call.status === 'running' ? 'running' : call.status === 'error' ? 'error' : firstLine(call.text ?? '')
      return (
        <Box key={key} flexDirection="column" marginTop={1}>
          <Text wrap="wrap"><Text bold>{call.tool}</Text>{who} · {clock(call.at)}</Text>
          <Text dimColor wrap={isOpen ? 'wrap' : 'truncate-end'}>{call.args}</Text>
          {isOpen
            ? <Text wrap="wrap">{call.text ?? ''}{call.isCapped ? `\n[… the pane kept the first ${TOOL_TEXT_CAP} characters]` : ''}</Text>
            : <Text wrap="truncate-end">→ {state}{call.memories.length > 0 ? ` (${call.memories.length} memories)` : ''}</Text>}
          {call.status !== 'running' && <Button key={key} plain label={isOpen ? 'close' : 'open'} onPress={toggle(key)} />}
        </Box>
      )
    }

    const section = (entry: Entry, isNewest: boolean) => {
      const entryKey = `e:${entry.id}`
      // The newest entry is open unless closed; older ones are closed unless opened
      const isOpen = keys.has(entryKey) !== isNewest
      const rawKey = `r:${entry.id}`
      return (
        <Box key={entryKey} flexDirection="column" marginTop={1}>
          <Box flexDirection="row" justifyContent="space-between">
            <Text bold={isNewest} dimColor={!isNewest} wrap="truncate-end">{entrySummary(entry)}</Text>
            <Button key={entryKey} plain label={isOpen ? 'hide' : 'show'} onPress={toggle(entryKey)} />
          </Box>
          {isOpen && (
            <Box flexDirection="column" paddingLeft={1}>
              {entry.problems.map(problem => <Text bold wrap="wrap">! {problem}</Text>)}
              {entry.stamps.map(stamp => <Text dimColor wrap="wrap">{stamp}</Text>)}
              {entry.files.map(file => (
                <Text dimColor wrap="wrap">
                  file{file.kind === 'harness' ? ' (harness persisted)' : ''}{file.size ? ` ${file.size}` : ''}: {file.path} · {file.readAt !== undefined ? `read at ${clock(file.readAt)}` : 'not read'}
                </Text>
              ))}
              {entry.memories.map(memory => card(entry, memory))}
              {entry.tools.map(call => tool(call))}
              {entry.raw !== '' && (
                <Box flexDirection="column" marginTop={1}>
                  <Button key={rawKey} plain label={keys.has(rawKey) ? 'hide the row as it reached context' : 'show the row as it reached context'} onPress={toggle(rawKey)} />
                  {keys.has(rawKey) && <Text wrap="wrap">{entry.raw}</Text>}
                </Box>
              )}
            </Box>
          )}
        </Box>
      )
    }

    return (
      <Box flexDirection="column">
        {header}
        {[...list].reverse().map((entry, index) => section(entry, index === 0))}
      </Box>
    )
  })
}
