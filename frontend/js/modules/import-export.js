/**
 * Import/Export Module
 * Handles conversation import and export functionality
 */

import { state } from './state.js';
import { showToast, escapeHtml, readFileAsText } from './utils.js';

// Reference to global API client
const api = window.api;

// Element references
let elements = {};

// Import state
let importFileContent = null;
let importPreviewData = null;
let importAbortController = null;

// Callbacks
let callbacks = {
    loadConversations: null,
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
 * Export current conversation to JSON file
 */
export async function exportConversation() {
    if (!state.currentConversationId) return;

    try {
        const data = await api.exportConversation(state.currentConversationId);

        // Create download link
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
        console.error('Export failed:', error);
    }
}

/**
 * Handle import file selection change
 */
export function handleImportFileChange() {
    if (!elements.importPreviewBtn || !elements.importFile) return;

    const hasFile = elements.importFile.files.length > 0;
    elements.importPreviewBtn.disabled = !hasFile;

    // Reset state
    importFileContent = null;
    importPreviewData = null;

    // Reset UI
    if (elements.importStatus) {
        elements.importStatus.style.display = 'none';
    }
    if (elements.importStep2) {
        elements.importStep2.style.display = 'none';
    }
    if (elements.importStep1) {
        elements.importStep1.style.display = 'block';
    }
    if (elements.importProgress) {
        elements.importProgress.style.display = 'none';
    }
}

/**
 * Preview import file
 */
export async function previewImportFile() {
    const file = elements.importFile?.files[0];
    if (!file) return;

    if (!elements.importPreviewBtn || !elements.importStatus) return;

    elements.importPreviewBtn.disabled = true;
    elements.importPreviewBtn.textContent = 'Loading...';
    elements.importStatus.style.display = 'block';
    elements.importStatus.className = 'import-status';
    elements.importStatus.textContent = 'Reading file...';

    try {
        // Read file content
        importFileContent = await readFileAsText(file);

        elements.importStatus.textContent = 'Analyzing conversations...';

        // Get source hint from select
        const source = elements.importSource?.value || null;
        const allowReimport = elements.importAllowReimport?.checked || false;

        // Call API to preview
        importPreviewData = await api.previewExternalConversations({
            content: importFileContent,
            entity_id: state.selectedEntityId,
            source: source,
            allow_reimport: allowReimport,
        });

        // Show step 2
        elements.importStatus.style.display = 'none';
        if (elements.importStep1) {
            elements.importStep1.style.display = 'none';
        }
        if (elements.importStep2) {
            elements.importStep2.style.display = 'block';
        }

        // Update preview info
        if (elements.importPreviewInfo) {
            elements.importPreviewInfo.textContent = `${importPreviewData.total_conversations} conversations found (${importPreviewData.source_format})`;
        }

        // Render conversation list
        renderImportConversationList();

    } catch (error) {
        elements.importStatus.className = 'import-status error';
        elements.importStatus.textContent = `Error: ${error.message}`;
        showToast('Failed to load conversations', 'error');
        console.error('Preview failed:', error);
    } finally {
        elements.importPreviewBtn.disabled = false;
        elements.importPreviewBtn.textContent = 'Load Conversations';
    }
}

/**
 * Render the import conversation list
 */
function renderImportConversationList() {
    if (!importPreviewData || !importPreviewData.conversations) {
        if (elements.importConversationList) {
            elements.importConversationList.innerHTML = '<p>No conversations found</p>';
        }
        return;
    }

    const html = importPreviewData.conversations.map(conv => {
        const alreadyImported = conv.already_imported;
        const partiallyImported = conv.imported_count > 0 && !alreadyImported;

        let statusText = '';
        let statusClass = '';
        if (alreadyImported) {
            statusText = ' (already imported)';
            statusClass = 'imported';
        } else if (partiallyImported) {
            statusText = ` (${conv.imported_count}/${conv.message_count} imported)`;
            statusClass = 'partial';
        }

        return `
            <div class="import-conversation-item ${statusClass}" data-index="${conv.index}">
                <div class="import-conversation-info">
                    <div class="import-conversation-title">${escapeHtml(conv.title)}</div>
                    <div class="import-conversation-meta">
                        ${conv.message_count} messages${statusText}
                    </div>
                </div>
                <div class="import-conversation-options">
                    <label title="Import as searchable memories">
                        <input type="checkbox" class="import-cb-memory" data-index="${conv.index}" ${alreadyImported ? '' : 'checked'} ${alreadyImported ? 'disabled' : ''}>
                        Memory
                    </label>
                    <label title="Also add to conversation history">
                        <input type="checkbox" class="import-cb-history" data-index="${conv.index}" ${alreadyImported ? 'disabled' : ''}>
                        History
                    </label>
                </div>
            </div>
        `;
    }).join('');

    if (elements.importConversationList) {
        elements.importConversationList.innerHTML = html;
    }
}

/**
 * Toggle all import checkboxes
 * @param {string} type - 'memory' or 'history'
 * @param {boolean} checked - Whether to check or uncheck
 */
export function toggleAllImportCheckboxes(type, checked) {
    const selector = type === 'memory' ? '.import-cb-memory' : '.import-cb-history';
    const checkboxes = elements.importConversationList?.querySelectorAll(selector + ':not(:disabled)');
    if (checkboxes) {
        checkboxes.forEach(cb => cb.checked = checked);
    }
}

/**
 * Import external conversations
 */
export async function importExternalConversations() {
    if (!importFileContent || !importPreviewData) {
        showToast('Please load a file first', 'error');
        return;
    }

    if (!state.selectedEntityId) {
        showToast('Please select an entity first', 'error');
        return;
    }

    // Gather selected conversations
    const selectedConversations = [];
    importPreviewData.conversations.forEach(conv => {
        const memoryCheckbox = elements.importConversationList?.querySelector(`.import-cb-memory[data-index="${conv.index}"]`);
        const historyCheckbox = elements.importConversationList?.querySelector(`.import-cb-history[data-index="${conv.index}"]`);

        const importAsMemory = memoryCheckbox && memoryCheckbox.checked;
        const importToHistory = historyCheckbox && historyCheckbox.checked;

        if (importAsMemory || importToHistory) {
            selectedConversations.push({
                index: conv.index,
                import_as_memory: importAsMemory,
                import_to_history: importToHistory,
            });
        }
    });

    if (selectedConversations.length === 0) {
        showToast('Please select at least one conversation to import', 'warning');
        return;
    }

    // Create abort controller for cancellation
    importAbortController = new AbortController();

    // Show loading state
    if (elements.importBtn) {
        elements.importBtn.disabled = true;
        elements.importBtn.style.display = 'none';
    }
    if (elements.importCancelBtn) {
        elements.importCancelBtn.style.display = 'inline-block';
    }
    if (elements.importProgress) {
        elements.importProgress.style.display = 'block';
    }
    if (elements.importProgressBar) {
        elements.importProgressBar.style.width = '0%';
    }
    if (elements.importProgressText) {
        elements.importProgressText.textContent = 'Starting import...';
    }
    if (elements.importStatus) {
        elements.importStatus.style.display = 'none';
    }

    let conversationsToHistory = 0;

    try {
        const source = elements.importSource?.value || null;
        const allowReimport = elements.importAllowReimport?.checked || false;

        // Call streaming API to import
        await api.importExternalConversationsStream(
            {
                content: importFileContent,
                entity_id: state.selectedEntityId,
                source: source,
                selected_conversations: selectedConversations,
                allow_reimport: allowReimport,
            },
            {
                onStart: (data) => {
                    if (elements.importProgressText) {
                        elements.importProgressText.textContent =
                            `Importing ${data.total_conversations} conversations (${data.total_messages} messages)...`;
                    }
                },
                onProgress: (data) => {
                    if (elements.importProgressBar) {
                        elements.importProgressBar.style.width = `${data.progress_percent}%`;
                    }
                    if (elements.importProgressText) {
                        elements.importProgressText.textContent =
                            `${data.messages_processed} / ${data.total_messages} messages (${data.progress_percent}%)`;
                    }
                },
                onDone: (result) => {
                    conversationsToHistory = result.conversations_to_history;

                    if (elements.importProgress) {
                        elements.importProgress.style.display = 'none';
                    }
                    if (elements.importStatus) {
                        elements.importStatus.style.display = 'block';
                        elements.importStatus.className = 'import-status success';

                        let statusHtml = `<strong>Import successful!</strong><br>
                            Conversations: ${result.conversations_imported}<br>
                            Messages: ${result.messages_imported}`;

                        if (result.messages_skipped > 0) {
                            statusHtml += `<br>Skipped (duplicates): ${result.messages_skipped}`;
                        }
                        if (result.conversations_to_history > 0) {
                            statusHtml += `<br>Added to history: ${result.conversations_to_history}`;
                        }
                        statusHtml += `<br>Memories stored: ${result.memories_stored}`;

                        elements.importStatus.innerHTML = statusHtml;
                    }
                    showToast(`Imported ${result.messages_imported} messages`, 'success');
                },
                onCancelled: (data) => {
                    if (elements.importProgress) {
                        elements.importProgress.style.display = 'none';
                    }
                    if (elements.importStatus) {
                        elements.importStatus.style.display = 'block';
                        elements.importStatus.className = 'import-status warning';
                        elements.importStatus.innerHTML = `<strong>Import cancelled</strong><br>
                            Some messages may have been imported before cancellation.`;
                    }
                    showToast('Import cancelled', 'warning');
                },
                onError: (data) => {
                    if (elements.importProgress) {
                        elements.importProgress.style.display = 'none';
                    }
                    if (elements.importStatus) {
                        elements.importStatus.style.display = 'block';
                        elements.importStatus.className = 'import-status error';
                        elements.importStatus.textContent = `Error: ${data.error}`;
                    }
                    showToast('Import failed', 'error');
                },
            },
            importAbortController.signal
        );

        // Reload conversations if any were added to history
        if (conversationsToHistory > 0 && callbacks.loadConversations) {
            await callbacks.loadConversations();
        }

    } catch (error) {
        if (error.name !== 'AbortError') {
            if (elements.importProgress) {
                elements.importProgress.style.display = 'none';
            }
            if (elements.importStatus) {
                elements.importStatus.style.display = 'block';
                elements.importStatus.className = 'import-status error';
                elements.importStatus.textContent = `Error: ${error.message}`;
            }
            showToast('Import failed', 'error');
            console.error('Import failed:', error);
        }
    } finally {
        importAbortController = null;
        if (elements.importBtn) {
            elements.importBtn.disabled = false;
            elements.importBtn.style.display = 'inline-block';
            elements.importBtn.textContent = 'Import Selected';
        }
        if (elements.importCancelBtn) {
            elements.importCancelBtn.style.display = 'none';
        }
    }
}

/**
 * Cancel ongoing import
 */
export function cancelImport() {
    if (importAbortController) {
        importAbortController.abort();
        showToast('Cancelling import...', 'info');
    }
}

/**
 * Reset import modal state
 */
export function resetImportModal() {
    importFileContent = null;
    importPreviewData = null;

    if (elements.importFile) {
        elements.importFile.value = '';
    }
    if (elements.importStatus) {
        elements.importStatus.style.display = 'none';
    }
    if (elements.importStep1) {
        elements.importStep1.style.display = 'block';
    }
    if (elements.importStep2) {
        elements.importStep2.style.display = 'none';
    }
    if (elements.importProgress) {
        elements.importProgress.style.display = 'none';
    }
    if (elements.importPreviewBtn) {
        elements.importPreviewBtn.disabled = true;
    }
}
