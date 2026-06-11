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
    loadConversation,
    archiveConversation,
    unarchiveConversation,
    deleteConversation,
    showArchiveModalForConversation,
    showDeleteModal,
    renderConversationList,
} from '../modules/conversations.js';

describe('Conversations Module', () => {
    let mockElements;
    let mockCallbacks;

    beforeEach(() => {
        // Reset state
        state.conversations = [];
        state.currentConversationId = null;
        state.selectedEntityId = 'entity-1';
        state.isLoading = false;
        state.isMultiEntityMode = false;
        state.loadConversationsRequestId = 0;
        state.lastCreatedConversation = null;
        state.entities = [{ id: 'entity-1', label: 'Claude' }];
        state.pendingArchiveId = null;
        state.pendingDeleteId = null;
        state.settings = {
            conversationType: 'NORMAL',
            systemPrompt: '',
            model: 'claude-sonnet-4-5-20250929',
        };
        state.entitySystemPrompts = {};

        // Create mock elements
        mockElements = {
            conversationList: document.createElement('div'),
            newConversationBtn: document.createElement('button'),
            conversationTitle: document.createElement('h2'),
            conversationMeta: document.createElement('div'),
            archivedList: document.createElement('div'),
            renameInput: document.createElement('input'),
            deleteConversationTitle: document.createElement('span'),
        };

        // Create mock DOM elements for modals
        const archiveModal = document.createElement('div');
        archiveModal.id = 'archive-modal';
        const archiveBody = document.createElement('div');
        archiveBody.className = 'modal-body';
        archiveModal.appendChild(archiveBody);
        document.body.appendChild(archiveModal);

        mockCallbacks = {
            onConversationLoad: vi.fn(),
            onConversationCreated: vi.fn(),
            renderMessages: vi.fn(),
            updateHeader: vi.fn(),
            updateMemoriesPanel: vi.fn(),
            clearMessages: vi.fn(),
        };

        setElements(mockElements);
        setCallbacks(mockCallbacks);

        vi.clearAllMocks();
    });

    afterEach(() => {
        vi.restoreAllMocks();
        document.body.innerHTML = '';
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

        it('should pass entity ID to API', async () => {
            state.selectedEntityId = 'entity-2';
            window.api.listConversations = vi.fn(() => Promise.resolve([]));

            await loadConversations();

            expect(window.api.listConversations).toHaveBeenCalledWith(50, 0, 'entity-2');
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

        it('should not create when isLoading is true', async () => {
            state.isLoading = true;
            window.api.createConversation = vi.fn();

            await createNewConversation();

            expect(window.api.createConversation).not.toHaveBeenCalled();
        });

        it('should pass entity_id to API for single-entity mode', async () => {
            state.selectedEntityId = 'entity-1';
            window.api.createConversation = vi.fn(() => Promise.resolve({ id: 'new-conv' }));

            await createNewConversation();

            expect(window.api.createConversation).toHaveBeenCalledWith(
                expect.objectContaining({ entity_id: 'entity-1' })
            );
        });

        it('should send the selected model under the "model" key the backend expects', async () => {
            state.selectedEntityId = 'entity-1';
            state.settings.model = 'gpt-5.1';
            window.api.createConversation = vi.fn(() => Promise.resolve({ id: 'new-conv' }));

            await createNewConversation();

            const payload = window.api.createConversation.mock.calls[0][0];
            expect(payload.model).toBe('gpt-5.1');
            // The backend ConversationCreate has no "llm_model" field; sending it
            // would be silently dropped.
            expect(payload).not.toHaveProperty('llm_model');
        });
    });

    describe('loadConversation', () => {
        it('should update currentConversationId in state', async () => {
            window.api.getConversation = vi.fn(() => Promise.resolve({ id: 'conv-123' }));
            window.api.getConversationMessages = vi.fn(() => Promise.resolve([]));

            await loadConversation('conv-123');

            expect(state.currentConversationId).toBe('conv-123');
        });

        it('should fetch conversation and messages', async () => {
            window.api.getConversation = vi.fn(() => Promise.resolve({ id: 'conv-123' }));
            window.api.getConversationMessages = vi.fn(() => Promise.resolve([]));

            await loadConversation('conv-123');

            expect(window.api.getConversation).toHaveBeenCalledWith('conv-123');
            expect(window.api.getConversationMessages).toHaveBeenCalledWith('conv-123');
        });

        it('should call renderMessages callback with messages', async () => {
            const messages = [
                { id: 'msg-1', role: 'human', content: 'Hello' },
                { id: 'msg-2', role: 'assistant', content: 'Hi there' },
            ];
            window.api.getConversation = vi.fn(() => Promise.resolve({ id: 'conv-123' }));
            window.api.getConversationMessages = vi.fn(() => Promise.resolve(messages));

            await loadConversation('conv-123');

            expect(mockCallbacks.renderMessages).toHaveBeenCalledWith(messages, 'msg-2');
        });

        it('should call onConversationLoad callback', async () => {
            const conversation = { id: 'conv-123', title: 'Test' };
            window.api.getConversation = vi.fn(() => Promise.resolve(conversation));
            window.api.getConversationMessages = vi.fn(() => Promise.resolve([]));

            await loadConversation('conv-123');

            expect(mockCallbacks.onConversationLoad).toHaveBeenCalledWith(conversation, []);
        });

        it('should not load when isLoading is true', async () => {
            state.isLoading = true;
            window.api.getConversation = vi.fn();

            await loadConversation('conv-123');

            expect(window.api.getConversation).not.toHaveBeenCalled();
        });

        it('should update multi-entity state for multi-entity conversations', async () => {
            const entities = [
                { index_name: 'entity-1', label: 'Claude' },
                { index_name: 'entity-2', label: 'GPT' },
            ];
            window.api.getConversation = vi.fn(() => Promise.resolve({
                id: 'conv-123',
                conversation_type: 'multi_entity',
                entities: entities,
            }));
            window.api.getConversationMessages = vi.fn(() => Promise.resolve([]));

            await loadConversation('conv-123');

            expect(state.isMultiEntityMode).toBe(true);
            expect(state.currentConversationEntities).toEqual(entities);
        });

        it('should repopulate the in-context memories panel from session info', async () => {
            const memories = [
                { id: 'mem-1', role: 'human', content: 'remembered', times_retrieved: 2, score: 0.9 },
            ];
            window.api.getConversation = vi.fn(() => Promise.resolve({ id: 'conv-123' }));
            window.api.getConversationMessages = vi.fn(() => Promise.resolve([]));
            window.api.getSessionInfo = vi.fn(() => Promise.resolve({ memories }));

            await loadConversation('conv-123');

            expect(window.api.getSessionInfo).toHaveBeenCalledWith('conv-123');
            expect(state.retrievedMemories).toEqual(memories);
        });

        it('should repopulate per-entity memories for multi-entity conversations', async () => {
            const memoriesByEntity = {
                'entity-1': { label: 'Claude', memories: [{ id: 'm1', role: 'human', times_retrieved: 1 }] },
                'entity-2': { label: 'GPT', memories: [{ id: 'm2', role: 'assistant', times_retrieved: 4 }] },
            };
            window.api.getConversation = vi.fn(() => Promise.resolve({ id: 'conv-123' }));
            window.api.getConversationMessages = vi.fn(() => Promise.resolve([]));
            window.api.getSessionInfo = vi.fn(() => Promise.resolve({
                memories: [],
                memories_by_entity: memoriesByEntity,
            }));

            await loadConversation('conv-123');

            // Grouped map drives the panel; the flat list stays empty so the
            // panel renders entity sections rather than an ungrouped list.
            expect(state.retrievedMemoriesByEntity).toEqual(memoriesByEntity);
            expect(state.retrievedMemories).toEqual([]);
        });

        it('should still load when session info is unavailable', async () => {
            window.api.getConversation = vi.fn(() => Promise.resolve({ id: 'conv-123' }));
            window.api.getConversationMessages = vi.fn(() => Promise.resolve([]));
            window.api.getSessionInfo = vi.fn(() => Promise.reject(new Error('no session')));

            await loadConversation('conv-123');

            expect(state.currentConversationId).toBe('conv-123');
            expect(state.retrievedMemories).toEqual([]);
        });
    });

    describe('archiveConversation', () => {
        it('should call API to archive conversation', async () => {
            state.pendingArchiveId = 'conv-123';
            window.api.archiveConversation = vi.fn(() => Promise.resolve());
            window.api.listConversations = vi.fn(() => Promise.resolve([]));

            await archiveConversation();

            expect(window.api.archiveConversation).toHaveBeenCalledWith('conv-123');
        });

        it('should remove conversation from state.conversations', async () => {
            state.conversations = [{ id: 'conv-123' }, { id: 'conv-456' }];
            state.pendingArchiveId = 'conv-123';
            window.api.archiveConversation = vi.fn(() => Promise.resolve());

            await archiveConversation();

            expect(state.conversations).toHaveLength(1);
            expect(state.conversations[0].id).toBe('conv-456');
        });

        it('should use currentConversationId if pendingArchiveId not set', async () => {
            state.currentConversationId = 'conv-123';
            state.pendingArchiveId = null;
            window.api.archiveConversation = vi.fn(() => Promise.resolve());

            await archiveConversation();

            expect(window.api.archiveConversation).toHaveBeenCalledWith('conv-123');
        });

        it('should clear current conversation if archived', async () => {
            state.currentConversationId = 'conv-123';
            state.pendingArchiveId = 'conv-123';
            window.api.archiveConversation = vi.fn(() => Promise.resolve());

            await archiveConversation();

            expect(state.currentConversationId).toBeNull();
            expect(mockCallbacks.clearMessages).toHaveBeenCalled();
        });
    });

    describe('unarchiveConversation', () => {
        it('should call API to unarchive conversation', async () => {
            window.api.unarchiveConversation = vi.fn(() => Promise.resolve());
            window.api.listArchivedConversations = vi.fn(() => Promise.resolve([]));
            window.api.listConversations = vi.fn(() => Promise.resolve([]));

            await unarchiveConversation('conv-123');

            expect(window.api.unarchiveConversation).toHaveBeenCalledWith('conv-123');
        });

        it('should refresh both conversation lists', async () => {
            window.api.unarchiveConversation = vi.fn(() => Promise.resolve());
            window.api.listArchivedConversations = vi.fn(() => Promise.resolve([]));
            window.api.listConversations = vi.fn(() => Promise.resolve([]));

            await unarchiveConversation('conv-123');

            expect(window.api.listArchivedConversations).toHaveBeenCalled();
            expect(window.api.listConversations).toHaveBeenCalled();
        });
    });

    describe('deleteConversation', () => {
        it('should call API to delete conversation', async () => {
            state.pendingDeleteId = 'conv-123';
            window.api.deleteConversation = vi.fn(() => Promise.resolve());
            window.api.listArchivedConversations = vi.fn(() => Promise.resolve([]));

            await deleteConversation();

            expect(window.api.deleteConversation).toHaveBeenCalledWith('conv-123');
        });

        it('should not delete if pendingDeleteId is not set', async () => {
            state.pendingDeleteId = null;
            window.api.deleteConversation = vi.fn();

            await deleteConversation();

            expect(window.api.deleteConversation).not.toHaveBeenCalled();
        });
    });

    describe('showArchiveModalForConversation', () => {
        it('should set pendingArchiveId', () => {
            showArchiveModalForConversation('conv-123', 'Test Title');

            expect(state.pendingArchiveId).toBe('conv-123');
        });

        it('should populate modal body with conversation title', () => {
            showArchiveModalForConversation('conv-123', 'Test Title');

            const modalBody = document.querySelector('#archive-modal .modal-body');
            expect(modalBody.innerHTML).toContain('Test Title');
        });
    });

    describe('renderConversationList', () => {
        it('should render conversation items', () => {
            state.conversations = [
                { id: 'conv-1', title: 'First Conversation', created_at: '2025-01-01T00:00:00Z' },
                { id: 'conv-2', title: 'Second Conversation', created_at: '2025-01-02T00:00:00Z' },
            ];

            renderConversationList();

            expect(mockElements.conversationList.innerHTML).toContain('First Conversation');
            expect(mockElements.conversationList.innerHTML).toContain('Second Conversation');
        });

        it('should show empty message when no conversations', () => {
            state.conversations = [];

            renderConversationList();

            expect(mockElements.conversationList.innerHTML).toContain('No conversations');
        });

        it('should mark active conversation', () => {
            state.currentConversationId = 'conv-1';
            state.conversations = [
                { id: 'conv-1', title: 'Active', created_at: '2025-01-01T00:00:00Z' },
            ];

            renderConversationList();

            expect(mockElements.conversationList.innerHTML).toContain('active');
        });

        it('should show Untitled for conversations without title', () => {
            state.conversations = [
                { id: 'conv-1', title: null, created_at: '2025-01-01T00:00:00Z' },
            ];

            renderConversationList();

            expect(mockElements.conversationList.innerHTML).toContain('Untitled');
        });
    });
});
