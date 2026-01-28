<script>
    import { onMount } from 'svelte';

    // Stores
    import { theme } from './lib/stores/app.js';
    import * as api from './lib/api.js';

    // Common components
    import ToastContainer from './components/common/Toast.svelte';
    import LoadingOverlay from './components/common/Loading.svelte';

    // Import ChatArea but DON'T render it
    import ChatArea from './components/layout/ChatArea.svelte';

    // Debug helper
    function debug(msg) {
        const el = document.getElementById('debug-log');
        if (el) el.innerHTML += '[App] ' + msg + '<br>';
    }

    onMount(() => {
        debug('onMount called - ChatArea imported but not rendered');
    });
</script>

<div class="app-container">
    <main class="chat-area">
        <h1>ChatArea Import Test</h1>
        <p>ChatArea is imported but NOT rendered.</p>
        <p>If you see this, the import itself works.</p>
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
