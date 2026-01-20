<script>
    import { renderMarkdown } from '../../lib/utils.js';
    import { getEntityLabel } from '../../lib/stores/entities.js';

    export let content = '';
    export let tools = [];
    export let metadata = null;

    $: speakerLabel = metadata?.speakerLabel || (metadata?.speaker_entity_id ? getEntityLabel(metadata.speaker_entity_id) : null);
</script>

{#each tools as tool}
    <div class="tool-message">
        <div class="tool-indicator">
            <span class="tool-icon">🔧</span>
            <span class="tool-name">{tool.name}</span>
            <span class="tool-status" class:loading={tool.status === 'loading'} class:success={tool.status === 'success'} class:error={tool.status === 'error'}>
                {#if tool.status === 'loading'}
                    ...
                {:else if tool.status === 'success'}
                    ✓
                {:else if tool.status === 'error'}
                    ✗
                {/if}
            </span>
        </div>
        {#if tool.input}
            <details class="tool-input-details">
                <summary>Input</summary>
                <pre class="tool-input">{JSON.stringify(tool.input, null, 2)}</pre>
            </details>
        {/if}
        {#if tool.result}
            <details class="tool-result-details" open>
                <summary>Result</summary>
                <pre class="tool-result" class:error={tool.result.error}>{typeof tool.result.content === 'string' ? tool.result.content : JSON.stringify(tool.result.content, null, 2)}</pre>
            </details>
        {/if}
    </div>
{/each}

{#if content}
    <div class="message assistant">
        <div class="message-bubble streaming">
            {#if speakerLabel}
                <span class="message-speaker-label">[{speakerLabel}]</span>
            {/if}
            <div class="message-content">
                {@html renderMarkdown(content)}<span class="streaming-cursor">|</span>
            </div>
        </div>
    </div>
{:else if tools.length === 0}
    <!-- Typing indicator when no content yet -->
    <div class="message assistant">
        <div class="typing-indicator">
            <span class="typing-dot"></span>
            <span class="typing-dot"></span>
            <span class="typing-dot"></span>
        </div>
    </div>
{/if}

<style>
    .message {
        margin-bottom: 16px;
        animation: fadeIn 0.3s ease;
    }

    .message-bubble {
        padding: 14px 18px;
        border-radius: 12px;
        max-width: 85%;
        line-height: 1.4;
        white-space: pre-wrap;
        word-wrap: break-word;
        background-color: var(--assistant-bg);
        border: 1px solid var(--border-color);
        border-bottom-left-radius: 4px;
    }

    .message-bubble.streaming {
        position: relative;
    }

    .message-speaker-label {
        font-size: 11px;
        color: var(--accent);
        font-weight: 500;
        margin-bottom: 4px;
        display: block;
    }

    .message-content {
        word-break: break-word;
    }

    .message-content :global(code) {
        background-color: var(--bg-tertiary);
        border: 1px solid var(--border-color);
        border-radius: 4px;
        padding: 2px 6px;
        font-family: var(--font-mono);
        font-size: 0.85em;
    }

    .message-content :global(pre) {
        background-color: var(--bg-tertiary);
        border: 1px solid var(--border-color);
        border-radius: 6px;
        padding: 12px 16px;
        margin: 4px 0;
        overflow-x: auto;
        white-space: pre;
    }

    .message-content :global(pre code) {
        background: none;
        border: none;
        padding: 0;
    }

    .streaming-cursor {
        display: inline;
        color: var(--accent);
        animation: blink 0.8s infinite;
        font-weight: normal;
        margin-left: 1px;
    }

    .typing-indicator {
        display: flex;
        gap: 4px;
        padding: 14px 18px;
        background-color: var(--assistant-bg);
        border-radius: 12px;
        border-bottom-left-radius: 4px;
        width: fit-content;
    }

    .typing-dot {
        width: 8px;
        height: 8px;
        background-color: var(--text-muted);
        border-radius: 50%;
        animation: bounce 1.4s infinite ease-in-out;
    }

    .typing-dot:nth-child(1) { animation-delay: 0s; }
    .typing-dot:nth-child(2) { animation-delay: 0.2s; }
    .typing-dot:nth-child(3) { animation-delay: 0.4s; }

    /* Tool messages */
    .tool-message {
        margin: 8px auto;
        max-width: 800px;
        padding: 8px 12px;
        background-color: var(--bg-tertiary);
        border-left: 3px solid var(--accent);
        border-radius: 4px;
        font-size: 0.85rem;
        color: var(--text-secondary);
    }

    .tool-indicator {
        display: flex;
        align-items: center;
        gap: 8px;
    }

    .tool-icon {
        font-size: 1rem;
        opacity: 0.8;
    }

    .tool-name {
        font-weight: 500;
        color: var(--text-primary);
        text-transform: capitalize;
    }

    .tool-status {
        margin-left: auto;
        font-size: 0.9rem;
    }

    .tool-status.loading {
        color: var(--accent);
        animation: pulse 1.5s ease-in-out infinite;
    }

    .tool-status.success {
        color: var(--success);
    }

    .tool-status.error {
        color: var(--danger);
    }

    .tool-input-details,
    .tool-result-details {
        margin-top: 8px;
    }

    .tool-input-details summary,
    .tool-result-details summary {
        cursor: pointer;
        color: var(--text-muted);
        font-size: 0.8rem;
        user-select: none;
    }

    .tool-input,
    .tool-result {
        margin-top: 6px;
        padding: 8px;
        background-color: var(--bg-secondary);
        border-radius: 4px;
        font-family: var(--font-mono);
        font-size: 0.75rem;
        line-height: 1.4;
        overflow-x: auto;
        max-height: 300px;
        overflow-y: auto;
        white-space: pre-wrap;
        word-break: break-word;
    }

    .tool-result.error {
        border-left: 2px solid var(--danger);
        color: var(--danger);
    }
</style>
