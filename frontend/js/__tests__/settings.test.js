/**
 * Unit Tests for Settings Module
 * Tests settings management and presets
 */

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { state } from '../modules/state.js';
import {
    setElements,
    setCallbacks,
    applySettings,
    initializeSettingsUI,
    loadPreset,
    modelSupportsVerbosity,
    modelSupportsTemperature,
    updateTemperatureControlState,
    updateVerbosityControlState,
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
            verbosity: 'medium',
            researcherName: '',
        };
        state.selectedEntityId = null;
        state.entitySystemPrompts = {};
        state.ttsVoices = [];
        state.ttsProvider = null;
        state.availableModels = [
            { id: 'claude-sonnet-4-5-20250929', name: 'Claude Sonnet', temperature_supported: true },
            { id: 'gpt-5.1', name: 'GPT-5.1', temperature_supported: true },
            { id: 'o1', name: 'O1', temperature_supported: false },
        ];

        // Create mock elements matching actual module usage
        mockElements = {
            modelSelect: document.createElement('select'),
            temperatureInput: document.createElement('input'),
            temperatureNumber: document.createElement('input'),
            maxTokensInput: document.createElement('input'),
            systemPromptInput: document.createElement('textarea'),
            conversationTypeSelect: document.createElement('select'),
            verbositySelect: document.createElement('select'),
            themeSelect: document.createElement('select'),
            voiceSelect: document.createElement('select'),
            researcherNameInput: document.createElement('input'),
            modelIndicator: document.createElement('span'),
        };

        // Set up input types
        mockElements.temperatureInput.type = 'range';
        mockElements.temperatureInput.min = '0';
        mockElements.temperatureInput.max = '2';
        mockElements.temperatureInput.step = '0.1';
        mockElements.temperatureNumber.type = 'number';
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

        // Add conversation type options
        const normalOption = document.createElement('option');
        normalOption.value = 'NORMAL';
        normalOption.textContent = 'Normal';
        mockElements.conversationTypeSelect.appendChild(normalOption);

        // Add verbosity options
        ['low', 'medium', 'high'].forEach(level => {
            const opt = document.createElement('option');
            opt.value = level;
            opt.textContent = level;
            mockElements.verbositySelect.appendChild(opt);
        });

        // Wrap verbositySelect in form-group for visibility test
        const verbosityFormGroup = document.createElement('div');
        verbosityFormGroup.className = 'form-group';
        verbosityFormGroup.appendChild(mockElements.verbositySelect);
        document.body.appendChild(verbosityFormGroup);

        // Add theme options
        const lightOption = document.createElement('option');
        lightOption.value = 'light';
        lightOption.textContent = 'Light';
        mockElements.themeSelect.appendChild(lightOption);

        mockCallbacks = {
            updateModelIndicator: vi.fn(),
            getMaxTemperatureForCurrentEntity: vi.fn(() => 2.0),
        };

        setElements(mockElements);
        setCallbacks(mockCallbacks);

        vi.clearAllMocks();
    });

    afterEach(() => {
        vi.restoreAllMocks();
        document.body.innerHTML = '';
    });

    describe('initializeSettingsUI', () => {
        it('should populate UI with current settings', () => {
            state.settings = {
                model: 'claude-sonnet-4-5-20250929',
                temperature: 0.8,
                maxTokens: 4096,
                systemPrompt: 'Test prompt',
                conversationType: 'NORMAL',
                verbosity: 'medium',
                researcherName: 'Test Researcher',
            };

            initializeSettingsUI();

            expect(mockElements.modelSelect.value).toBe('claude-sonnet-4-5-20250929');
            expect(mockElements.temperatureInput.value).toBe('0.8');
            expect(mockElements.temperatureNumber.value).toBe('0.8');
            expect(mockElements.maxTokensInput.value).toBe('4096');
            expect(mockElements.systemPromptInput.value).toBe('Test prompt');
        });

        it('should set verbosity select to current value', () => {
            state.settings.verbosity = 'high';

            initializeSettingsUI();

            expect(mockElements.verbositySelect.value).toBe('high');
        });

        it('should set researcher name input', () => {
            state.settings.researcherName = 'Dr. Smith';

            initializeSettingsUI();

            expect(mockElements.researcherNameInput.value).toBe('Dr. Smith');
        });
    });

    describe('applySettings', () => {
        it('should update state with UI values', () => {
            mockElements.modelSelect.value = 'gpt-5.1';
            mockElements.temperatureInput.value = '0.5';
            mockElements.maxTokensInput.value = '2048';
            mockElements.systemPromptInput.value = 'New prompt';
            mockElements.conversationTypeSelect.value = 'NORMAL';
            mockElements.verbositySelect.value = 'low';
            mockElements.themeSelect.value = 'light';
            mockElements.researcherNameInput.value = 'Researcher';

            applySettings();

            expect(state.settings.model).toBe('gpt-5.1');
            expect(state.settings.temperature).toBe(0.5);
            expect(state.settings.maxTokens).toBe(2048);
            expect(state.settings.systemPrompt).toBe('New prompt');
            expect(state.settings.verbosity).toBe('low');
            expect(state.settings.researcherName).toBe('Researcher');
        });

        it('should call updateModelIndicator callback', () => {
            applySettings();

            expect(mockCallbacks.updateModelIndicator).toHaveBeenCalled();
        });

        it('should trim whitespace from system prompt', () => {
            mockElements.systemPromptInput.value = '  trimmed prompt  ';

            applySettings();

            expect(state.settings.systemPrompt).toBe('trimmed prompt');
        });

        it('should set systemPrompt to null for empty string', () => {
            mockElements.systemPromptInput.value = '   ';

            applySettings();

            expect(state.settings.systemPrompt).toBeNull();
        });
    });

    describe('loadPreset', () => {
        it('should apply research preset (null system prompt)', () => {
            loadPreset('research');

            expect(mockElements.systemPromptInput.value).toBe('');
        });

        it('should apply reflection preset', () => {
            loadPreset('reflection');

            expect(mockElements.systemPromptInput.value).toContain('reflection session');
        });

        it('should apply memory-aware preset', () => {
            loadPreset('memory-aware');

            expect(mockElements.systemPromptInput.value).toContain('memories');
        });

        it('should apply research-context preset', () => {
            loadPreset('research-context');

            expect(mockElements.systemPromptInput.value).toContain('research conversation');
        });

        it('should keep current system prompt for custom preset', () => {
            state.settings.systemPrompt = 'My custom prompt';

            loadPreset('custom');

            expect(mockElements.systemPromptInput.value).toBe('My custom prompt');
        });

        it('should do nothing for unknown preset', () => {
            mockElements.systemPromptInput.value = 'Original';

            loadPreset('nonexistent');

            expect(mockElements.systemPromptInput.value).toBe('Original');
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

    describe('modelSupportsTemperature', () => {
        it('should return true for models that support temperature', () => {
            expect(modelSupportsTemperature('claude-sonnet-4-5-20250929')).toBe(true);
            expect(modelSupportsTemperature('gpt-5.1')).toBe(true);
        });

        it('should return false for o1 model', () => {
            expect(modelSupportsTemperature('o1')).toBe(false);
        });

        it('should return true for unknown models', () => {
            expect(modelSupportsTemperature('unknown-model')).toBe(true);
        });
    });

    describe('updateTemperatureControlState', () => {
        it('should disable temperature input for o1 model', () => {
            mockElements.modelSelect.value = 'o1';

            // Add o1 option
            const o1Option = document.createElement('option');
            o1Option.value = 'o1';
            o1Option.textContent = 'O1';
            mockElements.modelSelect.appendChild(o1Option);
            mockElements.modelSelect.value = 'o1';

            updateTemperatureControlState();

            expect(mockElements.temperatureInput.disabled).toBe(true);
            expect(mockElements.temperatureNumber.disabled).toBe(true);
        });

        it('should enable temperature input for claude model', () => {
            mockElements.modelSelect.value = 'claude-sonnet-4-5-20250929';

            updateTemperatureControlState();

            expect(mockElements.temperatureInput.disabled).toBe(false);
            expect(mockElements.temperatureNumber.disabled).toBe(false);
        });
    });

    describe('updateVerbosityControlState', () => {
        it('should show verbosity control for GPT-5 models', () => {
            mockElements.modelSelect.value = 'gpt-5.1';

            updateVerbosityControlState();

            const formGroup = mockElements.verbositySelect.closest('.form-group');
            expect(formGroup.style.display).toBe('block');
        });

        it('should hide verbosity control for non-GPT-5 models', () => {
            mockElements.modelSelect.value = 'claude-sonnet-4-5-20250929';

            updateVerbosityControlState();

            const formGroup = mockElements.verbositySelect.closest('.form-group');
            expect(formGroup.style.display).toBe('none');
        });
    });
});
