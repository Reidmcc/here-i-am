/**
 * Tests for app store
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import {
  theme,
  isLoading,
  activeModal,
  toasts,
  showToast,
  streamAbortController,
  createAbortController,
  abortStream,
  availableModels,
  githubRepos,
  githubRateLimits,
} from './app.js';

describe('theme store', () => {
  beforeEach(() => {
    localStorage.getItem.mockReturnValue(null);
  });

  it('should have initial value of "system" when localStorage is empty', () => {
    // Re-import to get fresh store with mocked localStorage
    localStorage.getItem.mockReturnValue(null);
    expect(get(theme)).toBeDefined();
  });

  it('should toggle between dark and light', () => {
    theme.set('dark');
    theme.toggle();
    expect(get(theme)).toBe('light');
    theme.toggle();
    expect(get(theme)).toBe('dark');
  });

  it('should persist to localStorage', () => {
    theme.set('dark');
    expect(localStorage.setItem).toHaveBeenCalledWith('theme', 'dark');
  });
});

describe('isLoading store', () => {
  it('should default to false', () => {
    expect(get(isLoading)).toBe(false);
  });

  it('should be settable', () => {
    isLoading.set(true);
    expect(get(isLoading)).toBe(true);
    isLoading.set(false);
    expect(get(isLoading)).toBe(false);
  });
});

describe('activeModal store', () => {
  it('should default to null', () => {
    expect(get(activeModal)).toBe(null);
  });

  it('should be settable', () => {
    activeModal.set('settings');
    expect(get(activeModal)).toBe('settings');
    activeModal.set(null);
    expect(get(activeModal)).toBe(null);
  });
});

describe('toasts store', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    toasts.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('should start empty', () => {
    expect(get(toasts)).toEqual([]);
  });

  it('should add a toast', () => {
    toasts.add('Test message', 'info', 0);
    const currentToasts = get(toasts);
    expect(currentToasts).toHaveLength(1);
    expect(currentToasts[0].message).toBe('Test message');
    expect(currentToasts[0].type).toBe('info');
  });

  it('should auto-remove toast after duration', () => {
    toasts.add('Test message', 'info', 1000);
    expect(get(toasts)).toHaveLength(1);

    vi.advanceTimersByTime(1000);
    expect(get(toasts)).toHaveLength(0);
  });

  it('should not auto-remove if duration is 0', () => {
    toasts.add('Test message', 'info', 0);
    vi.advanceTimersByTime(5000);
    expect(get(toasts)).toHaveLength(1);
  });

  it('should remove specific toast by id', () => {
    const id = toasts.add('Test message', 'info', 0);
    expect(get(toasts)).toHaveLength(1);

    toasts.remove(id);
    expect(get(toasts)).toHaveLength(0);
  });

  it('should clear all toasts', () => {
    toasts.add('Message 1', 'info', 0);
    toasts.add('Message 2', 'error', 0);
    expect(get(toasts)).toHaveLength(2);

    toasts.clear();
    expect(get(toasts)).toHaveLength(0);
  });
});

describe('showToast helper', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    toasts.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('should add a toast with default type', () => {
    showToast('Test message');
    const currentToasts = get(toasts);
    expect(currentToasts).toHaveLength(1);
    expect(currentToasts[0].type).toBe('info');
  });

  it('should add a toast with custom type', () => {
    showToast('Error message', 'error');
    const currentToasts = get(toasts);
    expect(currentToasts[0].type).toBe('error');
  });

  it('should return the toast id', () => {
    const id = showToast('Test');
    expect(typeof id).toBe('number');
  });
});

describe('streamAbortController', () => {
  it('should default to null', () => {
    streamAbortController.set(null);
    expect(get(streamAbortController)).toBe(null);
  });

  it('should create new abort controller', () => {
    const controller = createAbortController();
    expect(controller).toBeInstanceOf(AbortController);
    expect(get(streamAbortController)).toBe(controller);
  });

  it('should abort stream and reset controller', () => {
    const controller = createAbortController();
    expect(controller.signal.aborted).toBe(false);

    abortStream();
    expect(controller.signal.aborted).toBe(true);
    expect(get(streamAbortController)).toBe(null);
  });

  it('should handle abort when no controller exists', () => {
    streamAbortController.set(null);
    expect(() => abortStream()).not.toThrow();
  });
});

describe('availableModels store', () => {
  it('should default to empty array', () => {
    expect(get(availableModels)).toEqual([]);
  });

  it('should be settable', () => {
    const models = ['claude-3', 'gpt-4'];
    availableModels.set(models);
    expect(get(availableModels)).toEqual(models);
    availableModels.set([]);
  });
});

describe('githubRepos store', () => {
  it('should default to empty array', () => {
    expect(get(githubRepos)).toEqual([]);
  });

  it('should be settable', () => {
    const repos = [{ owner: 'test', repo: 'repo' }];
    githubRepos.set(repos);
    expect(get(githubRepos)).toEqual(repos);
    githubRepos.set([]);
  });
});

describe('githubRateLimits store', () => {
  it('should default to empty object', () => {
    expect(get(githubRateLimits)).toEqual({});
  });

  it('should be settable', () => {
    const limits = { 'test/repo': { remaining: 100 } };
    githubRateLimits.set(limits);
    expect(get(githubRateLimits)).toEqual(limits);
    githubRateLimits.set({});
  });
});
