/**
 * Unit Tests for Voice Module
 * Tests TTS and STT functionality
 */

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { state, LOCAL_VOICE_DEFAULTS } from '../modules/state.js';
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
        state.localVoice = { ...LOCAL_VOICE_DEFAULTS };
        state.localTtsUrl = null;
        window.api.localVoiceHealth = vi.fn(() => Promise.reject(new Error('Failed to fetch')));
        window.api.localListVoices = vi.fn(() => Promise.resolve({ voices: [] }));
        delete window.SpeechRecognition;
        delete window.webkitSpeechRecognition;

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

        it('never probes localhost when local voice is off in this browser', async () => {
            window.api.getTTSStatus = vi.fn(() => Promise.resolve({ configured: false }));

            await checkTTSStatus();

            expect(window.api.localVoiceHealth).not.toHaveBeenCalled();
            expect(state.ttsEnabled).toBe(false);
        });

        it('uses StyleTTS 2 first when both local servers answer', async () => {
            const voices = [{ voice_id: 'voice-1', label: 'Voice 1', is_cloned: true }];
            state.localVoice.enabled = true;
            window.api.localVoiceHealth = vi.fn(() => Promise.resolve({ status: 'healthy' }));
            window.api.localListVoices = vi.fn(() => Promise.resolve({ provider: 'styletts2', voices }));
            window.api.getTTSStatus = vi.fn();

            await checkTTSStatus();

            expect(window.api.localListVoices).toHaveBeenCalledWith('http://localhost:8021');
            expect(state.ttsProvider).toBe('styletts2');
            expect(state.localTtsUrl).toBe('http://localhost:8021');
            expect(state.ttsVoices).toEqual(voices);
            expect(state.localTtsServerHealthy).toBe(true);
            expect(window.api.getTTSStatus).not.toHaveBeenCalled();
        });

        it('uses XTTS when only it answers', async () => {
            state.localVoice.enabled = true;
            window.api.localVoiceHealth = vi.fn(url => url.endsWith('8020')
                ? Promise.resolve({ status: 'healthy' })
                : Promise.reject(new Error('Failed to fetch')));

            await checkTTSStatus();

            expect(state.ttsProvider).toBe('xtts');
            expect(state.localTtsUrl).toBe('http://localhost:8020');
        });

        it('falls back to ElevenLabs through the backend when no local server answers', async () => {
            state.localVoice.enabled = true;
            window.api.getTTSStatus = vi.fn(() => Promise.resolve({
                configured: true,
                provider: 'elevenlabs',
                voices: [{ voice_id: 'el-1', label: 'Rachel' }],
            }));

            await checkTTSStatus();

            expect(window.api.localVoiceHealth).toHaveBeenCalledTimes(2);
            expect(state.ttsProvider).toBe('elevenlabs');
            expect(state.localTtsUrl).toBeNull();
        });

        it('replaces a saved voice the server no longer has', async () => {
            localStorage.setItem('selected_voice_id', 'deleted-voice');
            state.localVoice.enabled = true;
            window.api.localVoiceHealth = vi.fn(() => Promise.resolve({ status: 'healthy' }));
            window.api.localListVoices = vi.fn(() => Promise.resolve({ voices: [{ voice_id: 'v2', label: 'V2' }] }));

            await checkTTSStatus();

            expect(state.selectedVoiceId).toBe('v2');
            localStorage.removeItem('selected_voice_id');
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

        it('speaks through the local server in use, with StyleTTS 2 overrides', async () => {
            state.ttsProvider = 'styletts2';
            state.localTtsUrl = 'http://localhost:8021';
            state.selectedVoiceId = 'v1';
            window.api.textToSpeech = vi.fn();
            window.api.localSpeak = vi.fn(() => Promise.resolve(new Blob(['audio'], { type: 'audio/wav' })));

            await speakMessage('Hello world', mockBtn, 'msg-local');

            expect(window.api.textToSpeech).not.toHaveBeenCalled();
            const [url, body] = window.api.localSpeak.mock.calls[0];
            expect(url).toBe('http://localhost:8021');
            expect(body).toMatchObject({ text: 'Hello world', voice_id: 'v1', alpha: 0.3, speed: 1.0 });
        });

        it('sends XTTS no StyleTTS 2 overrides', async () => {
            state.ttsProvider = 'xtts';
            state.localTtsUrl = 'http://localhost:8020';
            window.api.localSpeak = vi.fn(() => Promise.resolve(new Blob(['audio'], { type: 'audio/wav' })));

            await speakMessage('Hello world', mockBtn, 'msg-xtts');

            expect(Object.keys(window.api.localSpeak.mock.calls[0][1]).sort()).toEqual(['text', 'voice_id']);
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
        function giveBrowserSpeechRecognition() {
            window.SpeechRecognition = vi.fn(() => ({ start: vi.fn(), stop: vi.fn() }));
        }

        it('uses the browser\'s own dictation without probing when local voice is off', async () => {
            giveBrowserSpeechRecognition();
            mockElements.voiceBtn.style.display = 'none';

            await checkSTTStatus();

            expect(window.api.localVoiceHealth).not.toHaveBeenCalled();
            expect(state.dictationMode).toBe('browser');
            expect(mockElements.voiceBtn.style.display).toBe('flex');
        });

        it('hides the button when the browser has no speech recognition', async () => {
            mockElements.voiceBtn.style.display = 'flex';

            await checkSTTStatus();

            expect(state.dictationMode).toBe('none');
            expect(mockElements.voiceBtn.style.display).toBe('none');
        });

        it('uses Whisper when it answers with its model loaded', async () => {
            state.localVoice.enabled = true;
            window.api.localVoiceHealth = vi.fn(() => Promise.resolve({ model_loaded: true }));

            await checkSTTStatus();

            expect(window.api.localVoiceHealth).toHaveBeenCalledWith('http://localhost:8030');
            expect(state.dictationMode).toBe('whisper');
            expect(mockElements.voiceBtn.style.display).toBe('flex');
        });

        it('falls back to the browser while Whisper\'s model is loading (auto)', async () => {
            giveBrowserSpeechRecognition();
            state.localVoice.enabled = true;
            window.api.localVoiceHealth = vi.fn(() => Promise.resolve({ model_loaded: false }));

            await checkSTTStatus();

            expect(state.dictationMode).toBe('browser');
        });

        it('offers no dictation when Whisper only is chosen and it is down', async () => {
            giveBrowserSpeechRecognition();
            state.localVoice = { ...state.localVoice, enabled: true, dictationMode: 'whisper' };

            await checkSTTStatus();

            expect(state.dictationMode).toBe('none');
            expect(mockElements.voiceBtn.style.display).toBe('none');
        });

        it('never probes Whisper when the browser\'s own dictation is chosen', async () => {
            giveBrowserSpeechRecognition();
            state.localVoice = { ...state.localVoice, enabled: true, dictationMode: 'browser' };

            await checkSTTStatus();

            expect(window.api.localVoiceHealth).not.toHaveBeenCalled();
            expect(state.dictationMode).toBe('browser');
        });
    });
});
