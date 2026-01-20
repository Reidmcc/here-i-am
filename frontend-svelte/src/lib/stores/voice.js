/**
 * Voice Store - TTS and STT state management
 */
import { writable, derived, get } from 'svelte/store';

// TTS status
export const ttsEnabled = writable(false);
export const ttsProvider = writable(null); // 'elevenlabs', 'xtts', or 'styletts2'
export const ttsVoices = writable([]);
export const voices = ttsVoices; // Alias for consistency

// Selected voice ID
function createSelectedVoiceStore() {
    const stored = typeof localStorage !== 'undefined'
        ? localStorage.getItem('selectedVoiceId')
        : null;

    const { subscribe, set } = writable(stored);

    return {
        subscribe,
        set: (value) => {
            if (typeof localStorage !== 'undefined') {
                if (value) {
                    localStorage.setItem('selectedVoiceId', value);
                } else {
                    localStorage.removeItem('selectedVoiceId');
                }
            }
            set(value);
        }
    };
}

export const selectedVoiceId = createSelectedVoiceStore();

// Audio cache for TTS responses
function createAudioCacheStore() {
    const { subscribe, update } = writable(new Map());

    return {
        subscribe,
        get: (key) => {
            let cache;
            update(c => { cache = c; return c; });
            const entry = cache.get(key);
            if (entry && entry.expires > Date.now()) {
                return entry;
            }
            return null;
        },
        set: (key, url, audio, ttl = 5 * 60 * 1000) => {
            update(cache => {
                cache.set(key, { url, audio, expires: Date.now() + ttl });
                return cache;
            });
        },
        clear: () => {
            update(cache => {
                // Revoke all blob URLs
                for (const [, entry] of cache) {
                    if (entry.url && entry.url.startsWith('blob:')) {
                        URL.revokeObjectURL(entry.url);
                    }
                }
                return new Map();
            });
        }
    };
}

export const audioCache = createAudioCacheStore();

// Currently playing audio
export const currentAudio = writable(null);

// STT status
export const sttEnabled = writable(false);
export const sttProvider = writable(null); // 'whisper' or 'browser'
export const dictationMode = writable('auto'); // 'whisper', 'browser', or 'auto'

// Recording state
export const isRecording = writable(false);
export const recordingDuration = writable(0);

// StyleTTS 2 parameters
function createStyleTTS2ParamsStore() {
    const defaultParams = {
        alpha: 0.3,
        beta: 0.7,
        diffusion_steps: 10,
        embedding_scale: 1.0,
    };

    let stored = defaultParams;
    if (typeof localStorage !== 'undefined') {
        try {
            const saved = localStorage.getItem('styletts2Params');
            if (saved) {
                stored = { ...defaultParams, ...JSON.parse(saved) };
            }
        } catch (e) {
            stored = defaultParams;
        }
    }

    const { subscribe, set, update } = writable(stored);

    return {
        subscribe,
        set: (value) => {
            if (typeof localStorage !== 'undefined') {
                localStorage.setItem('styletts2Params', JSON.stringify(value));
            }
            set(value);
        },
        update: (fn) => {
            update(current => {
                const newValue = fn(current);
                if (typeof localStorage !== 'undefined') {
                    localStorage.setItem('styletts2Params', JSON.stringify(newValue));
                }
                return newValue;
            });
        },
        reset: () => {
            if (typeof localStorage !== 'undefined') {
                localStorage.removeItem('styletts2Params');
            }
            set(defaultParams);
        }
    };
}

export const styleTTS2Params = createStyleTTS2ParamsStore();
export const styletts2Params = styleTTS2Params; // Alias for consistency

// Helper to update StyleTTS2 parameters
export function updateStyleTTS2Params(updates) {
    styleTTS2Params.update(current => ({ ...current, ...updates }));
}

// Derived store: selected voice object
export const selectedVoice = derived(
    [ttsVoices, selectedVoiceId],
    ([$ttsVoices, $selectedVoiceId]) => {
        if (!$selectedVoiceId) return null;
        return $ttsVoices.find(v => v.voice_id === $selectedVoiceId) || null;
    }
);

// Derived store: is StyleTTS2 provider
export const isStyleTTS2 = derived(
    ttsProvider,
    ($ttsProvider) => $ttsProvider === 'styletts2'
);

// Helper to stop current audio
export function stopCurrentAudio() {
    currentAudio.update(audio => {
        if (audio) {
            audio.pause();
            audio.currentTime = 0;
        }
        return null;
    });
}

// Helper to play audio for a message
export async function playAudio(url) {
    stopCurrentAudio();

    const audio = new Audio(url);
    currentAudio.set(audio);

    return new Promise((resolve, reject) => {
        audio.onended = () => {
            currentAudio.set(null);
            resolve();
        };
        audio.onerror = (e) => {
            currentAudio.set(null);
            reject(e);
        };
        audio.play().catch(reject);
    });
}

// Reset voice state
export function resetVoiceState() {
    stopCurrentAudio();
    isRecording.set(false);
    recordingDuration.set(0);
}

// Load voices from API (to be called with api.listTTSVoices)
export async function loadVoices() {
    // This is a placeholder - the actual loading is done in App.svelte
    // This function allows components to trigger a reload if needed
    // For now, it's handled in the app initialization
}
