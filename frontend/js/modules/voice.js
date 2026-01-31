/**
 * Voice Module
 * Handles TTS (text-to-speech) and STT (speech-to-text) functionality
 */

import {
    state,
    loadSelectedVoiceFromStorage,
    saveSelectedVoiceToStorage,
    clearAudioCache,
    loadLocalTtsSettingsFromStorage,
    saveLocalTtsSettingsToStorage,
    loadLocalSttSettingsFromStorage,
    saveLocalSttSettingsToStorage
} from './state.js';
import { showToast, escapeHtml, stripMarkdown } from './utils.js';
import { showModal, hideModal } from './modals.js';

// Reference to global API client
const api = window.api;

// Element references
let elements = {};

/**
 * Set element references
 * @param {Object} els - Element references
 */
export function setElements(els) {
    elements = els;
}

// =========================================================================
// TTS (Text-to-Speech)
// =========================================================================

/**
 * Check TTS status and update state
 * Handles both proxied TTS (through backend) and direct local TTS modes
 */
export async function checkTTSStatus() {
    try {
        const status = await api.getTTSStatus();

        // Check if local TTS direct mode is enabled by the server
        if (status.local_tts_enabled) {
            // Load any user-configured URL overrides from localStorage
            loadLocalTtsSettingsFromStorage();

            // Use server-provided URL if no localStorage override
            if (!localStorage.getItem('local_tts_settings')) {
                state.localTtsUrl = status.local_tts_url;
                state.localTtsProvider = status.local_tts_provider;
            }

            state.localTtsEnabled = true;

            // Check health of the local TTS server directly
            const health = await api.checkLocalTtsHealth(state.localTtsUrl);
            state.localTtsServerHealthy = health.healthy;

            if (health.healthy) {
                // Get voices directly from the local server
                try {
                    const voicesData = await api.getLocalTtsVoices(state.localTtsUrl);
                    state.ttsVoices = voicesData.voices || [];
                    state.ttsProvider = state.localTtsProvider;
                    state.ttsEnabled = true;

                    // Load selected voice from storage
                    loadSelectedVoiceFromStorage();
                    if (!state.selectedVoiceId && state.ttsVoices.length > 0) {
                        state.selectedVoiceId = state.ttsVoices[0].voice_id;
                    }

                    console.log(`Local TTS (${state.localTtsProvider}) enabled with ${state.ttsVoices.length} voice(s)`);
                } catch (e) {
                    console.warn('Failed to get voices from local TTS server:', e);
                    state.ttsEnabled = false;
                }
            } else {
                console.warn('Local TTS server not healthy:', health.error);
                state.ttsEnabled = false;
            }
        } else if (status.configured) {
            // Standard proxied TTS mode (through backend)
            state.localTtsEnabled = false;
            state.ttsEnabled = true;
            state.ttsProvider = status.provider;
            state.ttsVoices = status.voices || [];
            state.localTtsServerHealthy = status.server_healthy || false;

            // If local TTS, store the default voice if none selected
            if (state.ttsProvider === 'xtts' || state.ttsProvider === 'styletts2') {
                loadSelectedVoiceFromStorage();
                // Set default if none selected
                if (!state.selectedVoiceId && state.ttsVoices.length > 0) {
                    state.selectedVoiceId = state.ttsVoices[0].voice_id;
                }
            } else if (state.ttsVoices.length > 0) {
                // ElevenLabs
                loadSelectedVoiceFromStorage();
                if (!state.selectedVoiceId) {
                    state.selectedVoiceId = state.ttsVoices[0].voice_id;
                }
            }
        } else {
            state.ttsEnabled = false;
            state.localTtsEnabled = false;
        }

        updateTTSUI();
    } catch (error) {
        console.warn('TTS not available:', error);
        state.ttsEnabled = false;
        state.localTtsEnabled = false;
    }
}

/**
 * Update TTS-related UI elements
 */
