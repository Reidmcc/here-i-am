/**
 * Conversations Module
 * Handles conversation listing, creation, loading, and management
 */

import { state, resetMemoryState, saveEntitySystemPromptsToStorage } from './state.js';
import { showToast, escapeHtml, escapeForInlineHandler } from './utils.js';
import { showModal, hideModal, closeAllDropdowns } from './modals.js';
import { getEntityLabel, updateModelSelectorMultiEntityState } from './entities.js';

// Reference to global API client
const api = window.api;

// Element references
let elements = {};

// Callbacks
let callbacks = {
    onConversationLoad: null,
    onConversationCreated: null,
    renderMessages: null,
    updateHeader: null,
    updateMemoriesPanel: null,
    clearMessages: null,
};

/**
 * Set element references
 * @param {Object} els - Element references
 */
export function setElements(els) {
    elements = els;
}

/**
 * Set callback functions
 * @param {Object} cbs - Callback functions
 */
export function setCallbacks(cbs) {
    callbacks = { ...callbacks, ...cbs };
}

/**
 * Load conversations from the API
 */
export async function loadConversations() {
    const requestId = ++state.loadConversationsRequestId;

    try {
        const conversations = await api.listConversations(50, 0, state.selectedEntityId);

        // Ignore stale responses
        if (requestId !== state.loadConversationsRequestId) {
            return;
        }

        state.conversations = conversations;

        // If we just created a conversation, ensure it's in the list
        if (state.lastCreatedConversation) {
            const conv = state.lastCreatedConversation;
            state.lastCreatedConversation = null;

            // Check if it should be in this list
            const shouldBeHere =
                (conv.entity_id === state.selectedEntityId) ||
                (conv.conversation_type === 'multi_entity' && state.selectedEntityId === 'multi-entity');

            const found = conversations.find(c => c.id === conv.id);
            if (shouldBeHere && !found) {
                // Add to beginning of list
                state.conversations.unshift(conv);
            }
        }

        renderConversationList();

        // If we have a current conversation, make sure it's still valid
        if (state.currentConversationId) {
            const current = state.conversations.find(c => c.id === state.currentConversationId);
            if (!current) {
                // Current conversation no longer in list (entity switched)
                state.currentConversationId = null;
                if (callbacks.clearMessages) {
                    callbacks.clearMessages();
                }
            }
        }

    } catch (error) {
        if (requestId === state.loadConversationsRequestId) {
            console.error('Failed to load conversations:', error);
            showToast('Failed to load conversations', 'error');
        }
    }
}

/**
 * Render the conversation list in the sidebar
 */
export function renderConversationList() {
    if (!elements.conversationList) return;

    if (state.conversations.length === 0) {
        elements.conversationList.innerHTML = `
            <div class="no-conversations">
                No conversations yet. Start a new one!
            </div>
        `;
        return;
    }

    elements.conversationList.innerHTML = state.conversations.map(conv => {
        const isActive = conv.id === state.currentConversationId ? 'active' : '';
        const isMulti = conv.conversation_type === 'multi_entity';

        // Build entity/model info
        let entityInfo = '';
        if (isMulti && conv.entities && conv.entities.length > 0) {
            const entityLabels = conv.entities.map(e => e.label).join(' & ');
            entityInfo = `<span class="conv-entity-label multi">${escapeHtml(entityLabels)}</span>`;
        } else if (state.entities.length > 1 && conv.entity_id) {
            const label = getEntityLabel(conv.entity_id);
            entityInfo = `<span class="conv-entity-label">${escapeHtml(label)}</span>`;
        }

        return `
            <div class="conversation-item ${isActive}" data-id="${conv.id}">
                <div class="conversation-item-content" onclick="app.loadConversation('${conv.id}')">
                    <div class="conversation-item-title">${escapeHtml(conv.title || 'Untitled')}</div>
                    <div class="conversation-item-meta">
                        ${entityInfo}
                        <span class="conv-date">${new Date(conv.created_at).toLocaleDateString()}</span>
                    </div>
                </div>
                <button class="conversation-menu-btn" onclick="event.stopPropagation(); app.toggleConversationDropdown('${conv.id}')">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                        <circle cx="12" cy="5" r="2"/>
                        <circle cx="12" cy="12" r="2"/>
                        <circle cx="12" cy="19" r="2"/>
                    </svg>
                </button>
                <div class="conversation-dropdown" data-id="${conv.id}">
                    <button class="conversation-dropdown-item" onclick="app.showRenameModalForConversation('${conv.id}', '${escapeForInlineHandler(conv.title || '')}')">Rename</button>
                    <button class="conversation-dropdown-item" onclick="app.showArchiveModalForConversation('${conv.id}', '${escapeForInlineHandler(conv.title || '')}')">Archive</button>
                </div>
            </div>
        `;
    }).join('');
}

