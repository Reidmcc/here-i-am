/**
 * Chat Module
 * Handles sending messages, streaming responses, regeneration, and editing
 */

import { state } from './state.js';
import { showToast, renderMarkdown, escapeHtml } from './utils.js';
import {
    addMessage, addToolMessage, createStreamingMessage,
    removeRegenerateButtons, updateAssistantMessageActions,
    scrollToBottom
} from './messages.js';
import { hasAttachments, getAttachmentsForRequest, clearAttachments, buildDisplayContentWithAttachments } from './attachments.js';
import { handleMemoryUpdate } from './memories.js';

// Element references
let elements = {};

// Callbacks to parent app
let callbacks = {
    onConversationUpdate: null,
    onLoadConversation: null,
    renderConversationList: null,
    getEntityLabel: null,
    showEntityResponderSelector: null,
    hideEntityResponderSelector: null,
    handleInputChange: null,
    createNewConversation: null,
    showMultiEntityModal: null,
    getGoGameContext: null,
    executeGoMove: null,
};

// Stream abort controller
let streamAbortController = null;

// Import abort controller
let importAbortController = null;

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
 * Get the stream abort controller
 * @returns {AbortController|null}
 */
export function getStreamAbortController() {
    return streamAbortController;
}

/**
 * Send a message (main entry point)
 * @param {boolean} skipEntityModal - Skip entity selection modal
 */
