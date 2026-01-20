/**
 * Settings Store - Application settings state
 */
import { writable, derived, get } from 'svelte/store';

// Default settings
const defaultSettings = {
    model: 'claude-sonnet-4-5-20250929',
    temperature: 1.0,
    maxTokens: 8192,
    systemPrompt: '',
    conversationType: 'normal',
    verbosity: 'medium', // For GPT-5.x models
};

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

// Presets loaded from backend
export const presets = writable({});

// Selected preset
export const selectedPreset = writable('custom');

// Derived store: is GPT-5 model
export const isGPT5Model = derived(
    settings,
    ($settings) => $settings.model?.startsWith('gpt-5')
);

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

// Load presets from API (placeholder - actual loading in App.svelte)
export async function loadPresets() {
    // This is handled in the app initialization
    // Keeping the function for components that may call it
}
