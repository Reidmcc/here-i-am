/**
 * Unit Tests for Conversations Module
 * Tests conversation CRUD, loading, and archiving
 */

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { state } from '../modules/state.js';
import {
    setElements,
    setCallbacks,
    loadConversations,
    createNewConversation,
    selectConversation,
    deleteConversation,
    archiveConversation,
    unarchiveConversation,
} from '../modules/conversations.js';

describe('Conversations Module', () => {
    let mockElements;
    let mockCallbacks;

    beforeEach(() => {
        // Reset state
        state.conversations = [];
        state.currentConversationId = null;
        state.selectedEntityId = 'entity-1';

        // Create mock elements
        mockElements = {
            conversationList: document.createElement('div'),
            newConversationBtn: document.createElement('button'),
        };

        mockCallbacks = {
            onConversationSelected: vi.fn(),
            onConversationCreated: vi.fn(),
            onConversationDeleted: vi.fn(),
        };

        setElements(mockElements);
        setCallbacks(mockCallbacks);

        vi.clearAllMocks();
    });

    afterEach(() => {
        vi.restoreAllMocks();
    });

    describe('loadConversations', () => {
        it('should fetch conversations from API', async () => {
            window.api.listConversations = vi.fn(() => Promise.resolve([]));

            await loadConversations();

            expect(window.api.listConversations).toHaveBeenCalled();
        });

        it('should store conversations in state', async () => {
            const conversations = [
                { id: 'conv-1', title: 'Test Conv' },
            ];
            window.api.listConversations = vi.fn(() => Promise.resolve(conversations));

            await loadConversations();

            expect(state.conversations).toEqual(conversations);
        });

        it('should render conversation list', async () => {
            window.api.listConversations = vi.fn(() => Promise.resolve([
                { id: 'conv-1', title: 'Test Conversation' },
            ]));

            await loadConversations();

            expect(mockElements.conversationList.innerHTML).toContain('Test Conversation');
        });

        it('should show empty state when no conversations', async () => {
            window.api.listConversations = vi.fn(() => Promise.resolve([]));

            await loadConversations();

            expect(mockElements.conversationList.innerHTML).toContain('No conversations');
        });
    });

    describe('createNewConversation', () => {
        it('should call API to create conversation', async () => {
            window.api.createConversation = vi.fn(() => Promise.resolve({ id: 'new-conv' }));
            window.api.listConversations = vi.fn(() => Promise.resolve([]));

            await createNewConversation();

            expect(window.api.createConversation).toHaveBeenCalled();
        });

        it('should set currentConversationId to new conversation', async () => {
            window.api.createConversation = vi.fn(() => Promise.resolve({ id: 'new-conv' }));
            window.api.listConversations = vi.fn(() => Promise.resolve([{ id: 'new-conv' }]));

            await createNewConversation();

            expect(state.currentConversationId).toBe('new-conv');
        });

        it('should call onConversationCreated callback', async () => {
            window.api.createConversation = vi.fn(() => Promise.resolve({ id: 'new-conv' }));
            window.api.listConversations = vi.fn(() => Promise.resolve([]));

            await createNewConversation();

            expect(mockCallbacks.onConversationCreated).toHaveBeenCalled();
        });
    });

    describe('selectConversation', () => {
        it('should update currentConversationId in state', async () => {
            window.api.getConversationMessages = vi.fn(() => Promise.resolve([]));

            await selectConversation('conv-123');

            expect(state.currentConversationId).toBe('conv-123');
        });

        it('should fetch conversation messages', async () => {
            window.api.getConversationMessages = vi.fn(() => Promise.resolve([]));

            await selectConversation('conv-123');

            expect(window.api.getConversationMessages).toHaveBeenCalledWith('conv-123');
        });

        it('should call onConversationSelected callback', async () => {
            window.api.getConversationMessages = vi.fn(() => Promise.resolve([]));

            await selectConversation('conv-123');

            expect(mockCallbacks.onConversationSelected).toHaveBeenCalled();
        });
    });

    describe('deleteConversation', () => {
        it('should call API to delete conversation', async () => {
            window.api.deleteConversation = vi.fn(() => Promise.resolve());
            window.api.listConversations = vi.fn(() => Promise.resolve([]));

            // Mock window.confirm
            vi.spyOn(window, 'confirm').mockReturnValue(true);

            await deleteConversation('conv-123');

            expect(window.api.deleteConversation).toHaveBeenCalledWith('conv-123');
        });

        it('should not delete if not confirmed', async () => {
            vi.spyOn(window, 'confirm').mockReturnValue(false);

            await deleteConversation('conv-123');

            expect(window.api.deleteConversation).not.toHaveBeenCalled();
        });

        it('should call onConversationDeleted callback after deletion', async () => {
            window.api.deleteConversation = vi.fn(() => Promise.resolve());
            window.api.listConversations = vi.fn(() => Promise.resolve([]));
            vi.spyOn(window, 'confirm').mockReturnValue(true);

            await deleteConversation('conv-123');

            expect(mockCallbacks.onConversationDeleted).toHaveBeenCalled();
        });
    });

    describe('archiveConversation', () => {
        it('should call API to archive conversation', async () => {
            window.api.archiveConversation = vi.fn(() => Promise.resolve());
            window.api.listConversations = vi.fn(() => Promise.resolve([]));

            await archiveConversation('conv-123');

            expect(window.api.archiveConversation).toHaveBeenCalledWith('conv-123');
        });

        it('should refresh conversation list after archiving', async () => {
            window.api.archiveConversation = vi.fn(() => Promise.resolve());
            window.api.listConversations = vi.fn(() => Promise.resolve([]));

            await archiveConversation('conv-123');

            expect(window.api.listConversations).toHaveBeenCalled();
        });
    });

    describe('unarchiveConversation', () => {
        it('should call API to unarchive conversation', async () => {
            window.api.unarchiveConversation = vi.fn(() => Promise.resolve());
            window.api.listArchivedConversations = vi.fn(() => Promise.resolve([]));

            await unarchiveConversation('conv-123');

            expect(window.api.unarchiveConversation).toHaveBeenCalledWith('conv-123');
        });
    });
});
