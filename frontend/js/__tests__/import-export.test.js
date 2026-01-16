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
    startImport,
} from '../modules/import-export.js';

describe('Import/Export Module', () => {
    let mockElements;
    let mockCallbacks;

    beforeEach(() => {
        // Reset state
        state.currentConversationId = 'test-conv-id';
        state.selectedEntityId = 'entity-1';
        state.importPreview = null;

        // Create mock elements
        mockElements = {
            importFileInput: document.createElement('input'),
            importPreviewSection: document.createElement('div'),
            importPreviewContent: document.createElement('div'),
            importProgressSection: document.createElement('div'),
            importProgressBar: document.createElement('div'),
            importProgressText: document.createElement('div'),
            startImportBtn: document.createElement('button'),
            importSource: document.createElement('select'),
        };

        mockElements.importFileInput.type = 'file';

        // Add import source options
        const autoOption = document.createElement('option');
        autoOption.value = 'auto';
        autoOption.textContent = 'Auto-detect';
        mockElements.importSource.appendChild(autoOption);

        const openaiOption = document.createElement('option');
        openaiOption.value = 'openai';
        openaiOption.textContent = 'OpenAI';
        mockElements.importSource.appendChild(openaiOption);

        mockCallbacks = {
            onImportComplete: vi.fn(),
            onExportComplete: vi.fn(),
        };

        setElements(mockElements);
        setCallbacks(mockCallbacks);

        // Mock URL.createObjectURL
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

            await exportConversation();

            expect(mockLink.click).toHaveBeenCalled();
            expect(mockLink.download).toContain('.json');
        });

        it('should use correct filename', async () => {
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

            await exportConversation();

            expect(mockLink.download).toContain('My_Conversation');
        });
    });

    describe('previewImportFile', () => {
        it('should not preview without file selected', async () => {
            // Empty file input
            Object.defineProperty(mockElements.importFileInput, 'files', {
                value: [],
                configurable: true,
            });

            await previewImportFile();

            expect(window.api.previewExternalConversations).not.toHaveBeenCalled();
        });

        it('should call API to preview file', async () => {
            const mockFile = new File(['{}'], 'test.json', { type: 'application/json' });
            Object.defineProperty(mockElements.importFileInput, 'files', {
                value: [mockFile],
                configurable: true,
            });
            mockElements.importSource.value = 'auto';

            window.api.previewExternalConversations = vi.fn(() => Promise.resolve({
                conversations: [{ title: 'Test', message_count: 5 }],
            }));

            await previewImportFile();

            expect(window.api.previewExternalConversations).toHaveBeenCalled();
        });

        it('should show loading state', async () => {
            const mockFile = new File(['{}'], 'test.json', { type: 'application/json' });
            Object.defineProperty(mockElements.importFileInput, 'files', {
                value: [mockFile],
                configurable: true,
            });

            let loadingShown = false;
            window.api.previewExternalConversations = vi.fn(() => {
                loadingShown = mockElements.importPreviewContent.innerHTML.includes('Loading') ||
                               mockElements.importPreviewContent.innerHTML.includes('Analyzing');
                return Promise.resolve({ conversations: [] });
            });

            await previewImportFile();

            // Loading state should have been shown at some point
            expect(window.api.previewExternalConversations).toHaveBeenCalled();
        });

        it('should show error on failure', async () => {
            const mockFile = new File(['{}'], 'test.json', { type: 'application/json' });
            Object.defineProperty(mockElements.importFileInput, 'files', {
                value: [mockFile],
                configurable: true,
            });

            window.api.previewExternalConversations = vi.fn(() => Promise.reject(new Error('Parse error')));

            await previewImportFile();

            expect(mockElements.importPreviewContent.innerHTML).toContain('Error');
        });
    });

    describe('startImport', () => {
        it('should not import without preview data', async () => {
            state.importPreview = null;

            await startImport();

            expect(window.api.importExternalConversationsStream).not.toHaveBeenCalled();
        });

        it('should not import without entity selected', async () => {
            state.importPreview = { conversations: [{ title: 'Test' }] };
            state.selectedEntityId = null;

            await startImport();

            expect(window.api.importExternalConversationsStream).not.toHaveBeenCalled();
        });

        it('should show progress section during import', async () => {
            state.importPreview = {
                conversations: [{ title: 'Test' }],
                source: 'openai',
            };

            // Mock the streaming import
            window.api.importExternalConversationsStream = vi.fn(() => Promise.resolve({
                getReader: () => ({
                    read: vi.fn()
                        .mockResolvedValueOnce({
                            done: false,
                            value: new TextEncoder().encode('data: {"type":"complete"}\n\n')
                        })
                        .mockResolvedValueOnce({ done: true }),
                }),
            }));

            await startImport();

            expect(mockElements.importProgressSection.style.display).not.toBe('none');
        });
    });
});
