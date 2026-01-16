/**
 * Unit Tests for Voice Module
 * Tests TTS and STT functionality
 */

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { state } from '../modules/state.js';
import {
    setElements,
    checkTTSStatus,
    updateTTSUI,
    speakMessage,
    stopSpeaking,
    checkSTTStatus,
} from '../modules/voice.js';

describe('Voice Module', () => {
    let mockElements;
    let mockAudioElement;

    beforeEach(() => {
        // Reset state
        state.ttsEnabled = false;
        state.sttEnabled = false;
        state.selectedVoiceId = null;
        state.ttsVoices = [];
        state.currentlyPlayingMessageId = null;

        // Create mock audio element
        mockAudioElement = {
            src: '',
            play: vi.fn(() => Promise.resolve()),
            pause: vi.fn(),
            addEventListener: vi.fn(),
            removeEventListener: vi.fn(),
        };

        // Create mock elements
        mockElements = {
            ttsToggle: document.createElement('input'),
            ttsVoiceSelect: document.createElement('select'),
            sttToggle: document.createElement('input'),
            dictationBtn: document.createElement('button'),
        };
        mockElements.ttsToggle.type = 'checkbox';
        mockElements.sttToggle.type = 'checkbox';

        // Add default option to voice select
        const defaultOption = document.createElement('option');
        defaultOption.value = '';
        defaultOption.textContent = 'Select Voice';
        mockElements.ttsVoiceSelect.appendChild(defaultOption);

        setElements(mockElements);

        // Mock Audio constructor
        global.Audio = vi.fn(() => mockAudioElement);

        vi.clearAllMocks();
    });

    afterEach(() => {
        vi.restoreAllMocks();
    });

    describe('checkTTSStatus', () => {
        it('should call API to get TTS status', async () => {
            window.api.getTTSStatus = vi.fn(() => Promise.resolve({
                enabled: true,
                provider: 'elevenlabs',
            }));
            window.api.listTTSVoices = vi.fn(() => Promise.resolve([]));

            await checkTTSStatus();

            expect(window.api.getTTSStatus).toHaveBeenCalled();
        });

        it('should update ttsEnabled state', async () => {
            window.api.getTTSStatus = vi.fn(() => Promise.resolve({
                enabled: true,
                provider: 'elevenlabs',
            }));
            window.api.listTTSVoices = vi.fn(() => Promise.resolve([]));

            await checkTTSStatus();

            expect(state.ttsEnabled).toBe(true);
        });

        it('should load voices when TTS is enabled', async () => {
            window.api.getTTSStatus = vi.fn(() => Promise.resolve({
                enabled: true,
                provider: 'styletts2',
            }));
            window.api.listTTSVoices = vi.fn(() => Promise.resolve([
                { voice_id: 'voice-1', label: 'Voice 1' },
            ]));

            await checkTTSStatus();

            expect(window.api.listTTSVoices).toHaveBeenCalled();
        });
    });

    describe('updateTTSUI', () => {
        it('should enable toggle when TTS is available', () => {
            state.ttsEnabled = true;

            updateTTSUI();

            expect(mockElements.ttsToggle.disabled).toBe(false);
        });

        it('should disable toggle when TTS is unavailable', () => {
            state.ttsEnabled = false;

            updateTTSUI();

            expect(mockElements.ttsToggle.disabled).toBe(true);
        });

        it('should populate voice select with available voices', () => {
            state.ttsEnabled = true;
            state.ttsVoices = [
                { voice_id: 'voice-1', label: 'Voice 1' },
                { voice_id: 'voice-2', label: 'Voice 2' },
            ];

            updateTTSUI();

            // +1 for default option
            expect(mockElements.ttsVoiceSelect.options.length).toBeGreaterThan(1);
        });
    });

    describe('speakMessage', () => {
        it('should not speak if TTS is disabled', async () => {
            state.ttsEnabled = false;

            await speakMessage('msg-1', 'Hello world');

            expect(window.api.speak).not.toHaveBeenCalled();
        });

        it('should call API to speak message', async () => {
            state.ttsEnabled = true;
            state.ttsUserEnabled = true;
            state.selectedVoiceId = 'voice-1';
            window.api.speak = vi.fn(() => Promise.resolve(new Blob(['audio'], { type: 'audio/wav' })));

            await speakMessage('msg-1', 'Hello world');

            expect(window.api.speak).toHaveBeenCalled();
        });

        it('should set currentlyPlayingMessageId', async () => {
            state.ttsEnabled = true;
            state.ttsUserEnabled = true;
            state.selectedVoiceId = 'voice-1';
            window.api.speak = vi.fn(() => Promise.resolve(new Blob(['audio'], { type: 'audio/wav' })));

            await speakMessage('msg-1', 'Hello world');

            expect(state.currentlyPlayingMessageId).toBe('msg-1');
        });
    });

    describe('stopSpeaking', () => {
        it('should pause audio playback', () => {
            state._currentAudio = mockAudioElement;

            stopSpeaking();

            expect(mockAudioElement.pause).toHaveBeenCalled();
        });

        it('should clear currentlyPlayingMessageId', () => {
            state.currentlyPlayingMessageId = 'msg-1';
            state._currentAudio = mockAudioElement;

            stopSpeaking();

            expect(state.currentlyPlayingMessageId).toBeNull();
        });
    });

    describe('checkSTTStatus', () => {
        it('should call API to get STT status', async () => {
            window.api.getSTTStatus = vi.fn(() => Promise.resolve({
                enabled: true,
                mode: 'whisper',
            }));

            await checkSTTStatus();

            expect(window.api.getSTTStatus).toHaveBeenCalled();
        });

        it('should update sttEnabled state', async () => {
            window.api.getSTTStatus = vi.fn(() => Promise.resolve({
                enabled: true,
                mode: 'whisper',
            }));

            await checkSTTStatus();

            expect(state.sttEnabled).toBe(true);
        });

        it('should enable dictation button when STT available', async () => {
            mockElements.dictationBtn.disabled = true;
            window.api.getSTTStatus = vi.fn(() => Promise.resolve({
                enabled: true,
                mode: 'browser',
            }));

            await checkSTTStatus();

            expect(mockElements.dictationBtn.disabled).toBe(false);
        });
    });
});
