/**
 * Messages Store - Message state management
 */
import { writable, derived } from 'svelte/store';

// Messages for the current conversation
export const messages = writable([]);

// Streaming message content (while response is being generated)
export const streamingContent = writable('');

// Streaming message metadata
export const streamingMessage = writable(null);

// Tool execution state during streaming
export const streamingTools = writable([]);

// Message currently being edited
export const editingMessageId = writable(null);

// Pending message content (for multi-entity responder selection)
export const pendingMessageContent = writable('');

// Pending message attachments
export const pendingMessageAttachments = writable({ images: [], files: [] });

// Responder selector mode
export const responderSelectorMode = writable(null);

// Derived store: is streaming
export const isStreaming = derived(
    streamingMessage,
    ($streamingMessage) => $streamingMessage !== null
);

// Helper to add a message
export function addMessage(message) {
    messages.update(msgs => [...msgs, message]);
}

// Helper to update a message
export function updateMessageContent(id, content) {
    messages.update(msgs => msgs.map(m => {
        if (m.id === id) {
            return { ...m, content };
        }
        return m;
    }));
}

// Helper to remove a message
export function removeMessage(id) {
    messages.update(msgs => msgs.filter(m => m.id !== id));
}

// Helper to append to streaming content
export function appendStreamingContent(token) {
    streamingContent.update(content => content + token);
}

// Helper to add tool to streaming
export function addStreamingTool(tool) {
    streamingTools.update(tools => [...tools, tool]);
}

// Helper to update streaming tool result
export function updateStreamingToolResult(toolUseId, result) {
    streamingTools.update(tools => tools.map(t => {
        if (t.id === toolUseId) {
            return { ...t, result, status: result.error ? 'error' : 'success' };
        }
        return t;
    }));
}

// Start streaming a new message
export function startStreaming(messageData = {}) {
    streamingMessage.set({
        role: 'assistant',
        ...messageData,
        isStreaming: true
    });
    streamingContent.set('');
    streamingTools.set([]);
}

// Stop streaming
export function stopStreaming() {
    streamingMessage.set(null);
    streamingContent.set('');
    streamingTools.set([]);
}

// Reset messages state
export function resetMessagesState() {
    messages.set([]);
    streamingContent.set('');
    streamingMessage.set(null);
    streamingTools.set([]);
    editingMessageId.set(null);
    pendingMessageContent.set('');
    pendingMessageAttachments.set({ images: [], files: [] });
    responderSelectorMode.set(null);
}

// Reset pending message state
export function resetPendingMessage() {
    pendingMessageContent.set('');
    pendingMessageAttachments.set({ images: [], files: [] });
    responderSelectorMode.set(null);
}
