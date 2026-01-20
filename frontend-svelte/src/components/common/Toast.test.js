/**
 * Tests for Toast component
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import { toasts } from '../../lib/stores/app.js';
import Toast from './Toast.svelte';

describe('Toast', () => {
  beforeEach(() => {
    toasts.clear();
  });

  afterEach(() => {
    cleanup();
    toasts.clear();
  });

  it('should render toast container', () => {
    const { container } = render(Toast);
    expect(container.querySelector('.toast-container')).toBeInTheDocument();
  });

  it('should render nothing when no toasts', () => {
    const { container } = render(Toast);
    expect(container.querySelectorAll('.toast')).toHaveLength(0);
  });

  it('should render toast when added to store', async () => {
    render(Toast);
    toasts.add('Test message', 'info', 0);

    // Wait for Svelte to update
    await new Promise(resolve => setTimeout(resolve, 10));

    expect(screen.getByText('Test message')).toBeInTheDocument();
  });

  it('should apply success type class', async () => {
    const { container } = render(Toast);
    toasts.add('Success message', 'success', 0);
    await new Promise(resolve => setTimeout(resolve, 10));
    expect(container.querySelector('.toast.success')).toBeInTheDocument();
  });

  it('should apply error type class', async () => {
    const { container } = render(Toast);
    toasts.add('Error message', 'error', 0);
    await new Promise(resolve => setTimeout(resolve, 10));
    expect(container.querySelector('.toast.error')).toBeInTheDocument();
  });

  it('should apply warning type class', async () => {
    const { container } = render(Toast);
    toasts.add('Warning message', 'warning', 0);
    await new Promise(resolve => setTimeout(resolve, 10));
    expect(container.querySelector('.toast.warning')).toBeInTheDocument();
  });

  it('should apply info type class', async () => {
    const { container } = render(Toast);
    toasts.add('Info message', 'info', 0);
    await new Promise(resolve => setTimeout(resolve, 10));
    expect(container.querySelector('.toast.info')).toBeInTheDocument();
  });

  it('should remove toast when removed from store', async () => {
    render(Toast);

    const id = toasts.add('Test message', 'info', 0);
    await new Promise(resolve => setTimeout(resolve, 10));
    expect(screen.getByText('Test message')).toBeInTheDocument();

    toasts.remove(id);
    await new Promise(resolve => setTimeout(resolve, 10));
    expect(screen.queryByText('Test message')).not.toBeInTheDocument();
  });
});
