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

// The assistant turn currently being streamed, if any.
//
// Tool and thinking cards are appended *into* that turn's element rather than
// onto the end of the message list, so each one keeps the position it had when
// it happened: the text that streamed before it stays above it, and the text
// that streams after it opens a fresh bubble below it. Without this the turn's
// text all accumulates in one bubble created before the first card, which
// leaves every card stranded at the bottom of the response.
//
// Null outside a stream — a conversation reload renders persisted tool cards
// straight into the message list, exactly as before.
let activeStreamingTurn = null;

/**
 * Settle the streaming turn, if any, and hand back the element a tool/thinking
 * card should be appended to.
 *
 * Closing the turn's open text bubble first is what puts the card *after* the
 * text that preceded it; the next token then opens a fresh bubble below.
 *
 * Falls back to the message list when no turn is streaming, and when the
 * streaming turn's element is no longer in it (an errored stream removes the
 * element without finalizing) — otherwise the card would render into limbo.
 *
 * @returns {HTMLElement}
 */
function prepareCardInsertion() {
    if (activeStreamingTurn && !elements.messages?.contains(activeStreamingTurn.element)) {
        activeStreamingTurn = null;
    }
    if (!activeStreamingTurn) return elements.messages;

    activeStreamingTurn.closeTextSegment();
    return activeStreamingTurn.element;
}

/**
 * Find an already-rendered tool card by its tool_use id.
 *
 * Scans rather than building a selector: tool ids come from the provider and
 * are interpolated nowhere, so a syntactically awkward one can't break the
 * lookup mid-stream.
 *
 * @param {string} toolId
 * @returns {HTMLElement|null}
 */
function findToolMessage(toolId) {
    if (!toolId || !elements.messages) return null;
    for (const el of elements.messages.querySelectorAll('.tool-message')) {
        if (el.dataset.toolId === toolId) return el;
    }
    return null;
}

/**
 * Render the collapsible "Input" section of a tool card.
 *
 * @param {Object|undefined} input - Tool arguments
 * @returns {string} - HTML, or '' when there are no arguments to show
 */
function renderToolInput(input) {
    if (!input || Object.keys(input).length === 0) return '';
    const inputStr = JSON.stringify(input, null, 2);
    return `
            <details class="tool-input-details">
                <summary>Input</summary>
                <pre class="tool-input">${escapeHtml(inputStr)}</pre>
            </details>
        `;
}

/**
 * Collect the reflection texts a loaded conversation already shows as the
 * input of a memory_save tool card, so the reflection rows the tool wrote
 * are not rendered a second time as loose messages.
 *
 * memory_save persists its text twice by design: inside the tool_use content
 * blocks (the tool call) and as a reflection row (the saved memory). Matching
 * on the text keeps older conversations - saved before tool exchanges were
 * rendered on reload - showing their reflections.
 *
 * @param {Array} messages - Messages as returned by the API
 * @returns {Set<string>} Trimmed reflection texts covered by a tool card
 */