export async function sendMessage(skipEntityModal = false) {
    const content = elements.messageInput.value.trim();
    const hasAttachmentsFlag = hasAttachments();

    // Need either content or attachments to send
    if ((!content && !hasAttachmentsFlag) || state.isLoading) return;

    // Capture attachments before clearing
    const attachments = getAttachmentsForRequest();

    // In multi-entity mode without a conversation, show entity selection modal
    if (!state.currentConversationId && state.isMultiEntityMode && !skipEntityModal) {
        state.pendingActionAfterEntitySelection = 'sendMessage';
        state.pendingMessageForEntitySelection = content;
        state.pendingAttachmentsForEntitySelection = attachments;
        if (callbacks.showMultiEntityModal) {
            callbacks.showMultiEntityModal();
        }
        return;
    }

    // Ensure we have a conversation
    if (!state.currentConversationId) {
        if (callbacks.createNewConversation) {
            await callbacks.createNewConversation(true);
        }
    }

    // In multi-entity mode, store message and show responder selector
    if (state.isMultiEntityMode) {
        state.pendingMessageContent = content;
        state.pendingMessageAttachments = attachments;
        elements.messageInput.value = '';
        elements.messageInput.style.height = 'auto';
        clearAttachments();

        // Add user message visually immediately
        const displayContent = buildDisplayContentWithAttachments(content, attachments);
        state.pendingUserMessageEl = addMessage('human', displayContent);
        scrollToBottom();

        // Show responder selector
        if (callbacks.showEntityResponderSelector) {
            callbacks.showEntityResponderSelector();
        }
        return;
    }

    // Standard single-entity flow
    state.isLoading = true;
    elements.sendBtn.disabled = true;
    elements.sendBtn.style.display = 'none';
    elements.stopBtn.style.display = 'flex';
    elements.messageInput.value = '';
    elements.messageInput.style.height = 'auto';
    clearAttachments();

    // Create abort controller for stop functionality
    streamAbortController = new AbortController();

    // Add user message
    const displayContent = buildDisplayContentWithAttachments(content, attachments);
    const userMessageEl = addMessage('human', displayContent);
    scrollToBottom();

    // Create streaming message element
    const streamingMessage = createStreamingMessage('assistant');
    let usageData = null;

    // Inject Go game context if there's an active game
    let messageToSend = content;
    if (callbacks.getGoGameContext) {
        const gameContext = callbacks.getGoGameContext();
        if (gameContext && content) {
            messageToSend = `[GO GAME STATE]\n${gameContext}\n[/GO GAME STATE]\n\n${content}`;
        }
    }

    try {
        await api.sendMessageStream(
            {
                conversation_id: state.currentConversationId,
                message: messageToSend || null,
                model: state.settings.model,
                temperature: state.settings.temperature,
                max_tokens: state.settings.maxTokens,
                system_prompt: state.settings.systemPrompt,
                verbosity: state.settings.verbosity,
                user_display_name: state.settings.researcherName || null,
                attachments: attachments,
            },
            {
                onMemories: (data) => {
                    handleMemoryUpdate(data);
                },
                onStart: (data) => {
                    // Stream has started
                },
                onAborted: () => {
                    streamingMessage.finalize({ showTimestamp: true, aborted: true });
                },
                onToken: (data) => {
                    if (data.content) {
                        streamingMessage.updateContent(data.content);
                    }
                },
                onToolStart: (data) => {
                    addToolMessage('start', data.tool_name, data);
                },
                onToolResult: (data) => {
                    addToolMessage('result', data.tool_name, data);
                },
                onDone: async (data) => {
                    streamingMessage.finalize({ showTimestamp: true });
                    usageData = data.usage;

                    if (usageData && elements.tokenCount) {
                        elements.tokenCount.textContent = `Tokens: ${usageData.input_tokens} in / ${usageData.output_tokens} out`;
                    }

                    // Parse AI response for Go moves
                    if (callbacks.executeGoMove) {
                        const responseContent = streamingMessage.getContent();
                        await callbacks.executeGoMove(responseContent);
                    }
                },
                onStored: async (data) => {
                    // Update user message with ID and add actions
                    if (data.human_message_id && userMessageEl) {
                        userMessageEl.dataset.messageId = data.human_message_id;
                        addUserMessageActions(userMessageEl, data.human_message_id, content);
                    }

                    if (data.assistant_message_id) {
                        streamingMessage.element.dataset.messageId = data.assistant_message_id;
                        removeRegenerateButtons();
                        addAssistantMessageActions(
                            streamingMessage.element.querySelector('.message-bubble'),
                            data.assistant_message_id,
                            streamingMessage.getContent()
                        );
                    }

                    // Update conversation title if it's the first message
                    await updateConversationTitleIfNeeded(content);
                },
                onError: (data) => {
                    streamingMessage.element.remove();
                    addMessage('assistant', `Error: ${data.error}`, { isError: true });
                    showToast('Failed to send message', 'error');
                    console.error('Streaming error:', data.error);
                },
            },
            streamAbortController.signal
        );

        scrollToBottom();

    } catch (error) {
        if (error.name !== 'AbortError') {
            streamingMessage.element.remove();
            addMessage('assistant', `Error: ${error.message}`, { isError: true });
            showToast('Failed to send message', 'error');
            console.error('Failed to send message:', error);
        }
    } finally {
        state.isLoading = false;
        streamAbortController = null;
        elements.stopBtn.style.display = 'none';
        elements.sendBtn.style.display = 'flex';
        if (callbacks.handleInputChange) {
            callbacks.handleInputChange();
        }
    }
}

/**
 * Send message with responder in multi-entity mode
 */
