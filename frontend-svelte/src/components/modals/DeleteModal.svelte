<script>
    import { createEventDispatcher } from 'svelte';
    import Modal from '../common/Modal.svelte';

    export let title = '';
    export let itemType = 'item';

    const dispatch = createEventDispatcher();

    let isDeleting = false;

    function close() {
        dispatch('close');
    }

    async function handleConfirm() {
        isDeleting = true;
        dispatch('confirm');
    }
</script>

<Modal title="Delete {itemType}" size="small" on:close={close}>
    <div class="delete-content">
        <div class="warning-icon">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
                <line x1="12" y1="9" x2="12" y2="13"></line>
                <line x1="12" y1="17" x2="12.01" y2="17"></line>
            </svg>
        </div>
        <p class="warning-text">
            Are you sure you want to delete <strong>"{title || 'this ' + itemType}"</strong>?
        </p>
        <p class="warning-subtext">
            This action cannot be undone.
        </p>
    </div>

    <svelte:fragment slot="footer">
        <button class="btn btn-secondary" on:click={close} disabled={isDeleting}>
            Cancel
        </button>
        <button class="btn btn-danger" on:click={handleConfirm} disabled={isDeleting}>
            {isDeleting ? 'Deleting...' : 'Delete'}
        </button>
    </svelte:fragment>
</Modal>

<style>
    .delete-content {
        text-align: center;
        padding: 20px 0;
    }

    .warning-icon {
        color: var(--danger);
        margin-bottom: 16px;
    }

    .warning-text {
        font-size: 1rem;
        color: var(--text-primary);
        margin-bottom: 8px;
    }

    .warning-subtext {
        font-size: 0.85rem;
        color: var(--text-muted);
    }

    .btn {
        padding: 10px 20px;
        border-radius: 6px;
        font-size: 0.95rem;
        cursor: pointer;
        transition: all 0.2s;
    }

    .btn-secondary {
        background-color: var(--bg-tertiary);
        color: var(--text-primary);
        border: 1px solid var(--border-color);
    }

    .btn-secondary:hover:not(:disabled) {
        background-color: var(--bg-primary);
    }

    .btn-danger {
        background-color: var(--danger);
        color: white;
        border: none;
    }

    .btn-danger:hover:not(:disabled) {
        background-color: var(--danger-hover);
    }

    .btn:disabled {
        opacity: 0.5;
        cursor: not-allowed;
    }
</style>
