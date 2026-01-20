<script>
    import { isLoading } from '../../lib/stores/app.js';
</script>

{#if $isLoading}
    <div class="loading-overlay active">
        <div class="loading-spinner"></div>
    </div>
{/if}

<style>
    .loading-overlay {
        display: none;
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background-color: var(--overlay-light);
        z-index: 2000;
        justify-content: center;
        align-items: center;
    }

    .loading-overlay.active {
        display: flex;
    }

    .loading-spinner {
        width: 50px;
        height: 50px;
        border: 3px solid var(--border-color);
        border-top-color: var(--accent);
        border-radius: 50%;
        animation: spin 1s linear infinite;
    }
</style>
