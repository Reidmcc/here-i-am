/**
 * Unit Tests for Modals Module
 * Tests modal management functionality
 */

import { describe, it, expect, beforeEach } from 'vitest';
import {
    setElements,
    showModal,
    hideModal,
    closeActiveModal,
    isModalOpen,
    closeAllDropdowns,
} from '../modules/modals.js';

describe('Modals Module', () => {
    let mockElements;

    beforeEach(() => {
        // Create mock modal elements
        mockElements = {
            settingsModal: document.createElement('div'),
            memoriesModal: document.createElement('div'),
            archiveModal: document.createElement('div'),
            renameModal: document.createElement('div'),
            deleteModal: document.createElement('div'),
            archivedModal: document.createElement('div'),
            voiceCloneModal: document.createElement('div'),
            voiceEditModal: document.createElement('div'),
            multiEntityModal: document.createElement('div'),
            goNewGameModal: document.createElement('div'),
        };

        // Set IDs for debugging
        Object.entries(mockElements).forEach(([name, el]) => {
            el.id = name;
        });

        setElements(mockElements);
    });

    describe('showModal', () => {
        it('should add active class to modal', () => {
            showModal('settingsModal');
            expect(mockElements.settingsModal.classList.contains('active')).toBe(true);
        });

        it('should work for different modals', () => {
            showModal('memoriesModal');
            expect(mockElements.memoriesModal.classList.contains('active')).toBe(true);

            showModal('archiveModal');
            expect(mockElements.archiveModal.classList.contains('active')).toBe(true);
        });

        it('should handle unknown modal names gracefully', () => {
            expect(() => showModal('unknownModal')).not.toThrow();
        });

        it('should not throw when elements not set', () => {
            setElements({});
            expect(() => showModal('settingsModal')).not.toThrow();
        });
    });

    describe('hideModal', () => {
        beforeEach(() => {
            // Open all modals first
            Object.values(mockElements).forEach(el => {
                el.classList.add('active');
            });
        });

        it('should remove active class from modal', () => {
            hideModal('settingsModal');
            expect(mockElements.settingsModal.classList.contains('active')).toBe(false);
        });

        it('should work for different modals', () => {
            hideModal('memoriesModal');
            expect(mockElements.memoriesModal.classList.contains('active')).toBe(false);

            hideModal('deleteModal');
            expect(mockElements.deleteModal.classList.contains('active')).toBe(false);
        });

        it('should handle unknown modal names gracefully', () => {
            expect(() => hideModal('unknownModal')).not.toThrow();
        });

        it('should not throw when modal already hidden', () => {
            mockElements.settingsModal.classList.remove('active');
            expect(() => hideModal('settingsModal')).not.toThrow();
        });
    });

    describe('closeActiveModal', () => {
        it('should close the first active modal found', () => {
            mockElements.settingsModal.classList.add('active');
            mockElements.memoriesModal.classList.add('active');

            closeActiveModal();

            // Should close settingsModal (first in the list)
            expect(mockElements.settingsModal.classList.contains('active')).toBe(false);
            // memoriesModal should still be open (only first is closed)
            expect(mockElements.memoriesModal.classList.contains('active')).toBe(true);
        });

        it('should handle no active modals gracefully', () => {
            expect(() => closeActiveModal()).not.toThrow();
        });

        it('should close modals in priority order', () => {
            // Open only memoriesModal
            mockElements.memoriesModal.classList.add('active');

            closeActiveModal();

            expect(mockElements.memoriesModal.classList.contains('active')).toBe(false);
        });
    });

    describe('isModalOpen', () => {
        it('should return true when a modal is active', () => {
            mockElements.settingsModal.classList.add('active');
            expect(isModalOpen()).toBe(true);
        });

        it('should return false when no modals are active', () => {
            expect(isModalOpen()).toBe(false);
        });

        it('should detect any active modal', () => {
            mockElements.multiEntityModal.classList.add('active');
            expect(isModalOpen()).toBe(true);
        });

        it('should handle missing elements gracefully', () => {
            setElements({});
            expect(isModalOpen()).toBe(false);
        });

        it('should return true for any of the tracked modals', () => {
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
                'goNewGameModal',
            ];

            for (const modalName of modalNames) {
                // Reset all modals
                Object.values(mockElements).forEach(el => el.classList.remove('active'));

                // Activate specific modal
                mockElements[modalName].classList.add('active');

                expect(isModalOpen()).toBe(true);
            }
        });
    });

    describe('closeAllDropdowns', () => {
        it('should remove active class from all conversation dropdowns', () => {
            // Create mock dropdowns in document
            const dropdown1 = document.createElement('div');
            dropdown1.className = 'conversation-dropdown active';
            const dropdown2 = document.createElement('div');
            dropdown2.className = 'conversation-dropdown active';
            const dropdown3 = document.createElement('div');
            dropdown3.className = 'conversation-dropdown'; // Not active

            document.body.appendChild(dropdown1);
            document.body.appendChild(dropdown2);
            document.body.appendChild(dropdown3);

            closeAllDropdowns();

            expect(dropdown1.classList.contains('active')).toBe(false);
            expect(dropdown2.classList.contains('active')).toBe(false);
            expect(dropdown3.classList.contains('active')).toBe(false);
        });

        it('should handle no dropdowns gracefully', () => {
            expect(() => closeAllDropdowns()).not.toThrow();
        });

        it('should not affect non-dropdown elements', () => {
            const otherElement = document.createElement('div');
            otherElement.className = 'other-element active';
            document.body.appendChild(otherElement);

            closeAllDropdowns();

            expect(otherElement.classList.contains('active')).toBe(true);
        });
    });

    describe('setElements', () => {
        it('should accept new element references', () => {
            const newElements = {
                settingsModal: document.createElement('div'),
            };

            setElements(newElements);
            newElements.settingsModal.classList.add('test');

            showModal('settingsModal');
            expect(newElements.settingsModal.classList.contains('active')).toBe(true);
        });

        it('should handle empty object', () => {
            setElements({});
            expect(() => showModal('settingsModal')).not.toThrow();
            expect(() => hideModal('settingsModal')).not.toThrow();
        });
    });

    describe('modal workflow', () => {
        it('should support show -> hide cycle', () => {
            showModal('settingsModal');
            expect(isModalOpen()).toBe(true);

            hideModal('settingsModal');
            expect(isModalOpen()).toBe(false);
        });

        it('should support multiple modals open at once', () => {
            showModal('settingsModal');
            showModal('memoriesModal');

            expect(mockElements.settingsModal.classList.contains('active')).toBe(true);
            expect(mockElements.memoriesModal.classList.contains('active')).toBe(true);
            expect(isModalOpen()).toBe(true);
        });

        it('should support closing one of multiple open modals', () => {
            showModal('settingsModal');
            showModal('memoriesModal');

            hideModal('settingsModal');

            expect(mockElements.settingsModal.classList.contains('active')).toBe(false);
            expect(mockElements.memoriesModal.classList.contains('active')).toBe(true);
            expect(isModalOpen()).toBe(true);
        });
    });
});
