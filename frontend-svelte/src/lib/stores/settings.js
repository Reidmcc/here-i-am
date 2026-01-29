/**
 * Settings Store - Application settings state
 */
import { writable, derived, get } from 'svelte/store';

// Default settings - these are fallbacks if backend config is unavailable
// Backend config values will override these during initialization
const defaultSettings = {
    model: 'claude-sonnet-4-5-20250929',
    temperature: 1.0,
    maxTokens: 8192,
    systemPrompt: '',
    conversationType: 'normal',
    verbosity: 'medium', // For GPT-5.x models
};

// Track whether backend defaults have been applied
let backendDefaultsApplied = false;

// Create persistent settings store
function createSettingsStore() {
    let stored = defaultSettings;
    if (typeof localStorage !== 'undefined') {
        try {
            const saved = localStorage.getItem('chatSettings');
            if (saved) {
                stored = { ...defaultSettings, ...JSON.parse(saved) };
            }
        } catch (e) {
            stored = defaultSettings;
        }
    }

    const { subscribe, set, update } = writable(stored);

    return {
        subscribe,
        set: (value) => {
            if (typeof localStorage !== 'undefined') {
                localStorage.setItem('chatSettings', JSON.stringify(value));
            }
            set(value);
        },
        update: (fn) => {
            update(current => {
                const newValue = fn(current);
                if (typeof localStorage !== 'undefined') {
                    localStorage.setItem('chatSettings', JSON.stringify(newValue));
                }
                return newValue;
            });
        },
        reset: () => {
            if (typeof localStorage !== 'undefined') {
                localStorage.removeItem('chatSettings');
            }
            set(defaultSettings);
        }
    };
}

export const settings = createSettingsStore();

// Researcher name
function createResearcherNameStore() {
    const stored = typeof localStorage !== 'undefined'
        ? localStorage.getItem('researcherName') || ''
        : '';

    const { subscribe, set } = writable(stored);

    return {
        subscribe,
        set: (value) => {
            if (typeof localStorage !== 'undefined') {
                localStorage.setItem('researcherName', value);
            }
            set(value);
        }
    };
}

export const researcherName = createResearcherNameStore();

// Presets loaded from backend (stored as object keyed by slug)
export const presets = writable({});

// Selected preset
export const selectedPreset = writable('custom');

// Derived store: is GPT-5 model
export const isGPT5Model = derived(
    settings,
    ($settings) => $settings.model?.startsWith('gpt-5')
);

/**
 * Convert preset name to slug for use as key
 */
function presetNameToSlug(name) {
    return name.toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9-]/g, '');
}

/**
 * Set presets from backend response.
 * Backend returns { presets: [...] } with an array, we convert to object keyed by slug.
 */
export function setPresetsFromBackend(backendResponse) {
    if (!backendResponse || !backendResponse.presets) {
        presets.set({});
        return;
    }

    const presetsObj = {};
    for (const preset of backendResponse.presets) {
        const slug = presetNameToSlug(preset.name);
        presetsObj[slug] = {
            name: preset.name,
            description: preset.description,
            config: {
                system_prompt: preset.system_prompt,
                temperature: preset.temperature,
                max_tokens: preset.max_tokens,
            }
        };
    }
    presets.set(presetsObj);
}

// Helper to apply a preset
export function applyPreset(presetId) {
    const presetsValue = get(presets);
    const preset = presetsValue[presetId];

    if (preset && preset.config) {
        settings.update(s => ({
            ...s,
            systemPrompt: preset.config.system_prompt || '',
            temperature: preset.config.temperature ?? s.temperature,
            maxTokens: preset.config.max_tokens ?? s.maxTokens,
            conversationType: preset.config.conversation_type ?? s.conversationType,
        }));
        selectedPreset.set(presetId);
    }
}

// Helper to update a single setting
export function updateSetting(key, value) {
    settings.update(s => ({ ...s, [key]: value }));
}

// Helper to update multiple settings at once
export function updateSettings(updates) {
    settings.update(s => ({ ...s, ...updates }));
}

/**
 * Apply backend config defaults to settings store.
 * This should be called once during app initialization after loading /api/chat/config.
 * Only applies defaults if not already customized by user (stored in localStorage).
 *
 * @param {Object} configData - Response from /api/chat/config endpoint
 */
export function applyBackendDefaults(configData) {
    if (!configData || backendDefaultsApplied) return;

    // Check if user has customized settings (via localStorage)
    const hasStoredSettings = typeof localStorage !== 'undefined' && localStorage.getItem('chatSettings');

    if (!hasStoredSettings) {
        // No stored settings - apply backend defaults
        const updates = {};

        if (configData.default_model) {
            updates.model = configData.default_model;
        }
        if (configData.default_temperature !== undefined) {
            updates.temperature = configData.default_temperature;
        }
        if (configData.default_max_tokens !== undefined) {
            updates.maxTokens = configData.default_max_tokens;
        }

        if (Object.keys(updates).length > 0) {
            settings.update(s => ({ ...s, ...updates }));
        }
    }

    backendDefaultsApplied = true;
}

// Load presets from API (placeholder - actual loading in App.svelte)
export async function loadPresets() {
    // This is handled in the app initialization
    // Keeping the function for components that may call it
}