export function updateTTSUI() {
    // Update voice button visibility
    if (state.ttsEnabled) {
        // Show TTS provider info in settings
        if (elements.ttsProviderGroup) {
            elements.ttsProviderGroup.style.display = 'block';
        }
        if (elements.ttsProviderName) {
            // Determine provider name and mode
            let providerName = '';
            let modeLabel = state.localTtsEnabled ? 'direct local' : 'local';

            if (state.ttsProvider === 'styletts2') {
                providerName = `StyleTTS 2 (${modeLabel})`;
                if (state.localTtsServerHealthy) {
                    elements.ttsProviderStatus.textContent = 'Connected';
                    elements.ttsProviderStatus.className = 'tts-status connected';
                } else {
                    elements.ttsProviderStatus.textContent = 'Server unavailable';
                    elements.ttsProviderStatus.className = 'tts-status error';
                }
            } else if (state.ttsProvider === 'xtts') {
                providerName = `XTTS v2 (${modeLabel})`;
                if (state.localTtsServerHealthy) {
                    elements.ttsProviderStatus.textContent = 'Connected';
                    elements.ttsProviderStatus.className = 'tts-status connected';
                } else {
                    elements.ttsProviderStatus.textContent = 'Server unavailable';
                    elements.ttsProviderStatus.className = 'tts-status error';
                }
            } else {
                providerName = 'ElevenLabs';
                elements.ttsProviderStatus.textContent = 'Connected';
                elements.ttsProviderStatus.className = 'tts-status connected';
            }

            elements.ttsProviderName.textContent = providerName;

            // Show local TTS URL if in direct local mode
            if (state.localTtsEnabled && elements.localTtsUrlDisplay) {
                elements.localTtsUrlDisplay.textContent = state.localTtsUrl;
                if (elements.localTtsUrlGroup) {
                    elements.localTtsUrlGroup.style.display = 'block';
                }
            } else if (elements.localTtsUrlGroup) {
                elements.localTtsUrlGroup.style.display = 'none';
            }
        }

        // Show voice cloning option for XTTS/StyleTTS2 (only in proxied mode)
        // In direct local mode, voice cloning should be done on the local machine
        if ((state.ttsProvider === 'xtts' || state.ttsProvider === 'styletts2') && !state.localTtsEnabled) {
            if (elements.voiceCloneGroup) {
                elements.voiceCloneGroup.style.display = 'block';
            }
        } else {
            if (elements.voiceCloneGroup) {
                elements.voiceCloneGroup.style.display = 'none';
            }
        }

        // Show StyleTTS 2 params if using StyleTTS 2
        if (state.ttsProvider === 'styletts2') {
            if (elements.styletts2ParamsGroup) {
                elements.styletts2ParamsGroup.style.display = 'block';
            }
        } else {
            if (elements.styletts2ParamsGroup) {
                elements.styletts2ParamsGroup.style.display = 'none';
            }
        }
    } else {
        if (elements.ttsProviderGroup) {
            elements.ttsProviderGroup.style.display = 'none';
        }
        if (elements.localTtsUrlGroup) {
            elements.localTtsUrlGroup.style.display = 'none';
        }
    }

    updateVoiceSelector();
    updateVoiceList();
}

/**
 * Update voice selector dropdown
 */
function updateVoiceSelector() {
    if (!elements.voiceSelect) return;

    if (state.ttsVoices.length > 0) {
        if (elements.voiceSelectGroup) {
            elements.voiceSelectGroup.style.display = 'block';
        }

        elements.voiceSelect.innerHTML = state.ttsVoices.map(voice => `
            <option value="${voice.voice_id}" ${voice.voice_id === state.selectedVoiceId ? 'selected' : ''}>
                ${escapeHtml(voice.label)}${voice.is_cloned ? ' (cloned)' : ''}
            </option>
        `).join('');
    } else {
        if (elements.voiceSelectGroup) {
            elements.voiceSelectGroup.style.display = 'none';
        }
    }
}

/**
 * Update voice management list
 */
