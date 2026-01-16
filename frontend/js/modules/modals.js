/**
 * Modal Management Module
 * Handles showing, hiding, and managing modals
 */

import { state } from './state.js';

// Cache of modal elements
let elements = {};

/**
 * Set element references for modals
 * @param {Object} els - Object containing modal element references
 */
export function setElements(els) {
    elements = els;
}

/**
 * Show a modal by name
 * @param {string} modalName - Name of the modal element
 */
export function showModal(modalName) {
    if (elements[modalName]) {
        elements[modalName].classList.add('active');
    }
}

/**
 * Hide a modal by name
 * @param {string} modalName - Name of the modal element
 */
export function hideModal(modalName) {
    if (elements[modalName]) {
        elements[modalName].classList.remove('active');
    }
}

/**
 * Close any active modal
 */
export function closeActiveModal() {
    const modalNames = [
        'settingsModal',
        'memoriesModal',
        'archiveModal',
        'renameModal',
        'deleteModal',
        'archivedModal',
        'voiceCloneModal',
        'voiceEditModal',
        'multiEntityModal',
        'gamesModal',
        'gameBoardModal'
    ];

    // Find and close the first active modal
    for (const modalName of modalNames) {
        if (elements[modalName]?.classList.contains('active')) {
            hideModal(modalName);
            return;
        }
    }
}

/**
 * Check if any modal is currently open
 * @returns {boolean}
 */
export function isModalOpen() {
    const modalNames = [
        'settingsModal',
        'memoriesModal',
        'archiveModal',
        'renameModal',
        'deleteModal',
        'archivedModal',
        'voiceCloneModal',
        'voiceEditModal',
        'multiEntityModal',
        'gamesModal',
        'gameBoardModal'
    ];

    for (const modalName of modalNames) {
        if (elements[modalName]?.classList.contains('active')) {
            return true;
        }
    }
    return false;
}

/**
 * Close all dropdown menus
 */
export function closeAllDropdowns() {
    document.querySelectorAll('.conversation-dropdown.active').forEach(dropdown => {
        dropdown.classList.remove('active');
    });
}
