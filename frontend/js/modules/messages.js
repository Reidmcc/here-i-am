/**
 * Messages Module
 * Handles message rendering and display
 */

import { state } from './state.js';
import { escapeHtml, renderMarkdown, showToast } from './utils.js';

// Element references
let elements = {};

// Callbacks
let callbacks = {
    onCopyMessage: null,
    onEditMessage: null,
    onRegenerateMessage: null,
    onSpeakMessage: null,
    scrollToBottom: null,
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
 * Add a message to the chat
 * @param {string} role - Message role ('human' or 'assistant')
 * @param {string} content - Message content
 * @param {Object} options - Display options
 * @returns {HTMLElement} - The message element
 */
export function addMessage(role, content, options = {}) {
    // Hide welcome message
    if (elements.welcomeMessage) {
        elements.welcomeMessage.style.display = 'none';
    }

    const message = document.createElement('div');
    message.className = `message ${role}`;

    // Store message ID and role as data attributes
    if (options.messageId) {
        message.dataset.messageId = options.messageId;
    }
    message.dataset.role = role;

    // Store speaker entity info for multi-entity conversations
    if (options.speakerEntityId) {
        message.dataset.speakerEntityId = options.speakerEntityId;
    }

    const timestamp = options.timestamp ? new Date(options.timestamp) : new Date();
    const timeStr = timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    // Build speaker label for multi-entity assistant messages
    let speakerLabelHtml = '';
    if (role === 'assistant' && options.speakerLabel) {
        speakerLabelHtml = `<span class="message-speaker-label">${escapeHtml(options.speakerLabel)}</span>`;
    }

    // Build action buttons based on role (now inside the bubble)
    let actionButtons = '';
    const copyBtn = `<button class="message-action-btn copy-btn" title="Copy to clipboard"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button>`;

    if (options.messageId && !options.isError) {
        if (role === 'human') {
            actionButtons = `<div class="message-bubble-actions">${copyBtn}<button class="message-action-btn edit-btn" title="Edit message"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button></div>`;
        } else if (role === 'assistant') {
            const speakBtn = state.ttsEnabled ? `<button class="message-action-btn speak-btn" title="Read aloud"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/></svg></button>` : '';
            const regenerateBtn = options.isLatestAssistant ? `<button class="message-action-btn regenerate-btn" title="Regenerate response"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6"/><path d="M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg></button>` : '';
            actionButtons = `<div class="message-bubble-actions">${copyBtn}${speakBtn}${regenerateBtn}</div>`;
        }
    }

    message.innerHTML = `
        <div class="message-bubble ${options.isError ? 'error' : ''}">${speakerLabelHtml}${renderMarkdown(content)}${actionButtons}</div>
        ${options.showTimestamp !== false ? `
            <div class="message-meta">
                <span>${timeStr}</span>
            </div>
        ` : ''}
    `;

    // Bind action button events
    if (options.messageId) {
        const copyBtnEl = message.querySelector('.copy-btn');
        if (copyBtnEl) {
            copyBtnEl.addEventListener('click', (e) => {
                e.stopPropagation();
                copyMessage(content, copyBtnEl);
            });
        }

        const editBtn = message.querySelector('.edit-btn');
        if (editBtn) {
            editBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                if (callbacks.onEditMessage) {
                    callbacks.onEditMessage(message, options.messageId, content);
                }
            });
        }

        const regenerateBtn = message.querySelector('.regenerate-btn');
        if (regenerateBtn) {
            regenerateBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                if (callbacks.onRegenerateMessage) {
                    callbacks.onRegenerateMessage(options.messageId);
                }
            });
        }

        const speakBtn = message.querySelector('.speak-btn');
        if (speakBtn) {
            speakBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                if (callbacks.onSpeakMessage) {
                    callbacks.onSpeakMessage(content, speakBtn, options.messageId);
                }
            });
        }
    }

    elements.messages.appendChild(message);
    return message;
}

