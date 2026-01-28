<script>
    import { createEventDispatcher } from 'svelte';
    import { entities, selectedEntityId, selectedEntity, isMultiEntityMode } from '../../lib/stores/entities.js';
    import { conversations, currentConversationId, currentConversation } from '../../lib/stores/conversations.js';
    import { activeModal, showToast } from '../../lib/stores/app.js';
    import { formatRelativeTime, truncateText } from '../../lib/utils.js';
    import * as api from '../../lib/api.js';

    const dispatch = createEventDispatcher();

    // Mobile sidebar open state (controlled by parent)
    export let isOpen = false;

    let openDropdownId = null;

    function handleEntityChange(event) {
        dispatch('entityChange', event.target.value);
    }

    function handleSelectConversation(id) {
        dispatch('selectConversation', id);
        openDropdownId = null;
    }

    function handleNewConversation() {
        dispatch('newConversation');
    }

    function openSettings() {
        dispatch('openSettings');
    }

    function openMemories() {
        dispatch('openMemories');
    }

    function openArchived() {
        dispatch('openArchived');
    }

    function openImportExport() {
        dispatch('openImportExport');
    }

    function toggleDropdown(id, event) {
        event.stopPropagation();
        openDropdownId = openDropdownId === id ? null : id;
    }

    function closeDropdowns() {
        openDropdownId = null;
    }

    async function handleRename(conv, event) {
        event.stopPropagation();
        openDropdownId = null;
        dispatch('renameConversation', { id: conv.id, title: conv.title });
    }

    async function handleArchive(conv, event) {
        event.stopPropagation();
        openDropdownId = null;
        try {
            await api.archiveConversation(conv.id);
            showToast('Conversation archived', 'success');
            dispatch('conversationsUpdated');
            if ($currentConversationId === conv.id) {
                dispatch('selectConversation', null);
            }
        } catch (error) {
            showToast(`Failed to archive: ${error.message}`, 'error');
        }
    }

    async function handleDelete(conv, event) {
        event.stopPropagation();
        openDropdownId = null;
        dispatch('deleteConversation', { id: conv.id, title: conv.title });
    }

    // Close dropdown when clicking outside
    function handleWindowClick() {
        openDropdownId = null;
    }
</script>

<svelte:window on:click={handleWindowClick} />

