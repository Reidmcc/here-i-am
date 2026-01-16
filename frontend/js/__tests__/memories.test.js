/**
 * Unit Tests for Memories Module
 * Tests memory panel, search, and orphan handling
 */

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { state } from '../modules/state.js';
import {
    setElements,
    updateMemoriesPanel,
    handleMemoryUpdate,
    loadMemoryStats,
    searchMemories,
    checkForOrphans,
    cleanupOrphans,
} from '../modules/memories.js';

describe('Memories Module', () => {
    let mockElements;

    beforeEach(() => {
        // Reset state - memories is an array, not object
        state.retrievedMemories = [];
        state.retrievedMemoriesByEntity = {};
        state.expandedMemoryIds = new Set();
        state.selectedEntityId = 'entity-1';
        state._orphanData = null;

        // Create mock elements matching actual module usage
        mockElements = {
            memoriesContent: document.createElement('div'),
            memoryCount: document.createElement('span'),
        };

        // Create mock DOM elements for getElementById calls
        const memoryStats = document.createElement('div');
        memoryStats.id = 'memory-stats';
        document.body.appendChild(memoryStats);

        const memoryList = document.createElement('div');
        memoryList.id = 'memory-list';
        document.body.appendChild(memoryList);

        const memorySearchInput = document.createElement('input');
        memorySearchInput.id = 'memory-search-input';
        document.body.appendChild(memorySearchInput);

        const orphanStatus = document.createElement('div');
        orphanStatus.id = 'orphan-status';
        document.body.appendChild(orphanStatus);

        const orphanDetails = document.createElement('div');
        orphanDetails.id = 'orphan-details';
        document.body.appendChild(orphanDetails);

        const cleanupBtn = document.createElement('button');
        cleanupBtn.id = 'cleanup-orphans-btn';
        document.body.appendChild(cleanupBtn);

        const checkBtn = document.createElement('button');
        checkBtn.id = 'check-orphans-btn';
        document.body.appendChild(checkBtn);

        setElements(mockElements);

        vi.clearAllMocks();
    });

    afterEach(() => {
        vi.restoreAllMocks();
        // Clean up DOM elements
        document.body.innerHTML = '';
    });

    describe('updateMemoriesPanel', () => {
        it('should show empty state when no memories', () => {
            state.retrievedMemories = [];
            state.retrievedMemoriesByEntity = {};

            updateMemoriesPanel();

            expect(mockElements.memoriesContent.innerHTML).toContain('No memories retrieved');
        });

        it('should render memory items for single entity', () => {
            state.retrievedMemories = [
                {
                    id: 'mem-1',
                    content: 'Test memory content',
                    role: 'assistant',
                    times_retrieved: 5,
                    score: 0.85,
                },
            ];

            updateMemoriesPanel();

            expect(mockElements.memoriesContent.innerHTML).toContain('Test memory content');
        });

        it('should update memory count', () => {
            state.retrievedMemories = [
                { id: 'mem-1', content: 'Memory 1', role: 'human', score: 0.9 },
                { id: 'mem-2', content: 'Memory 2', role: 'assistant', score: 0.8 },
            ];

            updateMemoriesPanel();

            expect(mockElements.memoryCount.textContent).toBe('2');
        });

        it('should handle expanded memories', () => {
            state.retrievedMemories = [
                { id: 'mem-1', content: 'A'.repeat(500), role: 'human', score: 0.9 },
            ];
            state.expandedMemoryIds.add('mem-1');

            updateMemoriesPanel();

            const memoryItem = mockElements.memoriesContent.querySelector('.memory-item');
            expect(memoryItem.classList.contains('expanded')).toBe(true);
        });

        it('should display multi-entity memories grouped by entity', () => {
            state.retrievedMemoriesByEntity = {
                'entity-1': {
                    label: 'Claude',
                    memories: [
                        { id: 'mem-1', content: 'Claude memory', role: 'assistant', score: 0.9 },
                    ],
                },
                'entity-2': {
                    label: 'GPT',
                    memories: [
                        { id: 'mem-2', content: 'GPT memory', role: 'assistant', score: 0.8 },
                    ],
                },
            };

            updateMemoriesPanel();

            expect(mockElements.memoriesContent.innerHTML).toContain('Claude');
            expect(mockElements.memoriesContent.innerHTML).toContain('GPT');
        });
    });

    describe('handleMemoryUpdate', () => {
        it('should add new memories to state (single entity)', () => {
            const data = {
                new_memories: [
                    { id: 'mem-1', content: 'Memory 1', role: 'human', score: 0.9 },
                ],
            };

            handleMemoryUpdate(data);

            expect(state.retrievedMemories).toHaveLength(1);
            expect(state.retrievedMemories[0].id).toBe('mem-1');
        });

        it('should not duplicate existing memories', () => {
            state.retrievedMemories = [
                { id: 'mem-1', content: 'Original', role: 'human', score: 0.9 },
            ];

            handleMemoryUpdate({
                new_memories: [{ id: 'mem-1', content: 'Updated', role: 'human', score: 0.9 }],
            });

            expect(state.retrievedMemories).toHaveLength(1);
        });

        it('should handle trimmed memories', () => {
            state.retrievedMemories = [
                { id: 'mem-1', content: 'Memory 1', role: 'human', score: 0.9 },
                { id: 'mem-2', content: 'Memory 2', role: 'assistant', score: 0.8 },
            ];

            handleMemoryUpdate({
                trimmed_memory_ids: ['mem-1'],
            });

            expect(state.retrievedMemories).toHaveLength(1);
            expect(state.retrievedMemories[0].id).toBe('mem-2');
        });

        it('should handle multi-entity memory updates', () => {
            const data = {
                entity_id: 'entity-1',
                entity_label: 'Claude',
                new_memories: [
                    { id: 'mem-1', content: 'Memory', role: 'assistant', score: 0.9 },
                ],
            };

            handleMemoryUpdate(data);

            expect(state.retrievedMemoriesByEntity['entity-1']).toBeDefined();
            expect(state.retrievedMemoriesByEntity['entity-1'].memories).toHaveLength(1);
        });
    });

    describe('loadMemoryStats', () => {
        it('should call API to get stats', async () => {
            window.api.getMemoryStats = vi.fn(() => Promise.resolve({
                total_count: 100,
                human_count: 50,
                assistant_count: 50,
                avg_times_retrieved: 3,
            }));

            await loadMemoryStats();

            expect(window.api.getMemoryStats).toHaveBeenCalledWith('entity-1');
        });

        it('should display stats in UI', async () => {
            window.api.getMemoryStats = vi.fn(() => Promise.resolve({
                total_count: 100,
                human_count: 50,
                assistant_count: 50,
                avg_times_retrieved: 3,
            }));

            await loadMemoryStats();

            const statsEl = document.getElementById('memory-stats');
            expect(statsEl.innerHTML).toContain('100');
        });
    });

    describe('searchMemories', () => {
        it('should not search with empty query', async () => {
            const searchInput = document.getElementById('memory-search-input');
            searchInput.value = '';

            await searchMemories();

            expect(window.api.searchMemories).not.toHaveBeenCalled();
        });

        it('should call API with correct parameters', async () => {
            const searchInput = document.getElementById('memory-search-input');
            searchInput.value = 'test query';
            window.api.searchMemories = vi.fn(() => Promise.resolve([]));

            await searchMemories();

            expect(window.api.searchMemories).toHaveBeenCalledWith('test query', 10, true, 'entity-1');
        });

        it('should display search results', async () => {
            const searchInput = document.getElementById('memory-search-input');
            searchInput.value = 'test';
            window.api.searchMemories = vi.fn(() => Promise.resolve([
                { id: 'mem-1', content: 'Test result', role: 'human', score: 0.95, times_retrieved: 2 },
            ]));

            await searchMemories();

            const listEl = document.getElementById('memory-list');
            expect(listEl.innerHTML).toContain('Test result');
        });

        it('should show no results message', async () => {
            const searchInput = document.getElementById('memory-search-input');
            searchInput.value = 'test';
            window.api.searchMemories = vi.fn(() => Promise.resolve([]));

            await searchMemories();

            const listEl = document.getElementById('memory-list');
            expect(listEl.innerHTML).toContain('No matching memories');
        });
    });

    describe('checkForOrphans', () => {
        it('should call API to check orphans', async () => {
            window.api.listOrphanedRecords = vi.fn(() => Promise.resolve({
                orphans_found: 0,
                orphans: [],
            }));

            await checkForOrphans();

            expect(window.api.listOrphanedRecords).toHaveBeenCalledWith('entity-1');
        });

        it('should update orphan status when none found', async () => {
            window.api.listOrphanedRecords = vi.fn(() => Promise.resolve({
                orphans_found: 0,
                orphans: [],
            }));

            await checkForOrphans();

            const statusEl = document.getElementById('orphan-status');
            expect(statusEl.innerHTML).toContain('No orphaned records');
        });

        it('should enable cleanup button when orphans found', async () => {
            const cleanupBtn = document.getElementById('cleanup-orphans-btn');
            cleanupBtn.disabled = true;

            window.api.listOrphanedRecords = vi.fn(() => Promise.resolve({
                orphans_found: 3,
                orphans: [{ id: 'o1' }, { id: 'o2' }, { id: 'o3' }],
            }));

            await checkForOrphans();

            expect(cleanupBtn.disabled).toBe(false);
        });
    });

    describe('cleanupOrphans', () => {
        it('should not cleanup if no orphan data', async () => {
            state._orphanData = null;

            await cleanupOrphans();

            expect(window.api.cleanupOrphanedRecords).not.toHaveBeenCalled();
        });

        it('should call API to cleanup orphans', async () => {
            state._orphanData = {
                orphans_found: 2,
                orphans: [{ id: 'o1' }, { id: 'o2' }],
            };
            window.api.cleanupOrphanedRecords = vi.fn(() => Promise.resolve({
                orphans_deleted: 2,
                errors: [],
            }));
            vi.spyOn(window, 'confirm').mockReturnValue(true);

            await cleanupOrphans();

            expect(window.api.cleanupOrphanedRecords).toHaveBeenCalledWith('entity-1', false);
        });
    });
});
