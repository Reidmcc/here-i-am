/**
 * App Store - General application state
 */
import { writable, derived } from 'svelte/store';

// Theme management
function createThemeStore() {
    const stored = typeof localStorage !== 'undefined'
        ? localStorage.getItem('theme') || 'system'
        : 'system';

    const { subscribe, set, update } = writable(stored);

    return {
        subscribe,
        set: (value) => {
            if (typeof localStorage !== 'undefined') {
                localStorage.setItem('theme', value);
            }
            set(value);
            applyTheme(value);
        },
        toggle: () => {
            update(current => {
                const next = current === 'dark' ? 'light' : 'dark';
                if (typeof localStorage !== 'undefined') {
                    localStorage.setItem('theme', next);
                }
                applyTheme(next);
                return next;
            });
        }
    };
}

function applyTheme(theme) {
    if (typeof document === 'undefined') return;

    const root = document.documentElement;
    root.classList.remove('theme-dark', 'theme-light');

    if (theme === 'system') {
        // Let CSS handle it via media query
        return;
    }

    root.classList.add(`theme-${theme}`);
}

export const theme = createThemeStore();

// Loading state
export const isLoading = writable(false);

// Active modal tracking
export const activeModal = writable(null);

// Toast notifications
function createToastStore() {
    const { subscribe, update } = writable([]);

    return {
        subscribe,
        add: (message, type = 'info', duration = 3000) => {
            const id = Date.now();
            update(toasts => [...toasts, { id, message, type }]);

            if (duration > 0) {
                setTimeout(() => {
                    update(toasts => toasts.filter(t => t.id !== id));
                }, duration);
            }

            return id;
        },
        remove: (id) => {
            update(toasts => toasts.filter(t => t.id !== id));
        },
        clear: () => {
            update(() => []);
        }
    };
}

export const toasts = createToastStore();

// Convenience functions for toasts
export function showToast(message, type = 'info', duration = 3000) {
    return toasts.add(message, type, duration);
}

// Abort controller for streaming operations
export const streamAbortController = writable(null);

// Create new abort controller
export function createAbortController() {
    const controller = new AbortController();
    streamAbortController.set(controller);
    return controller;
}

// Abort current stream
export function abortStream() {
    streamAbortController.update(controller => {
        if (controller) {
            controller.abort();
        }
        return null;
    });
}

// Available models from backend
export const availableModels = writable([]);

// GitHub repos and rate limits
export const githubRepos = writable([]);
export const githubRateLimits = writable({});
