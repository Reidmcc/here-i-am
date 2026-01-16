/**
 * Unit Tests for Entities Module
 * Tests entity loading, selection, and multi-entity functionality
 */

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { state } from '../modules/state.js';
import {
    setElements,
    setCallbacks,
    loadEntities,
    handleEntityChange,
    getEntityLabel,
} from '../modules/entities.js';

describe('Entities Module', () => {
    let mockElements;
    let mockCallbacks;

    beforeEach(() => {
        // Reset state
        state.entities = [];
        state.selectedEntityId = null;
        state.isMultiEntityMode = false;
        state.currentConversationEntities = [];

        // Create mock elements
        mockElements = {
            entitySelect: document.createElement('select'),
            entityInfo: document.createElement('div'),
            entityResponderSelector: document.createElement('div'),
        };

        // Add options to entity select
        const defaultOption = document.createElement('option');
        defaultOption.value = '';
        defaultOption.textContent = 'Select Entity';
        mockElements.entitySelect.appendChild(defaultOption);

        mockCallbacks = {
            onEntityLoaded: vi.fn(),
            onEntityChanged: vi.fn(),
        };

        setElements(mockElements);
        setCallbacks(mockCallbacks);

        vi.clearAllMocks();
    });

    afterEach(() => {
        vi.restoreAllMocks();
    });

    describe('loadEntities', () => {
        it('should fetch entities from API', async () => {
            window.api.listEntities = vi.fn(() => Promise.resolve({
                entities: [
                    { id: 'entity-1', label: 'Claude', description: 'Primary AI' },
                ],
                default_entity: 'entity-1',
            }));

            await loadEntities();

            expect(window.api.listEntities).toHaveBeenCalled();
        });

        it('should populate entity select dropdown', async () => {
            window.api.listEntities = vi.fn(() => Promise.resolve({
                entities: [
                    { id: 'entity-1', label: 'Claude' },
                    { id: 'entity-2', label: 'GPT' },
                ],
                default_entity: 'entity-1',
            }));

            await loadEntities();

            // Should have options for entities plus multi-entity
            expect(mockElements.entitySelect.options.length).toBeGreaterThan(1);
        });

        it('should store entities in state', async () => {
            const entities = [
                { id: 'entity-1', label: 'Claude' },
            ];
            window.api.listEntities = vi.fn(() => Promise.resolve({
                entities,
                default_entity: 'entity-1',
            }));

            await loadEntities();

            expect(state.entities).toEqual(entities);
        });

        it('should call onEntityLoaded callback', async () => {
            window.api.listEntities = vi.fn(() => Promise.resolve({
                entities: [{ id: 'entity-1', label: 'Claude' }],
                default_entity: 'entity-1',
            }));

            await loadEntities();

            expect(mockCallbacks.onEntityLoaded).toHaveBeenCalled();
        });
    });

    describe('getEntityLabel', () => {
        it('should return empty string when entity not found', () => {
            state.entities = [];

            const result = getEntityLabel('nonexistent');

            expect(result).toBe('');
        });

        it('should return entity label when found', () => {
            state.entities = [
                { id: 'entity-1', label: 'Claude' },
            ];

            const result = getEntityLabel('entity-1');

            expect(result).toBe('Claude');
        });
    });

    describe('handleEntityChange', () => {
        it('should update selectedEntityId in state', () => {
            state.entities = [{ id: 'entity-1', label: 'Claude' }];

            handleEntityChange('entity-1');

            expect(state.selectedEntityId).toBe('entity-1');
        });

        it('should call onEntityChanged callback', () => {
            state.entities = [{ id: 'entity-1', label: 'Claude' }];

            handleEntityChange('entity-1');

            expect(mockCallbacks.onEntityChanged).toHaveBeenCalled();
        });
    });

    describe('state.isMultiEntityMode', () => {
        it('should be false by default', () => {
            expect(state.isMultiEntityMode).toBe(false);
        });

        it('can be set to true for multi-entity mode', () => {
            state.isMultiEntityMode = true;

            expect(state.isMultiEntityMode).toBe(true);
        });
    });
});