export async function sendMessageWithResponder() {
    if (!state.pendingResponderId) {
        showToast('No entity selected', 'error');
        return;
    }

    const content = state.pendingMessageContent;
    const attachments = state.pendingMessageAttachments;
    const responderId = state.pendingResponderId;
    const userMessageEl = state.pendingUserMessageEl;
    const isContinuation = !content && !attachments;

    // Clear pending state
    state.pendingMessageContent = null;
    state.pendingMessageAttachments = null;
    state.pendingResponderId = null;
    state.pendingUserMessageEl = null;

    state.isLoading = true;
    elements.sendBtn.disabled = true;

    // Get the responding entity's label
    const responderEntity = state.currentConversationEntities.find(e => e.index_name === responderId);
    const responderLabel = responderEntity?.label || responderId;

    // Create streaming message element with speaker label
    const streamingMessage = createStreamingMessage('assistant', responderLabel);
    let usageData = null;

    // Inject Go game context if there's an active game
    let messageToSend = content;
    if (callbacks.getGoGameContext) {
        const gameContext = callbacks.getGoGameContext();
        if (gameContext && content) {
            messageToSend = `[GO GAME STATE]\n${gameContext}\n[/GO GAME STATE]\n\n${content}`;
        }
    }

    try {
        const request = {
            conversation_id: state.currentConversationId,
            message: messageToSend,
            temperature: state.settings.temperature,
            max_tokens: state.settings.maxTokens,
            system_prompt: state.settings.systemPrompt,
            verbosity: state.settings.verbosity,
            responding_entity_id: responderId,
            user_display_name: state.settings.researcherName || null,
            attachments: attachments,
        };
        // Only include model override if NOT in multi-entity mode
        if (!state.isMultiEntityMode) {
            request.model = state.settings.model;
        }

        await api.sendMessageStream(
            request,
            {
                onMemories: (data) => {
                    handleMemoryUpdate(data);
                },
                onStart: (data) => {
                    // Stream has started
                },
                onToken: (data) => {
                    if (data.content) {
                        streamingMessage.updateContent(data.content);
                    }
                },
                onToolStart: (data) => {
                    addToolMessage('start', data.tool_name, data);
                },
                onToolResult: (data) => {
                    addToolMessage('result', data.tool_name, data);
                },
                onDone: async (data) => {
                    streamingMessage.finalize({
                        showTimestamp: true,
                        speakerLabel: responderLabel,
                    });
                    usageData = data.usage;

                    if (usageData && elements.tokenCount) {
                        elements.tokenCount.textContent = `Tokens: ${usageData.input_tokens} in / ${usageData.output_tokens} out`;
                    }

                    // Parse AI response for Go moves
                    if (callbacks.executeGoMove) {
                        const responseContent = streamingMessage.getContent();
                        await callbacks.executeGoMove(responseContent);
                    }
                },
                onStored: async (data) => {
                    console.log('[MULTI-ENTITY] onStored callback triggered:', data);

                    // Update user message with ID
                    if (data.human_message_id && userMessageEl) {
                        userMessageEl.dataset.messageId = data.human_message_id;
                        addUserMessageActions(userMessageEl, data.human_message_id, content);
                    }

                    // Update assistant message with ID and speaker info
                    if (data.assistant_message_id) {
                        streamingMessage.element.dataset.messageId = data.assistant_message_id;
                        streamingMessage.element.dataset.speakerEntityId = responderId;
                        updateAssistantMessageActions(streamingMessage.element, data.assistant_message_id, streamingMessage.getContent());
                    }

                    // Auto-generate title for new conversations
                    if (content) {
                        await updateConversationTitleIfNeeded(content);
                    }

                    // Show responder selector for next turn
                    if (callbacks.showEntityResponderSelector) {
                        callbacks.showEntityResponderSelector(true);
                    }
                },
                onError: (data) => {
                    streamingMessage.element.remove();
                    addMessage('assistant', `Error: ${data.error}`, { isError: true });
                    showToast('Failed to send message', 'error');
                    console.error('Streaming error:', data.error);
                },
            }
        );

        scrollToBottom();

    } catch (error) {
        streamingMessage.element.remove();
        addMessage('assistant', `Error: ${error.message}`, { isError: true });
        showToast('Failed to send message', 'error');
        console.error('Failed to send message:', error);
    } finally {
        state.isLoading = false;
        if (callbacks.handleInputChange) {
            callbacks.handleInputChange();
        }
    }
}

/**
 * Stop the current generation/stream
 */
export function stopGeneration() {
    if (streamAbortController) {
        streamAbortController.abort();
        streamAbortController = null;
    }
    elements.stopBtn.style.display = 'none';
    elements.sendBtn.style.display = 'flex';
    state.isLoading = false;
    if (callbacks.handleInputChange) {
        callbacks.handleInputChange();
    }
}

/**
 * Regenerate an AI response for a given message
 * @param {string} messageId - ID of the message to regenerate from
 */
