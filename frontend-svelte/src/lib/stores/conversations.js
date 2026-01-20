/**
 * Conversations Store - Conversation management state
 */
import { writable, derived, get } from 'svelte/store';

// List of all conversations
export const conversations = writable([]);

// Currently selected conversation ID
export const currentConversationId = writable(null);

// Current conversation object (loaded from API)
export const currentConversation = writable(null);

// Archived conversations
export const archivedConversations = writable([]);

// Loading state for conversations
export const conversationsLoading = writable(false);

// Request ID for conversation loading (to handle stale responses)
let loadConversationsRequestId = 0;

export function getNextRequestId() {
    return ++loadConversationsRequestId;
}

export function isValidRequestId(id) {
    return id === loadConversationsRequestId;
}

// Derived store: current conversation title
export const currentConversationTitle = derived(
    currentConversation,
    ($currentConversation) => $currentConversation?.title || 'New Conversation'
);

// Derived store: is multi-entity conversation
export const isMultiEntityConversation = derived(
    currentConversation,
    ($currentConversation) => $currentConversation?.conversation_type === 'multi_entity'
);

// Helper to update a conversation in the list
export function updateConversationInList(id, updates) {
    conversations.update(convs => {
        return convs.map(c => {
            if (c.id === id) {
                return { ...c, ...updates };
            }
            return c;
        });
    });
}

// Helper to remove a conversation from the list
export function removeConversationFromList(id) {
    conversations.update(convs => convs.filter(c => c.id !== id));
}

// Helper to add a conversation to the list
export function addConversationToList(conversation) {
    conversations.update(convs => [conversation, ...convs]);
}

// Reset conversation state
export function resetConversationState() {
    currentConversationId.set(null);
    currentConversation.set(null);
}
