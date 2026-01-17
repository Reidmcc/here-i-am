/**
 * Unit Tests for Settings Module
 * Tests settings management and presets
 */

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { state } from '../modules/state.js';
import {
    setElements,
    setCallbacks,
    loadSettings,
    saveSettings,
    loadPresets,
    applyPreset,
    modelSupportsVerbosity,
} from '../modules/settings.js';

describe('Settings Module', () => {
    let mockElements;
    let mockCallbacks;

    beforeEach(() => {
        // Reset state
        state.settings = {
            model: 'claude-sonnet-4-5-20250929',
            temperature: 1.0,
            maxTokens: 8192,
            systemPrompt: '',
            conversationType: 'NORMAL',
        };
        state.presets = [];

        // Create mock elements
        mockElements = {
            modelSelect: document.createElement('select'),
            temperatureSlider: document.createElement('input'),
            temperatureValue: document.createElement('span'),
            maxTokensInput: document.createElement('input'),
            systemPromptInput: document.createElement('textarea'),
            presetSelect: document.createElement('select'),
            verbositySelect: document.createElement('select'),
            verbosityContainer: document.createElement('div'),
        };

        // Set up input types
        mockElements.temperatureSlider.type = 'range';
        mockElements.temperatureSlider.min = '0';
        mockElements.temperatureSlider.max = '2';
        mockElements.temperatureSlider.step = '0.1';
        mockElements.maxTokensInput.type = 'number';

        // Add model options
        const claudeOption = document.createElement('option');
        claudeOption.value = 'claude-sonnet-4-5-20250929';
        claudeOption.textContent = 'Claude Sonnet';
        mockElements.modelSelect.appendChild(claudeOption);

        const gptOption = document.createElement('option');
        gptOption.value = 'gpt-5.1';
        gptOption.textContent = 'GPT-5.1';
        mockElements.modelSelect.appendChild(gptOption);

        // Add preset options
        const defaultPreset = document.createElement('option');
        defaultPreset.value = '';
        defaultPreset.textContent = 'Select Preset';
        mockElements.presetSelect.appendChild(defaultPreset);

        // Add verbosity options
        ['low', 'medium', 'high'].forEach(level => {
            const opt = document.createElement('option');
            opt.value = level;
            opt.textContent = level;
            mockElements.verbositySelect.appendChild(opt);
        });

        mockCallbacks = {
            onSettingsChanged: vi.fn(),
        };

        setElements(mockElements);
        setCallbacks(mockCallbacks);

        vi.clearAllMocks();
    });

    afterEach(() => {
        vi.restoreAllMocks();
    });

    describe('loadSettings', () => {
        it('should populate UI with current settings', () => {
            state.settings = {
                model: 'claude-sonnet-4-5-20250929',
                temperature: 0.8,
                maxTokens: 4096,
                systemPrompt: 'Test prompt',
            };

            loadSettings();

            expect(mockElements.modelSelect.value).toBe('claude-sonnet-4-5-20250929');
            expect(mockElements.temperatureSlider.value).toBe('0.8');
            expect(mockElements.maxTokensInput.value).toBe('4096');
            expect(mockElements.systemPromptInput.value).toBe('Test prompt');
        });

        it('should display temperature value', () => {
            state.settings.temperature = 0.7;

            loadSettings();

            expect(mockElements.temperatureValue.textContent).toBe('0.7');
        });
    });

    describe('saveSettings', () => {
        it('should update state with UI values', () => {
            mockElements.modelSelect.value = 'gpt-5.1';
            mockElements.temperatureSlider.value = '0.5';
            mockElements.maxTokensInput.value = '2048';
            mockElements.systemPromptInput.value = 'New prompt';

            saveSettings();

            expect(state.settings.model).toBe('gpt-5.1');
            expect(state.settings.temperature).toBe(0.5);
            expect(state.settings.maxTokens).toBe(2048);
            expect(state.settings.systemPrompt).toBe('New prompt');
        });

        it('should call onSettingsChanged callback', () => {
            saveSettings();

            expect(mockCallbacks.onSettingsChanged).toHaveBeenCalled();
        });
    });

    describe('loadPresets', () => {
        it('should fetch presets from API', async () => {
            window.api.getPresets = vi.fn(() => Promise.resolve({
                default: { name: 'Default', config: {} },
            }));

            await loadPresets();

            expect(window.api.getPresets).toHaveBeenCalled();
        });

        it('should populate preset select', async () => {
            window.api.getPresets = vi.fn(() => Promise.resolve({
                default: { name: 'Default', config: {} },
                creative: { name: 'Creative', config: {} },
            }));

            await loadPresets();

            // Original + 2 presets
            expect(mockElements.presetSelect.options.length).toBe(3);
        });
    });

    describe('applyPreset', () => {
        it('should apply preset configuration', () => {
            state.presets = {
                creative: {
                    name: 'Creative',
                    config: {
                        temperature: 1.5,
                        max_tokens: 8192,
                        system_prompt: 'Be creative',
                    },
                },
            };

            applyPreset('creative');

            expect(state.settings.temperature).toBe(1.5);
            expect(state.settings.maxTokens).toBe(8192);
            expect(state.settings.systemPrompt).toBe('Be creative');
        });

        it('should do nothing for unknown preset', () => {
            const originalTemp = state.settings.temperature;

            applyPreset('nonexistent');

            expect(state.settings.temperature).toBe(originalTemp);
        });
    });

    describe('modelSupportsVerbosity', () => {
        it('should return true for GPT-5 models', () => {
            expect(modelSupportsVerbosity('gpt-5.1')).toBe(true);
            expect(modelSupportsVerbosity('gpt-5.2')).toBe(true);
            expect(modelSupportsVerbosity('gpt-5-mini')).toBe(true);
        });

        it('should return false for Claude models', () => {
            expect(modelSupportsVerbosity('claude-sonnet-4-5-20250929')).toBe(false);
            expect(modelSupportsVerbosity('claude-opus-4-5-20251101')).toBe(false);
        });

        it('should return false for GPT-4 models', () => {
            expect(modelSupportsVerbosity('gpt-4o')).toBe(false);
            expect(modelSupportsVerbosity('gpt-4-turbo')).toBe(false);
        });

        it('should handle null/undefined input', () => {
            expect(modelSupportsVerbosity(null)).toBeFalsy();
            expect(modelSupportsVerbosity(undefined)).toBeFalsy();
        });
    });
});
