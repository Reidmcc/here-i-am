/**
 * Unit Tests for State Module
 * Tests centralized state management functionality
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
    state,
    resetMemoryState,
    resetAttachments,
    clearAudioCache,
    loadEntitySystemPromptsFromStorage,
    saveEntitySystemPromptsToStorage,
    loadSelectedVoiceFromStorage,
    saveSelectedVoiceToStorage,
    loadResearcherName,
    saveResearcherName,
} from '../modules/state.js';

describe('State Module', () => {
    describe('state object', () => {
        it('should have initial conversation state', () => {
            expect(state.currentConversationId).toBe(null);
            expect(Array.isArray(state.conversations)).toBe(true);
        });

        it('should have initial entity state', () => {
            expect(state.selectedEntityId).toBe(null);
            expect(Array.isArray(state.entities)).toBe(true);
            expect(typeof state.entitySystemPrompts).toBe('object');
        });

        it('should have initial multi-entity state', () => {
            expect(state.isMultiEntityMode).toBe(false);
            expect(Array.isArray(state.currentConversationEntities)).toBe(true);
            expect(state.pendingResponderId).toBe(null);
        });

        it('should have initial UI state', () => {
            expect(state.isLoading).toBe(false);
            expect(state.streamAbortController).toBe(null);
        });

        it('should have initial settings with defaults', () => {
            expect(state.settings).toBeDefined();
            expect(state.settings.model).toBe('claude-sonnet-4-5-20250929');
            expect(state.settings.temperature).toBe(1.0);
            expect(state.settings.maxTokens).toBe(4096);
            expect(state.settings.systemPrompt).toBe(null);
            expect(state.settings.conversationType).toBe('normal');
        });

        it('should have initial memory state', () => {
            expect(Array.isArray(state.retrievedMemories)).toBe(true);
            expect(typeof state.retrievedMemoriesByEntity).toBe('object');
            expect(state.expandedMemoryIds).toBeInstanceOf(Set);
        });

        it('should have initial TTS state', () => {
            expect(state.ttsEnabled).toBe(false);
            expect(state.ttsProvider).toBe(null);
            expect(Array.isArray(state.ttsVoices)).toBe(true);
            expect(state.selectedVoiceId).toBe(null);
            expect(state.audioCache).toBeInstanceOf(Map);
        });

        it('should have initial attachment state', () => {
            expect(state.pendingAttachments).toBeDefined();
            expect(Array.isArray(state.pendingAttachments.images)).toBe(true);
            expect(Array.isArray(state.pendingAttachments.files)).toBe(true);
        });

        it('should track construction time', () => {
            expect(typeof state.constructedAt).toBe('number');
            expect(state.constructedAt).toBeLessThanOrEqual(Date.now());
        });
    });

    describe('resetMemoryState', () => {
        beforeEach(() => {
            // Setup state with some data
            state.retrievedMemories = [{ id: '1' }, { id: '2' }];
            state.retrievedMemoriesByEntity = { entity1: [{ id: '1' }] };
            state.expandedMemoryIds.add('1');
            state.expandedMemoryIds.add('2');
        });

        it('should clear retrieved memories', () => {
            resetMemoryState();
            expect(state.retrievedMemories).toEqual([]);
        });

        it('should clear retrieved memories by entity', () => {
            resetMemoryState();
            expect(state.retrievedMemoriesByEntity).toEqual({});
        });

        it('should clear expanded memory IDs', () => {
            resetMemoryState();
            expect(state.expandedMemoryIds.size).toBe(0);
        });
    });

    describe('resetAttachments', () => {
        beforeEach(() => {
            // Setup state with mock attachments
            state.pendingAttachments = {
                images: [
                    { name: 'img1.png', previewUrl: 'blob:mock-url-1' },
                    { name: 'img2.jpg', previewUrl: 'blob:mock-url-2' },
                ],
                files: [
                    { name: 'doc.txt', content: 'content' },
                ],
            };
        });

        it('should revoke blob URLs for images', () => {
            resetAttachments();
            expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2);
            expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:mock-url-1');
            expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:mock-url-2');
        });

        it('should clear pending attachments', () => {
            resetAttachments();
            expect(state.pendingAttachments).toEqual({ images: [], files: [] });
        });

        it('should handle empty attachments gracefully', () => {
            state.pendingAttachments = { images: [], files: [] };
            expect(() => resetAttachments()).not.toThrow();
            expect(state.pendingAttachments).toEqual({ images: [], files: [] });
        });

        it('should handle images without previewUrl', () => {
            state.pendingAttachments = {
                images: [{ name: 'img.png' }],
                files: [],
            };
            expect(() => resetAttachments()).not.toThrow();
        });
    });

    describe('clearAudioCache', () => {
        beforeEach(() => {
            // Setup audio cache with mock entries
            state.audioCache.set('msg1', { url: 'blob:audio-url-1', blob: {} });
            state.audioCache.set('msg2', { url: 'blob:audio-url-2', blob: {} });
            state.audioCache.set('msg3', { data: 'no url' }); // Entry without URL
        });

        it('should revoke blob URLs for cached audio', () => {
            clearAudioCache();
            expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2);
            expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:audio-url-1');
            expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:audio-url-2');
        });

        it('should clear the audio cache', () => {
            clearAudioCache();
            expect(state.audioCache.size).toBe(0);
        });

        it('should handle empty cache gracefully', () => {
            state.audioCache.clear();
            expect(() => clearAudioCache()).not.toThrow();
        });
    });

    describe('loadEntitySystemPromptsFromStorage', () => {
        it('should load saved prompts from localStorage', () => {
            const savedPrompts = { 'entity-1': 'prompt 1', 'entity-2': 'prompt 2' };
            localStorage.setItem('entity_system_prompts', JSON.stringify(savedPrompts));

            loadEntitySystemPromptsFromStorage();

            expect(state.entitySystemPrompts).toEqual(savedPrompts);
        });

        it('should apply prompt for selected entity', () => {
            const savedPrompts = { 'entity-1': 'entity 1 prompt' };
            localStorage.setItem('entity_system_prompts', JSON.stringify(savedPrompts));
            state.selectedEntityId = 'entity-1';

            loadEntitySystemPromptsFromStorage();

            expect(state.settings.systemPrompt).toBe('entity 1 prompt');
        });

        it('should not apply prompt for multi-entity mode', () => {
            const savedPrompts = { 'multi-entity': 'multi prompt' };
            localStorage.setItem('entity_system_prompts', JSON.stringify(savedPrompts));
            state.selectedEntityId = 'multi-entity';
            state.settings.systemPrompt = 'original';

            loadEntitySystemPromptsFromStorage();

            expect(state.settings.systemPrompt).toBe('original');
        });

        it('should handle missing localStorage gracefully', () => {
            expect(() => loadEntitySystemPromptsFromStorage()).not.toThrow();
        });

        it('should handle invalid JSON gracefully', () => {
            localStorage.setItem('entity_system_prompts', 'invalid json');
            expect(() => loadEntitySystemPromptsFromStorage()).not.toThrow();
        });
    });

    describe('saveEntitySystemPromptsToStorage', () => {
        it('should save prompts to localStorage', () => {
            state.entitySystemPrompts = { 'entity-1': 'prompt 1' };

            saveEntitySystemPromptsToStorage();

            expect(localStorage.setItem).toHaveBeenCalledWith(
                'entity_system_prompts',
                JSON.stringify({ 'entity-1': 'prompt 1' })
            );
        });

        it('should handle empty prompts', () => {
            state.entitySystemPrompts = {};

            expect(() => saveEntitySystemPromptsToStorage()).not.toThrow();
            expect(localStorage.setItem).toHaveBeenCalled();
        });
    });

    describe('loadSelectedVoiceFromStorage', () => {
        it('should load saved voice ID from localStorage', () => {
            localStorage.setItem('selected_voice_id', 'voice-123');

            loadSelectedVoiceFromStorage();

            expect(state.selectedVoiceId).toBe('voice-123');
        });

        it('should handle missing localStorage gracefully', () => {
            state.selectedVoiceId = null;
            expect(() => loadSelectedVoiceFromStorage()).not.toThrow();
            expect(state.selectedVoiceId).toBe(null);
        });
    });

    describe('saveSelectedVoiceToStorage', () => {
        it('should save voice ID to localStorage', () => {
            state.selectedVoiceId = 'voice-456';

            saveSelectedVoiceToStorage();

            expect(localStorage.setItem).toHaveBeenCalledWith('selected_voice_id', 'voice-456');
        });

        it('should remove from localStorage when voice is null', () => {
            state.selectedVoiceId = null;

            saveSelectedVoiceToStorage();

            expect(localStorage.removeItem).toHaveBeenCalledWith('selected_voice_id');
        });
    });

    describe('loadResearcherName', () => {
        it('should load and return saved researcher name', () => {
            localStorage.setItem('researcher_name', 'Dr. Smith');

            const result = loadResearcherName();

            expect(result).toBe('Dr. Smith');
            expect(state.settings.researcherName).toBe('Dr. Smith');
        });

        it('should return null when no name is saved', () => {
            const result = loadResearcherName();

            expect(result).toBe(null);
        });
    });

    describe('saveResearcherName', () => {
        it('should save researcher name to state and localStorage', () => {
            saveResearcherName('Dr. Jones');

            expect(state.settings.researcherName).toBe('Dr. Jones');
            expect(localStorage.setItem).toHaveBeenCalledWith('researcher_name', 'Dr. Jones');
        });

        it('should use existing state value when name is undefined', () => {
            state.settings.researcherName = 'Existing Name';

            saveResearcherName(undefined);

            expect(localStorage.setItem).toHaveBeenCalledWith('researcher_name', 'Existing Name');
        });

        it('should handle empty string', () => {
            saveResearcherName('');

            expect(state.settings.researcherName).toBe('');
            expect(localStorage.setItem).toHaveBeenCalledWith('researcher_name', '');
        });
    });

    describe('direct state mutation', () => {
        it('should allow direct mutation of state properties', () => {
            state.currentConversationId = 'new-conv-id';
            expect(state.currentConversationId).toBe('new-conv-id');

            state.isLoading = true;
            expect(state.isLoading).toBe(true);

            state.settings.temperature = 0.7;
            expect(state.settings.temperature).toBe(0.7);
        });

        it('should allow mutation of nested objects', () => {
            state.pendingAttachments.images.push({ name: 'test.png' });
            expect(state.pendingAttachments.images.length).toBe(1);
        });

        it('should allow mutation of Set objects', () => {
            state.expandedMemoryIds.add('memory-1');
            expect(state.expandedMemoryIds.has('memory-1')).toBe(true);

            state.expandedMemoryIds.delete('memory-1');
            expect(state.expandedMemoryIds.has('memory-1')).toBe(false);
        });

        it('should allow mutation of Map objects', () => {
            state.audioCache.set('key', { value: 'data' });
            expect(state.audioCache.get('key')).toEqual({ value: 'data' });

            state.audioCache.delete('key');
            expect(state.audioCache.has('key')).toBe(false);
        });
    });
});