function updateVoiceList() {
    if (!elements.voiceList) return;

    if (state.ttsProvider !== 'xtts' && state.ttsProvider !== 'styletts2') {
        if (elements.voiceManageGroup) {
            elements.voiceManageGroup.style.display = 'none';
        }
        return;
    }

    if (state.ttsVoices.length === 0) {
        if (elements.voiceManageGroup) {
            elements.voiceManageGroup.style.display = 'none';
        }
        return;
    }

    const clonedVoices = state.ttsVoices.filter(v => v.is_cloned);

    if (clonedVoices.length === 0) {
        if (state.ttsProvider === 'styletts2') {
            elements.voiceList.innerHTML = '<p class="no-voices">No cloned voices yet. Create one above!</p>';
        } else {
            elements.voiceList.innerHTML = '<p class="no-voices">No cloned voices yet. Create one above!</p>';
        }
        if (elements.voiceManageGroup) {
            elements.voiceManageGroup.style.display = 'block';
        }
        return;
    }

    elements.voiceList.innerHTML = clonedVoices.map(voice => `
        <div class="voice-list-item">
            <div class="voice-info">
                <span class="voice-name">${escapeHtml(voice.label)}</span>
                ${voice.description ? `<span class="voice-description">${escapeHtml(voice.description)}</span>` : ''}
            </div>
            <div class="voice-actions">
                <button class="voice-edit-btn" onclick="app.showVoiceEditModal('${voice.voice_id}')">Edit</button>
                <button class="voice-delete-btn" onclick="app.deleteVoice('${voice.voice_id}')">Delete</button>
            </div>
        </div>
    `).join('');

    if (elements.voiceManageGroup) {
        elements.voiceManageGroup.style.display = 'block';
    }
}

/**
 * Speak a message using TTS
 * Handles both proxied TTS (through backend) and direct local TTS modes
 * @param {string} content - Message content
 * @param {HTMLElement} btn - Speak button element
 * @param {string} messageId - Optional message ID for caching
 */
export async function speakMessage(content, btn, messageId = null) {
    // If currently playing, stop it
    if (state.currentAudio && state.currentSpeakingBtn === btn) {
        stopSpeaking();
        return;
    }

    // Stop any other playing audio
    stopSpeaking();

    // Check cache first (only if same voice)
    const cacheKey = messageId || content;
    const cached = state.audioCache.get(cacheKey);
    if (cached && cached.voiceId === state.selectedVoiceId) {
        playAudioFromCache(cached, btn);
        return;
    }

    // Update button state to loading
    btn.classList.add('loading');
    btn.title = 'Loading...';
    state.currentSpeakingBtn = btn;

    try {
        // Strip markdown for cleaner speech
        const textContent = stripMarkdown(content);

        let audioBlob;

        if (state.localTtsEnabled) {
            // Direct local TTS mode - call local server directly
            const params = state.ttsProvider === 'styletts2' ? getStyleTTS2Params() : {};
            audioBlob = await api.localTextToSpeech(
                state.localTtsUrl,
                textContent,
                state.selectedVoiceId,
                params
            );
        } else {
            // Proxied TTS mode - call through backend
            const styletts2Params = state.ttsProvider === 'styletts2' ? getStyleTTS2Params() : null;
            audioBlob = await api.textToSpeech(textContent, state.selectedVoiceId, styletts2Params);
        }

        const audioUrl = URL.createObjectURL(audioBlob);

        // Cache the audio
        state.audioCache.set(cacheKey, {
            blob: audioBlob,
            url: audioUrl,
            voiceId: state.selectedVoiceId
        });

        // Create and play audio
        state.currentAudio = new Audio(audioUrl);

        // Update button to playing state
        btn.classList.remove('loading');
        btn.classList.add('speaking');
        btn.title = 'Stop';

        // Handle audio end
        state.currentAudio.onended = () => {
            stopSpeaking();
        };

        state.currentAudio.onerror = () => {
            showToast('Failed to play audio', 'error');
            stopSpeaking();
        };

        await state.currentAudio.play();
    } catch (error) {
        console.error('TTS error:', error);
        showToast('Failed to generate speech', 'error');
        stopSpeaking();
    }
}

