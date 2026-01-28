<script>
    import { onMount } from 'svelte';

    // Debug helper
    function debug(msg) {
        const el = document.getElementById('debug-log');
        if (el) el.innerHTML += '[App] ' + msg + '<br>';
    }

    let count = 0;

    onMount(() => {
        debug('onMount called - pure minimal test');
    });
</script>

<div class="container">
    <h1>Pure Minimal Test</h1>
    <p>No external components imported.</p>
    <button on:click={() => count++}>
        Clicked {count} times
    </button>
</div>

<style>
    .container {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        height: 100vh;
        background: #1a1a1a;
        color: #e0e0e0;
    }

    h1 {
        color: #4a9eff;
    }

    button {
        margin-top: 20px;
        padding: 10px 20px;
        background: #4a9eff;
        color: white;
        border: none;
        border-radius: 4px;
        cursor: pointer;
    }
</style>
