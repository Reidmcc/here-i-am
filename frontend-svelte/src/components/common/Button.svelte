<script>
    export let variant = 'primary'; // primary, secondary, danger
    export let size = 'normal'; // small, normal
    export let disabled = false;
    export let type = 'button';
</script>

<button
    {type}
    {disabled}
    class="{variant}-btn {size === 'small' ? 'small' : ''}"
    on:click
>
    <slot />
</button>

<style>
    .primary-btn {
        padding: 10px 20px;
        background-color: var(--accent);
        color: white;
        border: none;
        border-radius: 6px;
        cursor: pointer;
        font-size: 0.9rem;
        font-weight: 500;
        transition: background-color 0.2s;
    }

    .primary-btn:hover:not(:disabled) {
        background-color: var(--accent-hover);
    }

    .primary-btn:disabled {
        opacity: 0.5;
        cursor: not-allowed;
    }

    .secondary-btn {
        padding: 10px 20px;
        background-color: var(--bg-tertiary);
        color: var(--text-secondary);
        border: 1px solid var(--border-color);
        border-radius: 6px;
        cursor: pointer;
        font-size: 0.9rem;
        transition: all 0.2s;
    }

    .secondary-btn:hover:not(:disabled) {
        background-color: var(--bg-primary);
        color: var(--text-primary);
    }

    .secondary-btn:disabled {
        opacity: 0.5;
        cursor: not-allowed;
    }

    .danger-btn {
        padding: 10px 20px;
        background-color: var(--danger);
        color: white;
        border: none;
        border-radius: 6px;
        cursor: pointer;
        font-size: 0.9rem;
        font-weight: 500;
        transition: background-color 0.2s;
    }

    .danger-btn:hover:not(:disabled) {
        background-color: var(--danger-hover);
    }

    .danger-btn:disabled {
        opacity: 0.5;
        cursor: not-allowed;
    }

    .small {
        padding: 6px 12px;
        font-size: 0.8rem;
    }
</style>
