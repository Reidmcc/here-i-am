/**
 * Unit Tests for Import/Export Module
 * Tests conversation import and export functionality
 */

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { state } from '../modules/state.js';
import {
    setElements,
    setCallbacks,
    exportConversation,
    previewImportFile,
    importExternalConversations,
    handleImportFileChange,
    resetImportModal,
} from '../modules/import-export.js';
import { readFileAsText } from '../modules/utils.js';

// Mock readFileAsText
vi.mock('../modules/utils.js', async (importOriginal) => {
    const actual = await importOriginal();
    return {
        ...actual,
        readFileAsText: vi.fn(),
    };
});

describe('Import/Export Module', () => {
    let mockElements;
    let mockCallbacks;

    beforeEach(() => {
        // Reset state
        state.currentConversationId = 'test-conv-id';
        state.selectedEntityId = 'entity-1';

        // Create mock elements matching actual module usage
        mockElements = {
            importFile: document.createElement('input'),
            importPreviewBtn: document.createElement('button'),
            importStatus: document.createElement('div'),
            importStep1: document.createElement('div'),
            importStep2: document.createElement('div'),
            importProgress: document.createElement('div'),
            importProgressBar: document.createElement('div'),
            importProgressText: document.createElement('div'),
            importPreviewInfo: document.createElement('div'),
            importConversationList: document.createElement('div'),
            importSource: document.createElement('select'),
            importAllowReimport: document.createElement('input'),
            importBtn: document.createElement('button'),
            importCancelBtn: document.createElement('button'),
        };

        mockElements.importFile.type = 'file';
        mockElements.importAllowReimport.type = 'checkbox';

        // Add import source options
        const autoOption = document.createElement('option');
        autoOption.value = '';
        autoOption.textContent = 'Auto-detect';
        mockElements.importSource.appendChild(autoOption);

        mockCallbacks = {
            loadConversations: vi.fn(),
        };

        setElements(mockElements);
        setCallbacks(mockCallbacks);

        // Mock URL methods
        global.URL.createObjectURL = vi.fn(() => 'blob:test');
        global.URL.revokeObjectURL = vi.fn();

        vi.clearAllMocks();
    });

    afterEach(() => {
        vi.restoreAllMocks();
    });

    describe('exportConversation', () => {
        it('should not export without conversation selected', async () => {
            state.currentConversationId = null;
            window.api.exportConversation = vi.fn();

            await exportConversation();

            expect(window.api.exportConversation).not.toHaveBeenCalled();
        });

        it('should call API to export conversation', async () => {
            window.api.exportConversation = vi.fn(() => Promise.resolve({
                id: 'test-conv-id',
                title: 'Test',
                messages: [],
            }));

            await exportConversation();

            expect(window.api.exportConversation).toHaveBeenCalledWith('test-conv-id');
        });

        it('should create and download JSON file', async () => {
            window.api.exportConversation = vi.fn(() => Promise.resolve({
                id: 'test-conv-id',
                title: 'Test Conversation',
                messages: [],
            }));

            // Mock createElement to capture the download link
            const mockLink = {
                href: '',
                download: '',
                click: vi.fn(),
            };
            const originalCreateElement = document.createElement.bind(document);
            vi.spyOn(document, 'createElement').mockImplementation((tag) => {
                if (tag === 'a') return mockLink;
                return originalCreateElement(tag);
            });

            // Mock document.body.appendChild and removeChild
            vi.spyOn(document.body, 'appendChild').mockImplementation(() => {});
            vi.spyOn(document.body, 'removeChild').mockImplementation(() => {});

            await exportConversation();

            expect(mockLink.click).toHaveBeenCalled();
            expect(mockLink.download).toContain('.json');
        });

        it('should use conversation ID in filename', async () => {
            window.api.exportConversation = vi.fn(() => Promise.resolve({
                id: 'test-conv-id',
                title: 'My Conversation',
                messages: [],
            }));

            const mockLink = { href: '', download: '', click: vi.fn() };
            const originalCreateElement = document.createElement.bind(document);
            vi.spyOn(document, 'createElement').mockImplementation((tag) => {
                if (tag === 'a') return mockLink;
                return originalCreateElement(tag);
            });
            vi.spyOn(document.body, 'appendChild').mockImplementation(() => {});
            vi.spyOn(document.body, 'removeChild').mockImplementation(() => {});

            await exportConversation();

            // Module uses conversation-{id}.json format
            expect(mockLink.download).toContain('conversation-test-conv-id.json');
        });
    });

    describe('handleImportFileChange', () => {
        it('should enable preview button when file selected', () => {
            mockElements.importPreviewBtn.disabled = true;
            Object.defineProperty(mockElements.importFile, 'files', {
                value: [new File(['{}'], 'test.json')],
                configurable: true,
            });

            handleImportFileChange();

            expect(mockElements.importPreviewBtn.disabled).toBe(false);
        });

        it('should disable preview button when no file selected', () => {
            mockElements.importPreviewBtn.disabled = false;
            Object.defineProperty(mockElements.importFile, 'files', {
                value: [],
                configurable: true,
            });

            handleImportFileChange();

            expect(mockElements.importPreviewBtn.disabled).toBe(true);
        });

        it('should hide import status', () => {
            mockElements.importStatus.style.display = 'block';
            Object.defineProperty(mockElements.importFile, 'files', {
                value: [],
                configurable: true,
            });

            handleImportFileChange();

            expect(mockElements.importStatus.style.display).toBe('none');
        });
    });

    describe('previewImportFile', () => {
        it('should not preview without file selected', async () => {
            Object.defineProperty(mockElements.importFile, 'files', {
                value: [],
                configurable: true,
            });
            window.api.previewExternalConversations = vi.fn();

            await previewImportFile();

            expect(window.api.previewExternalConversations).not.toHaveBeenCalled();
        });

        it('should call API to preview file', async () => {
            const mockFile = new File(['{}'], 'test.json', { type: 'application/json' });
            Object.defineProperty(mockElements.importFile, 'files', {
                value: [mockFile],
                configurable: true,
            });

            // Mock readFileAsText
            readFileAsText.mockResolvedValue('{"conversations": []}');

            window.api.previewExternalConversations = vi.fn(() => Promise.resolve({
                conversations: [{ title: 'Test', message_count: 5, index: 0 }],
                total_conversations: 1,
                source_format: 'openai',
            }));

            await previewImportFile();

            expect(window.api.previewExternalConversations).toHaveBeenCalled();
        });

        it('should show loading state during preview', async () => {
            const mockFile = new File(['{}'], 'test.json', { type: 'application/json' });
            Object.defineProperty(mockElements.importFile, 'files', {
                value: [mockFile],
                configurable: true,
            });

            readFileAsText.mockResolvedValue('{}');

            let statusDuringCall = '';
            window.api.previewExternalConversations = vi.fn(() => {
                statusDuringCall = mockElements.importStatus.textContent;
                return Promise.resolve({
                    conversations: [],
                    total_conversations: 0,
                    source_format: 'auto',
                });
            });

            await previewImportFile();

            expect(statusDuringCall).toContain('Analyzing');
        });

        it('should show error on API failure', async () => {
            const mockFile = new File(['{}'], 'test.json', { type: 'application/json' });
            Object.defineProperty(mockElements.importFile, 'files', {
                value: [mockFile],
                configurable: true,
            });

            readFileAsText.mockResolvedValue('{}');
            window.api.previewExternalConversations = vi.fn(() =>
                Promise.reject(new Error('Parse error'))
            );

            await previewImportFile();

            expect(mockElements.importStatus.className).toContain('error');
            expect(mockElements.importStatus.textContent).toContain('Parse error');
        });
    });

    describe('importExternalConversations', () => {
        it('should not import without entity selected', async () => {
            state.selectedEntityId = null;
            window.api.importExternalConversationsStream = vi.fn();

            await importExternalConversations();

            expect(window.api.importExternalConversationsStream).not.toHaveBeenCalled();
        });

        it('should show progress during import', async () => {
            // Setup internal state by calling preview first
            const mockFile = new File(['{}'], 'test.json', { type: 'application/json' });
            Object.defineProperty(mockElements.importFile, 'files', {
                value: [mockFile],
                configurable: true,
            });

            readFileAsText.mockResolvedValue('{}');
            window.api.previewExternalConversations = vi.fn(() => Promise.resolve({
                conversations: [{ title: 'Test', message_count: 5, index: 0, already_imported: false, imported_count: 0 }],
                total_conversations: 1,
                source_format: 'openai',
            }));

            await previewImportFile();

            // Add checkboxes to the conversation list for selection
            mockElements.importConversationList.innerHTML = `
                <input type="checkbox" class="import-cb-memory" data-index="0" checked>
                <input type="checkbox" class="import-cb-history" data-index="0">
            `;

            let progressShownDuringImport = false;
            window.api.importExternalConversationsStream = vi.fn((data, handlers) => {
                // Check progress visibility during the import call
                progressShownDuringImport = mockElements.importProgress.style.display === 'block';
                handlers.onStart({ total_conversations: 1, total_messages: 5 });
                handlers.onDone({
                    conversations_imported: 1,
                    messages_imported: 5,
                    messages_skipped: 0,
                    conversations_to_history: 0,
                    memories_stored: 5,
                });
                return Promise.resolve();
            });

            await importExternalConversations();

            // Progress should have been shown during the import
            expect(progressShownDuringImport).toBe(true);
        });
    });

    describe('resetImportModal', () => {
        it('should reset file input to empty value', () => {
            // File inputs can only have their value programmatically set to empty string
            // We verify the module sets value to '' (which is the reset behavior)
            resetImportModal();

            expect(mockElements.importFile.value).toBe('');
        });

        it('should hide import status', () => {
            mockElements.importStatus.style.display = 'block';

            resetImportModal();

            expect(mockElements.importStatus.style.display).toBe('none');
        });

        it('should show step 1 and hide step 2', () => {
            mockElements.importStep1.style.display = 'none';
            mockElements.importStep2.style.display = 'block';

            resetImportModal();

            expect(mockElements.importStep1.style.display).toBe('block');
            expect(mockElements.importStep2.style.display).toBe('none');
        });

        it('should disable preview button', () => {
            mockElements.importPreviewBtn.disabled = false;

            resetImportModal();

            expect(mockElements.importPreviewBtn.disabled).toBe(true);
        });
    });
});
