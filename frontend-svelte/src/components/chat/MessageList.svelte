<script>
    import { createEventDispatcher } from 'svelte';
    import MessageItem from './MessageItem.svelte';
    import StreamingMessage from './StreamingMessage.svelte';

    import { messages, streamingMessage, streamingContent, streamingTools, isStreaming } from '../../lib/stores/messages.js';
    import { isMultiEntityMode } from '../../lib/stores/entities.js';

    const dispatch = createEventDispatcher();

    function handleRegenerate(event) {
        dispatch('regenerate', event.detail);
    }

    function handleEditMessage(event) {
        dispatch('editMessage', event.detail);
    }

    function handleDeleteMessage(event) {
        dispatch('deleteMessage', event.detail);
    }

    // Check if this is the last assistant message (for regenerate button)
    function isLastAssistantMessage(msg, index) {
        if (msg.role !== 'assistant') return false;
        // Find last assistant message
        for (let i = $messages.length - 1; i >= 0; i--) {
            if ($messages[i].role === 'assistant') {
                return $messages[i].id === msg.id;
            }
        }
        return false;
    }
</script>

<div class="messages">
    {#if $messages.length === 0 && !$isStreaming}
        <div class="welcome-message">
            <h3>Welcome</h3>
            <p>Start a new conversation by typing a message below.</p>
            <p>Your conversations and memories are stored for continuity.</p>
        </div>
    {:else}
        {#each $messages as message, index (message.id)}
            <MessageItem
                {message}
                isMultiEntity={$isMultiEntityMode}
                canRegenerate={isLastAssistantMessage(message, index)}
                on:regenerate={handleRegenerate}
                on:edit={handleEditMessage}
                on:delete={handleDeleteMessage}
            />
        {/each}

        {#if $isStreaming}
            <StreamingMessage
                content={$streamingContent}
                tools={$streamingTools}
                metadata={$streamingMessage}
            />
        {/if}
    {/if}
</div>

<style>
    .messages {
        max-width: 800px;
        margin: 0 auto;
    }

    .welcome-message {
        text-align: center;
        padding: 60px 40px;
        color: var(--text-secondary);
    }

    .welcome-message h3 {
        font-size: 1.5rem;
        margin-bottom: 16px;
        color: var(--text-primary);
    }

    .welcome-message p {
        margin-bottom: 8px;
    }
</style>
