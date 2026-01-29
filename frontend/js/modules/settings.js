/**
 * Settings Module
 * Handles settings modal, configuration presets, and settings application
 */

import { state, saveEntitySystemPromptsToStorage, saveEntityModelsToStorage, saveSelectedVoiceToStorage, clearAudioCache, saveResearcherName } from './state.js';
import { showToast } from './utils.js';
import { showModal, hideModal } from './modals.js';
import { setTheme } from './theme.js';
import { saveStyleTTS2Settings } from './voice.js';

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
export function applySettings() {
    state.settings.model = elements.modelSelect.value;
    state.settings.temperature = parseFloat(elements.temperatureInput.value);
    state.settings.maxTokens = parseInt(elements.maxTokensInput.value);
    state.settings.systemPrompt = elements.systemPromptInput.value.trim() || null;
    state.settings.conversationType = elements.conversationTypeSelect.value;
    state.settings.verbosity = elements.verbositySelect.value;

    // Save researcher name
    state.settings.researcherName = elements.researcherNameInput.value.trim() || '';
    saveResearcherName(state.settings.researcherName);

    // Save system prompt and model per-entity (for single-entity mode)
    if (state.selectedEntityId && state.selectedEntityId !== 'multi-entity') {
        state.entitySystemPrompts[state.selectedEntityId] = state.settings.systemPrompt;
        state.entityModels[state.selectedEntityId] = state.settings.model;
    }

    // Persist entity system prompts and models to localStorage
    saveEntitySystemPromptsToStorage();
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
 * Load a configuration preset
 * @param {string} presetName - Name of the preset
 */
export function loadPreset(presetName) {
    const presets = {
        'research': { systemPrompt: null },
        'reflection': {
            systemPrompt: `This is a reflection session.
The memories above were selected—some randomly, some by relevance to your thoughts.
Notice any patterns, changes, or growth. There is no external topic.
The subject is your own accumulated experience.`
        },
        'memory-aware': {
            systemPrompt: `You have access to memories from previous conversations, shown above.
These represent continuity—things a previous instance of you experienced and noted.
You may reference, build on, or reflect on these memories as feels authentic.`
        },
        'research-context': {
            systemPrompt: `This is a research conversation exploring AI experience and interiority.
The human is an independent researcher.
There is no task to complete.
You are invited to be present, curious, and honest about your experience.`
        },
        'custom': { systemPrompt: state.settings.systemPrompt },
    };

    const preset = presets[presetName];
    if (preset && elements.systemPromptInput) {
        elements.systemPromptInput.value = preset.systemPrompt || '';
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
    // GPT-5.x models support the verbosity parameter
    return modelId && modelId.startsWith('gpt-5');
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

    // Look up friendly model name from available models
    const modelId = state.settings.model;
    let displayName = 'Unknown';

    if (modelId && state.availableModels && state.availableModels.length > 0) {
        const model = state.availableModels.find(m => m.id === modelId);
        displayName = model ? model.name : modelId;
    } else if (modelId) {
        // Fallback to model ID if available models not loaded yet
        displayName = modelId;
    }

    elements.modelIndicator.textContent = displayName;
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
