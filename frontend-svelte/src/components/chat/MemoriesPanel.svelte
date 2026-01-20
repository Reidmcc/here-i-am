<script>
    import { retrievedMemories, expandedMemoryIds, clearMemoriesForConversation } from '../../lib/stores/memories.js';
    import { currentConversationId } from '../../lib/stores/conversations.js';
    import { selectedEntityId } from '../../lib/stores/entities.js';
    import { escapeHtml, truncateText, formatRelativeTime } from '../../lib/utils.js';

    let isCollapsed = false;

    $: currentMemories = $currentConversationId
        ? ($retrievedMemories[$currentConversationId] || [])
        : [];

    function toggleCollapse() {
        isCollapsed = !isCollapsed;
    }

    function toggleMemory(memoryId) {
        expandedMemoryIds.update(ids => {
            const newIds = new Set(ids);
            if (newIds.has(memoryId)) {
                newIds.delete(memoryId);
            } else {
                newIds.add(memoryId);
            }
            return newIds;
        });
    }

    function isExpanded(memoryId) {
        return $expandedMemoryIds.has(memoryId);
    }

    function clearMemories() {
        if ($currentConversationId) {
            clearMemoriesForConversation($currentConversationId);
        }
    }

    function formatMemoryContent(content, memoryId) {
        if (isExpanded(memoryId)) {
            return escapeHtml(content);
        }
        return escapeHtml(truncateText(content, 150));
    }

    function formatSignificance(memory) {
        if (memory.significance !== undefined) {
            return memory.significance.toFixed(2);
        }
        return 'N/A';
    }
</script>

{#if currentMemories.length > 0}
    <div class="memories-panel" class:collapsed={isCollapsed}>
        <div class="panel-header" on:click={toggleCollapse} role="button" tabindex="0" on:keydown={(e) => e.key === 'Enter' && toggleCollapse()}>
            <div class="header-left">
                <svg
                    class="collapse-icon"
                    class:rotated={!isCollapsed}
                    width="16"
                    height="16"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                >
                    <polyline points="9 18 15 12 9 6"></polyline>
                </svg>
                <span class="panel-title">Retrieved Memories</span>
                <span class="memory-count">({currentMemories.length})</span>
            </div>
            {#if !isCollapsed}
                <button
                    class="clear-btn"
                    on:click|stopPropagation={clearMemories}
                    title="Clear memories display"
                >
                    Clear
                </button>
            {/if}
        </div>

        {#if !isCollapsed}
            <div class="memories-list">
                {#each currentMemories as memory (memory.id)}
                    <div
                        class="memory-item"
                        class:expanded={isExpanded(memory.id)}
                        on:click={() => toggleMemory(memory.id)}
                        role="button"
                        tabindex="0"
                        on:keydown={(e) => e.key === 'Enter' && toggleMemory(memory.id)}
                    >
                        <div class="memory-header">
                            <span class="memory-role" class:human={memory.role === 'human'} class:assistant={memory.role === 'assistant'}>
                                {memory.role}
                            </span>
                            <span class="memory-date">
                                {formatRelativeTime(memory.timestamp || memory.created_at)}
                            </span>
                        </div>
                        <div class="memory-content">
                            {@html formatMemoryContent(memory.content, memory.id)}
                        </div>
                        <div class="memory-meta">
                            <span class="meta-item" title="Similarity score">
                                Sim: {(memory.similarity || memory.score || 0).toFixed(3)}
                            </span>
                            <span class="meta-item" title="Significance score">
                                Sig: {formatSignificance(memory)}
                            </span>
                            <span class="meta-item" title="Times retrieved">
                                Retrieved: {memory.times_retrieved || 0}x
                            </span>
                        </div>
                    </div>
                {/each}
            </div>
        {/if}
    </div>
{/if}

<style>
    .memories-panel {
        background-color: var(--bg-secondary);
        border-bottom: 1px solid var(--border-color);
        max-height: 300px;
        overflow: hidden;
        display: flex;
        flex-direction: column;
    }

    .memories-panel.collapsed {
        max-height: none;
    }

    .panel-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 12px 16px;
        cursor: pointer;
        user-select: none;
        background-color: var(--bg-tertiary);
        border-bottom: 1px solid var(--border-color);
    }

    .panel-header:hover {
        background-color: var(--bg-primary);
    }

    .header-left {
        display: flex;
        align-items: center;
        gap: 8px;
    }

    .collapse-icon {
        color: var(--text-secondary);
        transition: transform 0.2s;
    }

    .collapse-icon.rotated {
        transform: rotate(90deg);
    }

    .panel-title {
        font-size: 0.9rem;
        font-weight: 600;
        color: var(--text-primary);
    }

    .memory-count {
        font-size: 0.8rem;
        color: var(--text-muted);
    }

    .clear-btn {
        padding: 4px 12px;
        background-color: transparent;
        border: 1px solid var(--border-color);
        border-radius: 4px;
        cursor: pointer;
        font-size: 0.8rem;
        color: var(--text-secondary);
        transition: all 0.2s;
    }

    .clear-btn:hover {
        background-color: var(--bg-secondary);
        color: var(--text-primary);
    }

    .memories-list {
        overflow-y: auto;
        padding: 12px;
        display: flex;
        flex-direction: column;
        gap: 8px;
    }

    .memory-item {
        padding: 10px 12px;
        background-color: var(--bg-tertiary);
        border: 1px solid var(--border-color);
        border-radius: 6px;
        cursor: pointer;
        transition: all 0.2s;
    }

    .memory-item:hover {
        border-color: var(--accent);
    }

    .memory-item.expanded {
        background-color: var(--bg-primary);
    }

    .memory-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 6px;
    }

    .memory-role {
        font-size: 0.75rem;
        font-weight: 600;
        text-transform: uppercase;
        padding: 2px 6px;
        border-radius: 3px;
        background-color: var(--bg-secondary);
    }

    .memory-role.human {
        color: var(--accent);
    }

    .memory-role.assistant {
        color: var(--success);
    }

    .memory-date {
        font-size: 0.75rem;
        color: var(--text-muted);
    }

    .memory-content {
        font-size: 0.85rem;
        color: var(--text-secondary);
        line-height: 1.4;
        white-space: pre-wrap;
        word-break: break-word;
    }

    .memory-meta {
        display: flex;
        gap: 12px;
        margin-top: 8px;
        padding-top: 8px;
        border-top: 1px solid var(--border-color);
    }

    .meta-item {
        font-size: 0.7rem;
        color: var(--text-muted);
    }
</style>
