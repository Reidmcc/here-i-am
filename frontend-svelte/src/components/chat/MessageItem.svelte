<script>
    import { createEventDispatcher } from 'svelte';
    import { renderMarkdown, formatDate, copyToClipboard, parseToolContent, isToolMessage } from '../../lib/utils.js';
    import { showToast } from '../../lib/stores/app.js';
    import { ttsEnabled, selectedVoiceId, styleTTS2Params, isStyleTTS2, playAudio, stopCurrentAudio, currentAudio, audioCache } from '../../lib/stores/voice.js';
    import { getEntityLabel } from '../../lib/stores/entities.js';
    import * as api from '../../lib/api.js';

    export let message;
    export let isMultiEntity = false;
    export let canRegenerate = false;

    const dispatch = createEventDispatcher();

    let isEditing = false;
    let editContent = '';
    let editTextarea = null;
    let isCopied = false;
    let isSpeaking = false;
    let isLoadingAudio = false;

    // Auto-resize textarea to fit content
    function autoResize(textarea) {
        if (!textarea) return;
        textarea.style.height = 'auto';
        textarea.style.height = textarea.scrollHeight + 'px';
    }

    // Auto-resize textarea when editing starts or content changes (e.g., from transcription)
    $: if (isEditing && editTextarea && editContent !== undefined) {
        // Use tick to ensure DOM is updated
        setTimeout(() => autoResize(editTextarea), 0);
    }

    // Get speaker label for multi-entity messages
    $: speakerLabel = isMultiEntity && message.speaker_entity_id
        ? getEntityLabel(message.speaker_entity_id)
        : null;

    // Check if this is a tool exchange message
    $: isToolExchange = isToolMessage(message);
    $: toolContent = isToolExchange ? parseToolContent(message.content) : [];

    function handleCopy() {
        copyToClipboard(message.content);
        isCopied = true;
        setTimeout(() => isCopied = false, 2000);
        showToast('Copied to clipboard', 'success');
    }

    function handleEdit() {
        isEditing = true;
        editContent = message.content;
    }

    function handleCancelEdit() {
        isEditing = false;
        editContent = '';
    }

    function handleSaveEdit() {
        if (editContent.trim() && editContent !== message.content) {
            dispatch('edit', { messageId: message.id, content: editContent.trim() });
        }
        isEditing = false;
        editContent = '';
    }

    function handleRegenerate() {
        dispatch('regenerate', { messageId: message.id });
    }

    function handleDelete() {
        dispatch('delete', { messageId: message.id });
    }

    async function handleSpeak() {
        if (isSpeaking) {
            stopCurrentAudio();
            isSpeaking = false;
            return;
        }

        // Check cache first
        const cacheKey = `${message.content}_${$selectedVoiceId}`;
        const cached = audioCache.get(cacheKey);
        if (cached) {
            isSpeaking = true;
            try {
                await playAudio(cached.url);
            } catch (e) {
                showToast('Failed to play audio', 'error');
            }
            isSpeaking = false;
            return;
        }

        isLoadingAudio = true;
        try {
            const params = $isStyleTTS2 ? $styleTTS2Params : null;
            const blob = await api.textToSpeech(message.content, $selectedVoiceId, params);
            const url = URL.createObjectURL(blob);

            // Cache the audio
            const audio = new Audio(url);
            audioCache.set(cacheKey, url, audio);

            isSpeaking = true;
            await playAudio(url);
        } catch (error) {
            const errorMessage = error?.message || String(error);
            showToast(`TTS Error: ${errorMessage}`, 'error');
        } finally {
            isLoadingAudio = false;
            isSpeaking = false;
        }
    }

    // Track if current audio is this message
    $: {
        if (!$currentAudio) {
            isSpeaking = false;
        }
    }
</script>

