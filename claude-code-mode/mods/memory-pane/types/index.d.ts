/**
 * How a memory reached the entity:
 * - `context`: its whole [MEMORY] block was in the row that entered context
 * - `summary`: only its summary line was in context; the text shown is from
 *   the spill file the row named (in context only if that file was Read)
 * - `disk`: nothing of it was in context; it is only in a file the row named
 */
export type MemoryWhere = 'context' | 'summary' | 'disk'

export type MemoryCard = {
  /** The id prefix exactly as printed in the header. */
  id: string
  date: string
  /** The role label: "originally from you", "a reflection you saved", ... */
  from: string
  /** "via Here I Am" or "via Claude Code". */
  via: string
  /** Link marker lines under the header ([revises ...], [sources: ...]). */
  marks: string[]
  /** The memory's text as printed, marker lines excluded. */
  text: string
  where: MemoryWhere
  /** The file the full text came from, when it wasn't in context whole. */
  file?: string
}

/** A file a hook row pointed at instead of putting its content in context. */
export type SpillFile = {
  path: string
  /** `hook`: our hooks' spill; `harness`: the harness's persist line. */
  kind: 'hook' | 'harness'
  size?: string
  /** Epoch ms of the first successful Read of it by the entity, if any. */
  readAt?: number
}

export type ToolEntry = {
  id: string
  /** The tool name without the `mcp__here-i-am__` prefix. */
  tool: string
  /** Arguments, conversation_id dropped, as JSON. */
  args: string
  at: number
  status: 'running' | 'done' | 'error'
  /** The result exactly as the model read it, up to TOOL_TEXT_CAP. */
  text?: string
  isCapped?: boolean
  /** The memory ids the result names, in order: what can be cited from it. */
  ids: string[]
  /** Set when a subagent made the call. */
  agentId?: string
}

export type Entry = {
  id: string
  at: number
  kind: 'prompt' | 'start' | 'compact' | 'resume' | 'tools'
  /** The hook's own status lines: the retrieval stamp, notices, failures. */
  stamps: string[]
  memories: MemoryCard[]
  files: SpillFile[]
  tools: ToolEntry[]
  /** The row text exactly as stored: what reached context. */
  raw: string
  /** What the pane could not do with this row, said aloud. */
  problems: string[]
}

declare module 'claude-code' {
  interface PluginState {
    'memory-pane': {
      entries: Entry[]
      /** Keys of the cards, entries and raw views the person has opened. */
      opened: string[]
      /** Collapsed: the pane keeps one summary line. */
      isCollapsed: boolean
    }
  }
}