/**
 * Play audio from cache
 * @param {Object} cached - Cached audio object
 * @param {HTMLElement} btn - Speak button
 */
function playAudioFromCache(cached, btn) {
    state.currentSpeakingBtn = btn;
    state.currentAudio = new Audio(cached.url);

    btn.classList.add('speaking');
    btn.title = 'Stop';

    state.currentAudio.onended = () => {
        stopSpeaking();
    };

    state.currentAudio.onerror = () => {
        showToast('Failed to play audio', 'error');
        stopSpeaking();
    };

    state.currentAudio.play();
}

/**
 * Stop currently playing audio
 */
export function stopSpeaking() {
    if (state.currentAudio) {
        state.currentAudio.pause();
        state.currentAudio = null;
    }
    if (state.currentSpeakingBtn) {
        state.currentSpeakingBtn.classList.remove('loading', 'speaking');
        state.currentSpeakingBtn.title = 'Read aloud';
        state.currentSpeakingBtn = null;
    }
}

/**
 * Get StyleTTS 2 parameters from localStorage
 * @returns {Object} - StyleTTS 2 parameters
 */
function getStyleTTS2Params() {
    const stored = localStorage.getItem('styletts2_params');
    if (stored) {
        try {
            return JSON.parse(stored);
        } catch (e) {
            // Fall through to defaults
        }
    }
    return {
        alpha: 0.3,
        beta: 0.7,
        diffusion_steps: 10,
        embedding_scale: 1.0,
        speed: 1.0,
    };
}

/**
 * Load StyleTTS 2 settings from localStorage
 */
export function loadStyleTTS2Settings() {
    const stored = localStorage.getItem('styletts2_params');
    if (stored) {
        try {
            const params = JSON.parse(stored);
            if (elements.styletts2Alpha) elements.styletts2Alpha.value = params.alpha ?? 0.3;
            if (elements.styletts2Beta) elements.styletts2Beta.value = params.beta ?? 0.7;
            if (elements.styletts2DiffusionSteps) elements.styletts2DiffusionSteps.value = params.diffusion_steps ?? 10;
            if (elements.styletts2EmbeddingScale) elements.styletts2EmbeddingScale.value = params.embedding_scale ?? 1.0;
            if (elements.styletts2Speed) elements.styletts2Speed.value = params.speed ?? 1.0;
        } catch (e) {
            console.warn('Failed to load StyleTTS 2 settings:', e);
        }
    }
}

/**
 * Save StyleTTS 2 settings to localStorage
 */
export function saveStyleTTS2Settings() {
    const params = {
        alpha: parseFloat(elements.styletts2Alpha?.value) || 0.3,
        beta: parseFloat(elements.styletts2Beta?.value) || 0.7,
        diffusion_steps: parseInt(elements.styletts2DiffusionSteps?.value) || 10,
        embedding_scale: parseFloat(elements.styletts2EmbeddingScale?.value) || 1.0,
        speed: parseFloat(elements.styletts2Speed?.value) || 1.0,
    };
    localStorage.setItem('styletts2_params', JSON.stringify(params));
    clearAudioCache();
}

// =========================================================================
// Voice Cloning
// =========================================================================

/**
 * Show voice clone modal
 */
export function showVoiceCloneModal() {
    // Reset form
    if (elements.voiceCloneFile) elements.voiceCloneFile.value = '';
    if (elements.voiceCloneName) elements.voiceCloneName.value = '';
    if (elements.voiceCloneDescription) elements.voiceCloneDescription.value = '';
    if (elements.voiceCloneTemperature) elements.voiceCloneTemperature.value = '0.75';
    if (elements.voiceCloneSpeed) elements.voiceCloneSpeed.value = '1.0';
    if (elements.voiceCloneLengthPenalty) elements.voiceCloneLengthPenalty.value = '1.0';
    if (elements.voiceCloneRepetitionPenalty) elements.voiceCloneRepetitionPenalty.value = '5.0';
    if (elements.voiceCloneStatus) {
        elements.voiceCloneStatus.style.display = 'none';
        elements.voiceCloneStatus.className = 'voice-clone-status';
    }
    if (elements.createVoiceCloneBtn) elements.createVoiceCloneBtn.disabled = true;

    showModal('voiceCloneModal');
}

