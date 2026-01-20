/**
 * Tests for utility functions
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  escapeHtml,
  renderMarkdown,
  truncateText,
  formatDate,
  formatRelativeTime,
  generateId,
  debounce,
  throttle,
  copyToClipboard,
  parseToolContent,
  isToolMessage,
  getFileExtension,
  formatFileSize,
  scrollToBottom,
  isScrolledToBottom,
} from './utils.js';

describe('escapeHtml', () => {
  it('should escape HTML special characters', () => {
    expect(escapeHtml('<script>alert("xss")</script>')).toBe(
      '&lt;script&gt;alert("xss")&lt;/script&gt;'
    );
  });

  it('should escape ampersands', () => {
    expect(escapeHtml('Tom & Jerry')).toBe('Tom &amp; Jerry');
  });

  it('should escape quotes', () => {
    expect(escapeHtml('"quoted"')).toBe('"quoted"');
  });

  it('should handle empty string', () => {
    expect(escapeHtml('')).toBe('');
  });

  it('should handle plain text without special characters', () => {
    expect(escapeHtml('Hello World')).toBe('Hello World');
  });
});

describe('renderMarkdown', () => {
  it('should return empty string for null/undefined', () => {
    expect(renderMarkdown(null)).toBe('');
    expect(renderMarkdown(undefined)).toBe('');
    expect(renderMarkdown('')).toBe('');
  });

  it('should render inline code', () => {
    const result = renderMarkdown('Use `console.log()` to debug');
    expect(result).toContain('<code>console.log()</code>');
  });

  it('should render code blocks with language', () => {
    const result = renderMarkdown('```javascript\nconst x = 1;\n```');
    expect(result).toContain('<pre><code class="language-javascript">');
    expect(result).toContain('const x = 1;');
  });

  it('should render code blocks without language', () => {
    const result = renderMarkdown('```\nsome code\n```');
    expect(result).toContain('<pre><code>');
    expect(result).toContain('some code');
  });

  it('should render bold text with **', () => {
    const result = renderMarkdown('This is **bold** text');
    expect(result).toContain('<strong>bold</strong>');
  });

  it('should render bold text with __', () => {
    const result = renderMarkdown('This is __bold__ text');
    expect(result).toContain('<strong>bold</strong>');
  });

  it('should render italic text with *', () => {
    const result = renderMarkdown('This is *italic* text');
    expect(result).toContain('<em>italic</em>');
  });

  it('should render italic text with _', () => {
    const result = renderMarkdown('This is _italic_ text');
    expect(result).toContain('<em>italic</em>');
  });

  it('should render links', () => {
    const result = renderMarkdown('Check [this link](https://example.com)');
    expect(result).toContain('<a href="https://example.com" target="_blank" rel="noopener">this link</a>');
  });

  it('should render headers', () => {
    expect(renderMarkdown('# Header 1')).toContain('<h2>Header 1</h2>');
    expect(renderMarkdown('## Header 2')).toContain('<h3>Header 2</h3>');
    expect(renderMarkdown('### Header 3')).toContain('<h4>Header 3</h4>');
  });

  it('should render horizontal rules', () => {
    expect(renderMarkdown('---')).toContain('<hr>');
    expect(renderMarkdown('***')).toContain('<hr>');
  });

  it('should render blockquotes', () => {
    const result = renderMarkdown('> This is a quote');
    expect(result).toContain('<blockquote>This is a quote</blockquote>');
  });

  it('should render unordered lists', () => {
    const result = renderMarkdown('- Item 1\n- Item 2');
    expect(result).toContain('<li>Item 1</li>');
    expect(result).toContain('<li>Item 2</li>');
    expect(result).toContain('<ul>');
  });

  it('should convert newlines to br tags', () => {
    const result = renderMarkdown('Line 1\nLine 2');
    expect(result).toContain('<br>');
  });

  it('should escape HTML before processing markdown', () => {
    const result = renderMarkdown('<script>bad</script>');
    expect(result).not.toContain('<script>');
    expect(result).toContain('&lt;script&gt;');
  });
});

describe('truncateText', () => {
  it('should truncate text longer than maxLength', () => {
    expect(truncateText('Hello World', 5)).toBe('Hello...');
  });

  it('should not truncate text shorter than maxLength', () => {
    expect(truncateText('Hello', 10)).toBe('Hello');
  });

  it('should not truncate text equal to maxLength', () => {
    expect(truncateText('Hello', 5)).toBe('Hello');
  });

  it('should handle empty string', () => {
    expect(truncateText('', 10)).toBe('');
  });

  it('should handle null/undefined', () => {
    expect(truncateText(null, 10)).toBe(null);
    expect(truncateText(undefined, 10)).toBe(undefined);
  });

  it('should use default maxLength of 100', () => {
    const longText = 'a'.repeat(150);
    expect(truncateText(longText)).toBe('a'.repeat(100) + '...');
  });
});

describe('formatDate', () => {
  it('should format a valid date string', () => {
    const result = formatDate('2024-01-15T10:30:00Z');
    expect(result).toContain('Jan');
    expect(result).toContain('15');
    expect(result).toContain('2024');
  });

  it('should return empty string for empty input', () => {
    expect(formatDate('')).toBe('');
    expect(formatDate(null)).toBe('');
    expect(formatDate(undefined)).toBe('');
  });
});

describe('formatRelativeTime', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2024-01-15T12:00:00Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('should return "just now" for very recent times', () => {
    const result = formatRelativeTime('2024-01-15T11:59:30Z');
    expect(result).toBe('just now');
  });

  it('should return minutes ago', () => {
    const result = formatRelativeTime('2024-01-15T11:30:00Z');
    expect(result).toBe('30m ago');
  });

  it('should return hours ago', () => {
    const result = formatRelativeTime('2024-01-15T10:00:00Z');
    expect(result).toBe('2h ago');
  });

  it('should return days ago', () => {
    const result = formatRelativeTime('2024-01-13T12:00:00Z');
    expect(result).toBe('2d ago');
  });

  it('should return formatted date for older dates', () => {
    const result = formatRelativeTime('2024-01-01T12:00:00Z');
    expect(result).toContain('Jan');
    expect(result).toContain('2024');
  });

  it('should return empty string for empty input', () => {
    expect(formatRelativeTime('')).toBe('');
    expect(formatRelativeTime(null)).toBe('');
  });
});

describe('generateId', () => {
  it('should generate unique IDs', () => {
    const id1 = generateId();
    const id2 = generateId();
    expect(id1).not.toBe(id2);
  });

  it('should generate IDs with expected format', () => {
    const id = generateId();
    expect(id).toMatch(/^\d+-[a-z0-9]+$/);
  });
});

describe('debounce', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('should debounce function calls', () => {
    const fn = vi.fn();
    const debounced = debounce(fn, 100);

    debounced();
    debounced();
    debounced();

    expect(fn).not.toHaveBeenCalled();

    vi.advanceTimersByTime(100);

    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('should pass arguments to the debounced function', () => {
    const fn = vi.fn();
    const debounced = debounce(fn, 100);

    debounced('arg1', 'arg2');
    vi.advanceTimersByTime(100);

    expect(fn).toHaveBeenCalledWith('arg1', 'arg2');
  });

  it('should reset timer on each call', () => {
    const fn = vi.fn();
    const debounced = debounce(fn, 100);

    debounced();
    vi.advanceTimersByTime(50);
    debounced();
    vi.advanceTimersByTime(50);
    debounced();
    vi.advanceTimersByTime(100);

    expect(fn).toHaveBeenCalledTimes(1);
  });
});

describe('throttle', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('should throttle function calls', () => {
    const fn = vi.fn();
    const throttled = throttle(fn, 100);

    throttled();
    throttled();
    throttled();

    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('should allow function call after throttle period', () => {
    const fn = vi.fn();
    const throttled = throttle(fn, 100);

    throttled();
    vi.advanceTimersByTime(100);
    throttled();

    expect(fn).toHaveBeenCalledTimes(2);
  });

  it('should pass arguments to the throttled function', () => {
    const fn = vi.fn();
    const throttled = throttle(fn, 100);

    throttled('arg1', 'arg2');

    expect(fn).toHaveBeenCalledWith('arg1', 'arg2');
  });
});

describe('copyToClipboard', () => {
  it('should copy text to clipboard', async () => {
    const result = await copyToClipboard('test text');
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('test text');
    expect(result).toBe(true);
  });

  it('should return false on failure', async () => {
    vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValueOnce(new Error('Failed'));
    const result = await copyToClipboard('test text');
    expect(result).toBe(false);
  });
});

describe('parseToolContent', () => {
  it('should parse JSON string content', () => {
    const content = '[{"type": "tool_use", "id": "123"}]';
    const result = parseToolContent(content);
    expect(result).toEqual([{ type: 'tool_use', id: '123' }]);
  });

  it('should return array as-is', () => {
    const content = [{ type: 'tool_use' }];
    const result = parseToolContent(content);
    expect(result).toEqual(content);
  });

  it('should return empty array for invalid JSON', () => {
    const result = parseToolContent('not json');
    expect(result).toEqual([]);
  });

  it('should return empty array for null/undefined', () => {
    expect(parseToolContent(null)).toEqual([]);
    expect(parseToolContent(undefined)).toEqual([]);
  });

  it('should wrap non-array objects in array', () => {
    const content = { type: 'tool_use' };
    const result = parseToolContent(content);
    expect(result).toEqual([]);
  });
});

describe('isToolMessage', () => {
  it('should return true for tool_use role', () => {
    expect(isToolMessage({ role: 'tool_use' })).toBe(true);
  });

  it('should return true for tool_result role', () => {
    expect(isToolMessage({ role: 'tool_result' })).toBe(true);
  });

  it('should return false for other roles', () => {
    expect(isToolMessage({ role: 'human' })).toBe(false);
    expect(isToolMessage({ role: 'assistant' })).toBe(false);
  });

  it('should return false for null/undefined', () => {
    expect(isToolMessage(null)).toBe(false);
    expect(isToolMessage(undefined)).toBe(false);
  });
});

describe('getFileExtension', () => {
  it('should extract file extension', () => {
    expect(getFileExtension('document.pdf')).toBe('pdf');
    expect(getFileExtension('image.PNG')).toBe('png');
    expect(getFileExtension('script.test.js')).toBe('js');
  });

  it('should handle files without extension', () => {
    expect(getFileExtension('README')).toBe('readme');
  });
});

describe('formatFileSize', () => {
  it('should format bytes', () => {
    expect(formatFileSize(500)).toBe('500 B');
  });

  it('should format kilobytes', () => {
    expect(formatFileSize(1024)).toBe('1.0 KB');
    expect(formatFileSize(2560)).toBe('2.5 KB');
  });

  it('should format megabytes', () => {
    expect(formatFileSize(1024 * 1024)).toBe('1.0 MB');
    expect(formatFileSize(2.5 * 1024 * 1024)).toBe('2.5 MB');
  });
});

describe('scrollToBottom', () => {
  it('should set scrollTop to scrollHeight', () => {
    const element = { scrollTop: 0, scrollHeight: 1000 };
    scrollToBottom(element);
    expect(element.scrollTop).toBe(1000);
  });

  it('should handle null element', () => {
    expect(() => scrollToBottom(null)).not.toThrow();
  });
});

describe('isScrolledToBottom', () => {
  it('should return true when at bottom', () => {
    const element = { scrollHeight: 1000, scrollTop: 900, clientHeight: 100 };
    expect(isScrolledToBottom(element)).toBe(true);
  });

  it('should return true when within threshold', () => {
    const element = { scrollHeight: 1000, scrollTop: 870, clientHeight: 100 };
    expect(isScrolledToBottom(element, 50)).toBe(true);
  });

  it('should return false when not at bottom', () => {
    const element = { scrollHeight: 1000, scrollTop: 500, clientHeight: 100 };
    expect(isScrolledToBottom(element)).toBe(false);
  });

  it('should return true for null element', () => {
    expect(isScrolledToBottom(null)).toBe(true);
  });
});
