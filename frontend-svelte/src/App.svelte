<script>
    // Minimal test - no imports except Svelte
    import { onMount } from 'svelte';

    let message = 'Loading...';

    onMount(() => {
        message = 'App mounted successfully!';

        // Write to debug div
        const el = document.getElementById('debug-log');
        if (el) el.innerHTML += '[App] onMount called!<br>';
    });
</script>

<h1>{message}</h1>
<p>If you can see this, the basic Svelte setup works.</p>

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