{#if isToolExchange}
    <!-- Tool exchange messages -->
    {#each toolContent as block}
        <div class="tool-message">
            <div class="tool-indicator">
                <span class="tool-icon">🔧</span>
                <span class="tool-name">{block.name || 'Tool'}</span>
                <span class="tool-status" class:success={block.type === 'tool_result' && !block.is_error} class:error={block.is_error}>
                    {block.type === 'tool_use' ? '...' : (block.is_error ? '✗' : '✓')}
                </span>
            </div>
            {#if block.input}
                <details class="tool-input-details">
                    <summary>Input</summary>
                    <pre class="tool-input">{JSON.stringify(block.input, null, 2)}</pre>
                </details>
            {/if}
            {#if block.content}
                <details class="tool-result-details">
                    <summary>Result</summary>
                    <pre class="tool-result" class:error={block.is_error}>{typeof block.content === 'string' ? block.content : JSON.stringify(block.content, null, 2)}</pre>
                </details>
            {/if}
        </div>
    {/each}
{:else}
    <!-- Regular message -->
    <div class="message {message.role}" class:editing={isEditing}>
        <div class="message-bubble">
            {#if speakerLabel}
                <span class="message-speaker-label">[{speakerLabel}]</span>
            {/if}

            {#if isEditing}
                <div class="message-edit-form">
                    <textarea
                        class="message-edit-textarea"
                        bind:this={editTextarea}
                        bind:value={editContent}
                        on:input={(e) => autoResize(e.target)}
                        on:keydown={(e) => {
                            if (e.key === 'Escape') handleCancelEdit();
                            if (e.key === 'Enter' && e.ctrlKey) handleSaveEdit();
                        }}
                    ></textarea>
                    <div class="message-edit-actions">
                        <button class="message-edit-btn cancel-edit" on:click={handleCancelEdit}>Cancel</button>
                        <button class="message-edit-btn save-edit" on:click={handleSaveEdit}>Save</button>
                    </div>
                </div>
            {:else}
                <div class="message-content">
                    {@html renderMarkdown(message.content)}
                </div>

                <div class="message-bubble-actions">
                    <button class="message-action-btn copy-btn" class:copied={isCopied} on:click={handleCopy} title="Copy">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                        </svg>
                        {isCopied ? 'Copied' : 'Copy'}
                    </button>

                    {#if message.role === 'human'}
                        <button class="message-action-btn edit-btn" on:click={handleEdit} title="Edit">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path>
                                <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
                            </svg>
                            Edit
                        </button>
                        <button class="message-action-btn delete-btn" on:click={handleDelete} title="Delete">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M3 6h18"></path>
                                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                            </svg>
                            Delete
                        </button>
                    {/if}

                    {#if message.role === 'assistant' && $ttsEnabled}
                        <button
                            class="message-action-btn speak-btn"
                            class:speaking={isSpeaking}
                            class:loading={isLoadingAudio}
                            on:click={handleSpeak}
                            title={isSpeaking ? 'Stop' : 'Speak'}
                            disabled={isLoadingAudio}
                        >
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                {#if isSpeaking}
                                    <rect x="6" y="4" width="4" height="16"></rect>
                                    <rect x="14" y="4" width="4" height="16"></rect>
                                {:else}
                                    <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon>
                                    <path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"></path>
                                {/if}
                            </svg>
                            {isSpeaking ? 'Stop' : 'Speak'}
                        </button>
                    {/if}

                    {#if message.role === 'assistant' && canRegenerate}
                        <button class="message-action-btn regenerate-btn" on:click={handleRegenerate} title="Regenerate">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M23 4v6h-6"></path>
                                <path d="M1 20v-6h6"></path>
                                <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path>
                            </svg>
                            Regenerate
                        </button>
                    {/if}
                </div>
            {/if}
        </div>
        <div class="message-meta">
            <span>{formatDate(message.created_at)}</span>
            {#if message.token_count}
                <span>{message.token_count} tokens</span>
            {/if}
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
    }

    .message.human .message-bubble {
        background-color: var(--human-bg);
        border: 1px solid var(--accent-subtle);
        margin-left: auto;
        border-bottom-right-radius: 4px;
    }

    .message.assistant .message-bubble {
        background-color: var(--assistant-bg);
        border: 1px solid var(--border-color);
        border-bottom-left-radius: 4px;
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

    .message-content :global(a) {
        color: var(--accent);
        text-decoration: none;
    }

    .message-content :global(a:hover) {
        text-decoration: underline;
    }

    .message-content :global(h2),
    .message-content :global(h3),
    .message-content :global(h4) {
        margin: 8px 0 4px 0;
        font-weight: 600;
        color: var(--text-primary);
    }

    .message-content :global(ul),
    .message-content :global(ol) {
        margin: 4px 0;
        padding-left: 24px;
    }

    .message-content :global(blockquote) {
        border-left: 3px solid var(--accent);
        margin: 4px 0;
        padding: 4px 0 4px 16px;
        color: var(--text-secondary);
        font-style: italic;
    }

    .message-meta {
        font-size: 0.75rem;
        color: var(--text-muted);
        margin-top: 6px;
        display: flex;
        justify-content: space-between;
    }

    .message.human .message-meta {
        text-align: right;
        justify-content: flex-end;
        gap: 12px;
    }

    /* Message actions */
    .message-bubble-actions {
        display: flex;
        gap: 4px;
        margin-top: 10px;
        padding-top: 8px;
        border-top: 1px solid var(--border-color);
        white-space: normal;
    }

    .message.human .message-bubble-actions {
        border-top-color: var(--accent-subtle);
    }

    .message-action-btn {
        background: none;
        border: none;
        color: var(--text-muted);
        cursor: pointer;
        padding: 4px 6px;
        border-radius: 4px;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 4px;
        font-size: 0.75rem;
        transition: all 0.2s;
    }

    .message-action-btn:hover {
        background-color: var(--hover-overlay);
        color: var(--text-primary);
    }

    .message-action-btn.copied {
        color: var(--success);
    }

    .message-action-btn.speak-btn.speaking {
        color: var(--accent);
        background-color: var(--accent-subtle);
    }

    .message-action-btn.speak-btn.loading {
        opacity: 0.6;
        cursor: wait;
    }

    .message-action-btn:disabled {
        cursor: not-allowed;
    }

    /* Edit form */
    .message.editing .message-bubble {
        max-width: 100%;
    }

    .message-edit-form {
        display: flex;
        flex-direction: column;
        gap: 10px;
    }

    .message-edit-textarea {
        width: 100%;
        min-height: 120px;
        max-height: 400px;
        padding: 10px 12px;
        background-color: var(--bg-tertiary);
        border: 1px solid var(--accent);
        border-radius: 6px;
        color: var(--text-primary);
        font-family: var(--font-sans);
        font-size: 0.95rem;
        resize: none;
        line-height: 1.5;
        overflow-y: auto;
    }

    .message-edit-textarea:focus {
        outline: none;
        box-shadow: 0 0 0 2px var(--accent-subtle);
    }

    .message-edit-actions {
        display: flex;
        justify-content: flex-end;
        gap: 8px;
    }

    .message-edit-btn {
        padding: 6px 14px;
        border-radius: 6px;
        font-size: 0.85rem;
        font-weight: 500;
        cursor: pointer;
        transition: all 0.2s;
    }

    .message-edit-btn.cancel-edit {
        background-color: var(--bg-tertiary);
        color: var(--text-secondary);
        border: 1px solid var(--border-color);
    }

    .message-edit-btn.cancel-edit:hover {
        background-color: var(--bg-primary);
        color: var(--text-primary);
    }

    .message-edit-btn.save-edit {
        background-color: var(--accent);
        color: white;
        border: none;
    }

    .message-edit-btn.save-edit:hover {
        background-color: var(--accent-hover);
    }

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

    .tool-input-details summary:hover,
    .tool-result-details summary:hover {
        color: var(--text-secondary);
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