/**
 * Toggle conversation dropdown menu
 * @param {string} conversationId - Conversation ID
 */
export function toggleConversationDropdown(conversationId) {
    // Close all other dropdowns first
    document.querySelectorAll('.conversation-dropdown.active').forEach(dropdown => {
        if (dropdown.dataset.id !== conversationId) {
            dropdown.classList.remove('active');
        }
    });

    // Toggle this dropdown
    const dropdown = document.querySelector(`.conversation-dropdown[data-id="${conversationId}"]`);
    if (dropdown) {
        dropdown.classList.toggle('active');
    }
}

/**
 * Create a new conversation
 * @param {boolean} skipEntityModal - Whether to skip the multi-entity modal
 */
export async function createNewConversation(skipEntityModal = false) {
    if (state.isLoading) {
        showToast('Please wait for the current operation to complete', 'warning');
        return;
    }

    // For multi-entity mode, show entity selection modal first
    if (state.isMultiEntityMode && !skipEntityModal) {
        state.pendingMultiEntityAction = 'createConversation';
        const { showMultiEntityModal } = await import('./entities.js');
        showMultiEntityModal();
        return;
    }

    try {
        let conversationData = {};

        // Multi-entity conversation
        if (state.isMultiEntityMode && state.currentConversationEntities.length >= 2) {
            const entityIds = state.currentConversationEntities.map(e => e.index_name);
            // Build per-entity system prompts
            const entitySystemPrompts = {};
            for (const entity of state.currentConversationEntities) {
                if (state.entitySystemPrompts[entity.index_name] !== undefined) {
                    entitySystemPrompts[entity.index_name] = state.entitySystemPrompts[entity.index_name];
                }
            }

            conversationData = {
                conversation_type: 'multi_entity',
                entity_ids: entityIds,
                entity_system_prompts: Object.keys(entitySystemPrompts).length > 0 ? entitySystemPrompts : null,
                system_prompt: state.settings.systemPrompt,
            };
        } else {
            // Single-entity conversation
            if (!state.selectedEntityId || state.selectedEntityId === 'multi-entity') {
                showToast('Please select an entity', 'error');
                return;
            }

            // Get the current entity-specific system prompt
            let systemPrompt = state.settings.systemPrompt;
            if (state.selectedEntityId && state.entitySystemPrompts[state.selectedEntityId] !== undefined) {
                systemPrompt = state.entitySystemPrompts[state.selectedEntityId];
            }

            conversationData = {
                conversation_type: state.settings.conversationType,
                system_prompt: systemPrompt,
                llm_model: state.settings.model,
                entity_id: state.selectedEntityId,
            };
        }

        const conversation = await api.createConversation(conversationData);
        state.lastCreatedConversation = conversation;

        // Store entities for multi-entity
        if (conversation.entities && conversation.entities.length > 0) {
            // Normalize API entities (entity_id -> index_name) for frontend consistency
            state.currentConversationEntities = conversation.entities.map(e => ({
                ...e,
                index_name: e.index_name || e.entity_id,
            }));
            state.isMultiEntityMode = true;
        }

        // Add to list and select it
        state.conversations.unshift(conversation);
        state.currentConversationId = conversation.id;

        renderConversationList();

        // Clear messages and reset memory state
        if (callbacks.clearMessages) {
            callbacks.clearMessages();
        }
        resetMemoryState();
        if (callbacks.updateMemoriesPanel) {
            callbacks.updateMemoriesPanel();
        }

        // Update header
        if (callbacks.updateHeader) {
            callbacks.updateHeader(conversation);
        }

        // Trigger callback
        if (callbacks.onConversationCreated) {
            callbacks.onConversationCreated(conversation);
        }

    } catch (error) {
        showToast('Failed to create conversation', 'error');
        console.error('Failed to create conversation:', error);
    }
}

/**
 * Load a specific conversation
 * @param {string} id - Conversation ID
 */