<aside class="sidebar" class:open={isOpen}>
    <div class="sidebar-header">
        <h1 class="app-title">Here I Am</h1>
        <p class="app-subtitle">Experiential Research Interface</p>
    </div>

    <div class="entity-selector">
        <label for="entity-select">Entity</label>
        <select
            id="entity-select"
            value={$isMultiEntityMode ? 'multi-entity' : ($selectedEntityId || '')}
            on:change={handleEntityChange}
        >
            {#each $entities as entity}
                <option value={entity.index_name}>{entity.label}</option>
            {/each}
            {#if $entities.length > 1}
                <option disabled>──────────</option>
                <option value="multi-entity">Multi-Entity Conversation</option>
            {/if}
        </select>
        {#if $selectedEntity?.description && !$isMultiEntityMode}
            <p class="entity-description">{$selectedEntity.description}</p>
        {/if}
        {#if $isMultiEntityMode}
            <p class="entity-description">Multiple entities participating</p>
        {/if}
    </div>

    <button class="new-conversation-btn" on:click={handleNewConversation}>
        + New Conversation
    </button>

    <div class="conversation-list">
        {#each $conversations as conv (conv.id)}
            <div
                class="conversation-item"
                class:active={$currentConversationId === conv.id}
                class:multi-entity={conv.conversation_type === 'multi_entity'}
            >
                <div
                    class="conversation-item-content"
                    on:click={() => handleSelectConversation(conv.id)}
                    on:keydown={(e) => e.key === 'Enter' && handleSelectConversation(conv.id)}
                    role="button"
                    tabindex="0"
                >
                    <div class="conversation-item-title">
                        {conv.title || 'New Conversation'}
                        {#if conv.conversation_type === 'multi_entity'}
                            <span class="multi-entity-badge">Multi</span>
                        {/if}
                    </div>
                    <div class="conversation-item-meta">
                        {formatRelativeTime(conv.updated_at || conv.created_at)}
                    </div>
                </div>
                <div class="conversation-item-menu">
                    <button
                        class="conversation-menu-btn"
                        on:click={(e) => toggleDropdown(conv.id, e)}
                        aria-label="Conversation menu"
                    >
                        ⋮
                    </button>
                    <div class="conversation-dropdown" class:active={openDropdownId === conv.id}>
                        <button class="conversation-dropdown-item" on:click={(e) => handleRename(conv, e)}>
                            Rename
                        </button>
                        <button class="conversation-dropdown-item" on:click={(e) => handleArchive(conv, e)}>
                            Archive
                        </button>
                        <button class="conversation-dropdown-item danger" on:click={(e) => handleDelete(conv, e)}>
                            Delete
                        </button>
                    </div>
                </div>
            </div>
        {:else}
            <div class="conversation-list-empty">
                <p>No conversations yet</p>
            </div>
        {/each}
    </div>

    <div class="sidebar-footer">
        <button class="footer-btn" on:click={openSettings} title="Settings">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="3"></circle>
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
            </svg>
        </button>
        <button class="footer-btn" on:click={openMemories} title="Memories">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path>
                <polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline>
                <line x1="12" y1="22.08" x2="12" y2="12"></line>
            </svg>
        </button>
        <button class="footer-btn" on:click={openArchived} title="Archived">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="21 8 21 21 3 21 3 8"></polyline>
                <rect x="1" y="3" width="22" height="5"></rect>
                <line x1="10" y1="12" x2="14" y2="12"></line>
            </svg>
        </button>
        <button class="footer-btn" on:click={openImportExport} title="Import/Export">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                <polyline points="7 10 12 15 17 10"></polyline>
                <line x1="12" y1="15" x2="12" y2="3"></line>
            </svg>
        </button>
    </div>
</aside>

<style>
    .sidebar {
        width: 280px;
        background-color: var(--bg-secondary);
        border-right: 1px solid var(--border-color);
        display: flex;
        flex-direction: column;
        flex-shrink: 0;
    }

    .sidebar-header {
        padding: 20px;
        border-bottom: 1px solid var(--border-color);
    }

    .app-title {
        font-size: 1.5rem;
        font-weight: 600;
        color: var(--text-primary);
        margin-bottom: 4px;
    }

    .app-subtitle {
        font-size: 0.75rem;
        color: var(--text-muted);
    }

    .entity-selector {
        padding: 12px 16px;
        border-bottom: 1px solid var(--border-color);
        display: block;
    }

    .entity-selector label {
        display: block;
        font-size: 0.75rem;
        color: var(--text-muted);
        margin-bottom: 6px;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }

    .entity-selector select {
        width: 100%;
        padding: 8px 12px;
        background-color: var(--bg-tertiary);
        border: 1px solid var(--border-color);
        border-radius: 6px;
        color: var(--text-primary);
        font-family: var(--font-sans);
        font-size: 0.9rem;
        cursor: pointer;
    }

    .entity-selector select:focus {
        outline: none;
        border-color: var(--accent);
        box-shadow: 0 0 0 2px var(--accent-subtle);
    }

    .entity-description {
        font-size: 0.75rem;
        color: var(--text-muted);
        margin-top: 6px;
        font-style: italic;
    }

    .new-conversation-btn {
        margin: 16px;
        padding: 12px 16px;
        background-color: var(--accent);
        color: white;
        border: none;
        border-radius: 8px;
        cursor: pointer;
        font-size: 0.9rem;
        font-weight: 500;
        transition: background-color 0.2s;
    }

    .new-conversation-btn:hover {
        background-color: var(--accent-hover);
    }

    .conversation-list {
        flex: 1;
        overflow-y: auto;
        padding: 8px;
    }

    .conversation-list-empty {
        text-align: center;
        padding: 24px;
        color: var(--text-muted);
        font-size: 0.9rem;
    }

    .conversation-item {
        display: flex;
        align-items: flex-start;
        padding: 12px;
        border-radius: 8px;
        margin-bottom: 4px;
        transition: background-color 0.2s;
        position: relative;
    }

    .conversation-item:hover {
        background-color: var(--bg-tertiary);
    }

    .conversation-item.active {
        background-color: var(--accent);
        box-shadow: 0 0 12px var(--glow);
    }

    .conversation-item.active .conversation-item-title {
        color: white;
    }

    .conversation-item.active .conversation-item-meta {
        color: var(--active-text-muted);
    }

    .conversation-item.multi-entity::before {
        content: '';
        position: absolute;
        left: 0;
        top: 0;
        bottom: 0;
        width: 3px;
        background: linear-gradient(to bottom, var(--accent), var(--success));
        border-radius: 3px 0 0 3px;
    }

    .conversation-item.multi-entity {
        padding-left: 12px;
    }

    .conversation-item-content {
        flex: 1;
        min-width: 0;
        cursor: pointer;
    }

    .conversation-item-title {
        font-size: 0.9rem;
        font-weight: 500;
        color: var(--text-primary);
        margin-bottom: 4px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    .conversation-item-meta {
        font-size: 0.75rem;
        color: var(--text-muted);
    }

    .multi-entity-badge {
        font-size: 10px;
        padding: 2px 6px;
        background: var(--accent-subtle);
        color: var(--accent);
        border-radius: 4px;
        margin-left: 6px;
    }

    .conversation-item.active .multi-entity-badge {
        background: rgba(255, 255, 255, 0.2);
        color: white;
    }

    .conversation-item-menu {
        position: relative;
        flex-shrink: 0;
        margin-left: 4px;
    }

    .conversation-menu-btn {
        background: none;
        border: none;
        color: var(--text-muted);
        cursor: pointer;
        padding: 4px 8px;
        font-size: 1.1rem;
        line-height: 1;
        border-radius: 4px;
        opacity: 0;
        transition: opacity 0.2s, background-color 0.2s, color 0.2s;
    }

    .conversation-item:hover .conversation-menu-btn,
    .conversation-menu-btn:focus {
        opacity: 1;
    }

    .conversation-menu-btn:hover {
        background-color: var(--hover-overlay);
        color: var(--text-primary);
    }

    .conversation-item.active .conversation-menu-btn {
        color: rgba(255, 255, 255, 0.7);
    }

    .conversation-item.active .conversation-menu-btn:hover {
        color: white;
        background-color: rgba(255, 255, 255, 0.1);
    }

    .conversation-dropdown {
        display: none;
        position: absolute;
        top: 100%;
        right: 0;
        background-color: var(--bg-elevated);
        border: 1px solid var(--border-color);
        border-radius: 6px;
        box-shadow: 0 4px 12px var(--shadow-lg);
        min-width: 120px;
        z-index: 100;
        overflow: hidden;
    }

    .conversation-dropdown.active {
        display: block;
    }

    .conversation-dropdown-item {
        display: block;
        width: 100%;
        padding: 10px 14px;
        background: none;
        border: none;
        color: var(--text-primary);
        font-size: 0.85rem;
        text-align: left;
        cursor: pointer;
        transition: background-color 0.2s;
    }

    .conversation-dropdown-item:hover {
        background-color: var(--hover-overlay);
    }

    .conversation-dropdown-item.danger {
        color: var(--danger);
    }

    .conversation-dropdown-item.danger:hover {
        background-color: rgba(229, 92, 92, 0.1);
    }

    .sidebar-footer {
        padding: 16px;
        border-top: 1px solid var(--border-color);
        display: flex;
        gap: 8px;
    }

    .footer-btn {
        flex: 1;
        padding: 10px;
        background-color: var(--bg-tertiary);
        color: var(--text-secondary);
        border: 1px solid var(--border-color);
        border-radius: 6px;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: all 0.2s;
    }

    .footer-btn:hover {
        background-color: var(--bg-primary);
        color: var(--text-primary);
        border-color: var(--accent);
    }

    @media (max-width: 768px) {
        .sidebar {
            position: fixed;
            left: -280px;
            height: 100%;
            z-index: 100;
            transition: left 0.3s ease;
        }

        .sidebar.open {
            left: 0;
        }
    }
</style>
