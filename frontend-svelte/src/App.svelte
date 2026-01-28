<script>
    import { onMount } from 'svelte';

    // Stores that ChatArea uses
    import { currentConversation, currentConversationId } from './lib/stores/conversations.js';
    import { messages } from './lib/stores/messages.js';
    import { settings } from './lib/stores/settings.js';
    import { isLoading, showToast } from './lib/stores/app.js';
    import * as api from './lib/api.js';

    // Common components
    import ToastContainer from './components/common/Toast.svelte';
    import LoadingOverlay from './components/common/Loading.svelte';

    // Debug helper
    function debug(msg) {
        const el = document.getElementById('debug-log');
        if (el) el.innerHTML += '[App] ' + msg + '<br>';
    }

    onMount(() => {
        debug('onMount called - ChatArea stores test');
    });
</script>

<div class="app-container">
    <main class="chat-area">
        <h1>ChatArea Stores Test</h1>
        <p>Testing ChatArea's store imports without child components.</p>
        <p>Current conversation: {$currentConversationId || 'none'}</p>
        <p>Messages count: {$messages.length}</p>
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
        align-items: center;
        justify-content: center;
        background: #1a1a1a;
        color: #e0e0e0;
    }

    h1 {
        color: #4a9eff;
    }
</style>