/**
 * Add a tool message (tool start or result)
 * @param {string} type - 'start' or 'result'
 * @param {string} toolName - Name of the tool
 * @param {Object} data - Tool data
 * @returns {HTMLElement|null} - The tool message element (only for start)
 */
export function addToolMessage(type, toolName, data) {
    // Check scroll position before adding content
    const wasNearBottom = isNearBottom();

    const message = document.createElement('div');
    message.className = 'tool-message';
    message.dataset.toolId = data.tool_id || '';
    message.dataset.toolName = toolName;

    if (type === 'start') {
        const displayName = toolName.replace(/_/g, ' ');

        let inputContent = '';
        if (data.input && Object.keys(data.input).length > 0) {
            const inputStr = JSON.stringify(data.input, null, 2);
            inputContent = `
                <details class="tool-input-details">
                    <summary>Input</summary>
                    <pre class="tool-input">${escapeHtml(inputStr)}</pre>
                </details>
            `;
        }

        message.innerHTML = `
            <div class="tool-indicator">
                <span class="tool-icon">🔧</span>
                <span class="tool-name">Using: ${escapeHtml(displayName)}</span>
                <span class="tool-status loading">...</span>
            </div>
            ${inputContent}
        `;

        elements.messages.appendChild(message);
        if (wasNearBottom && callbacks.scrollToBottom) {
            callbacks.scrollToBottom();
        }
        return message;
    } else if (type === 'result') {
        // Find the corresponding start message
        const startMessage = elements.messages.querySelector(
            `.tool-message[data-tool-id="${data.tool_id}"]`
        );

        if (startMessage) {
            const statusEl = startMessage.querySelector('.tool-status');
            if (statusEl) {
                statusEl.classList.remove('loading');
                statusEl.classList.add(data.is_error ? 'error' : 'success');
                statusEl.textContent = data.is_error ? '✗' : '✓';
            }

            const resultDetails = document.createElement('details');
            resultDetails.className = 'tool-result-details';

            let resultContent = data.content || '';
            const maxDisplayLength = 2000;
            if (resultContent.length > maxDisplayLength) {
                resultContent = resultContent.substring(0, maxDisplayLength) + '\n...[truncated]';
            }

            resultDetails.innerHTML = `
                <summary>Result${data.is_error ? ' (Error)' : ''}</summary>
                <pre class="tool-result ${data.is_error ? 'error' : ''}">${escapeHtml(resultContent)}</pre>
            `;

            startMessage.appendChild(resultDetails);
            if (wasNearBottom && callbacks.scrollToBottom) {
                callbacks.scrollToBottom();
            }
        }
        return null;
    }
    return null;
}

/**
 * Create a streaming message element
 * @param {string} role - Message role
 * @param {string} speakerLabel - Speaker label for multi-entity
 * @returns {Object} - Object with element, updateContent, finalize, getContent methods
 */
export function createStreamingMessage(role, speakerLabel = null) {
    // Hide welcome message
    if (elements.welcomeMessage) {
        elements.welcomeMessage.style.display = 'none';
    }

    const message = document.createElement('div');
    message.className = `message ${role}`;
    message.dataset.role = role;

    const bubble = document.createElement('div');
    bubble.className = 'message-bubble streaming';

    // Add speaker label for multi-entity conversations
    if (speakerLabel && role === 'assistant') {
        const labelSpan = document.createElement('span');
        labelSpan.className = 'message-speaker-label';
        labelSpan.textContent = speakerLabel;
        bubble.appendChild(labelSpan);
    }

    const contentSpan = document.createElement('span');
    contentSpan.className = 'message-content';
    bubble.appendChild(contentSpan);

    // Add cursor element for visual feedback
    const cursor = document.createElement('span');
    cursor.className = 'streaming-cursor';
    cursor.textContent = '\u258c'; // Block cursor character
    bubble.appendChild(cursor);

    message.appendChild(bubble);
    elements.messages.appendChild(message);

    let accumulatedContent = '';

    return {
        element: message,
        updateContent: (newToken) => {
            accumulatedContent += newToken;
            contentSpan.textContent = accumulatedContent;
        },
        finalize: (options = {}) => {
            cursor.remove();
            bubble.classList.remove('streaming');

            // Render final content with markdown
            contentSpan.innerHTML = renderMarkdown(accumulatedContent);

            // Add timestamp
            const timestamp = options.timestamp ? new Date(options.timestamp) : new Date();
            const timeStr = timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

            const meta = document.createElement('div');
            meta.className = 'message-meta';
            meta.innerHTML = `<span>${timeStr}</span>`;
            message.appendChild(meta);

            return accumulatedContent;
        },
        getContent: () => accumulatedContent,
    };
}