export async function regenerateMessage(messageId) {
    if (state.isLoading) {
        showToast('Please wait for the current operation to complete', 'warning');
        return;
    }

    // In multi-entity mode, show entity selector first
    if (state.isMultiEntityMode && state.currentConversationEntities.length > 0) {
        state.pendingRegenerateMessageId = messageId;
        if (callbacks.showEntityResponderSelector) {
            callbacks.showEntityResponderSelector('regenerate');
        }
        return;
    }

    // Single-entity mode: proceed with regeneration directly
    await performRegeneration(messageId);
}

/**
 * Regenerate a message after entity selection in multi-entity mode
 */
export async function regenerateMessageWithEntity() {
    const messageId = state.pendingRegenerateMessageId;
    const responderId = state.pendingResponderId;

    // Clear pending state
    state.pendingRegenerateMessageId = null;
    state.pendingResponderId = null;

    if (!messageId || !responderId) {
        showToast('Missing message or entity selection', 'error');
        return;
    }

    await performRegeneration(messageId, responderId);
}

/**
 * Perform the actual message regeneration
 * @param {string} messageId - ID of the message to regenerate from
 * @param {string|null} respondingEntityId - Entity to generate response (multi-entity only)
 */
export async function performRegeneration(messageId, respondingEntityId = null) {
    state.isLoading = true;
    elements.sendBtn.disabled = true;

    // Find the assistant message element to replace
    const messageEl = elements.messages.querySelector(`[data-message-id="${messageId}"]`);
    let assistantEl = null;

    if (messageEl && messageEl.dataset.role === 'assistant') {
        assistantEl = messageEl;
    } else if (messageEl && messageEl.dataset.role === 'human') {
        assistantEl = messageEl.nextElementSibling;
        while (assistantEl && assistantEl.dataset.role !== 'assistant') {
            assistantEl = assistantEl.nextElementSibling;
        }
    }

    // Remove the old assistant message from UI
    if (assistantEl) {
        assistantEl.remove();
    }

    // Get the responding entity's label for multi-entity mode
    let responderLabel = null;
    if (state.isMultiEntityMode && respondingEntityId) {
        const responderEntity = state.currentConversationEntities.find(e => e.index_name === respondingEntityId);
        responderLabel = responderEntity?.label || respondingEntityId;
    }

    // Create streaming message element
    const streamingMessage = createStreamingMessage('assistant', responderLabel);

    try {
        const requestData = {
            message_id: messageId,
            temperature: state.settings.temperature,
            max_tokens: state.settings.maxTokens,
            system_prompt: state.settings.systemPrompt,
            verbosity: state.settings.verbosity,
            user_display_name: state.settings.researcherName || null,
        };

        if (!state.isMultiEntityMode) {
            requestData.model = state.settings.model;
        }

        if (respondingEntityId) {
            requestData.responding_entity_id = respondingEntityId;
        }

        await api.regenerateStream(
            requestData,
            {
                onMemories: (data) => {
                    handleMemoryUpdate(data);
                },
                onStart: (data) => {
                    // Stream has started
                },
                onToken: (data) => {
                    if (data.content) {
                        streamingMessage.updateContent(data.content);
                    }
                },
                onToolStart: (data) => {
                    addToolMessage('start', data.tool_name, data);
                },
                onToolResult: (data) => {
                    addToolMessage('result', data.tool_name, data);
                },
                onDone: (data) => {
                    streamingMessage.finalize({ showTimestamp: true });

                    if (data.usage && elements.tokenCount) {
                        elements.tokenCount.textContent = `Tokens: ${data.usage.input_tokens} in / ${data.usage.output_tokens} out`;
                    }
                },
                onStored: (data) => {
                    streamingMessage.element.dataset.messageId = data.assistant_message_id;

                    if (data.speaker_entity_id) {
                        streamingMessage.element.dataset.speakerEntityId = data.speaker_entity_id;
                    }

                    removeRegenerateButtons();
                    addAssistantMessageActions(
                        streamingMessage.element.querySelector('.message-bubble'),
                        data.assistant_message_id,
                        streamingMessage.getContent()
                    );

                    // In multi-entity mode, show responder selector for next turn
                    if (state.isMultiEntityMode && callbacks.showEntityResponderSelector) {
                        callbacks.showEntityResponderSelector('continuation');
                    }

                    showToast('Response regenerated', 'success');
                },
                onError: (data) => {
                    streamingMessage.element.remove();
                    addMessage('assistant', `Error: ${data.error}`, { isError: true });
                    showToast('Failed to regenerate response', 'error');
                    console.error('Regeneration error:', data.error);
                },
            }
        );

        scrollToBottom();

    } catch (error) {
        streamingMessage.element.remove();
        addMessage('assistant', `Error: ${error.message}`, { isError: true });
        showToast('Failed to regenerate response', 'error');
        console.error('Failed to regenerate:', error);
    } finally {
        state.isLoading = false;
        if (callbacks.handleInputChange) {
            callbacks.handleInputChange();
        }
    }
}

