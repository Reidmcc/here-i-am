<script>
    import { createEventDispatcher, onMount } from 'svelte';
    import Modal from '../common/Modal.svelte';
    import { selectedEntityId, entities, entitiesMap } from '../../lib/stores/entities.js';
    import { showToast } from '../../lib/stores/app.js';
    import { escapeHtml, truncateText, formatRelativeTime } from '../../lib/utils.js';
    import * as api from '../../lib/api.js';

    const dispatch = createEventDispatcher();

    let activeTab = 'search';
    let searchQuery = '';
    let searchResults = [];
    let isSearching = false;
    let allMemories = [];
    let isLoadingAll = false;
    let memoryStats = null;
    let isLoadingStats = false;
    let expandedIds = new Set();

    $: currentEntity = $entities.find(e => e.index_name === $selectedEntityId);

    onMount(async () => {
        await loadStats();
    });

    function close() {
        dispatch('close');
    }

    async function handleSearch() {
        if (!searchQuery.trim() || !$selectedEntityId) return;

        isSearching = true;
        try {
            const response = await api.searchMemories({
                query: searchQuery,
                entity_id: $selectedEntityId,
                top_k: 20
            });
            searchResults = response.results || [];
        } catch (error) {
            showToast(`Search failed: ${error.message}`, 'error');
        } finally {
            isSearching = false;
        }
    }

    async function loadAllMemories() {
        if (!$selectedEntityId) return;

        isLoadingAll = true;
        try {
            const response = await api.getMemories($selectedEntityId, 100);
            allMemories = response.memories || [];
        } catch (error) {
            showToast(`Failed to load memories: ${error.message}`, 'error');
        } finally {
            isLoadingAll = false;
        }
    }

    async function loadStats() {
        if (!$selectedEntityId) return;

        isLoadingStats = true;
        try {
            const response = await api.getMemoryStats($selectedEntityId);
            memoryStats = response;
        } catch (error) {
            console.error('Failed to load stats:', error);
        } finally {
            isLoadingStats = false;
        }
    }

    async function deleteMemory(memoryId) {
        if (!confirm('Are you sure you want to delete this memory?')) return;

        try {
            await api.deleteMemory(memoryId);
            searchResults = searchResults.filter(m => m.id !== memoryId);
            allMemories = allMemories.filter(m => m.id !== memoryId);
            showToast('Memory deleted', 'success');
            await loadStats();
        } catch (error) {
            showToast(`Failed to delete: ${error.message}`, 'error');
        }
    }

    function toggleExpand(id) {
        if (expandedIds.has(id)) {
            expandedIds.delete(id);
        } else {
            expandedIds.add(id);
        }
        expandedIds = expandedIds;
    }

    function formatContent(content, id) {
        if (expandedIds.has(id)) {
            return escapeHtml(content);
        }
        return escapeHtml(truncateText(content, 200));
    }

    function formatSignificance(value) {
        return value !== undefined ? value.toFixed(3) : 'N/A';
    }

    function handleKeydown(event) {
        if (event.key === 'Enter') {
            handleSearch();
        }
    }

    function handleTabChange(tab) {
        activeTab = tab;
        if (tab === 'browse' && allMemories.length === 0) {
            loadAllMemories();
        }
        if (tab === 'stats') {
            loadStats();
        }
    }
</script>