/**
 * Add typing indicator
 * @returns {HTMLElement} - The indicator element
 */
export function addTypingIndicator() {
    const indicator = document.createElement('div');
    indicator.className = 'message assistant';
    indicator.innerHTML = `
        <div class="typing-indicator">
            <span class="typing-dot"></span>
            <span class="typing-dot"></span>
            <span class="typing-dot"></span>
        </div>
    `;
    elements.messages.appendChild(indicator);
    if (callbacks.scrollToBottom) {
        callbacks.scrollToBottom();
    }
    return indicator;
}

/**
 * Clear all messages
 */
export function clearMessages() {
    if (elements.messages) {
        elements.messages.innerHTML = '';
    }
    if (elements.welcomeMessage) {
        elements.welcomeMessage.style.display = 'block';
        elements.messages.appendChild(elements.welcomeMessage);
    }
}

/**
 * Remove regenerate buttons from all assistant messages
 */
export function removeRegenerateButtons() {
    const regenerateBtns = elements.messages.querySelectorAll('.message.assistant .regenerate-btn');
    regenerateBtns.forEach(btn => btn.remove());
}

/**
 * Copy message content to clipboard
 * @param {string} content - Content to copy
 * @param {HTMLElement} btn - The copy button
 */
export async function copyMessage(content, btn) {
    try {
        await navigator.clipboard.writeText(content);
        btn.classList.add('copied');
        btn.title = 'Copied!';

        const originalSvg = btn.innerHTML;
        btn.innerHTML = `
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="20 6 9 17 4 12"/>
            </svg>
        `;

        setTimeout(() => {
            btn.classList.remove('copied');
            btn.title = 'Copy to clipboard';
            btn.innerHTML = originalSvg;
        }, 2000);
    } catch (error) {
        console.error('Failed to copy:', error);
        showToast('Failed to copy to clipboard', 'error');
    }
}

/**
 * Update assistant message with action buttons
 * @param {HTMLElement} messageElement - The message element
 * @param {string} messageId - Message ID
 * @param {string} messageContent - Message content
 */
export function updateAssistantMessageActions(messageElement, messageId, messageContent) {
    removeRegenerateButtons();

    const assistantBubble = messageElement.querySelector('.message-bubble');
    if (assistantBubble) {
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

        assistantBubble.appendChild(actionsDiv);

        const copyBtn = actionsDiv.querySelector('.copy-btn');
        copyBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            copyMessage(messageContent, copyBtn);
        });

        const regenerateBtn = actionsDiv.querySelector('.regenerate-btn');
        regenerateBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            if (callbacks.onRegenerateMessage) {
                callbacks.onRegenerateMessage(messageId);
            }
        });

        const speakBtn = actionsDiv.querySelector('.speak-btn');
        if (speakBtn) {
            speakBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                if (callbacks.onSpeakMessage) {
                    callbacks.onSpeakMessage(messageContent, speakBtn, messageId);
                }
            });
        }
    }
}

/**
 * Check if scroll is near the bottom
 * @param {number} threshold - Pixels from bottom
 * @returns {boolean}
 */
export function isNearBottom(threshold = 100) {
    const container = elements.messagesContainer;
    return container.scrollTop + container.clientHeight >= container.scrollHeight - threshold;
}

/**
 * Scroll to the bottom of the messages container
 */
export function scrollToBottom() {
    elements.messagesContainer.scrollTop = elements.messagesContainer.scrollHeight;
}
