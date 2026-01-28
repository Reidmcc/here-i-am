<script>
    import { onMount } from 'svelte';

    // Stores
    import { currentConversation, currentConversationId } from './lib/stores/conversations.js';
    import { messages } from './lib/stores/messages.js';
    import { settings } from './lib/stores/settings.js';
    import { isLoading, showToast } from './lib/stores/app.js';
    import * as api from './lib/api.js';

    // Common components
    import ToastContainer from './components/common/Toast.svelte';
    import LoadingOverlay from './components/common/Loading.svelte';

    // Test MemoriesPanel
    import MemoriesPanel from './components/chat/MemoriesPanel.svelte';

    // Debug helper
    function debug(msg) {
        const el = document.getElementById('debug-log');
        if (el) el.innerHTML += '[App] ' + msg + '<br>';
    }

    onMount(() => {
        debug('onMount called - MemoriesPanel test');
    });
</script>

<div class="app-container">
    <main class="chat-area">
        <h1>MemoriesPanel Test</h1>
        <MemoriesPanel />
        <div class="spacer"></div>
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

    .spacer {
        flex: 1;
    }
</style>
