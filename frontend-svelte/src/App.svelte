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

    // Layout components
    import Sidebar from './components/layout/Sidebar.svelte';
    import ChatArea from './components/layout/ChatArea.svelte';

    // Debug helper
    function debug(msg) {
        const el = document.getElementById('debug-log');
        if (el) el.innerHTML += '[App] ' + msg + '<br>';
    }

    let initComplete = false;

    onMount(() => {
        debug('onMount called');
        initComplete = true;
        debug('initComplete set to true');
    });
</script>

{#if initComplete}
    <div class="app-container">
        <Sidebar />
        <ChatArea />
    </div>
{:else}
    <div class="loading">
        <p>Loading...</p>
    </div>
{/if}

<ToastContainer />
<LoadingOverlay />

<style>
    .app-container {
        display: flex;
        height: 100vh;
        overflow: hidden;
    }

    .loading {
        display: flex;
        align-items: center;
        justify-content: center;
        height: 100vh;
        color: var(--text-primary, #e0e0e0);
        background: var(--bg-primary, #1a1a1a);
    }
</style>
