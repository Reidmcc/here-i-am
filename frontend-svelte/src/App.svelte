<script>
    import { onMount } from 'svelte';

    // Test importing stores
    import { theme } from './lib/stores/app.js';
    import { entities, selectedEntityId } from './lib/stores/entities.js';
    import { conversations } from './lib/stores/conversations.js';
    import { messages } from './lib/stores/messages.js';
    import { settings, presets } from './lib/stores/settings.js';
    import { ttsEnabled } from './lib/stores/voice.js';

    // Test importing api
    import * as api from './lib/api.js';

    let message = 'Loading...';

    // Debug helper
    function debug(msg) {
        const el = document.getElementById('debug-log');
        if (el) el.innerHTML += '[App] ' + msg + '<br>';
    }

    onMount(() => {
        debug('onMount called - stores and api imported successfully!');
        message = 'Stores and API imported successfully!';
    });
</script>

<h1>{message}</h1>
<p>Stores imported: theme, entities, conversations, messages, settings, voice</p>
<p>API imported: ✓</p>

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
