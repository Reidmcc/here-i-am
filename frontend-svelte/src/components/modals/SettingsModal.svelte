<script>
    import { createEventDispatcher, onMount } from 'svelte';
    import Modal from '../common/Modal.svelte';
    import { settings, presets, loadPresets, applyPreset, updateSettings } from '../../lib/stores/settings.js';
    import { theme } from '../../lib/stores/app.js';
    import { availableModels } from '../../lib/stores/app.js';
    import { selectedEntityId, entities, entitySystemPrompts } from '../../lib/stores/entities.js';
    import { ttsEnabled, sttEnabled, voices, selectedVoiceId, loadVoices, ttsProvider, styletts2Params, updateStyleTTS2Params } from '../../lib/stores/voice.js';
    import { githubRepos, githubRateLimits } from '../../lib/stores/app.js';
    import * as api from '../../lib/api.js';
    import { showToast } from '../../lib/stores/app.js';

    const dispatch = createEventDispatcher();

    let activeTab = 'general';
    let localSystemPrompt = '';
    let entityPrompt = '';
    let loadingRateLimits = false;

    $: currentEntity = $entities.find(e => e.index_name === $selectedEntityId);

    $: {
        // Load entity-specific system prompt when entity changes
        if ($selectedEntityId) {
            entityPrompt = entitySystemPrompts.getForEntity($selectedEntityId);
        }
    }

    onMount(async () => {
        localSystemPrompt = $settings.systemPrompt || '';
        await loadPresets();

        if ($ttsEnabled) {
            await loadVoices();
        }

        // Load GitHub rate limits
        await loadRateLimits();
    });

    async function loadRateLimits() {
        loadingRateLimits = true;
        try {
            const limits = await api.getGitHubRateLimits();
            githubRateLimits.set(limits);
        } catch (error) {
            console.error('Failed to load rate limits:', error);
        } finally {
            loadingRateLimits = false;
        }
    }

    function close() {
        dispatch('close');
    }

    function handlePresetChange(event) {
        const presetId = event.target.value;
        if (presetId) {
            applyPreset(presetId);
            localSystemPrompt = $settings.systemPrompt || '';
        }
    }

    function handleModelChange(event) {
        updateSettings({ model: event.target.value });
    }

    function handleTemperatureChange(event) {
        updateSettings({ temperature: parseFloat(event.target.value) });
    }

    function handleMaxTokensChange(event) {
        updateSettings({ maxTokens: parseInt(event.target.value) });
    }

    function handleSystemPromptChange(event) {
        localSystemPrompt = event.target.value;
        updateSettings({ systemPrompt: localSystemPrompt });
    }

    function handleEntityPromptChange(event) {
        entityPrompt = event.target.value;
        if ($selectedEntityId) {
            entitySystemPrompts.setForEntity($selectedEntityId, entityPrompt);
        }
    }

    function handleThemeChange(event) {
        theme.set(event.target.value);
    }

    function handleVoiceChange(event) {
        selectedVoiceId.set(event.target.value);
    }

    function handleStyleTTS2ParamChange(param, value) {
        updateStyleTTS2Params({ [param]: value });
    }

    function formatRateLimitTime(resetTime) {
        if (!resetTime) return 'N/A';
        const date = new Date(resetTime * 1000);
        return date.toLocaleTimeString();
    }

    function getRateLimitPercent(used, limit) {
        if (!limit) return 0;
        return Math.round((used / limit) * 100);
    }
</script>

