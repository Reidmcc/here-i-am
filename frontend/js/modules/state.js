/**
 * State Management Module
 * Centralized state store for the application
 */

/**
 * Application state store
 * This is a simple state object that modules can read from and write to
 */
export const state = {
    // Current conversation
    currentConversationId: null,
    conversations: [],

    // Entity state
    selectedEntityId: null,
    entities: [],
    entitySystemPrompts: {},
    entityModels: {},  // Per-entity model selection persistence

    // Multi-entity state
    isMultiEntityMode: false,
    currentConversationEntities: [],
    pendingResponderId: null,
    pendingRegenerateMessageId: null,
    pendingMultiEntityAction: null,
    responderSelectorMode: null,
    pendingMessageContent: null,
    pendingMessageAttachments: null,
    pendingUserMessageEl: null,
    pendingActionAfterEntitySelection: null,
    pendingMessageForEntitySelection: null,
    pendingAttachmentsForEntitySelection: null,

    // UI state
    isLoading: false,
    streamAbortController: null,
    importAbortController: null,

    // Settings
    settings: {
        model: 'claude-sonnet-4-5-20250929',
        temperature: 1.0,
        maxTokens: 4096,
        systemPrompt: null,
        conversationType: 'normal',
        verbosity: 'medium',
        researcherName: '',
    },

    // Memory state
    retrievedMemories: [],
    retrievedMemoriesByEntity: {},
    expandedMemoryIds: new Set(),

    // TTS state
    ttsEnabled: false,
    ttsProvider: null,
    ttsVoices: [],
    selectedVoiceId: null,
    localTtsServerHealthy: false,
    audioCache: new Map(),
    currentAudio: null,
    currentSpeakingBtn: null,

    // STT state
    dictationMode: 'none',
    isRecording: false,
    speechRecognition: null,
    mediaRecorder: null,
    audioChunks: [],

    // Attachments
    pendingAttachments: {
        images: [],
        files: []
    },

    // Go game reference
    goGame: null,

    // Pending actions for modals
    pendingArchiveId: null,
    pendingDeleteId: null,
    pendingRenameId: null,

    // Import state
    importFileContent: null,
    importPreviewData: null,

    // Orphan maintenance
    _orphanData: null,

    // Conversation loading tracking
    loadConversationsRequestId: 0,
    lastCreatedConversation: null,

    // App construction time
    constructedAt: Date.now(),

    // Config
    availableModels: [],
};

/**
 * Reset memory state (called when switching conversations)
 */
export function resetMemoryState() {
    state.retrievedMemories = [];
    state.retrievedMemoriesByEntity = {};
    state.expandedMemoryIds.clear();
}

/**
 * Reset attachment state
 */
export function resetAttachments() {
    // Revoke blob URLs for images
    for (const img of state.pendingAttachments.images) {
        if (img.previewUrl) {
            URL.revokeObjectURL(img.previewUrl);
        }
    }
    state.pendingAttachments = { images: [], files: [] };
}

/**
 * Clear audio cache
 */
export function clearAudioCache() {
    for (const [key, cached] of state.audioCache) {
        if (cached.url) {
            URL.revokeObjectURL(cached.url);
        }
    }
    state.audioCache.clear();
}

/**
 * Load entity system prompts from localStorage
 */
export function loadEntitySystemPromptsFromStorage() {
    try {
        const saved = localStorage.getItem('entity_system_prompts');
        if (saved) {
            state.entitySystemPrompts = JSON.parse(saved);
            // If we have a selected entity, apply its system prompt
            if (state.selectedEntityId && state.selectedEntityId !== 'multi-entity') {
                if (state.entitySystemPrompts[state.selectedEntityId] !== undefined) {
                    state.settings.systemPrompt = state.entitySystemPrompts[state.selectedEntityId];
                }
            }
        }
    } catch (e) {
        console.warn('Failed to load entity system prompts:', e);
    }
}

/**
 * Save entity system prompts to localStorage
 */
export function saveEntitySystemPromptsToStorage() {
    try {
        localStorage.setItem('entity_system_prompts', JSON.stringify(state.entitySystemPrompts));
    } catch (e) {
        console.warn('Failed to save entity system prompts:', e);
    }
}

/**
 * Load entity models from localStorage
 */
export function loadEntityModelsFromStorage() {
    try {
        const saved = localStorage.getItem('entity_models');
        if (saved) {
            state.entityModels = JSON.parse(saved);
        }
    } catch (e) {
        console.warn('Failed to load entity models:', e);
    }
}

/**
 * Save entity models to localStorage
 */
export function saveEntityModelsToStorage() {
    try {
        localStorage.setItem('entity_models', JSON.stringify(state.entityModels));
    } catch (e) {
        console.warn('Failed to save entity models:', e);
    }
}

/**
 * Load selected voice from localStorage
 */
export function loadSelectedVoiceFromStorage() {
    try {
        const saved = localStorage.getItem('selected_voice_id');
        if (saved) {
            state.selectedVoiceId = saved;
        }
    } catch (e) {
        console.warn('Failed to load selected voice:', e);
    }
}

/**
 * Save selected voice to localStorage
 */
export function saveSelectedVoiceToStorage() {
    try {
        if (state.selectedVoiceId) {
            localStorage.setItem('selected_voice_id', state.selectedVoiceId);
        } else {
            localStorage.removeItem('selected_voice_id');
        }
    } catch (e) {
        console.warn('Failed to save selected voice:', e);
    }
}

/**
 * Load researcher name from localStorage
 * @returns {string|null} The saved researcher name or null
 */
export function loadResearcherName() {
    const savedName = localStorage.getItem('researcher_name');
    if (savedName) {
        state.settings.researcherName = savedName;
        return savedName;
    }
    return null;
}

/**
 * Save researcher name to localStorage
 * @param {string} name - Researcher name to save
 */
export function saveResearcherName(name) {
    if (name !== undefined) {
        state.settings.researcherName = name;
    }
    localStorage.setItem('researcher_name', state.settings.researcherName || '');
}