/**
 * Start editing a human message
 * @param {HTMLElement} messageElement - The message DOM element
 * @param {string} messageId - The message ID
 * @param {string} currentContent - Current message content
 */
export function startEditMessage(messageElement, messageId, currentContent) {
    if (state.isLoading) return;

    if (messageElement.classList.contains('editing')) return;

    messageElement.classList.add('editing');
    const bubble = messageElement.querySelector('.message-bubble');
    const originalContent = currentContent;

    bubble.innerHTML = `
        <div class="message-edit-form">
            <textarea class="message-edit-textarea">${escapeHtml(originalContent)}</textarea>
            <div class="message-edit-actions">
                <button class="message-edit-btn cancel-edit">Cancel</button>
                <button class="message-edit-btn save-edit primary">Save & Regenerate</button>
            </div>
        </div>
    `;

    const textarea = bubble.querySelector('.message-edit-textarea');
    const cancelBtn = bubble.querySelector('.cancel-edit');
    const saveBtn = bubble.querySelector('.save-edit');

    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 300) + 'px';
    textarea.focus();
    textarea.setSelectionRange(textarea.value.length, textarea.value.length);

    textarea.addEventListener('input', () => {
        textarea.style.height = 'auto';
        textarea.style.height = Math.min(textarea.scrollHeight, 300) + 'px';
    });

    cancelBtn.addEventListener('click', () => {
        messageElement.classList.remove('editing');
        bubble.innerHTML = renderMarkdown(originalContent);
        rebindMessageActions(messageElement, messageId, originalContent);
    });

    saveBtn.addEventListener('click', async () => {
        const newContent = textarea.value.trim();
        if (!newContent) {
            showToast('Message cannot be empty', 'error');
            return;
        }

        if (newContent === originalContent) {
            cancelBtn.click();
            return;
        }

        await saveEditAndRegenerate(messageElement, messageId, newContent);
    });

    textarea.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            cancelBtn.click();
        }
    });
}

/**
 * Save an edited message and regenerate the AI response
 */
async function saveEditAndRegenerate(messageElement, messageId, newContent) {
    state.isLoading = true;
    elements.sendBtn.disabled = true;

    try {
        const result = await api.updateMessage(messageId, newContent);

        messageElement.classList.remove('editing');
        const bubble = messageElement.querySelector('.message-bubble');
        bubble.innerHTML = renderMarkdown(newContent);

        rebindMessageActions(messageElement, messageId, newContent);

        // Remove the old assistant message from UI if it was deleted
        if (result.deleted_assistant_message_id) {
            const assistantEl = elements.messages.querySelector(
                `[data-message-id="${result.deleted_assistant_message_id}"]`
            );
            if (assistantEl) {
                assistantEl.remove();
            }
        }

        state.isLoading = false;
        await regenerateMessage(messageId);

    } catch (error) {
        showToast('Failed to update message', 'error');
        console.error('Failed to update message:', error);

        messageElement.classList.remove('editing');
        const bubble = messageElement.querySelector('.message-bubble');
        bubble.innerHTML = renderMarkdown(newContent);
        state.isLoading = false;
        if (callbacks.handleInputChange) {
            callbacks.handleInputChange();
        }
    }
}

