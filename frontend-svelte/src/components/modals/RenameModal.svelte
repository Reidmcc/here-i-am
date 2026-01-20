<script>
    import { createEventDispatcher, onMount } from 'svelte';
    import Modal from '../common/Modal.svelte';

    export let currentTitle = '';
    export let itemType = 'item';

    const dispatch = createEventDispatcher();

    let newTitle = '';
    let inputEl;

    onMount(() => {
        newTitle = currentTitle || '';
        inputEl?.focus();
        inputEl?.select();
    });

    function close() {
        dispatch('close');
    }

    function handleSubmit() {
        if (!newTitle.trim()) return;
        dispatch('confirm', { title: newTitle.trim() });
    }

    function handleKeydown(event) {
        if (event.key === 'Enter') {
            handleSubmit();
        }
    }
</script>

<Modal title="Rename {itemType}" size="small" on:close={close}>
    <div class="rename-content">
        <label for="rename-input">New Title</label>
        <input
            bind:this={inputEl}
            type="text"
            id="rename-input"
            bind:value={newTitle}
            on:keydown={handleKeydown}
            placeholder="Enter new title..."
        />
    </div>

    <svelte:fragment slot="footer">
        <button class="btn btn-secondary" on:click={close}>
            Cancel
        </button>
        <button class="btn btn-primary" on:click={handleSubmit} disabled={!newTitle.trim()}>
            Rename
        </button>
    </svelte:fragment>
</Modal>

<style>
    .rename-content {
        display: flex;
        flex-direction: column;
        gap: 8px;
    }

    label {
        font-size: 0.9rem;
        color: var(--text-secondary);
    }

    input {
        width: 100%;
        padding: 12px 16px;
        background-color: var(--bg-tertiary);
        border: 1px solid var(--border-color);
        border-radius: 8px;
        color: var(--text-primary);
        font-size: 1rem;
    }

    input:focus {
        outline: none;
        border-color: var(--accent);
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

    .btn-secondary:hover {
        background-color: var(--bg-primary);
    }

    .btn-primary {
        background-color: var(--accent);
        color: white;
        border: none;
    }

    .btn-primary:hover:not(:disabled) {
        background-color: var(--accent-hover);
    }

    .btn-primary:disabled {
        opacity: 0.5;
        cursor: not-allowed;
    }
</style>
