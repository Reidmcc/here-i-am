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

    // Layout components (confirmed working)
    import Sidebar from './components/layout/Sidebar.svelte';
    import ChatArea from './components/layout/ChatArea.svelte';

    // Test importing modal components
    import SettingsModal from './components/modals/SettingsModal.svelte';
    import MemoriesModal from './components/modals/MemoriesModal.svelte';
    import DeleteModal from './components/modals/DeleteModal.svelte';
    import RenameModal from './components/modals/RenameModal.svelte';
    import ArchivedModal from './components/modals/ArchivedModal.svelte';
    import MultiEntityModal from './components/modals/MultiEntityModal.svelte';
    import ImportExportModal from './components/modals/ImportExportModal.svelte';

    let message = 'Loading...';

    function debug(msg) {
        const el = document.getElementById('debug-log');
        if (el) el.innerHTML += '[App] ' + msg + '<br>';
    }

    onMount(() => {
        debug('onMount called - ALL components imported successfully!');
        message = 'ALL components imported successfully!';
    });
</script>

<h1>{message}</h1>
<p>All imports working!</p>

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
