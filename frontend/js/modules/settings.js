/**
 * Settings Module
 * Handles settings modal, configuration presets, and settings application
 */

import { state, saveEntityModelsToStorage, saveSelectedVoiceToStorage, clearAudioCache, saveResearcherName } from './state.js';
import { showToast } from './utils.js';
import { showModal, hideModal } from './modals.js';
import { setTheme } from './theme.js';
import { saveStyleTTS2Settings } from './voice.js';

// Reference to global API client
const api = window.api;

// Element references
let elements = {};

// Callbacks
let callbacks = {
    updateModelIndicator: null,
    getMaxTemperatureForCurrentEntity: null,
};

/**
 * Set element references
 * @param {Object} els - Element references
 */
export function setElements(els) {
    elements = els;
}

/**
 * Set callback functions
 * @param {Object} cbs - Callback functions
 */
export function setCallbacks(cbs) {
    callbacks = { ...callbacks, ...cbs };
}

/**
 * Apply settings from the settings modal
 */
export async function applySettings() {
    state.settings.model = elements.modelSelect.value;
    state.settings.temperature = parseFloat(elements.temperatureInput.value);
    state.settings.maxTokens = parseInt(elements.maxTokensInput.value);
    state.settings.systemPrompt = elements.systemPromptInput.value.trim() || null;
    state.settings.conversationType = elements.conversationTypeSelect.value;
    state.settings.verbosity = elements.verbositySelect.value;

    // Save researcher name
    state.settings.researcherName = elements.researcherNameInput.value.trim() || '';
    saveResearcherName(state.settings.researcherName);

    // Persist the system prompt per-entity on the backend (the source of
    // truth) so it survives across sessions and browsers. The model stays a
    // client-side preference in localStorage.
    if (state.selectedEntityId && state.selectedEntityId !== 'multi-entity') {
        state.entityModels[state.selectedEntityId] = state.settings.model;

        const entityId = state.selectedEntityId;
        const newPrompt = state.settings.systemPrompt;
        if (state.entitySystemPrompts[entityId] !== newPrompt) {
            state.entitySystemPrompts[entityId] = newPrompt;
            try {
                await api.updateEntitySystemPrompt(entityId, newPrompt);
            } catch (error) {
                console.error('Failed to save entity system prompt:', error);
                showToast('Failed to save system prompt', 'error');
            }
        }
    }

    // Persist per-entity model selection to localStorage
    saveEntityModelsToStorage();

    // Apply theme
    setTheme(elements.themeSelect.value);

    // Apply voice selection
    if (state.ttsVoices.length > 1) {
        const newVoiceId = elements.voiceSelect.value;
        if (newVoiceId !== state.selectedVoiceId) {
            state.selectedVoiceId = newVoiceId;
            saveSelectedVoiceToStorage();
            clearAudioCache();
        }
    }

    // Save StyleTTS 2 parameters if using StyleTTS 2
    if (state.ttsProvider === 'styletts2') {
        saveStyleTTS2Settings();
    }

    if (callbacks.updateModelIndicator) {
        callbacks.updateModelIndicator();
    }

    hideModal('settingsModal');
    showToast('Settings applied', 'success');
}

/**
 * Fetch the system-prompt presets from the backend (the source of truth) and
 * populate the preset dropdown. Falls back to leaving the static "Custom"
 * option in place if the request fails.
 */
export async function loadPresets() {
    try {
        const data = await api.getPresets();
        state.presets = data?.presets || [];
    } catch (error) {
        console.error('Failed to load presets:', error);
        state.presets = [];
    }
    populatePresetSelect();
}

/**
 * Rebuild the preset dropdown from state.presets, keeping a trailing "Custom"
 * option that preserves whatever system prompt is currently set.
 */