/**
 * Hide voice clone modal
 */
export function hideVoiceCloneModal() {
    hideModal('voiceCloneModal');
}

/**
 * Update voice clone button state
 */
export function updateVoiceCloneButton() {
    const hasFile = elements.voiceCloneFile?.files.length > 0;
    const hasName = elements.voiceCloneName?.value.trim().length > 0;
    if (elements.createVoiceCloneBtn) {
        elements.createVoiceCloneBtn.disabled = !(hasFile && hasName);
    }
}

/**
 * Create a voice clone
 */
export async function createVoiceClone() {
    const file = elements.voiceCloneFile?.files[0];
    const name = elements.voiceCloneName?.value.trim();
    const description = elements.voiceCloneDescription?.value.trim();

    if (!file || !name) return;

    const options = {
        temperature: parseFloat(elements.voiceCloneTemperature?.value) || 0.75,
        length_penalty: parseFloat(elements.voiceCloneLengthPenalty?.value) || 1.0,
        repetition_penalty: parseFloat(elements.voiceCloneRepetitionPenalty?.value) || 5.0,
        speed: parseFloat(elements.voiceCloneSpeed?.value) || 1.0,
    };

    // Show loading status
    if (elements.voiceCloneStatus) {
        elements.voiceCloneStatus.textContent = 'Creating voice... This may take a moment.';
        elements.voiceCloneStatus.className = 'voice-clone-status loading';
        elements.voiceCloneStatus.style.display = 'block';
    }
    if (elements.createVoiceCloneBtn) elements.createVoiceCloneBtn.disabled = true;

    try {
        const result = await api.cloneVoice(file, name, description, options);

        if (result.success) {
            if (elements.voiceCloneStatus) {
                elements.voiceCloneStatus.textContent = `Voice "${name}" created successfully!`;
                elements.voiceCloneStatus.className = 'voice-clone-status success';
            }

            // Refresh TTS status
            await checkTTSStatus();

            setTimeout(() => {
                hideVoiceCloneModal();
                showToast(`Voice "${name}" created`, 'success');
            }, 1500);
        } else {
            throw new Error(result.message || 'Failed to create voice');
        }
    } catch (error) {
        console.error('Voice cloning failed:', error);
        if (elements.voiceCloneStatus) {
            elements.voiceCloneStatus.textContent = error.message || 'Failed to create voice. Please try again.';
            elements.voiceCloneStatus.className = 'voice-clone-status error';
        }
        if (elements.createVoiceCloneBtn) elements.createVoiceCloneBtn.disabled = false;
    }
}

/**
 * Delete a voice
 * @param {string} voiceId - Voice ID
 */
export async function deleteVoice(voiceId) {
    const voice = state.ttsVoices.find(v => v.voice_id === voiceId);
    const voiceName = voice ? voice.label : 'this voice';

    if (!confirm(`Delete ${voiceName}? This cannot be undone.`)) {
        return;
    }

    try {
        await api.deleteTTSVoice(voiceId);
        showToast(`Voice "${voiceName}" deleted`, 'success');
        await checkTTSStatus();
    } catch (error) {
        console.error('Failed to delete voice:', error);
        showToast('Failed to delete voice', 'error');
    }
}

/**
 * Show voice edit modal
 * @param {string} voiceId - Voice ID
 */