<Modal title="Settings" size="large" on:close={close}>
    <div class="settings-layout">
        <div class="settings-tabs">
            <button
                class="tab-btn"
                class:active={activeTab === 'general'}
                on:click={() => activeTab = 'general'}
            >
                General
            </button>
            <button
                class="tab-btn"
                class:active={activeTab === 'model'}
                on:click={() => activeTab = 'model'}
            >
                Model
            </button>
            <button
                class="tab-btn"
                class:active={activeTab === 'voice'}
                on:click={() => activeTab = 'voice'}
            >
                Voice
            </button>
            {#if $githubRepos.length > 0}
                <button
                    class="tab-btn"
                    class:active={activeTab === 'github'}
                    on:click={() => activeTab = 'github'}
                >
                    GitHub
                </button>
            {/if}
        </div>

        <div class="settings-content">
            {#if activeTab === 'general'}
                <div class="settings-section">
                    <h3>Appearance</h3>
                    <div class="setting-row">
                        <label for="theme-select">Theme</label>
                        <select id="theme-select" value={$theme} on:change={handleThemeChange}>
                            <option value="system">System</option>
                            <option value="dark">Dark</option>
                            <option value="light">Light</option>
                        </select>
                    </div>
                </div>

                <div class="settings-section">
                    <h3>Presets</h3>
                    <div class="setting-row">
                        <label for="preset-select">Configuration Preset</label>
                        <select id="preset-select" on:change={handlePresetChange}>
                            <option value="">Select a preset...</option>
                            {#each Object.entries($presets) as [id, preset]}
                                <option value={id}>{preset.name}</option>
                            {/each}
                        </select>
                    </div>
                </div>
            {/if}

            {#if activeTab === 'model'}
                <div class="settings-section">
                    <h3>Model Settings</h3>

                    <div class="setting-row">
                        <label for="model-select">Model</label>
                        <select id="model-select" value={$settings.model} on:change={handleModelChange}>
                            {#each $availableModels as model}
                                <option value={model.id}>{model.name}</option>
                            {/each}
                        </select>
                    </div>

                    <div class="setting-row">
                        <label for="temperature">
                            Temperature: {$settings.temperature.toFixed(2)}
                        </label>
                        <input
                            type="range"
                            id="temperature"
                            min="0"
                            max="2"
                            step="0.1"
                            value={$settings.temperature}
                            on:input={handleTemperatureChange}
                        />
                    </div>

                    <div class="setting-row">
                        <label for="max-tokens">Max Tokens</label>
                        <input
                            type="number"
                            id="max-tokens"
                            min="1"
                            max="200000"
                            value={$settings.maxTokens}
                            on:input={handleMaxTokensChange}
                        />
                    </div>
                </div>

                <div class="settings-section">
                    <h3>System Prompt (Global)</h3>
                    <p class="section-description">
                        Applied to all conversations. Leave empty for research-style conversations without role assignment.
                    </p>
                    <textarea
                        class="prompt-textarea"
                        value={localSystemPrompt}
                        on:input={handleSystemPromptChange}
                        placeholder="No system prompt (research mode)"
                        rows="4"
                    ></textarea>
                </div>

                {#if currentEntity}
                    <div class="settings-section">
                        <h3>Entity Prompt ({currentEntity.label})</h3>
                        <p class="section-description">
                            Additional prompt prepended when chatting with this entity. Useful for entity-specific context.
                        </p>
                        <textarea
                            class="prompt-textarea"
                            value={entityPrompt}
                            on:input={handleEntityPromptChange}
                            placeholder="No entity-specific prompt"
                            rows="3"
                        ></textarea>
                    </div>
                {/if}
            {/if}

            {#if activeTab === 'voice'}
                <div class="settings-section">
                    <h3>Text-to-Speech</h3>
                    {#if $ttsEnabled}
                        <div class="status-badge enabled">
                            <span class="status-dot"></span>
                            Enabled ({$ttsProvider})
                        </div>

                        <div class="setting-row">
                            <label for="voice-select">Voice</label>
                            <select id="voice-select" value={$selectedVoiceId || ''} on:change={handleVoiceChange}>
                                <option value="">Default</option>
                                {#each $voices as voice}
                                    <option value={voice.voice_id || voice.id}>
                                        {voice.label || voice.name}
                                    </option>
                                {/each}
                            </select>
                        </div>

                        {#if $ttsProvider === 'styletts2'}
                            <div class="setting-row">
                                <label for="alpha">
                                    Timbre Diversity (α): {$styletts2Params.alpha.toFixed(2)}
                                </label>
                                <input
                                    type="range"
                                    id="alpha"
                                    min="0"
                                    max="1"
                                    step="0.05"
                                    value={$styletts2Params.alpha}
                                    on:input={(e) => handleStyleTTS2ParamChange('alpha', parseFloat(e.target.value))}
                                />
                            </div>

                            <div class="setting-row">
                                <label for="beta">
                                    Prosody Diversity (β): {$styletts2Params.beta.toFixed(2)}
                                </label>
                                <input
                                    type="range"
                                    id="beta"
                                    min="0"
                                    max="1"
                                    step="0.05"
                                    value={$styletts2Params.beta}
                                    on:input={(e) => handleStyleTTS2ParamChange('beta', parseFloat(e.target.value))}
                                />
                            </div>

                            <div class="setting-row">
                                <label for="diffusion-steps">
                                    Diffusion Steps: {$styletts2Params.diffusionSteps}
                                </label>
                                <input
                                    type="range"
                                    id="diffusion-steps"
                                    min="1"
                                    max="50"
                                    step="1"
                                    value={$styletts2Params.diffusionSteps}
                                    on:input={(e) => handleStyleTTS2ParamChange('diffusionSteps', parseInt(e.target.value))}
                                />
                            </div>
                        {/if}
                    {:else}
                        <div class="status-badge disabled">
                            <span class="status-dot"></span>
                            Disabled
                        </div>
                        <p class="section-description">
                            TTS is not configured. Set up ElevenLabs, XTTS, or StyleTTS 2 in the backend.
                        </p>
                    {/if}
                </div>

                <div class="settings-section">
                    <h3>Speech-to-Text</h3>
                    {#if $sttEnabled}
                        <div class="status-badge enabled">
                            <span class="status-dot"></span>
                            Enabled
                        </div>
                        <p class="section-description">
                            Click the microphone button in the input area to start voice input.
                        </p>
                    {:else}
                        <div class="status-badge disabled">
                            <span class="status-dot"></span>
                            Disabled
                        </div>
                        <p class="section-description">
                            STT is not configured. Set up Whisper server or use browser Web Speech API.
                        </p>
                    {/if}
                </div>
            {/if}

            {#if activeTab === 'github'}
                <div class="settings-section">
                    <h3>Configured Repositories</h3>
                    {#each $githubRepos as repo}
                        <div class="github-repo">
                            <div class="repo-header">
                                <span class="repo-name">{repo.label}</span>
                                <span class="repo-path">{repo.owner}/{repo.repo}</span>
                            </div>
                            <div class="repo-capabilities">
                                {#each repo.capabilities || [] as cap}
                                    <span class="capability-badge">{cap}</span>
                                {/each}
                            </div>
                        </div>
                    {/each}
                </div>

                <div class="settings-section">
                    <h3>Rate Limits</h3>
                    {#if loadingRateLimits}
                        <p class="section-description">Loading rate limits...</p>
                    {:else if Object.keys($githubRateLimits).length > 0}
                        {#each Object.entries($githubRateLimits) as [repoKey, limits]}
                            <div class="rate-limit-section">
                                <h4>{repoKey}</h4>
                                {#if limits.core}
                                    <div class="rate-limit-row">
                                        <span class="rate-label">Core API</span>
                                        <div class="rate-bar-container">
                                            <div
                                                class="rate-bar"
                                                style="width: {getRateLimitPercent(limits.core.used, limits.core.limit)}%"
                                            ></div>
                                        </div>
                                        <span class="rate-value">
                                            {limits.core.remaining}/{limits.core.limit}
                                        </span>
                                    </div>
                                {/if}
                            </div>
                        {/each}
                    {:else}
                        <p class="section-description">No rate limit data available.</p>
                    {/if}
                </div>
            {/if}
        </div>
    </div>

    <svelte:fragment slot="footer">
        <button class="btn btn-secondary" on:click={close}>Close</button>
    </svelte:fragment>
</Modal>

<style>
    .settings-layout {
        display: flex;
        gap: 24px;
        min-height: 400px;
    }

    .settings-tabs {
        display: flex;
        flex-direction: column;
        gap: 4px;
        min-width: 120px;
        border-right: 1px solid var(--border-color);
        padding-right: 24px;
    }

    .tab-btn {
        padding: 10px 16px;
        background: transparent;
        border: none;
        border-radius: 6px;
        cursor: pointer;
        text-align: left;
        color: var(--text-secondary);
        font-size: 0.95rem;
        transition: all 0.2s;
    }

    .tab-btn:hover {
        background-color: var(--bg-tertiary);
        color: var(--text-primary);
    }

    .tab-btn.active {
        background-color: var(--accent-subtle);
        color: var(--accent);
        font-weight: 500;
    }

    .settings-content {
        flex: 1;
        overflow-y: auto;
    }

    .settings-section {
        margin-bottom: 32px;
    }

    .settings-section h3 {
        margin: 0 0 16px 0;
        font-size: 1rem;
        font-weight: 600;
        color: var(--text-primary);
    }

    .section-description {
        font-size: 0.85rem;
        color: var(--text-muted);
        margin-bottom: 12px;
    }

    .setting-row {
        margin-bottom: 16px;
    }

    .setting-row label {
        display: block;
        margin-bottom: 6px;
        font-size: 0.9rem;
        color: var(--text-secondary);
    }

    .setting-row select,
    .setting-row input[type="number"] {
        width: 100%;
        padding: 10px 12px;
        background-color: var(--bg-tertiary);
        border: 1px solid var(--border-color);
        border-radius: 6px;
        color: var(--text-primary);
        font-size: 0.95rem;
    }

    .setting-row input[type="range"] {
        width: 100%;
    }

    .prompt-textarea {
        width: 100%;
        padding: 12px;
        background-color: var(--bg-tertiary);
        border: 1px solid var(--border-color);
        border-radius: 6px;
        color: var(--text-primary);
        font-size: 0.9rem;
        font-family: var(--font-mono);
        resize: vertical;
    }

    .prompt-textarea:focus {
        outline: none;
        border-color: var(--accent);
    }

    .status-badge {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 6px 12px;
        border-radius: 20px;
        font-size: 0.85rem;
        margin-bottom: 12px;
    }

    .status-badge.enabled {
        background-color: var(--success-subtle);
        color: var(--success);
    }

    .status-badge.disabled {
        background-color: var(--bg-tertiary);
        color: var(--text-muted);
    }

    .status-dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background-color: currentColor;
    }

    .github-repo {
        padding: 12px;
        background-color: var(--bg-tertiary);
        border: 1px solid var(--border-color);
        border-radius: 8px;
        margin-bottom: 12px;
    }

    .repo-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 8px;
    }

    .repo-name {
        font-weight: 600;
        color: var(--text-primary);
    }

    .repo-path {
        font-size: 0.85rem;
        color: var(--text-muted);
        font-family: var(--font-mono);
    }

    .repo-capabilities {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
    }

    .capability-badge {
        padding: 2px 8px;
        background-color: var(--bg-secondary);
        border-radius: 4px;
        font-size: 0.75rem;
        color: var(--text-secondary);
    }

    .rate-limit-section {
        margin-bottom: 16px;
    }

    .rate-limit-section h4 {
        margin: 0 0 8px 0;
        font-size: 0.9rem;
        color: var(--text-secondary);
    }

    .rate-limit-row {
        display: flex;
        align-items: center;
        gap: 12px;
    }

    .rate-label {
        font-size: 0.85rem;
        color: var(--text-muted);
        width: 80px;
    }

    .rate-bar-container {
        flex: 1;
        height: 8px;
        background-color: var(--bg-tertiary);
        border-radius: 4px;
        overflow: hidden;
    }

    .rate-bar {
        height: 100%;
        background-color: var(--accent);
        transition: width 0.3s;
    }

    .rate-value {
        font-size: 0.8rem;
        color: var(--text-secondary);
        font-family: var(--font-mono);
        width: 80px;
        text-align: right;
    }

    .btn {
        padding: 10px 20px;
        border-radius: 6px;
        font-size: 0.95rem;
        cursor: pointer;
        transition: all 0.2s;
    }

    .btn-secondary {
        background-color: var(--bg-tertiary);
        color: var(--text-primary);
        border: 1px solid var(--border-color);
    }

    .btn-secondary:hover {
        background-color: var(--bg-primary);
    }
</style>
