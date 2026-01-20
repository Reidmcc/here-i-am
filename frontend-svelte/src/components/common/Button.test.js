/**
 * Tests for Button component
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/svelte';
import Button from './Button.svelte';

describe('Button', () => {
  it('should render with default props', () => {
    render(Button);
    const button = screen.getByRole('button');
    expect(button).toBeInTheDocument();
    expect(button).toHaveAttribute('type', 'button');
  });

  it('should apply primary variant class by default', () => {
    render(Button);
    const button = screen.getByRole('button');
    expect(button).toHaveClass('primary-btn');
  });

  it('should apply secondary variant class', () => {
    render(Button, { props: { variant: 'secondary' } });
    const button = screen.getByRole('button');
    expect(button).toHaveClass('secondary-btn');
  });

  it('should apply danger variant class', () => {
    render(Button, { props: { variant: 'danger' } });
    const button = screen.getByRole('button');
    expect(button).toHaveClass('danger-btn');
  });

  it('should apply small size class', () => {
    render(Button, { props: { size: 'small' } });
    const button = screen.getByRole('button');
    expect(button).toHaveClass('small');
  });

  it('should not apply small class for normal size', () => {
    render(Button, { props: { size: 'normal' } });
    const button = screen.getByRole('button');
    expect(button).not.toHaveClass('small');
  });

  it('should be disabled when disabled prop is true', () => {
    render(Button, { props: { disabled: true } });
    const button = screen.getByRole('button');
    expect(button).toBeDisabled();
  });

  it('should set button type', () => {
    render(Button, { props: { type: 'submit' } });
    const button = screen.getByRole('button');
    expect(button).toHaveAttribute('type', 'submit');
  });

  it('should emit click event', async () => {
    const handleClick = vi.fn();
    // In Svelte 5, use the events option instead of $on
    render(Button, {
      props: {},
      events: { click: handleClick },
    });

    const button = screen.getByRole('button');
    await fireEvent.click(button);

    expect(handleClick).toHaveBeenCalledTimes(1);
  });

  it('should have disabled attribute when disabled', () => {
    render(Button, { props: { disabled: true } });
    const button = screen.getByRole('button');

    // Verify the button has the disabled attribute
    expect(button).toHaveAttribute('disabled');
  });
});