export function showVoiceEditModal(voiceId) {
    const voice = state.ttsVoices.find(v => v.voice_id === voiceId);
    if (!voice) {
        showToast('Voice not found', 'error');
        return;
    }

    // Populate form
    if (elements.voiceEditId) elements.voiceEditId.value = voice.voice_id;
    if (elements.voiceEditName) elements.voiceEditName.value = voice.label || '';
    if (elements.voiceEditDescription) elements.voiceEditDescription.value = voice.description || '';

    // Show/hide provider-specific params
    if (state.ttsProvider === 'styletts2') {
        if (elements.xttsParamsSection) elements.xttsParamsSection.style.display = 'none';
        if (elements.styletts2ParamsSection) elements.styletts2ParamsSection.style.display = 'block';
        if (elements.voiceEditAlpha) elements.voiceEditAlpha.value = voice.alpha ?? 0.3;
        if (elements.voiceEditBeta) elements.voiceEditBeta.value = voice.beta ?? 0.7;
        if (elements.voiceEditDiffusionSteps) elements.voiceEditDiffusionSteps.value = voice.diffusion_steps ?? 10;
        if (elements.voiceEditEmbeddingScale) elements.voiceEditEmbeddingScale.value = voice.embedding_scale ?? 1.0;
    } else {
        if (elements.xttsParamsSection) elements.xttsParamsSection.style.display = 'block';
        if (elements.styletts2ParamsSection) elements.styletts2ParamsSection.style.display = 'none';
        if (elements.voiceEditTemperature) elements.voiceEditTemperature.value = voice.temperature ?? 0.75;
        if (elements.voiceEditSpeed) elements.voiceEditSpeed.value = voice.speed ?? 1.0;
        if (elements.voiceEditLengthPenalty) elements.voiceEditLengthPenalty.value = voice.length_penalty ?? 1.0;
        if (elements.voiceEditRepetitionPenalty) elements.voiceEditRepetitionPenalty.value = voice.repetition_penalty ?? 5.0;
    }

    // Reset status
    if (elements.voiceEditStatus) {
        elements.voiceEditStatus.style.display = 'none';
        elements.voiceEditStatus.className = 'voice-clone-status';
    }
    if (elements.saveVoiceEditBtn) elements.saveVoiceEditBtn.disabled = false;

    showModal('voiceEditModal');
}

/**
 * Hide voice edit modal
 */
export function hideVoiceEditModal() {
    hideModal('voiceEditModal');
}

/**
 * Save voice edit
 */
export async function saveVoiceEdit() {
    const voiceId = elements.voiceEditId?.value;
    if (!voiceId) return;

    const updates = {
        label: elements.voiceEditName?.value.trim() || null,
        description: elements.voiceEditDescription?.value.trim() || null,
    };

    // Add provider-specific parameters
    if (state.ttsProvider === 'styletts2') {
        updates.alpha = parseFloat(elements.voiceEditAlpha?.value) || null;
        updates.beta = parseFloat(elements.voiceEditBeta?.value) || null;
        updates.diffusion_steps = parseInt(elements.voiceEditDiffusionSteps?.value) || null;
        updates.embedding_scale = parseFloat(elements.voiceEditEmbeddingScale?.value) || null;
    } else {
        updates.temperature = parseFloat(elements.voiceEditTemperature?.value) || null;
        updates.speed = parseFloat(elements.voiceEditSpeed?.value) || null;
        updates.length_penalty = parseFloat(elements.voiceEditLengthPenalty?.value) || null;
        updates.repetition_penalty = parseFloat(elements.voiceEditRepetitionPenalty?.value) || null;
    }

    // Show loading
    if (elements.voiceEditStatus) {
        elements.voiceEditStatus.textContent = 'Saving changes...';
        elements.voiceEditStatus.className = 'voice-clone-status loading';
        elements.voiceEditStatus.style.display = 'block';
    }
    if (elements.saveVoiceEditBtn) elements.saveVoiceEditBtn.disabled = true;

    try {
        const result = await api.updateVoice(voiceId, updates);

        if (result.success) {
            if (elements.voiceEditStatus) {
                elements.voiceEditStatus.textContent = 'Voice updated successfully!';
                elements.voiceEditStatus.className = 'voice-clone-status success';
            }

            await checkTTSStatus();

            setTimeout(() => {
                hideVoiceEditModal();
                showToast('Voice settings updated', 'success');
            }, 1000);
        } else {
            throw new Error(result.message || 'Failed to update voice');
        }
    } catch (error) {
        console.error('Failed to update voice:', error);
        if (elements.voiceEditStatus) {
            elements.voiceEditStatus.textContent = error.message || 'Failed to update voice. Please try again.';
            elements.voiceEditStatus.className = 'voice-clone-status error';
        }
        if (elements.saveVoiceEditBtn) elements.saveVoiceEditBtn.disabled = false;
    }
}

