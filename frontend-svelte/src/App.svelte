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

    // Just ChatArea - no Sidebar
    import ChatArea from './components/layout/ChatArea.svelte';

    // Debug helper
    function debug(msg) {
        const el = document.getElementById('debug-log');
        if (el) el.innerHTML += '[App] ' + msg + '<br>';
    }

    onMount(() => {
        debug('onMount called - ChatArea only test');
    });
</script>

<div class="app-container">
    <aside class="placeholder-sidebar">
        <h2>ChatArea Test</h2>
        <p>Testing ChatArea alone</p>
    </aside>
    <ChatArea />
</div>

<ToastContainer />
<LoadingOverlay />

<style>
    .app-container {
        display: flex;
        height: 100vh;
        overflow: hidden;
    }

    .placeholder-sidebar {
        width: 280px;
        background: #2d2d2d;
        padding: 20px;
        color: #e0e0e0;
        border-right: 1px solid #3d3d3d;
    }

    .placeholder-sidebar h2 {
        color: #4a9eff;
    }
</style>
