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

// Store backend defaults for reference when switching entities
// These are the .env values that should be used when no user preference is set
let storedBackendDefaults = {
    model: null,
    temperature: null,
    maxTokens: null,
};

// Track which settings the user has explicitly modified during this session
// This is NOT persisted - resets on page refresh, which is when .env should be re-applied
const userModifiedThisSession = new Set();

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

// Helper to update a single setting (marks as user-modified for this session)
export function updateSetting(key, value) {
    userModifiedThisSession.add(key);
    settings.update(s => ({ ...s, [key]: value }));
}

// Helper to update multiple settings at once (marks as user-modified for this session)
export function updateSettings(updates) {
    Object.keys(updates).forEach(key => userModifiedThisSession.add(key));
    settings.update(s => ({ ...s, ...updates }));
}

/**
 * Check if a setting was explicitly modified by the user during this session.
 * Used to prevent automatic defaults (like entity switching) from overriding user choices.
 */
export function wasModifiedThisSession(key) {
    return userModifiedThisSession.has(key);
}

/**
 * Update a setting without marking it as user-modified.
 * Used for applying defaults (backend config, entity defaults) that should
 * not prevent future automatic updates.
 */
export function updateSettingQuietly(key, value) {
    settings.update(s => ({ ...s, [key]: value }));
}

/**
 * Apply backend config defaults to settings store.
 * This should be called once during app initialization after loading /api/chat/config.
 * Backend .env values always take precedence as the source of truth.
 *
 * @param {Object} configData - Response from /api/chat/config endpoint
 */
export function applyBackendDefaults(configData) {
    if (!configData || backendDefaultsApplied) return;

    // Always apply backend defaults - .env is the source of truth
    const updates = {};

    if (configData.default_model) {
        updates.model = configData.default_model;
        storedBackendDefaults.model = configData.default_model;
    }
    if (configData.default_temperature !== undefined) {
        updates.temperature = configData.default_temperature;
        storedBackendDefaults.temperature = configData.default_temperature;
    }
    if (configData.default_max_tokens !== undefined) {
        updates.maxTokens = configData.default_max_tokens;
        storedBackendDefaults.maxTokens = configData.default_max_tokens;
    }

    if (Object.keys(updates).length > 0) {
        settings.update(s => ({ ...s, ...updates }));
    }

    backendDefaultsApplied = true;
}

/**
 * Get the backend defaults loaded from .env config.
 * Used when switching entities to reset settings that have no user preference.
 */
export function getBackendDefaults() {
    return { ...storedBackendDefaults };
}

// Load presets from API (placeholder - actual loading in App.svelte)
export async function loadPresets() {
    // This is handled in the app initialization
    // Keeping the function for components that may call it
}