// =========================================================================
// STT (Speech-to-Text)
// =========================================================================

/**
 * Check STT status
 * Handles both proxied STT (through backend) and direct local STT modes
 */
export async function checkSTTStatus() {
    try {
        const status = await api.getSTTStatus();

        // Check if local STT direct mode is enabled by the server
        if (status.local_stt_enabled) {
            // Load any user-configured URL overrides from localStorage
            loadLocalSttSettingsFromStorage();

            // Use server-provided URL if no localStorage override
            if (!localStorage.getItem('local_stt_settings')) {
                state.localSttUrl = status.local_stt_url;
            }

            state.localSttEnabled = true;

            // Check health of the local STT server directly
            const health = await api.checkLocalSttHealth(state.localSttUrl);
            state.localSttServerHealthy = health.healthy;

            if (health.healthy) {
                state.dictationMode = 'whisper';  // Use whisper mode for local
                initWhisperDictation();
                console.log(`Local STT (Whisper) enabled at ${state.localSttUrl}`);

                // Show voice button
                if (elements.voiceBtn) {
                    elements.voiceBtn.style.display = 'flex';
                }
            } else {
                console.warn('Local STT server not healthy:', health.error);
                // Fall back to browser dictation if available
                state.dictationMode = 'browser';
                initBrowserDictation();
                if (elements.voiceBtn) {
                    elements.voiceBtn.style.display = 'flex';
                }
            }
        } else if (status.enabled) {
            // Standard proxied STT mode (through backend)
            state.localSttEnabled = false;
            state.dictationMode = status.effective_mode;

            if (status.effective_mode === 'whisper') {
                initWhisperDictation();
            } else if (status.effective_mode === 'browser') {
                initBrowserDictation();
            }

            // Show voice button
            if (elements.voiceBtn) {
                elements.voiceBtn.style.display = 'flex';
            }
        } else {
            state.dictationMode = 'none';
            state.localSttEnabled = false;
            if (elements.voiceBtn) {
                elements.voiceBtn.style.display = 'none';
            }
        }
    } catch (error) {
        console.warn('STT not available:', error);
        state.dictationMode = 'none';
        state.localSttEnabled = false;
    }
}

/**
 * Initialize Whisper dictation
 */
function initWhisperDictation() {
    // Whisper mode uses MediaRecorder
    state.audioChunks = [];
}

/**
 * Initialize browser dictation
 */
function initBrowserDictation() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

    if (!SpeechRecognition) {
        console.warn('Browser SpeechRecognition not supported');
        state.dictationMode = 'none';
        if (elements.voiceBtn) {
            elements.voiceBtn.style.display = 'none';
        }
        return;
    }

    state.speechRecognition = new SpeechRecognition();
    state.speechRecognition.continuous = true;
    state.speechRecognition.interimResults = true;
    state.speechRecognition.lang = 'en-US';

    state.speechRecognition.onresult = (event) => {
        let transcript = '';
        for (let i = 0; i < event.results.length; i++) {
            if (event.results[i].isFinal) {
                transcript += event.results[i][0].transcript;
            }
        }
        if (transcript && elements.messageInput) {
            elements.messageInput.value += transcript;
            elements.messageInput.dispatchEvent(new Event('input'));
        }
    };

    state.speechRecognition.onerror = (event) => {
        console.error('Speech recognition error:', event.error);
        if (event.error === 'not-allowed') {
            showToast('Microphone access denied', 'error');
        }
        state.isRecording = false;
        updateRecordingUI(false);
    };

    state.speechRecognition.onend = () => {
        if (state.isRecording) {
            state.speechRecognition.start();
        }
    };
}