export function populatePresetSelect() {
    if (!elements.presetSelect) return;

    elements.presetSelect.innerHTML = '';

    state.presets.forEach(preset => {
        const option = document.createElement('option');
        option.value = preset.name;
        option.textContent = preset.description
            ? `${preset.name} (${preset.description})`
            : preset.name;
        elements.presetSelect.appendChild(option);
    });

    const customOption = document.createElement('option');
    customOption.value = 'custom';
    customOption.textContent = 'Custom';
    elements.presetSelect.appendChild(customOption);
}

/**
 * Load a configuration preset by name. Applies the preset's system prompt
 * (the field that varies between presets); "custom" keeps the current prompt.
 * @param {string} presetName - Name of the preset
 */
export function loadPreset(presetName) {
    if (!elements.systemPromptInput) return;

    if (presetName === 'custom') {
        elements.systemPromptInput.value = state.settings.systemPrompt || '';
        return;
    }

    const preset = state.presets.find(p => p.name === presetName);
    if (preset) {
        elements.systemPromptInput.value = preset.system_prompt || '';
    }
}

/**
 * Check if a model supports temperature parameter
 * @param {string} modelId - Model ID to check
 * @returns {boolean}
 */
export function modelSupportsTemperature(modelId) {
    const model = state.availableModels.find(m => m.id === modelId);
    return model ? model.temperature_supported !== false : true;
}

/**
 * Update temperature control enabled/disabled state
 */
export function updateTemperatureControlState() {
    if (!elements.modelSelect || !elements.temperatureInput || !elements.temperatureNumber) return;

    const selectedModel = elements.modelSelect.value;
    const supportsTemp = modelSupportsTemperature(selectedModel);

    elements.temperatureInput.disabled = !supportsTemp;
    elements.temperatureNumber.disabled = !supportsTemp;

    const formGroup = elements.temperatureInput.closest('.form-group');
    if (formGroup) {
        if (supportsTemp) {
            formGroup.classList.remove('disabled');
        } else {
            formGroup.classList.add('disabled');
        }
    }
}

/**
 * Check if a model supports verbosity parameter (GPT-5.x models)
 * @param {string} modelId - Model ID to check
 * @returns {boolean}
 */
export function modelSupportsVerbosity(modelId) {
    if (!modelId) return false;
    // Prefer the backend's per-model flag (it owns which models accept
    // verbosity). Fall back to a prefix heuristic only if the model isn't in
    // the available-models list yet.
    const model = state.availableModels.find(m => m.id === modelId);
    if (model && model.verbosity_supported !== undefined) {
        return model.verbosity_supported === true;
    }
    return modelId.startsWith('gpt-5');
}

/**
 * Update verbosity control visibility based on selected model
 */
export function updateVerbosityControlState() {
    if (!elements.modelSelect || !elements.verbositySelect) return;

    const selectedModel = elements.modelSelect.value;
    const supportsVerbosity = modelSupportsVerbosity(selectedModel);

    const formGroup = elements.verbositySelect.closest('.form-group');
    if (formGroup) {
        formGroup.style.display = supportsVerbosity ? 'block' : 'none';
    }
}

/**
 * Update temperature range based on current entity's provider
 */
export function updateTemperatureRange() {
    if (!elements.temperatureInput || !elements.temperatureNumber) return;

    let maxTemp = 1.0;
    if (callbacks.getMaxTemperatureForCurrentEntity) {
        maxTemp = callbacks.getMaxTemperatureForCurrentEntity();
    }

    elements.temperatureInput.max = maxTemp;
    elements.temperatureNumber.max = maxTemp;

    // Clamp current value if needed
    if (state.settings.temperature > maxTemp) {
        state.settings.temperature = maxTemp;
        elements.temperatureInput.value = maxTemp;
        elements.temperatureNumber.value = maxTemp;
    }
}

/**
 * Sync temperature slider and number input
 */
