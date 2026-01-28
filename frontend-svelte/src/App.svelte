<script>
    import { onMount, tick } from 'svelte';

    // All stores that ChatArea imports
    import { currentConversation, currentConversationId, addConversationToList, updateConversationInList } from './lib/stores/conversations.js';
    import { messages, streamingContent, streamingMessage, streamingTools, startStreaming, stopStreaming, addMessage, appendStreamingContent, addStreamingTool, updateStreamingToolResult, resetPendingMessage, pendingMessageContent, pendingMessageAttachments, responderSelectorMode } from './lib/stores/messages.js';
    import { retrievedMemories, addMemories, resetMemoriesState } from './lib/stores/memories.js';
    import { selectedEntityId, isMultiEntityMode, currentConversationEntities, pendingResponderId, getEntityLabel, entitySystemPrompts } from './lib/stores/entities.js';
    import { settings, researcherName } from './lib/stores/settings.js';
    import { isLoading, showToast, createAbortController, abortStream, streamAbortController } from './lib/stores/app.js';
    import { pendingAttachments } from './lib/stores/attachments.js';
    import * as api from './lib/api.js';

    // Common components
    import ToastContainer from './components/common/Toast.svelte';
    import LoadingOverlay from './components/common/Loading.svelte';

    // All four ChatArea child components
    import MessageList from './components/chat/MessageList.svelte';
    import InputArea from './components/chat/InputArea.svelte';
    import MemoriesPanel from './components/chat/MemoriesPanel.svelte';
    import EntityResponderSelector from './components/chat/EntityResponderSelector.svelte';

    // Debug helper
    function debug(msg) {
        const el = document.getElementById('debug-log');
        if (el) el.innerHTML += '[App] ' + msg + '<br>';
    }

    onMount(() => {
        debug('onMount called - Full ChatArea stores test');
    });
</script>

<div class="app-container">
    <main class="chat-area">
        <h1>Full ChatArea Stores Test</h1>
        <MemoriesPanel />
        <div class="messages-container">
            <MessageList />
        </div>
        <InputArea />
        {#if $responderSelectorMode}
            <EntityResponderSelector mode={$responderSelectorMode} />
        {/if}
    </main>
</div>

<ToastContainer />
<LoadingOverlay />

<style>
    .app-container {
        display: flex;
        height: 100vh;
        overflow: hidden;
    }

    .chat-area {
        flex: 1;
        display: flex;
        flex-direction: column;
        background: #1a1a1a;
        color: #e0e0e0;
        padding: 20px;
    }

    h1 {
        color: #4a9eff;
        text-align: center;
    }

    .messages-container {
        flex: 1;
        overflow-y: auto;
    }
</style>
