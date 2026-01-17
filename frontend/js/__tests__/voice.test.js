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
        state.ttsProvider = null;
        state.ttsVoices = [];
        state.localTtsServerHealthy = false;
        state.selectedVoiceId = null;
        state.currentAudio = null;
        state.currentSpeakingBtn = null;
        state.audioCache = new Map();
        state.dictationMode = 'none';
        state.isRecording = false;

        // Create mock audio element
        mockAudioElement = {
            src: '',
            play: vi.fn(() => Promise.resolve()),
            pause: vi.fn(),
            addEventListener: vi.fn(),
            removeEventListener: vi.fn(),
            onended: null,
            onerror: null,
        };

        // Create mock elements
        mockElements = {
            ttsProviderGroup: document.createElement('div'),
            ttsProviderName: document.createElement('span'),
            ttsProviderStatus: document.createElement('span'),
            voiceCloneGroup: document.createElement('div'),
            voiceSelectGroup: document.createElement('div'),
            voiceSelect: document.createElement('select'),
            voiceManageGroup: document.createElement('div'),
            voiceList: document.createElement('div'),
            styletts2ParamsGroup: document.createElement('div'),
            voiceBtn: document.createElement('button'),
            messageInput: document.createElement('textarea'),
        };

        // Add default option to voice select
        const defaultOption = document.createElement('option');
        defaultOption.value = '';
        defaultOption.textContent = 'Select Voice';
        mockElements.voiceSelect.appendChild(defaultOption);

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
                configured: true,
                provider: 'elevenlabs',
                voices: [],
            }));

            await checkTTSStatus();

            expect(window.api.getTTSStatus).toHaveBeenCalled();
        });

        it('should update ttsEnabled state when configured', async () => {
            window.api.getTTSStatus = vi.fn(() => Promise.resolve({
                configured: true,
                provider: 'elevenlabs',
                voices: [{ voice_id: 'v1', label: 'Voice 1' }],
            }));

            await checkTTSStatus();

            expect(state.ttsEnabled).toBe(true);
        });

        it('should set ttsEnabled to false when not configured', async () => {
            window.api.getTTSStatus = vi.fn(() => Promise.resolve({
                configured: false,
            }));

            await checkTTSStatus();

            expect(state.ttsEnabled).toBe(false);
        });

        it('should store voices from TTS status response', async () => {
            const voices = [
                { voice_id: 'voice-1', label: 'Voice 1' },
                { voice_id: 'voice-2', label: 'Voice 2' },
            ];
            window.api.getTTSStatus = vi.fn(() => Promise.resolve({
                configured: true,
                provider: 'styletts2',
                voices: voices,
                server_healthy: true,
            }));

            await checkTTSStatus();

            expect(state.ttsVoices).toEqual(voices);
            expect(state.ttsProvider).toBe('styletts2');
            expect(state.localTtsServerHealthy).toBe(true);
        });

        it('should set default voice when none selected', async () => {
            window.api.getTTSStatus = vi.fn(() => Promise.resolve({
                configured: true,
                provider: 'elevenlabs',
                voices: [{ voice_id: 'voice-1', label: 'Voice 1' }],
            }));

            await checkTTSStatus();

            expect(state.selectedVoiceId).toBe('voice-1');
        });
    });

    describe('updateTTSUI', () => {
        it('should show provider group when TTS is enabled', () => {
            state.ttsEnabled = true;
            state.ttsProvider = 'elevenlabs';
            mockElements.ttsProviderGroup.style.display = 'none';

            updateTTSUI();

            expect(mockElements.ttsProviderGroup.style.display).toBe('block');
        });

        it('should hide provider group when TTS is disabled', () => {
            state.ttsEnabled = false;
            mockElements.ttsProviderGroup.style.display = 'block';

            updateTTSUI();

            expect(mockElements.ttsProviderGroup.style.display).toBe('none');
        });

        it('should show voice cloning option for XTTS provider', () => {
            state.ttsEnabled = true;
            state.ttsProvider = 'xtts';

            updateTTSUI();

            expect(mockElements.voiceCloneGroup.style.display).toBe('block');
        });

        it('should show voice cloning option for StyleTTS2 provider', () => {
            state.ttsEnabled = true;
            state.ttsProvider = 'styletts2';

            updateTTSUI();

            expect(mockElements.voiceCloneGroup.style.display).toBe('block');
        });

        it('should hide voice cloning for ElevenLabs provider', () => {
            state.ttsEnabled = true;
            state.ttsProvider = 'elevenlabs';

            updateTTSUI();

            expect(mockElements.voiceCloneGroup.style.display).toBe('none');
        });

        it('should show StyleTTS2 params group for StyleTTS2 provider', () => {
            state.ttsEnabled = true;
            state.ttsProvider = 'styletts2';

            updateTTSUI();

            expect(mockElements.styletts2ParamsGroup.style.display).toBe('block');
        });

        it('should hide StyleTTS2 params group for other providers', () => {
            state.ttsEnabled = true;
            state.ttsProvider = 'xtts';

            updateTTSUI();

            expect(mockElements.styletts2ParamsGroup.style.display).toBe('none');
        });

        it('should populate voice select with available voices', () => {
            state.ttsEnabled = true;
            state.ttsProvider = 'elevenlabs';
            state.ttsVoices = [
                { voice_id: 'voice-1', label: 'Voice 1' },
                { voice_id: 'voice-2', label: 'Voice 2' },
            ];

            updateTTSUI();

            expect(mockElements.voiceSelect.innerHTML).toContain('Voice 1');
            expect(mockElements.voiceSelect.innerHTML).toContain('Voice 2');
        });
    });

    describe('speakMessage', () => {
        let mockBtn;

        beforeEach(() => {
            mockBtn = document.createElement('button');
            mockBtn.classList.add = vi.fn();
            mockBtn.classList.remove = vi.fn();
            state.ttsEnabled = true;
            state.audioCache = new Map();
        });

        it('should stop if clicking the same button while playing', async () => {
            state.currentAudio = mockAudioElement;
            state.currentSpeakingBtn = mockBtn;

            await speakMessage('Hello world', mockBtn, 'msg-1');

            expect(mockAudioElement.pause).toHaveBeenCalled();
        });

        it('should call API to speak message when not cached', async () => {
            window.api.textToSpeech = vi.fn(() => Promise.resolve(new Blob(['audio'], { type: 'audio/wav' })));

            await speakMessage('Hello world', mockBtn, 'msg-1');

            expect(window.api.textToSpeech).toHaveBeenCalled();
        });

        it('should use cached audio if available with same voice', async () => {
            const cachedBlob = new Blob(['audio'], { type: 'audio/wav' });
            const cachedUrl = 'blob:cached-url';
            state.selectedVoiceId = 'voice-1';
            state.audioCache.set('msg-1', {
                blob: cachedBlob,
                url: cachedUrl,
                voiceId: 'voice-1',
            });

            window.api.textToSpeech = vi.fn();

            await speakMessage('Hello world', mockBtn, 'msg-1');

            expect(window.api.textToSpeech).not.toHaveBeenCalled();
            expect(mockAudioElement.play).toHaveBeenCalled();
        });

        it('should set loading class on button during synthesis', async () => {
            window.api.textToSpeech = vi.fn(() => Promise.resolve(new Blob(['audio'], { type: 'audio/wav' })));

            await speakMessage('Hello world', mockBtn, 'msg-1');

            expect(mockBtn.classList.add).toHaveBeenCalledWith('loading');
        });
    });

    describe('stopSpeaking', () => {
        it('should pause audio playback', () => {
            state.currentAudio = mockAudioElement;

            stopSpeaking();

            expect(mockAudioElement.pause).toHaveBeenCalled();
        });

        it('should clear currentAudio', () => {
            state.currentAudio = mockAudioElement;

            stopSpeaking();

            expect(state.currentAudio).toBeNull();
        });

        it('should reset button state', () => {
            const mockBtn = {
                classList: {
                    remove: vi.fn(),
                },
                title: 'Stop',
            };
            state.currentSpeakingBtn = mockBtn;

            stopSpeaking();

            expect(mockBtn.classList.remove).toHaveBeenCalledWith('loading', 'speaking');
            expect(mockBtn.title).toBe('Read aloud');
            expect(state.currentSpeakingBtn).toBeNull();
        });
    });

    describe('checkSTTStatus', () => {
        it('should call API to get STT status', async () => {
            window.api.getSTTStatus = vi.fn(() => Promise.resolve({
                enabled: true,
                effective_mode: 'whisper',
            }));

            await checkSTTStatus();

            expect(window.api.getSTTStatus).toHaveBeenCalled();
        });

        it('should update dictationMode state when enabled', async () => {
            window.api.getSTTStatus = vi.fn(() => Promise.resolve({
                enabled: true,
                effective_mode: 'whisper',
            }));

            await checkSTTStatus();

            expect(state.dictationMode).toBe('whisper');
        });

        it('should set dictationMode to none when STT disabled', async () => {
            window.api.getSTTStatus = vi.fn(() => Promise.resolve({
                enabled: false,
            }));

            await checkSTTStatus();

            expect(state.dictationMode).toBe('none');
        });

        it('should show voice button when STT enabled', async () => {
            mockElements.voiceBtn.style.display = 'none';
            window.api.getSTTStatus = vi.fn(() => Promise.resolve({
                enabled: true,
                effective_mode: 'browser',
            }));

            await checkSTTStatus();

            expect(mockElements.voiceBtn.style.display).toBe('flex');
        });

        it('should hide voice button when STT disabled', async () => {
            mockElements.voiceBtn.style.display = 'flex';
            window.api.getSTTStatus = vi.fn(() => Promise.resolve({
                enabled: false,
            }));

            await checkSTTStatus();

            expect(mockElements.voiceBtn.style.display).toBe('none');
        });
    });
});
