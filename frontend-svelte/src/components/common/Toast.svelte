<script>
    import { toasts } from '../../lib/stores/app.js';
</script>

<div class="toast-container">
    {#each $toasts as toast (toast.id)}
        <div class="toast {toast.type}">
            {toast.message}
        </div>
    {/each}
</div>

<style>
    .toast-container {
        position: fixed;
        bottom: 24px;
        right: 24px;
        z-index: 3000;
    }

    .toast {
        background-color: var(--bg-elevated);
        color: var(--text-primary);
        padding: 12px 20px;
        border-radius: 8px;
        margin-top: 8px;
        box-shadow: 0 4px 16px var(--shadow-lg);
        border: 1px solid var(--border-color);
        display: flex;
        align-items: center;
        gap: 12px;
        animation: slideIn 0.3s ease;
    }

    .toast.success { border-left: 4px solid var(--success); }
    .toast.error { border-left: 4px solid var(--danger); }
    .toast.warning { border-left: 4px solid var(--warning); }
    .toast.info { border-left: 4px solid var(--accent); }
</style>
