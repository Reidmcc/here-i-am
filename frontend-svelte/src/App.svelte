<script>
    import { onMount } from 'svelte';

    // Stores
    import { theme } from './lib/stores/app.js';
    import { entities, selectedEntityId } from './lib/stores/entities.js';
    import { conversations } from './lib/stores/conversations.js';
    import * as api from './lib/api.js';

    // Common components
    import ToastContainer from './components/common/Toast.svelte';
    import LoadingOverlay from './components/common/Loading.svelte';

    // Just Sidebar - no ChatArea
    import Sidebar from './components/layout/Sidebar.svelte';

    // Debug helper
    function debug(msg) {
        const el = document.getElementById('debug-log');
        if (el) el.innerHTML += '[App] ' + msg + '<br>';
    }

    onMount(() => {
        debug('onMount called - Sidebar only test');
    });
</script>

<div class="app-container">
    <Sidebar />
    <main class="placeholder">
        <h1>Sidebar Only Test</h1>
        <p>If you see this, Sidebar rendered successfully.</p>
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

    .placeholder {
        flex: 1;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        color: var(--text-primary, #e0e0e0);
        background: var(--bg-primary, #1a1a1a);
    }

    .placeholder h1 {
        color: #4a9eff;
    }
</style>