/**
 * Toggle voice dictation
 */
export function toggleVoiceDictation() {
    if (state.dictationMode === 'whisper') {
        toggleWhisperDictation();
    } else if (state.dictationMode === 'browser') {
        toggleBrowserDictation();
    }
}

/**
 * Toggle browser dictation
 */
function toggleBrowserDictation() {
    if (state.isRecording) {
        state.isRecording = false;
        state.speechRecognition?.stop();
        updateRecordingUI(false);
    } else {
        state.isRecording = true;
        state.speechRecognition?.start();
        updateRecordingUI(true);
    }
}

/**
 * Toggle Whisper dictation
 */
async function toggleWhisperDictation() {
    if (state.isRecording) {
        stopWhisperRecording();
    } else {
        await startWhisperRecording();
    }
}

/**
 * Start Whisper recording
 */
async function startWhisperRecording() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

        state.mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
        state.audioChunks = [];

        state.mediaRecorder.ondataavailable = (event) => {
            if (event.data.size > 0) {
                state.audioChunks.push(event.data);
            }
        };

        state.mediaRecorder.onstop = async () => {
            stream.getTracks().forEach(track => track.stop());
            await processWhisperRecording();
        };

        state.mediaRecorder.start();
        state.isRecording = true;
        updateRecordingUI(true);

    } catch (error) {
        console.error('Failed to start recording:', error);
        if (error.name === 'NotAllowedError') {
            showToast('Microphone access denied', 'error');
        } else {
            showToast('Failed to start recording', 'error');
        }
    }
}

/**
 * Stop Whisper recording
 */
function stopWhisperRecording() {
    if (state.mediaRecorder && state.mediaRecorder.state === 'recording') {
        state.mediaRecorder.stop();
        state.isRecording = false;
        updateRecordingUI(false);
    }
}

/**
 * Process Whisper recording
 * Handles both proxied STT (through backend) and direct local STT modes
 */
async function processWhisperRecording() {
    if (state.audioChunks.length === 0) {
        resetWhisperUI();
        return;
    }

    const audioBlob = new Blob(state.audioChunks, { type: 'audio/webm' });
    state.audioChunks = [];

    // Show processing state
    if (elements.voiceBtn) {
        elements.voiceBtn.classList.add('processing');
        elements.voiceBtn.title = 'Processing...';
    }

    try {
        let result;

        if (state.localSttEnabled) {
            // Direct local STT mode - call local server directly
            result = await api.localTranscribeAudio(state.localSttUrl, audioBlob);
        } else {
            // Proxied STT mode - call through backend
            result = await api.transcribeAudio(audioBlob);
        }

        if (result.text && elements.messageInput) {
            elements.messageInput.value += result.text;
            elements.messageInput.dispatchEvent(new Event('input'));
        }
    } catch (error) {
        console.error('Transcription failed:', error);
        showToast('Failed to transcribe audio', 'error');
    } finally {
        resetWhisperUI();
    }
}

/**
 * Reset Whisper UI state
 */
function resetWhisperUI() {
    if (elements.voiceBtn) {
        elements.voiceBtn.classList.remove('recording', 'processing');
        elements.voiceBtn.title = 'Voice dictation';
    }
}

/**
 * Update recording UI
 * @param {boolean} isRecording - Whether recording is active
 */
function updateRecordingUI(isRecording) {
    if (elements.voiceBtn) {
        if (isRecording) {
            elements.voiceBtn.classList.add('recording');
            elements.voiceBtn.title = 'Stop recording';
        } else {
            elements.voiceBtn.classList.remove('recording');
            elements.voiceBtn.title = 'Voice dictation';
        }
    }
}
