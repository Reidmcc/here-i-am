<script>
    import { createEventDispatcher } from 'svelte';
    import { currentConversationEntities, pendingResponderId, entitiesMap } from '../../lib/stores/entities.js';

    const dispatch = createEventDispatcher();

    function selectResponder(entityId) {
        pendingResponderId.set(entityId);
        dispatch('select', { entityId });
    }

    function getEntityLabel(entityId) {
        const entity = $entitiesMap.get(entityId);
        return entity?.label || entityId;
    }
</script>

<div class="responder-selector">
    <div class="selector-header">
        <span class="selector-title">Select responding entity:</span>
    </div>
    <div class="entity-options">
        {#each $currentConversationEntities as entityId}
            <button
                class="entity-option"
                class:selected={$pendingResponderId === entityId}
                on:click={() => selectResponder(entityId)}
            >
                <span class="entity-label">{getEntityLabel(entityId)}</span>
            </button>
        {/each}
    </div>
</div>

<style>
    .responder-selector {
        padding: 16px 24px;
        background-color: var(--bg-tertiary);
        border-top: 1px solid var(--border-color);
        border-bottom: 1px solid var(--border-color);
    }

    .selector-header {
        margin-bottom: 12px;
    }

    .selector-title {
        font-size: 0.9rem;
        color: var(--text-secondary);
        font-weight: 500;
    }

    .entity-options {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        max-width: 800px;
        margin: 0 auto;
    }

    .entity-option {
        padding: 10px 20px;
        background-color: var(--bg-secondary);
        border: 2px solid var(--border-color);
        border-radius: 8px;
        cursor: pointer;
        transition: all 0.2s;
        color: var(--text-primary);
        font-size: 0.95rem;
    }

    .entity-option:hover {
        border-color: var(--accent);
        background-color: var(--bg-primary);
    }

    .entity-option.selected {
        border-color: var(--accent);
        background-color: var(--accent-subtle);
        color: var(--accent);
    }

    .entity-label {
        font-weight: 500;
    }
</style>
