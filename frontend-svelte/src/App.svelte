<script>
    import { onMount } from 'svelte';

    // Stores and API (confirmed working)
    import { theme } from './lib/stores/app.js';
    import { entities, selectedEntityId } from './lib/stores/entities.js';
    import { conversations } from './lib/stores/conversations.js';
    import { messages } from './lib/stores/messages.js';
    import { settings, presets } from './lib/stores/settings.js';
    import { ttsEnabled } from './lib/stores/voice.js';
    import * as api from './lib/api.js';

    // Common components (confirmed working)
    import ToastContainer from './components/common/Toast.svelte';
    import LoadingOverlay from './components/common/Loading.svelte';

    // Test importing layout components
    import Sidebar from './components/layout/Sidebar.svelte';
    import ChatArea from './components/layout/ChatArea.svelte';

    let message = 'Loading...';

    function debug(msg) {
        const el = document.getElementById('debug-log');
        if (el) el.innerHTML += '[App] ' + msg + '<br>';
    }

    onMount(() => {
        debug('onMount called - layout components imported successfully!');
        message = 'Layout components imported successfully!';
    });
</script>

<h1>{message}</h1>
<p>Layout components: Sidebar ✓, ChatArea ✓</p>

<ToastContainer />
<LoadingOverlay />

<style>
    h1 {
        color: #4a9eff;
        margin-top: 250px;
        text-align: center;
    }
    p {
        text-align: center;
        color: #888;
    }
</style>
