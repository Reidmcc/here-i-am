/**
 * Tests for Modal component
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/svelte';
import Modal from './Modal.svelte';

describe('Modal', () => {
  afterEach(() => {
    cleanup();
    // Reset body overflow style
    document.body.style.overflow = '';
  });

  it('should render with default props', () => {
    render(Modal);
    const dialog = screen.getByRole('dialog');
    expect(dialog).toBeInTheDocument();
    expect(dialog).toHaveAttribute('aria-modal', 'true');
  });

  it('should render title when provided', () => {
    render(Modal, { props: { title: 'Test Modal' } });
    expect(screen.getByText('Test Modal')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Test Modal' })).toBeInTheDocument();
  });

  it('should render close button by default', () => {
    render(Modal, { props: { title: 'Test' } });
    expect(screen.getByLabelText('Close modal')).toBeInTheDocument();
  });

  it('should hide close button when showClose is false', () => {
    render(Modal, { props: { title: 'Test', showClose: false } });
    expect(screen.queryByLabelText('Close modal')).not.toBeInTheDocument();
  });

  it('should apply size classes', () => {
    const { container, rerender } = render(Modal, { props: { size: 'small' } });
    expect(container.querySelector('.small')).toBeInTheDocument();

    rerender({ size: 'medium' });
    expect(container.querySelector('.medium')).toBeInTheDocument();

    rerender({ size: 'large' });
    expect(container.querySelector('.large')).toBeInTheDocument();

    rerender({ size: 'xlarge' });
    expect(container.querySelector('.xlarge')).toBeInTheDocument();
  });

  it('should dispatch close event when close button clicked', async () => {
    const handleClose = vi.fn();
    // In Svelte 5, use events option
    render(Modal, {
      props: { title: 'Test' },
      events: { close: handleClose },
    });

    const closeBtn = screen.getByLabelText('Close modal');
    await fireEvent.click(closeBtn);

    expect(handleClose).toHaveBeenCalledTimes(1);
  });

  it('should dispatch close event when backdrop clicked', async () => {
    const handleClose = vi.fn();
    render(Modal, {
      props: {},
      events: { close: handleClose },
    });

    const backdrop = screen.getByRole('dialog');
    await fireEvent.click(backdrop);

    expect(handleClose).toHaveBeenCalledTimes(1);
  });

  it('should not close when clicking inside modal container', async () => {
    const handleClose = vi.fn();
    const { container } = render(Modal, {
      props: { title: 'Test' },
      events: { close: handleClose },
    });

    const modalContainer = container.querySelector('.modal-container');
    await fireEvent.click(modalContainer);

    expect(handleClose).not.toHaveBeenCalled();
  });

  it('should dispatch close event on Escape key', async () => {
    const handleClose = vi.fn();
    render(Modal, {
      props: {},
      events: { close: handleClose },
    });

    await fireEvent.keyDown(document, { key: 'Escape' });

    expect(handleClose).toHaveBeenCalledTimes(1);
  });

  it('should not close on other key presses', async () => {
    const handleClose = vi.fn();
    render(Modal, {
      props: {},
      events: { close: handleClose },
    });

    await fireEvent.keyDown(document, { key: 'Enter' });
    await fireEvent.keyDown(document, { key: 'Tab' });

    expect(handleClose).not.toHaveBeenCalled();
  });

  it('should set body overflow to hidden on mount', () => {
    render(Modal);
    expect(document.body.style.overflow).toBe('hidden');
  });

  it('should reset body overflow on unmount', () => {
    const { unmount } = render(Modal);
    expect(document.body.style.overflow).toBe('hidden');

    unmount();
    expect(document.body.style.overflow).toBe('');
  });

  it('should have proper aria attributes', () => {
    render(Modal, { props: { title: 'Accessible Modal' } });
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(dialog).toHaveAttribute('aria-labelledby', 'modal-title');
  });
});
