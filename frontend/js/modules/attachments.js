/**
 * Attachments Module
 * Handles file/image attachments for messages
 */

import { state, resetAttachments } from './state.js';
import { showToast, escapeHtml, readFileAsBase64, readFileAsText } from './utils.js';

// Element references
let elements = {};

// Constants
const MAX_FILE_SIZE = 5 * 1024 * 1024; // 5MB
const ALLOWED_IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/gif', 'image/webp'];
const ALLOWED_TEXT_EXTENSIONS = ['.txt', '.md', '.py', '.js', '.ts', '.json', '.yaml', '.yml', '.html', '.css', '.xml', '.csv', '.log', '.pdf', '.docx'];

// Callbacks
let callbacks = {
    onAttachmentsChanged: null,
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
 * Initialize drag and drop handlers
 */
export function initDragAndDrop() {
    const inputArea = document.querySelector('.input-area');
    if (!inputArea) return;

    inputArea.addEventListener('dragover', handleDragOver);
    inputArea.addEventListener('dragleave', handleDragLeave);
    inputArea.addEventListener('drop', handleDrop);
}

/**
 * Handle file selection from file input
 * @param {Event} e - File input change event
 */
export function handleFileSelect(e) {
    const files = e.target.files;
    if (files.length > 0) {
        processFiles(Array.from(files));
    }
    // Reset input so the same file can be selected again
    e.target.value = '';
}

/**
 * Handle drag over event
 * @param {DragEvent} e
 */
function handleDragOver(e) {
    e.preventDefault();
    e.stopPropagation();
    e.currentTarget.classList.add('drag-over');
}

/**
 * Handle drag leave event
 * @param {DragEvent} e
 */
function handleDragLeave(e) {
    e.preventDefault();
    e.stopPropagation();
    e.currentTarget.classList.remove('drag-over');
}

/**
 * Handle drop event
 * @param {DragEvent} e
 */
function handleDrop(e) {
    e.preventDefault();
    e.stopPropagation();
    e.currentTarget.classList.remove('drag-over');

    const files = e.dataTransfer.files;
    if (files.length > 0) {
        processFiles(Array.from(files));
    }
}

/**
 * Process files for attachment
 * @param {File[]} files - Files to process
 */
export async function processFiles(files) {
    for (const file of files) {
        // Check file size
        if (file.size > MAX_FILE_SIZE) {
            showToast(`File ${file.name} is too large (max 5MB)`, 'error');
            continue;
        }

        // Check if image
        if (ALLOWED_IMAGE_TYPES.includes(file.type)) {
            try {
                const base64 = await readFileAsBase64(file);
                const previewUrl = URL.createObjectURL(file);

                state.pendingAttachments.images.push({
                    name: file.name,
                    type: file.type,
                    base64: base64,
                    previewUrl: previewUrl,
                });
            } catch (error) {
                console.error('Failed to read image:', error);
                showToast(`Failed to read ${file.name}`, 'error');
            }
            continue;
        }

        // Check if text/document file
        const ext = '.' + file.name.split('.').pop().toLowerCase();
        if (ALLOWED_TEXT_EXTENSIONS.includes(ext)) {
            // For PDF and DOCX, the server will extract text
            // For other text files, read directly
            if (ext === '.pdf' || ext === '.docx') {
                // Store file for server-side processing
                state.pendingAttachments.files.push({
                    name: file.name,
                    type: file.type || `application/${ext.slice(1)}`,
                    file: file, // Keep original file for upload
                    content: null, // Will be extracted server-side
                });
            } else {
                try {
                    const content = await readFileAsText(file);
                    state.pendingAttachments.files.push({
                        name: file.name,
                        type: file.type || 'text/plain',
                        content: content,
                    });
                } catch (error) {
                    console.error('Failed to read file:', error);
                    showToast(`Failed to read ${file.name}`, 'error');
                }
            }
            continue;
        }

        showToast(`File type not supported: ${file.name}`, 'error');
    }

    updateAttachmentPreview();

    if (callbacks.onAttachmentsChanged) {
        callbacks.onAttachmentsChanged();
    }
}

/**
 * Update the attachment preview UI
 */
export function updateAttachmentPreview() {
    const hasAttachments = state.pendingAttachments.images.length > 0 || state.pendingAttachments.files.length > 0;

    if (!hasAttachments) {
        if (elements.attachmentPreview) {
            elements.attachmentPreview.style.display = 'none';
        }
        return;
    }

    if (elements.attachmentPreview) {
        elements.attachmentPreview.style.display = 'block';
    }

    if (!elements.attachmentList) return;

    let html = '';

    // Images
    for (let i = 0; i < state.pendingAttachments.images.length; i++) {
        const img = state.pendingAttachments.images[i];
        html += `
            <div class="attachment-item image" data-type="image" data-index="${i}">
                <img src="${img.previewUrl}" alt="${escapeHtml(img.name)}">
                <span class="attachment-name">${escapeHtml(img.name)}</span>
                <button class="attachment-remove" onclick="app.removeAttachment('image', ${i})">&times;</button>
            </div>
        `;
    }

    // Files
    for (let i = 0; i < state.pendingAttachments.files.length; i++) {
        const file = state.pendingAttachments.files[i];
        const ext = file.name.split('.').pop().toUpperCase();
        html += `
            <div class="attachment-item file" data-type="file" data-index="${i}">
                <span class="attachment-icon">${ext}</span>
                <span class="attachment-name">${escapeHtml(file.name)}</span>
                <button class="attachment-remove" onclick="app.removeAttachment('file', ${i})">&times;</button>
            </div>
        `;
    }

    elements.attachmentList.innerHTML = html;
}

/**
 * Remove an attachment
 * @param {string} type - 'image' or 'file'
 * @param {number} index - Index of attachment
 */
export function removeAttachment(type, index) {
    if (type === 'image') {
        const img = state.pendingAttachments.images[index];
        if (img && img.previewUrl) {
            URL.revokeObjectURL(img.previewUrl);
        }
        state.pendingAttachments.images.splice(index, 1);
    } else {
        state.pendingAttachments.files.splice(index, 1);
    }

    updateAttachmentPreview();

    if (callbacks.onAttachmentsChanged) {
        callbacks.onAttachmentsChanged();
    }
}

/**
 * Clear all attachments
 */
export function clearAttachments() {
    // Revoke blob URLs
    for (const img of state.pendingAttachments.images) {
        if (img.previewUrl) {
            URL.revokeObjectURL(img.previewUrl);
        }
    }

    state.pendingAttachments = { images: [], files: [] };
    updateAttachmentPreview();

    if (callbacks.onAttachmentsChanged) {
        callbacks.onAttachmentsChanged();
    }
}

/**
 * Check if there are pending attachments
 * @returns {boolean}
 */
export function hasAttachments() {
    return state.pendingAttachments.images.length > 0 || state.pendingAttachments.files.length > 0;
}

/**
 * Get attachments for API request
 * @returns {Object} - Attachments in API format
 */
export function getAttachmentsForRequest() {
    const attachments = {
        images: [],
        files: [],
    };

    // Images
    for (const img of state.pendingAttachments.images) {
        attachments.images.push({
            name: img.name,
            media_type: img.type,
            data: img.base64,
        });
    }

    // Text files
    for (const file of state.pendingAttachments.files) {
        attachments.files.push({
            name: file.name,
            type: file.type,
            content: file.content,
        });
    }

    return attachments;
}

/**
 * Build display content with attachment info
 * @param {string} textContent - Text message content
 * @param {Object} attachments - Attachments object
 * @returns {string} - Display content
 */
export function buildDisplayContentWithAttachments(textContent, attachments) {
    const imageCount = attachments.images?.length || 0;
    const fileCount = attachments.files?.length || 0;

    let displayContent = '';

    if (imageCount > 0 || fileCount > 0) {
        const parts = [];
        if (imageCount > 0) {
            parts.push(`[${imageCount} image${imageCount > 1 ? 's' : ''} attached]`);
        }
        if (fileCount > 0) {
            parts.push(`[${fileCount} file${fileCount > 1 ? 's' : ''} attached]`);
        }
        displayContent = parts.join(' ') + '\n\n';
    }

    if (textContent) {
        displayContent += textContent;
    }

    return displayContent || '[Attachments only]';
}
