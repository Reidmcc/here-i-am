<script>
    import { createEventDispatcher } from 'svelte';
    import Modal from '../common/Modal.svelte';
    import { entities } from '../../lib/stores/entities.js';

    const dispatch = createEventDispatcher();

    let selectedEntityIds = new Set();

    function close() {
        dispatch('close');
    }

    function toggleEntity(entityId) {
        if (selectedEntityIds.has(entityId)) {
            selectedEntityIds.delete(entityId);
        } else {
            selectedEntityIds.add(entityId);
        }
        selectedEntityIds = selectedEntityIds;
    }

    function handleCreate() {
        if (selectedEntityIds.size < 2) return;
        dispatch('create', { entityIds: Array.from(selectedEntityIds) });
    }

    $: canCreate = selectedEntityIds.size >= 2;
</script>

<Modal title="Create Multi-Entity Conversation" size="medium" on:close={close}>
    <div class="multi-entity-content">
        <p class="description">
            Select two or more entities to participate in this conversation.
            Each entity will have their own turn to respond.
        </p>

        <div class="entity-grid">
            {#each $entities as entity (entity.index_name)}
                <button
                    class="entity-card"
                    class:selected={selectedEntityIds.has(entity.index_name)}
                    on:click={() => toggleEntity(entity.index_name)}
                >
                    <div class="entity-checkbox">
                        {#if selectedEntityIds.has(entity.index_name)}
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                                <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/>
                            </svg>
                        {/if}
                    </div>
                    <div class="entity-info">
                        <span class="entity-label">{entity.label}</span>
                        {#if entity.description}
                            <span class="entity-description">{entity.description}</span>
                        {/if}
                        <span class="entity-provider">{entity.llm_provider || 'anthropic'}</span>
                    </div>
                </button>
            {/each}
        </div>

        {#if selectedEntityIds.size > 0}
            <div class="selection-summary">
                Selected: {selectedEntityIds.size} entities
                {#if selectedEntityIds.size < 2}
                    <span class="warning">(need at least 2)</span>
                {/if}
            </div>
        {/if}
    </div>

    <svelte:fragment slot="footer">
        <button class="btn btn-secondary" on:click={close}>
            Cancel
        </button>
        <button class="btn btn-primary" on:click={handleCreate} disabled={!canCreate}>
            Create Conversation
        </button>
    </svelte:fragment>
</Modal>

<style>
    .multi-entity-content {
        display: flex;
        flex-direction: column;
        gap: 20px;
    }

    .description {
        font-size: 0.9rem;
        color: var(--text-secondary);
        line-height: 1.5;
    }

    .entity-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
        gap: 12px;
    }

    .entity-card {
        display: flex;
        align-items: flex-start;
        gap: 12px;
        padding: 16px;
        background-color: var(--bg-tertiary);
        border: 2px solid var(--border-color);
        border-radius: 10px;
        cursor: pointer;
        text-align: left;
        transition: all 0.2s;
    }

    .entity-card:hover {
        border-color: var(--accent);
        background-color: var(--bg-primary);
    }

    .entity-card.selected {
        border-color: var(--accent);
        background-color: var(--accent-subtle);
    }

    .entity-checkbox {
        width: 20px;
        height: 20px;
        border: 2px solid var(--border-color);
        border-radius: 4px;
        display: flex;
        align-items: center;
        justify-content: center;
        flex-shrink: 0;
        transition: all 0.2s;
    }

    .entity-card.selected .entity-checkbox {
        background-color: var(--accent);
        border-color: var(--accent);
        color: white;
    }

    .entity-info {
        display: flex;
        flex-direction: column;
        gap: 4px;
        min-width: 0;
    }

    .entity-label {
        font-weight: 600;
        color: var(--text-primary);
    }

    .entity-description {
        font-size: 0.8rem;
        color: var(--text-muted);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    .entity-provider {
        font-size: 0.75rem;
        padding: 2px 6px;
        background-color: var(--bg-secondary);
        border-radius: 4px;
        color: var(--text-muted);
        width: fit-content;
    }

    .selection-summary {
        font-size: 0.9rem;
        color: var(--text-secondary);
        padding: 12px;
        background-color: var(--bg-tertiary);
        border-radius: 6px;
    }

    .warning {
        color: var(--warning);
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