export function collectSavedReflections(messages) {
    const saved = new Set();

    for (const msg of messages) {
        if (msg.role !== 'tool_use') continue;

        let contentBlocks;
        try {
            contentBlocks = JSON.parse(msg.content);
        } catch {
            // Unparseable rows are reported where they are rendered
            continue;
        }
        if (!Array.isArray(contentBlocks)) continue;

        for (const block of contentBlocks) {
            if (block?.type !== 'tool_use' || block.name !== 'memory_save') continue;
            const content = block.input?.content;
            if (typeof content === 'string') {
                saved.add(content.trim());
            }
        }
    }

    return saved;
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
        const inputContent = renderToolInput(data.input);

        // A tool is announced twice: once when its block opens (arguments still
        // streaming, so no input yet) and again with the parsed arguments just
        // before it runs. Fill in the input on the second announcement instead
        // of rendering a second card for the same call.
        const existing = findToolMessage(data.tool_id);
        if (existing) {
            const staleInput = existing.querySelector('.tool-input-details');
            if (staleInput) staleInput.remove();
            const indicator = existing.querySelector('.tool-indicator');
            if (inputContent && indicator) {
                indicator.insertAdjacentHTML('afterend', inputContent);
            }
            return existing;
        }

        message.innerHTML = `
            <div class="tool-indicator">
                <span class="tool-icon">🔧</span>
                <span class="tool-name">Using: ${escapeHtml(displayName)}</span>
                <span class="tool-status loading">...</span>
            </div>
            ${inputContent}
        `;

        prepareCardInsertion().appendChild(message);
        if (wasNearBottom && callbacks.scrollToBottom) {
            callbacks.scrollToBottom();
        }
        return message;
    } else if (type === 'result') {
        // Find the corresponding start message
        const startMessage = findToolMessage(data.tool_id);

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

// The thinking block currently being streamed, if any. Reasoning arrives as a
// contiguous run of deltas between thinking_start and thinking_stop, so a
// single module-level handle is enough — a second block cannot open before the
// first closes.
let activeThinkingMessage = null;

// How much of the reasoning to show inline before it collapses behind the
// expander. Long enough to convey what the model is working on, short enough
// to stay on one line at normal window widths.
const THINKING_SUMMARY_MAX_LENGTH = 120;

/**
 * Condense streamed reasoning down to a single-line summary.
 *
 * The provider already sends summarized reasoning rather than a raw chain of
 * thought, and each summary opens with a short topic line — so the first
 * non-empty line is the summary, and everything after it is detail.
 *
 * @param {string} text - Accumulated reasoning text
 * @returns {string} - Single-line summary (may be truncated with an ellipsis)
 */
function summarizeThinking(text) {
    const firstLine = text.split('\n').find((line) => line.trim().length > 0);
    if (!firstLine) return '';

    const trimmed = firstLine.trim();
    if (trimmed.length <= THINKING_SUMMARY_MAX_LENGTH) return trimmed;
    return `${trimmed.slice(0, THINKING_SUMMARY_MAX_LENGTH).trimEnd()}…`;
}

/**
 * Refresh the inline summary from the block's accumulated reasoning, and hide
 * the expander while the summary *is* the whole text (nothing to expand into).
 *
 * @param {HTMLElement} message - A `.thinking-message` element
 */
function refreshThinkingSummary(message) {
    const body = message.querySelector('.thinking-body');
    const summaryEl = message.querySelector('.thinking-summary');
    const details = message.querySelector('.thinking-details');
    if (!body || !summaryEl || !details) return;

    const full = body.textContent.trim();
    const summary = summarizeThinking(body.textContent);

    // textContent, not innerHTML — reasoning is model output and must never be
    // parsed as markup.
    summaryEl.textContent = summary;
    details.hidden = summary === full;
}

/**
 * Render streamed reasoning ("thinking") from the model.
 *
 * This element is transient chrome: it is not part of the message the backend
 * stores or re-renders on reload. (The backend does send reasoning back to the
 * model as thinking blocks, but that is a separate channel from these events.)
 * Models from Claude 4.6 onward reason before answering, which can take minutes
 * on a hard turn with no other output on the wire — without this the UI is
 * simply blank for that whole period.
 *
 * Only the one-line summary is shown inline; the rest of the reasoning stays
 * collapsed behind an expander so a long block can't push the conversation off
 * screen. The full text is still accumulated, just not displayed by default.
 *
 * @param {'start'|'delta'|'stop'} phase
 * @param {Object} data - Event payload ({ content } on 'delta')
 */
export function addThinkingMessage(phase, data = {}) {
    if (phase === 'start') {
        // Close any block left open by an interrupted stream, so its element
        // cannot collect deltas belonging to this one.
        resetThinkingMessage();

        const wasNearBottom = isNearBottom();

        const message = document.createElement('div');
        message.className = 'thinking-message';
        message.innerHTML = `
            <div class="tool-indicator">
                <span class="tool-icon">💭</span>
                <span class="tool-name">Thinking</span>
                <span class="tool-status loading">...</span>
            </div>
            <div class="thinking-summary"></div>
            <details class="thinking-details" hidden>
                <summary>Full reasoning</summary>
                <div class="thinking-body"></div>
            </details>
        `;

        prepareCardInsertion().appendChild(message);
        activeThinkingMessage = message;

        if (wasNearBottom && callbacks.scrollToBottom) {
            callbacks.scrollToBottom();
        }
        return message;
    }

    if (phase === 'delta') {
        // A delta with no open block would mean the stream sent content
        // outside a thinking_start/stop pair; drop it rather than guess.
        if (!activeThinkingMessage || !data.content) return null;

        const wasNearBottom = isNearBottom();
        const body = activeThinkingMessage.querySelector('.thinking-body');
        if (body) {
            // textContent, not innerHTML — reasoning is model output and must
            // never be parsed as markup.
            body.textContent += data.content;
            refreshThinkingSummary(activeThinkingMessage);
        }
        if (wasNearBottom && callbacks.scrollToBottom) {
            callbacks.scrollToBottom();
        }
        return activeThinkingMessage;
    }

    if (phase === 'stop') {
        if (!activeThinkingMessage) return null;

        const statusEl = activeThinkingMessage.querySelector('.tool-status');
        if (statusEl) {
            statusEl.classList.remove('loading');
            statusEl.classList.add('success');
            statusEl.textContent = '✓';
        }
        // An empty block means the model reasoned but returned no summary
        // (thinking_summaries_enabled off, or a model that omits it). Say so
        // rather than leaving a blank panel.
        const body = activeThinkingMessage.querySelector('.thinking-body');
        const summaryEl = activeThinkingMessage.querySelector('.thinking-summary');
        if (body && summaryEl && !body.textContent) {
            summaryEl.classList.add('thinking-empty');
            summaryEl.textContent = '(no summary returned)';
        }

        const finished = activeThinkingMessage;
        activeThinkingMessage = null;
        return finished;
    }

    return null;
}

/**
 * Drop the handle to any in-progress thinking block.
 *
 * Called when a turn ends by any path — done, error, or abort — so a block left
 * open by an interrupted stream cannot collect deltas from the next turn.
 */
export function resetThinkingMessage() {
    if (activeThinkingMessage) {
        const statusEl = activeThinkingMessage.querySelector('.tool-status');
        if (statusEl) {
            statusEl.classList.remove('loading');
            statusEl.textContent = '';
        }
        activeThinkingMessage = null;
    }
}

/**
 * Create a streaming message element.
 *
 * The turn is a container, not a single bubble: its text is split into one
 * bubble per run of tokens between tool/thinking cards, and those cards are
 * appended into the same container by `addToolMessage`/`addThinkingMessage`
 * while this turn is streaming. The result reads top to bottom in the order
 * things actually happened, instead of one text blob with every card below it.
 *
 * `getContent()` still returns the whole turn's text — the backend stores it as
 * one assistant message, and copy/TTS/regenerate all work off that.
 *
 * @param {string} role - Message role
 * @param {string} speakerLabel - Speaker label for multi-entity
 * @returns {Object} - Object with element, bubble, updateContent, finalize, getContent
 */
export function createStreamingMessage(role, speakerLabel = null) {
    // Hide welcome message
    if (elements.welcomeMessage) {
        elements.welcomeMessage.style.display = 'none';
    }

    // A turn left un-finalized (an errored stream removes its element without
    // finalizing) must not keep collecting this turn's cards.
    activeStreamingTurn = null;

    const message = document.createElement('div');
    message.className = `message ${role}`;
    message.dataset.role = role;

    // One cursor for the turn, moved into whichever bubble is currently open.
    const cursor = document.createElement('span');
    cursor.className = 'streaming-cursor';
    cursor.textContent = '\u258c'; // Block cursor character

    // One entry per run of text between cards: { bubble, contentSpan, text }.
    const segments = [];
    let activeSegment = null;
    let accumulatedContent = '';

    const openSegment = () => {
        const bubble = document.createElement('div');
        bubble.className = 'message-bubble streaming';

        // The speaker label names the turn, so it goes on the first bubble
        // only — repeating it above every text run would read as several
        // separate messages from the same entity.
        if (speakerLabel && role === 'assistant' && segments.length === 0) {
            const labelSpan = document.createElement('span');
            labelSpan.className = 'message-speaker-label';
            labelSpan.textContent = speakerLabel;
            bubble.appendChild(labelSpan);
        }

        const contentSpan = document.createElement('span');
        contentSpan.className = 'message-content';
        bubble.appendChild(contentSpan);
        bubble.appendChild(cursor);

        message.appendChild(bubble);

        const segment = { bubble, contentSpan, text: '' };
        segments.push(segment);
        activeSegment = segment;
        return segment;
    };

    // No more tokens can land in this segment, so render its markdown now.
    const settleSegment = (segment) => {
        segment.bubble.classList.remove('streaming');
        segment.contentSpan.innerHTML = renderMarkdown(segment.text);
    };

    const dropSegment = (segment) => {
        segment.bubble.remove();
        const at = segments.indexOf(segment);
        if (at !== -1) segments.splice(at, 1);
    };

    // Called just before a tool/thinking card is appended to this turn.
    const closeTextSegment = () => {
        if (!activeSegment) return;
        const segment = activeSegment;
        activeSegment = null;

        if (segment.text) {
            cursor.remove();
            settleSegment(segment);
        } else {
            // Nothing streamed into it — the usual case, since a model
            // typically thinks or calls a tool before it writes anything. Drop
            // the empty bubble rather than leave a blank card above the block.
            dropSegment(segment);
        }
    };

    // Opened eagerly so the turn shows a bubble (and a blinking cursor) the
    // moment it starts, before the first token or card arrives.
    openSegment();

    activeStreamingTurn = { element: message, closeTextSegment };

    elements.messages.appendChild(message);

    return {
        element: message,
        // The bubble that action buttons belong on: the last one, so they sit
        // at the end of the response rather than mid-turn.
        get bubble() {
            return segments.length ? segments[segments.length - 1].bubble : null;
        },
        updateContent: (newToken) => {
            accumulatedContent += newToken;
            const segment = activeSegment || openSegment();
            segment.text += newToken;
            segment.contentSpan.textContent = segment.text;
        },
        finalize: (options = {}) => {
            cursor.remove();

            if (activeSegment) {
                // Keep a trailing empty bubble only when it is all the turn
                // has: a response that produced no text still needs somewhere
                // to hang its action buttons.
                if (activeSegment.text || segments.length === 1) {
                    settleSegment(activeSegment);
                } else {
                    dropSegment(activeSegment);
                }
                activeSegment = null;
            }

            // Every card and no text at all (an aborted turn, say) would
            // otherwise leave the turn without a bubble.
            if (segments.length === 0) {
                const segment = openSegment();
                activeSegment = null;
                cursor.remove();
                settleSegment(segment);
            }

            if (activeStreamingTurn && activeStreamingTurn.element === message) {
                activeStreamingTurn = null;
            }

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
    activeStreamingTurn = null;
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

    // A turn with tool/thinking cards holds several bubbles; the actions belong
    // at the end of the response, on the last one.
    const bubbles = messageElement.querySelectorAll(':scope > .message-bubble');
    const assistantBubble = bubbles.length ? bubbles[bubbles.length - 1] : null;
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