<Modal title="Memories - {currentEntity?.label || 'Select Entity'}" size="xlarge" on:close={close}>
    <div class="memories-layout">
        <div class="memories-tabs">
            <button
                class="tab-btn"
                class:active={activeTab === 'search'}
                on:click={() => handleTabChange('search')}
            >
                Search
            </button>
            <button
                class="tab-btn"
                class:active={activeTab === 'browse'}
                on:click={() => handleTabChange('browse')}
            >
                Browse
            </button>
            <button
                class="tab-btn"
                class:active={activeTab === 'stats'}
                on:click={() => handleTabChange('stats')}
            >
                Statistics
            </button>
        </div>

        <div class="memories-content">
            {#if activeTab === 'search'}
                <div class="search-section">
                    <div class="search-input-row">
                        <input
                            type="text"
                            bind:value={searchQuery}
                            on:keydown={handleKeydown}
                            placeholder="Search memories semantically..."
                            class="search-input"
                        />
                        <button
                            class="search-btn"
                            on:click={handleSearch}
                            disabled={isSearching || !searchQuery.trim()}
                        >
                            {isSearching ? 'Searching...' : 'Search'}
                        </button>
                    </div>

                    {#if searchResults.length > 0}
                        <div class="results-list">
                            {#each searchResults as memory (memory.id)}
                                <div class="memory-card" class:expanded={expandedIds.has(memory.id)}>
                                    <div class="memory-header">
                                        <span class="memory-role" class:human={memory.role === 'human'}>
                                            {memory.role}
                                        </span>
                                        <span class="memory-date">
                                            {formatRelativeTime(memory.timestamp || memory.created_at)}
                                        </span>
                                        <button
                                            class="delete-btn"
                                            on:click={() => deleteMemory(memory.id)}
                                            title="Delete memory"
                                        >
                                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                                <path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"></path>
                                            </svg>
                                        </button>
                                    </div>
                                    <div
                                        class="memory-content"
                                        on:click={() => toggleExpand(memory.id)}
                                        role="button"
                                        tabindex="0"
                                        on:keydown={(e) => e.key === 'Enter' && toggleExpand(memory.id)}
                                    >
                                        {@html formatContent(memory.content, memory.id)}
                                    </div>
                                    <div class="memory-meta">
                                        <span>Similarity: {(memory.score || memory.similarity || 0).toFixed(4)}</span>
                                        <span>Significance: {formatSignificance(memory.significance)}</span>
                                        <span>Retrieved: {memory.times_retrieved || 0}x</span>
                                    </div>
                                </div>
                            {/each}
                        </div>
                    {:else if !isSearching && searchQuery}
                        <p class="no-results">No memories found matching your query.</p>
                    {/if}
                </div>
            {/if}

            {#if activeTab === 'browse'}
                {#if isLoadingAll}
                    <p class="loading-text">Loading memories...</p>
                {:else if allMemories.length > 0}
                    <div class="results-list">
                        {#each allMemories as memory (memory.id)}
                            <div class="memory-card" class:expanded={expandedIds.has(memory.id)}>
                                <div class="memory-header">
                                    <span class="memory-role" class:human={memory.role === 'human'}>
                                        {memory.role}
                                    </span>
                                    <span class="memory-date">
                                        {formatRelativeTime(memory.timestamp || memory.created_at)}
                                    </span>
                                    <button
                                        class="delete-btn"
                                        on:click={() => deleteMemory(memory.id)}
                                        title="Delete memory"
                                    >
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                            <path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"></path>
                                        </svg>
                                    </button>
                                </div>
                                <div
                                    class="memory-content"
                                    on:click={() => toggleExpand(memory.id)}
                                    role="button"
                                    tabindex="0"
                                    on:keydown={(e) => e.key === 'Enter' && toggleExpand(memory.id)}
                                >
                                    {@html formatContent(memory.content, memory.id)}
                                </div>
                                <div class="memory-meta">
                                    <span>Significance: {formatSignificance(memory.significance)}</span>
                                    <span>Retrieved: {memory.times_retrieved || 0}x</span>
                                </div>
                            </div>
                        {/each}
                    </div>
                {:else}
                    <p class="no-results">No memories stored for this entity yet.</p>
                {/if}
            {/if}

            {#if activeTab === 'stats'}
                {#if isLoadingStats}
                    <p class="loading-text">Loading statistics...</p>
                {:else if memoryStats}
                    <div class="stats-grid">
                        <div class="stat-card">
                            <div class="stat-value">{memoryStats.total_memories || 0}</div>
                            <div class="stat-label">Total Memories</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-value">{memoryStats.human_count || 0}</div>
                            <div class="stat-label">Human Messages</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-value">{memoryStats.assistant_count || 0}</div>
                            <div class="stat-label">Assistant Messages</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-value">{memoryStats.total_retrievals || 0}</div>
                            <div class="stat-label">Total Retrievals</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-value">{(memoryStats.avg_significance || 0).toFixed(3)}</div>
                            <div class="stat-label">Avg Significance</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-value">{(memoryStats.avg_retrievals || 0).toFixed(1)}</div>
                            <div class="stat-label">Avg Retrievals</div>
                        </div>
                    </div>
                {:else}
                    <p class="no-results">No statistics available.</p>
                {/if}
            {/if}
        </div>
    </div>

    <svelte:fragment slot="footer">
        <button class="btn btn-secondary" on:click={close}>Close</button>
    </svelte:fragment>
</Modal>

<style>
    .memories-layout {
        display: flex;
        gap: 24px;
        min-height: 500px;
    }

    .memories-tabs {
        display: flex;
        flex-direction: column;
        gap: 4px;
        min-width: 120px;
        border-right: 1px solid var(--border-color);
        padding-right: 24px;
    }

    .tab-btn {
        padding: 10px 16px;
        background: transparent;
        border: none;
        border-radius: 6px;
        cursor: pointer;
        text-align: left;
        color: var(--text-secondary);
        font-size: 0.95rem;
        transition: all 0.2s;
    }

    .tab-btn:hover {
        background-color: var(--bg-tertiary);
        color: var(--text-primary);
    }

    .tab-btn.active {
        background-color: var(--accent-subtle);
        color: var(--accent);
        font-weight: 500;
    }

    .memories-content {
        flex: 1;
        overflow-y: auto;
    }

    .search-section {
        display: flex;
        flex-direction: column;
        gap: 16px;
    }

    .search-input-row {
        display: flex;
        gap: 12px;
    }

    .search-input {
        flex: 1;
        padding: 12px 16px;
        background-color: var(--bg-tertiary);
        border: 1px solid var(--border-color);
        border-radius: 8px;
        color: var(--text-primary);
        font-size: 0.95rem;
    }

    .search-input:focus {
        outline: none;
        border-color: var(--accent);
    }

    .search-btn {
        padding: 12px 24px;
        background-color: var(--accent);
        color: white;
        border: none;
        border-radius: 8px;
        cursor: pointer;
        font-size: 0.95rem;
        font-weight: 500;
        transition: background-color 0.2s;
    }

    .search-btn:hover:not(:disabled) {
        background-color: var(--accent-hover);
    }

    .search-btn:disabled {
        opacity: 0.5;
        cursor: not-allowed;
    }

    .results-list {
        display: flex;
        flex-direction: column;
        gap: 12px;
    }

    .memory-card {
        padding: 16px;
        background-color: var(--bg-tertiary);
        border: 1px solid var(--border-color);
        border-radius: 8px;
    }

    .memory-header {
        display: flex;
        align-items: center;
        gap: 12px;
        margin-bottom: 10px;
    }

    .memory-role {
        padding: 3px 8px;
        border-radius: 4px;
        font-size: 0.75rem;
        font-weight: 600;
        text-transform: uppercase;
        background-color: var(--success-subtle);
        color: var(--success);
    }

    .memory-role.human {
        background-color: var(--accent-subtle);
        color: var(--accent);
    }

    .memory-date {
        font-size: 0.8rem;
        color: var(--text-muted);
        margin-left: auto;
    }

    .delete-btn {
        padding: 4px;
        background: transparent;
        border: none;
        cursor: pointer;
        color: var(--text-muted);
        border-radius: 4px;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: all 0.2s;
    }

    .delete-btn:hover {
        background-color: var(--danger-subtle);
        color: var(--danger);
    }

    .memory-content {
        font-size: 0.9rem;
        color: var(--text-secondary);
        line-height: 1.5;
        cursor: pointer;
        white-space: pre-wrap;
        word-break: break-word;
    }

    .memory-meta {
        display: flex;
        gap: 16px;
        margin-top: 12px;
        padding-top: 12px;
        border-top: 1px solid var(--border-color);
        font-size: 0.8rem;
        color: var(--text-muted);
    }

    .stats-grid {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 16px;
    }

    .stat-card {
        padding: 24px;
        background-color: var(--bg-tertiary);
        border: 1px solid var(--border-color);
        border-radius: 12px;
        text-align: center;
    }

    .stat-value {
        font-size: 2rem;
        font-weight: 700;
        color: var(--accent);
        margin-bottom: 8px;
    }

    .stat-label {
        font-size: 0.85rem;
        color: var(--text-muted);
    }

    .loading-text,
    .no-results {
        color: var(--text-muted);
        text-align: center;
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