export async function loadConversation(id) {
    if (state.isLoading) {
        showToast('Please wait for the current operation to complete', 'warning');
        return;
    }

    try {
        state.currentConversationId = id;
        resetMemoryState();

        // Get conversation details and messages
        const [conversation, messages] = await Promise.all([
            api.getConversation(id),
            api.getConversationMessages(id)
        ]);

        // Update multi-entity state
        if (conversation.conversation_type === 'multi_entity' && conversation.entities) {
            state.isMultiEntityMode = true;
            // Normalize API entities (entity_id -> index_name) for frontend consistency
            state.currentConversationEntities = conversation.entities.map(e => ({
                ...e,
                index_name: e.index_name || e.entity_id,
            }));
        } else {
            // Don't override isMultiEntityMode if we're in multi-entity mode viewing a multi-entity conversation
            if (state.selectedEntityId !== 'multi-entity') {
                state.isMultiEntityMode = false;
                state.currentConversationEntities = [];
            } else if (conversation.conversation_type !== 'multi_entity') {
                // Viewing single-entity conversation while in multi-entity mode
                state.isMultiEntityMode = true;
                state.currentConversationEntities = [];
            }
        }

        // Update model selector state (disabled in multi-entity mode)
        updateModelSelectorMultiEntityState();

        // Clear and render messages
        if (callbacks.clearMessages) {
            callbacks.clearMessages();
        }

        // Find the latest assistant message ID
        let latestAssistantId = null;
        for (let i = messages.length - 1; i >= 0; i--) {
            if (messages[i].role === 'assistant') {
                latestAssistantId = messages[i].id;
                break;
            }
        }

        // Render messages
        if (callbacks.renderMessages) {
            callbacks.renderMessages(messages, latestAssistantId);
        }

        // Update header
        if (callbacks.updateHeader) {
            callbacks.updateHeader(conversation);
        }

        // Update memories panel
        if (callbacks.updateMemoriesPanel) {
            callbacks.updateMemoriesPanel();
        }

        // Update active state in list
        renderConversationList();

        // Trigger callback
        if (callbacks.onConversationLoad) {
            callbacks.onConversationLoad(conversation, messages);
        }

    } catch (error) {
        showToast('Failed to load conversation', 'error');
        console.error('Failed to load conversation:', error);
    }
}

// =========================================================================
// Archive/Rename/Delete Functions
// =========================================================================

/**
 * Show archive modal for a conversation
 * @param {string} conversationId - Conversation ID
 * @param {string} conversationTitle - Conversation title
 */
export function showArchiveModalForConversation(conversationId, conversationTitle) {
    state.pendingArchiveId = conversationId;

    const modalBody = document.querySelector('#archive-modal .modal-body');
    if (modalBody) {
        modalBody.innerHTML = `
            <p><strong>${escapeHtml(conversationTitle || 'Untitled')}</strong></p>
            <p>This conversation will be hidden from the main list and its memories will be excluded from retrieval.</p>
            <p>You can restore it later from the Archived section.</p>
        `;
    }

    closeAllDropdowns();
    showModal('archiveModal');
}

/**
 * Archive the pending conversation
 */
export async function archiveConversation() {
    const conversationId = state.pendingArchiveId || state.currentConversationId;
    if (!conversationId) return;

    try {
        await api.archiveConversation(conversationId);

        // Remove from list
        state.conversations = state.conversations.filter(c => c.id !== conversationId);

        // Clear current view if we archived the active conversation
        if (conversationId === state.currentConversationId) {
            state.currentConversationId = null;
            resetMemoryState();
            if (callbacks.clearMessages) {
                callbacks.clearMessages();
            }
            if (callbacks.updateMemoriesPanel) {
                callbacks.updateMemoriesPanel();
            }
            if (elements.conversationTitle) {
                elements.conversationTitle.textContent = 'Select a conversation';
            }
            if (elements.conversationMeta) {
                elements.conversationMeta.textContent = '';
            }
        }

        renderConversationList();
        hideModal('archiveModal');
        state.pendingArchiveId = null;
        showToast('Conversation archived', 'success');
    } catch (error) {
        showToast('Failed to archive conversation', 'error');
        console.error('Failed to archive conversation:', error);
    }
}

/**
 * Show rename modal for a conversation
 * @param {string} conversationId - Conversation ID
 * @param {string} conversationTitle - Current title
 */
export function showRenameModalForConversation(conversationId, conversationTitle) {
    state.pendingRenameId = conversationId;
    if (elements.renameInput) {
        elements.renameInput.value = conversationTitle || '';
    }
    closeAllDropdowns();
    showModal('renameModal');
    setTimeout(() => elements.renameInput?.focus(), 50);
}

