<script>
    import { createEventDispatcher, onMount } from 'svelte';
    import Modal from '../common/Modal.svelte';
    import { selectedEntityId, entitiesMap } from '../../lib/stores/entities.js';
    import { showToast } from '../../lib/stores/app.js';
    import { formatRelativeTime } from '../../lib/utils.js';
    import * as api from '../../lib/api.js';

    const dispatch = createEventDispatcher();

    let archivedConversations = [];
    let isLoading = true;

    onMount(async () => {
        await loadArchived();
    });

    async function loadArchived() {
        isLoading = true;
        try {
            const response = await api.getArchivedConversations($selectedEntityId);
            archivedConversations = response.conversations || [];
        } catch (error) {
            showToast(`Failed to load archived: ${error.message}`, 'error');
        } finally {
            isLoading = false;
        }
    }

    function close() {
        dispatch('close');
    }

    async function handleUnarchive(conversationId) {
        try {
            await api.unarchiveConversation(conversationId);
            archivedConversations = archivedConversations.filter(c => c.id !== conversationId);
            showToast('Conversation restored', 'success');
            dispatch('unarchive', { conversationId });
        } catch (error) {
            showToast(`Failed to restore: ${error.message}`, 'error');
        }
    }

    async function handleDelete(conversationId, title) {
        if (!confirm(`Delete "${title || 'Untitled'}" permanently? This cannot be undone.`)) {
            return;
        }

        try {
            await api.deleteConversation(conversationId);
            archivedConversations = archivedConversations.filter(c => c.id !== conversationId);
            showToast('Conversation deleted', 'success');
        } catch (error) {
            showToast(`Failed to delete: ${error.message}`, 'error');
        }
    }

    function getEntityLabel(entityId) {
        if (entityId === 'multi-entity') return 'Multi-Entity';
        const entity = $entitiesMap.get(entityId);
        return entity?.label || entityId;
    }
</script>

<Modal title="Archived Conversations" size="large" on:close={close}>
    <div class="archived-content">
        {#if isLoading}
            <p class="loading-text">Loading archived conversations...</p>
        {:else if archivedConversations.length > 0}
            <p class="info-text">
                Archived conversations are excluded from memory retrieval.
            </p>
            <div class="archived-list">
                {#each archivedConversations as conversation (conversation.id)}
                    <div class="archived-item">
                        <div class="item-info">
                            <span class="item-title">
                                {conversation.title || 'Untitled Conversation'}
                            </span>
                            <div class="item-meta">
                                <span class="item-entity">
                                    {getEntityLabel(conversation.entity_id)}
                                </span>
                                <span class="item-date">
                                    {formatRelativeTime(conversation.updated_at || conversation.created_at)}
                                </span>
                            </div>
                        </div>
                        <div class="item-actions">
                            <button
                                class="action-btn restore"
                                on:click={() => handleUnarchive(conversation.id)}
                                title="Restore conversation"
                            >
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"></path>
                                    <path d="M3 3v5h5"></path>
                                </svg>
                                Restore
                            </button>
                            <button
                                class="action-btn delete"
                                on:click={() => handleDelete(conversation.id, conversation.title)}
                                title="Delete permanently"
                            >
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"></path>
                                </svg>
                                Delete
                            </button>
                        </div>
                    </div>
                {/each}
            </div>
        {:else}
            <p class="empty-text">No archived conversations.</p>
        {/if}
    </div>

    <svelte:fragment slot="footer">
        <button class="btn btn-secondary" on:click={close}>Close</button>
    </svelte:fragment>
</Modal>

<style>
    .archived-content {
        min-height: 200px;
    }

    .info-text {
        font-size: 0.85rem;
        color: var(--text-muted);
        margin-bottom: 16px;
        padding: 10px 12px;
        background-color: var(--bg-tertiary);
        border-radius: 6px;
    }

    .archived-list {
        display: flex;
        flex-direction: column;
        gap: 12px;
    }

    .archived-item {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 16px;
        background-color: var(--bg-tertiary);
        border: 1px solid var(--border-color);
        border-radius: 8px;
    }

    .item-info {
        flex: 1;
        min-width: 0;
    }

    .item-title {
        display: block;
        font-weight: 500;
        color: var(--text-primary);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        margin-bottom: 4px;
    }

    .item-meta {
        display: flex;
        gap: 12px;
        font-size: 0.8rem;
        color: var(--text-muted);
    }

    .item-entity {
        padding: 2px 6px;
        background-color: var(--bg-secondary);
        border-radius: 4px;
    }

    .item-actions {
        display: flex;
        gap: 8px;
        margin-left: 16px;
    }

    .action-btn {
        display: flex;
        align-items: center;
        gap: 6px;
        padding: 8px 12px;
        background: transparent;
        border: 1px solid var(--border-color);
        border-radius: 6px;
        cursor: pointer;
        font-size: 0.85rem;
        color: var(--text-secondary);
        transition: all 0.2s;
    }

    .action-btn:hover {
        background-color: var(--bg-secondary);
    }

    .action-btn.restore:hover {
        color: var(--success);
        border-color: var(--success);
    }

    .action-btn.delete:hover {
        color: var(--danger);
        border-color: var(--danger);
    }

    .loading-text,
    .empty-text {
        text-align: center;
        color: var(--text-muted);
        padding: 40px;
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
</style>
