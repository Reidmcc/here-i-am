<script>
    import { createEventDispatcher, onMount, onDestroy } from 'svelte';

    export let title = '';
    export let size = 'medium'; // small, medium, large, xlarge
    export let showClose = true;

    const dispatch = createEventDispatcher();

    function close() {
        dispatch('close');
    }

    function handleKeydown(event) {
        if (event.key === 'Escape') {
            close();
        }
    }

    function handleBackdropClick(event) {
        if (event.target === event.currentTarget) {
            close();
        }
    }

    onMount(() => {
        document.addEventListener('keydown', handleKeydown);
        document.body.style.overflow = 'hidden';
    });

    onDestroy(() => {
        document.removeEventListener('keydown', handleKeydown);
        document.body.style.overflow = '';
    });
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_interactive_supports_focus -->
<div class="modal-backdrop" on:click={handleBackdropClick} role="dialog" aria-modal="true" aria-labelledby="modal-title">
    <div class="modal-container {size}">
        {#if title || showClose}
            <div class="modal-header">
                {#if title}
                    <h2 id="modal-title" class="modal-title">{title}</h2>
                {/if}
                {#if showClose}
                    <button class="close-btn" on:click={close} aria-label="Close modal">
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <line x1="18" y1="6" x2="6" y2="18"></line>
                            <line x1="6" y1="6" x2="18" y2="18"></line>
                        </svg>
                    </button>
                {/if}
            </div>
        {/if}
        <div class="modal-content">
            <slot />
        </div>
        {#if $$slots.footer}
            <div class="modal-footer">
                <slot name="footer" />
            </div>
        {/if}
    </div>
</div>

<style>
    .modal-backdrop {
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background-color: rgba(0, 0, 0, 0.6);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 1000;
        padding: 20px;
        animation: fadeIn 0.15s ease-out;
    }

    @keyframes fadeIn {
        from { opacity: 0; }
        to { opacity: 1; }
    }

    .modal-container {
        background-color: var(--bg-secondary);
        border-radius: 12px;
        box-shadow: 0 20px 60px rgba(0, 0, 0, 0.4);
        max-height: 90vh;
        display: flex;
        flex-direction: column;
        animation: slideIn 0.2s ease-out;
    }

    @keyframes slideIn {
        from {
            opacity: 0;
            transform: translateY(-20px) scale(0.95);
        }
        to {
            opacity: 1;
            transform: translateY(0) scale(1);
        }
    }

    .modal-container.small {
        width: 400px;
        max-width: 100%;
    }

    .modal-container.medium {
        width: 600px;
        max-width: 100%;
    }

    .modal-container.large {
        width: 800px;
        max-width: 100%;
    }

    .modal-container.xlarge {
        width: 1000px;
        max-width: 100%;
    }

    .modal-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 20px 24px;
        border-bottom: 1px solid var(--border-color);
    }

    .modal-title {
        margin: 0;
        font-size: 1.25rem;
        font-weight: 600;
        color: var(--text-primary);
    }

    .close-btn {
        padding: 8px;
        background: transparent;
        border: none;
        border-radius: 6px;
        cursor: pointer;
        color: var(--text-secondary);
        display: flex;
        align-items: center;
        justify-content: center;
        transition: all 0.2s;
    }

    .close-btn:hover {
        background-color: var(--bg-tertiary);
        color: var(--text-primary);
    }

    .modal-content {
        padding: 24px;
        overflow-y: auto;
        flex: 1;
    }

    .modal-footer {
        padding: 16px 24px;
        border-top: 1px solid var(--border-color);
        display: flex;
        justify-content: flex-end;
        gap: 12px;
    }
</style>
