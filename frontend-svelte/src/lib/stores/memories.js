/**
 * Memories Store - Memory state management
 */
import { writable, derived } from 'svelte/store';

// Retrieved memories organized by conversation ID
// { conversationId: [memory1, memory2, ...] }
export const retrievedMemories = writable({});

// Retrieved memories organized by entity (for multi-entity)
export const retrievedMemoriesByEntity = writable({});

// Expanded memory IDs (for showing full content)
export const expandedMemoryIds = writable(new Set());

// Memory search results
export const memorySearchResults = writable([]);

// Memory statistics
export const memoryStats = writable(null);

// Orphaned records
export const orphanedRecords = writable([]);

// Memories panel expanded state
export const memoriesPanelExpanded = writable(false);

// Helper to add memories to a conversation
export function addMemories(newMemories, conversationId, entityId = null) {
    if (!conversationId) return;

    retrievedMemories.update(byConv => {
        const existing = byConv[conversationId] || [];
        const existingIds = new Set(existing.map(m => m.id || m.message_id));
        const unique = newMemories.filter(m => !existingIds.has(m.id || m.message_id));
        return {
            ...byConv,
            [conversationId]: [...existing, ...unique]
        };
    });

    if (entityId) {
        retrievedMemoriesByEntity.update(byEntity => {
            const existing = byEntity[entityId] || [];
            const existingIds = new Set(existing.map(m => m.id || m.message_id));
            const unique = newMemories.filter(m => !existingIds.has(m.id || m.message_id));
            return {
                ...byEntity,
                [entityId]: [...existing, ...unique]
            };
        });
    }
}

// Clear memories for a specific conversation
export function clearMemoriesForConversation(conversationId) {
    if (!conversationId) return;
    retrievedMemories.update(byConv => {
        const { [conversationId]: removed, ...rest } = byConv;
        return rest;
    });
}

// Helper to toggle memory expanded state
export function toggleMemoryExpanded(memoryId) {
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

// Reset memories state
export function resetMemoriesState() {
    retrievedMemories.set({});
    retrievedMemoriesByEntity.set({});
    expandedMemoryIds.set(new Set());
}

// Reset search state
export function resetSearchState() {
    memorySearchResults.set([]);
}