export function syncTemperatureInputs() {
    if (!elements.temperatureInput || !elements.temperatureNumber) return;

    elements.temperatureInput.addEventListener('input', (e) => {
        elements.temperatureNumber.value = e.target.value;
    });

    elements.temperatureNumber.addEventListener('input', (e) => {
        let value = parseFloat(e.target.value);
        let maxTemp = 1.0;
        if (callbacks.getMaxTemperatureForCurrentEntity) {
            maxTemp = callbacks.getMaxTemperatureForCurrentEntity();
        }
        if (isNaN(value)) value = 1.0;
        if (value < 0) value = 0;
        if (value > maxTemp) value = maxTemp;
        elements.temperatureInput.value = value;
    });
}

/**
 * Update the model indicator display
 */
export function updateModelIndicator() {
    if (!elements.modelIndicator) return;

    // In multi-entity mode, show "Per-Entity" instead of a specific model
    if (state.isMultiEntityMode) {
        elements.modelIndicator.textContent = 'Per-Entity';
        return;
    }

    // Look up friendly model name from available models
    const modelId = state.settings.model;
    const displayName = getModelDisplayName(modelId);
    elements.modelIndicator.textContent = displayName;
}

/**
 * Get the display name for a model ID
 * @param {string} modelId - Model ID
 * @returns {string} - Friendly display name
 */
function getModelDisplayName(modelId) {
    if (modelId && state.availableModels && state.availableModels.length > 0) {
        const model = state.availableModels.find(m => m.id === modelId);
        if (model) return model.name;
    }
    return modelId || 'Unknown';
}

/**
 * Resolve which model an entity is using.
 * Priority: saved user preference > entity default > first provider model > global setting
 * @param {Object} entity - Entity object with index_name, llm_provider, default_model
 * @returns {string} - Model ID
 */
function resolveEntityModel(entity) {
    const provider = entity.llm_provider || 'anthropic';

    // 1. Saved per-entity preference
    const savedModel = state.entityModels[entity.index_name];
    if (savedModel) {
        const isValid = state.availableModels &&
            state.availableModels.some(m => m.id === savedModel && m.provider === provider);
        if (isValid) return savedModel;
    }

    // 2. Entity's configured default
    if (entity.default_model) return entity.default_model;

    // 3. First available model for the provider
    if (state.availableModels) {
        const providerModels = state.availableModels.filter(m => m.provider === provider);
        if (providerModels.length > 0) return providerModels[0].id;
    }

    // 4. Global fallback
    return state.settings.model;
}

/**
 * Populate model select options
 * @param {Array} models - Available models
 * @param {string} entityProvider - Current entity's provider
 */
export function populateModelSelect(models, entityProvider) {
    if (!elements.modelSelect) return;

    elements.modelSelect.innerHTML = '';

    models.forEach(model => {
        const option = document.createElement('option');
        option.value = model.id;
        option.textContent = model.name;
        elements.modelSelect.appendChild(option);
    });

    // Set selected model
    if (state.settings.model) {
        elements.modelSelect.value = state.settings.model;
    }
}

/**
 * Initialize settings UI with current values
 */
export function initializeSettingsUI() {
    if (elements.modelSelect && state.settings.model) {
        elements.modelSelect.value = state.settings.model;
    }
    if (elements.temperatureInput) {
        elements.temperatureInput.value = state.settings.temperature;
    }
    if (elements.temperatureNumber) {
        elements.temperatureNumber.value = state.settings.temperature;
    }
    if (elements.maxTokensInput) {
        elements.maxTokensInput.value = state.settings.maxTokens;
    }
    if (elements.systemPromptInput) {
        elements.systemPromptInput.value = state.settings.systemPrompt || '';
    }
    if (elements.conversationTypeSelect) {
        elements.conversationTypeSelect.value = state.settings.conversationType;
    }
    if (elements.verbositySelect) {
        elements.verbositySelect.value = state.settings.verbosity || 'medium';
    }
    if (elements.researcherNameInput) {
        elements.researcherNameInput.value = state.settings.researcherName || '';
    }

    updateTemperatureControlState();
    updateVerbosityControlState();
}