/**
 * Rename the pending conversation
 */
export async function renameConversation() {
    const conversationId = state.pendingRenameId;
    if (!conversationId) return;

    const newTitle = elements.renameInput?.value.trim();
    if (!newTitle) {
        showToast('Please enter a title', 'error');
        return;
    }

    try {
        await api.updateConversation(conversationId, { title: newTitle });

        // Update in local list
        const conv = state.conversations.find(c => c.id === conversationId);
        if (conv) {
            conv.title = newTitle;
        }

        // Update header if this is the current conversation
        if (conversationId === state.currentConversationId && elements.conversationTitle) {
            elements.conversationTitle.textContent = newTitle;
        }

        renderConversationList();
        hideModal('renameModal');
        state.pendingRenameId = null;
        showToast('Conversation renamed', 'success');
    } catch (error) {
        showToast('Failed to rename conversation', 'error');
        console.error('Failed to rename conversation:', error);
    }
}

/**
 * Show delete modal
 * @param {string} conversationId - Conversation ID
 * @param {string} conversationTitle - Conversation title
 */
export function showDeleteModal(conversationId, conversationTitle) {
    state.pendingDeleteId = conversationId;
    if (elements.deleteConversationTitle) {
        elements.deleteConversationTitle.textContent = conversationTitle;
    }
    showModal('deleteModal');
}

/**
 * Delete the pending conversation
 */
export async function deleteConversation() {
    const conversationId = state.pendingDeleteId;
    if (!conversationId) return;

    try {
        await api.deleteConversation(conversationId);
        await loadArchivedConversations();
        hideModal('deleteModal');
        state.pendingDeleteId = null;
        showToast('Conversation permanently deleted', 'success');
    } catch (error) {
        showToast('Failed to delete conversation', 'error');
        console.error('Failed to delete conversation:', error);
    }
}

// =========================================================================
// Archived Conversations
// =========================================================================

/**
 * Show archived conversations modal
 */
export async function showArchivedModal() {
    showModal('archivedModal');
    await loadArchivedConversations();
}

/**
 * Load archived conversations list
 */
export async function loadArchivedConversations() {
    try {
        const conversations = await api.listArchivedConversations(50, 0, state.selectedEntityId);

        if (!elements.archivedList) return;

        if (conversations.length === 0) {
            elements.archivedList.innerHTML = `
                <div class="archived-empty">
                    <p>No archived conversations</p>
                </div>
            `;
            return;
        }

        elements.archivedList.innerHTML = conversations.map(conv => `
            <div class="archived-item" data-id="${conv.id}">
                <div class="archived-item-info">
                    <div class="archived-item-title">${escapeHtml(conv.title || 'Untitled')}</div>
                    <div class="archived-item-meta">
                        ${conv.message_count} messages &middot; ${new Date(conv.created_at).toLocaleDateString()}
                    </div>
                </div>
                <div class="archived-item-actions">
                    <button class="unarchive-btn" onclick="app.unarchiveConversation('${conv.id}')">Restore</button>
                    <button class="delete-btn" onclick="app.showDeleteModal('${conv.id}', '${escapeForInlineHandler(conv.title || 'Untitled')}')">Delete</button>
                </div>
            </div>
        `).join('');
    } catch (error) {
        if (elements.archivedList) {
            elements.archivedList.innerHTML = `
                <div class="archived-empty">
                    <p>Failed to load archived conversations</p>
                </div>
            `;
        }
        console.error('Failed to load archived conversations:', error);
    }
}

/**
 * Unarchive a conversation
 * @param {string} conversationId - Conversation ID
 */
export async function unarchiveConversation(conversationId) {
    try {
        await api.unarchiveConversation(conversationId);
        await loadArchivedConversations();
        await loadConversations();
        showToast('Conversation restored', 'success');
    } catch (error) {
        showToast('Failed to restore conversation', 'error');
        console.error('Failed to unarchive conversation:', error);
    }
}

// =========================================================================
// Export
// =========================================================================

/**
 * Export current conversation to JSON
 */
export async function exportConversation() {
    if (!state.currentConversationId) return;

    try {
        const data = await api.exportConversation(state.currentConversationId);

        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);

        const a = document.createElement('a');
        a.href = url;
        a.download = `conversation-${state.currentConversationId}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);

        showToast('Conversation exported', 'success');
    } catch (error) {
        showToast('Failed to export conversation', 'error');
        console.error('Failed to export conversation:', error);
    }
}