/**
 * Re-bind action buttons after canceling edit
 */
function rebindMessageActions(messageElement, messageId, content) {
    const editBtn = messageElement.querySelector('.edit-btn');
    if (editBtn) {
        editBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            startEditMessage(messageElement, messageId, content);
        });
    }
}

/**
 * Add action buttons to user message bubble
 */
function addUserMessageActions(userMessageEl, messageId, content) {
    const userBubble = userMessageEl.querySelector('.message-bubble');
    if (!userBubble) return;

    const actionsDiv = document.createElement('div');
    actionsDiv.className = 'message-bubble-actions';
    actionsDiv.innerHTML = `
        <button class="message-action-btn copy-btn" title="Copy to clipboard">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
            </svg>
        </button>
        <button class="message-action-btn edit-btn" title="Edit message">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
            </svg>
        </button>
    `;
    userBubble.appendChild(actionsDiv);

    const copyBtn = actionsDiv.querySelector('.copy-btn');
    copyBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (callbacks.copyMessage) {
            callbacks.copyMessage(content, copyBtn);
        }
    });

    const editBtn = actionsDiv.querySelector('.edit-btn');
    editBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        startEditMessage(userMessageEl, messageId, content);
    });
}

/**
 * Add action buttons to assistant message bubble
 */
function addAssistantMessageActions(bubble, messageId, messageContent) {
    if (!bubble) return;

    const actionsDiv = document.createElement('div');
    actionsDiv.className = 'message-bubble-actions';

    const speakBtnHtml = state.ttsEnabled ? `
        <button class="message-action-btn speak-btn" title="Read aloud">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
                <path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>
                <path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>
            </svg>
        </button>
    ` : '';

    actionsDiv.innerHTML = `
        <button class="message-action-btn copy-btn" title="Copy to clipboard">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
            </svg>
        </button>
        ${speakBtnHtml}
        <button class="message-action-btn regenerate-btn" title="Regenerate response">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M23 4v6h-6"/>
                <path d="M1 20v-6h6"/>
                <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
            </svg>
        </button>
    `;
    bubble.appendChild(actionsDiv);

    const copyBtn = actionsDiv.querySelector('.copy-btn');
    copyBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (callbacks.copyMessage) {
            callbacks.copyMessage(messageContent, copyBtn);
        }
    });

    const regenerateBtn = actionsDiv.querySelector('.regenerate-btn');
    regenerateBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        regenerateMessage(messageId);
    });

    const speakBtn = actionsDiv.querySelector('.speak-btn');
    if (speakBtn && callbacks.speakMessage) {
        speakBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            callbacks.speakMessage(messageContent, speakBtn, messageId);
        });
    }
}

/**
 * Update conversation title if it's the first message
 */
async function updateConversationTitleIfNeeded(content) {
    const conv = state.conversations.find(c => c.id === state.currentConversationId);
    if (conv && !conv.title && content) {
        const title = content.substring(0, 50) + (content.length > 50 ? '...' : '');
        try {
            await api.updateConversation(state.currentConversationId, { title });
            conv.title = title;
            if (callbacks.renderConversationList) {
                callbacks.renderConversationList();
            }
            if (elements.conversationTitle) {
                elements.conversationTitle.textContent = title;
            }
        } catch (e) {
            console.error('Failed to auto-set title:', e);
        }
    }
}

/**
 * Start continuation mode for multi-entity conversations
 */
export function startContinuationMode() {
    if (!state.currentConversationId) {
        showToast('No active conversation', 'error');
        return;
    }

    // Clear any pending message state
    state.pendingMessageContent = null;
    state.pendingUserMessageEl = null;

    // Show the responder selector in continuation mode
    if (callbacks.showEntityResponderSelector) {
        callbacks.showEntityResponderSelector('continuation');
    }
}
