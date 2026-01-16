/**
 * Unit Tests for Utils Module
 * Tests utility functions used across the application
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
    escapeHtml,
    truncateText,
    renderMarkdown,
    stripMarkdown,
    readFileAsText,
    readFileAsBase64,
    setToastContainer,
    showToast,
    showLoading,
} from '../modules/utils.js';

describe('Utils Module', () => {
    describe('escapeHtml', () => {
        it('should escape < and > characters', () => {
            expect(escapeHtml('<script>alert("xss")</script>')).toBe(
                '&lt;script&gt;alert("xss")&lt;/script&gt;'
            );
        });

        it('should escape & character', () => {
            expect(escapeHtml('Tom & Jerry')).toBe('Tom &amp; Jerry');
        });

        it('should escape " character', () => {
            expect(escapeHtml('He said "hello"')).toBe('He said "hello"');
        });

        it('should handle empty string', () => {
            expect(escapeHtml('')).toBe('');
        });

        it('should handle string with no special characters', () => {
            expect(escapeHtml('Hello World')).toBe('Hello World');
        });

        it('should handle multiple special characters', () => {
            expect(escapeHtml('<div class="test">&nbsp;</div>')).toBe(
                '&lt;div class="test"&gt;&amp;nbsp;&lt;/div&gt;'
            );
        });
    });

    describe('truncateText', () => {
        it('should truncate text longer than maxLength', () => {
            expect(truncateText('Hello World!', 5)).toBe('Hello...');
        });

        it('should not truncate text shorter than maxLength', () => {
            expect(truncateText('Hello', 10)).toBe('Hello');
        });

        it('should not truncate text equal to maxLength', () => {
            expect(truncateText('Hello', 5)).toBe('Hello');
        });

        it('should handle null input', () => {
            expect(truncateText(null, 10)).toBe(null);
        });

        it('should handle undefined input', () => {
            expect(truncateText(undefined, 10)).toBe(undefined);
        });

        it('should handle empty string', () => {
            expect(truncateText('', 10)).toBe('');
        });

        it('should truncate at specified boundary', () => {
            expect(truncateText('abcdefghij', 7)).toBe('abcdefg...');
        });
    });

    describe('renderMarkdown', () => {
        it('should handle empty input', () => {
            expect(renderMarkdown('')).toBe('');
            expect(renderMarkdown(null)).toBe('');
            expect(renderMarkdown(undefined)).toBe('');
        });

        describe('code blocks', () => {
            it('should render fenced code blocks with language', () => {
                const input = '```javascript\nconst x = 1;\n```';
                const result = renderMarkdown(input);
                expect(result).toContain('<pre class="md-code-block"');
                expect(result).toContain('data-language="javascript"');
                expect(result).toContain('const x = 1;');
            });

            it('should render fenced code blocks without language', () => {
                const input = '```\nsome code\n```';
                const result = renderMarkdown(input);
                expect(result).toContain('<pre class="md-code-block"');
                expect(result).toContain('some code');
            });

            it('should render inline code', () => {
                const result = renderMarkdown('Use `const` for constants');
                expect(result).toContain('<code class="md-inline-code">const</code>');
            });
        });

        describe('emphasis', () => {
            it('should render bold with double asterisks', () => {
                const result = renderMarkdown('This is **bold** text');
                expect(result).toContain('<strong>bold</strong>');
            });

            it('should render bold with double underscores', () => {
                const result = renderMarkdown('This is __bold__ text');
                expect(result).toContain('<strong>bold</strong>');
            });

            it('should render italic with single asterisks', () => {
                const result = renderMarkdown('This is *italic* text');
                expect(result).toContain('<em>italic</em>');
            });

            it('should render italic with underscores at word boundaries', () => {
                const result = renderMarkdown('This is _italic_ text');
                expect(result).toContain('<em>italic</em>');
            });
        });

        describe('links', () => {
            it('should render links', () => {
                const result = renderMarkdown('[Click here](https://example.com)');
                expect(result).toContain('<a href="https://example.com"');
                expect(result).toContain('target="_blank"');
                expect(result).toContain('rel="noopener noreferrer"');
                expect(result).toContain('>Click here</a>');
            });
        });

        describe('headers', () => {
            it('should render h2 headers', () => {
                const result = renderMarkdown('# Heading 1');
                expect(result).toContain('<h2 class="md-header">Heading 1</h2>');
            });

            it('should render h3 headers', () => {
                const result = renderMarkdown('## Heading 2');
                expect(result).toContain('<h3 class="md-header">Heading 2</h3>');
            });

            it('should render h4 headers', () => {
                const result = renderMarkdown('### Heading 3');
                expect(result).toContain('<h4 class="md-header">Heading 3</h4>');
            });
        });

        describe('lists', () => {
            it('should render unordered lists', () => {
                const result = renderMarkdown('- Item 1\n- Item 2');
                expect(result).toContain('<ul class="md-list">');
                expect(result).toContain('<li class="md-list-item">Item 1</li>');
                expect(result).toContain('<li class="md-list-item">Item 2</li>');
            });

            it('should render ordered lists', () => {
                const result = renderMarkdown('1. First\n2. Second');
                expect(result).toContain('<ol class="md-list">');
                expect(result).toContain('<li class="md-list-item-ordered">First</li>');
                expect(result).toContain('<li class="md-list-item-ordered">Second</li>');
            });
        });

        describe('blockquotes', () => {
            it('should render blockquotes', () => {
                const result = renderMarkdown('> This is a quote');
                expect(result).toContain('<blockquote class="md-blockquote">This is a quote</blockquote>');
            });
        });

        describe('horizontal rules', () => {
            it('should render horizontal rules with dashes', () => {
                const result = renderMarkdown('---');
                expect(result).toContain('<hr class="md-hr">');
            });

            it('should render horizontal rules with asterisks', () => {
                const result = renderMarkdown('***');
                expect(result).toContain('<hr class="md-hr">');
            });
        });

        describe('XSS prevention', () => {
            it('should escape HTML in input', () => {
                const result = renderMarkdown('<script>alert("xss")</script>');
                expect(result).not.toContain('<script>');
                expect(result).toContain('&lt;script&gt;');
            });
        });
    });

    describe('stripMarkdown', () => {
        it('should handle empty input', () => {
            expect(stripMarkdown('')).toBe('');
            expect(stripMarkdown(null)).toBe('');
            expect(stripMarkdown(undefined)).toBe('');
        });

        it('should remove code blocks', () => {
            const input = 'Before ```code block``` After';
            expect(stripMarkdown(input)).toBe('Before  After');
        });

        it('should remove inline code', () => {
            const result = stripMarkdown('Use `const` for constants');
            expect(result).toBe('Use  for constants');
        });

        it('should remove headers', () => {
            const result = stripMarkdown('# Heading\nParagraph');
            expect(result).toBe('Heading\nParagraph');
        });

        it('should remove bold formatting but keep text', () => {
            const result = stripMarkdown('This is **bold** text');
            expect(result).toBe('This is bold text');
        });

        it('should remove italic formatting but keep text', () => {
            const result = stripMarkdown('This is *italic* text');
            expect(result).toBe('This is italic text');
        });

        it('should remove links but keep text', () => {
            const result = stripMarkdown('[Click here](https://example.com)');
            expect(result).toBe('Click here');
        });

        it('should remove blockquote markers', () => {
            const result = stripMarkdown('> This is a quote');
            expect(result).toBe('This is a quote');
        });

        it('should remove list markers', () => {
            const result = stripMarkdown('- Item 1\n- Item 2');
            expect(result).toBe('Item 1\nItem 2');
        });

        it('should remove ordered list markers', () => {
            const result = stripMarkdown('1. First\n2. Second');
            expect(result).toBe('First\nSecond');
        });

        it('should remove horizontal rules', () => {
            const result = stripMarkdown('Before\n---\nAfter');
            expect(result).toBe('Before\n\nAfter');
        });

        it('should clean up extra whitespace', () => {
            const result = stripMarkdown('Line 1\n\n\n\nLine 2');
            expect(result).toBe('Line 1\n\nLine 2');
        });
    });

    describe('readFileAsText', () => {
        it('should read file content as text', async () => {
            const mockFile = new File(['test content'], 'test.txt', { type: 'text/plain' });
            mockFile._mockContent = 'test content';

            const result = await readFileAsText(mockFile);
            expect(result).toBe('test content');
        });

        it('should reject on read error', async () => {
            const mockFile = new File([''], 'test.txt');
            mockFile._mockError = true;

            await expect(readFileAsText(mockFile)).rejects.toThrow('Failed to read file');
        });
    });

    describe('readFileAsBase64', () => {
        it('should read file content as base64', async () => {
            const mockFile = new File(['test'], 'test.png', { type: 'image/png' });
            mockFile._mockBase64 = 'dGVzdA==';

            const result = await readFileAsBase64(mockFile);
            expect(result).toBe('dGVzdA==');
        });

        it('should reject on read error', async () => {
            const mockFile = new File([''], 'test.png');
            mockFile._mockError = true;

            await expect(readFileAsBase64(mockFile)).rejects.toThrow('Failed to read file');
        });
    });

    describe('showToast', () => {
        let toastContainer;

        beforeEach(() => {
            toastContainer = document.createElement('div');
            toastContainer.id = 'toast-container';
            document.body.appendChild(toastContainer);
            setToastContainer(toastContainer);
        });

        it('should create and append toast element', () => {
            showToast('Test message', 'info');

            const toast = toastContainer.querySelector('.toast');
            expect(toast).toBeTruthy();
            expect(toast.textContent).toBe('Test message');
            expect(toast.classList.contains('info')).toBe(true);
        });

        it('should support different toast types', () => {
            showToast('Success', 'success');
            const successToast = toastContainer.querySelector('.toast.success');
            expect(successToast).toBeTruthy();

            showToast('Warning', 'warning');
            const warningToast = toastContainer.querySelector('.toast.warning');
            expect(warningToast).toBeTruthy();

            showToast('Error', 'error');
            const errorToast = toastContainer.querySelector('.toast.error');
            expect(errorToast).toBeTruthy();
        });

        it('should default to info type', () => {
            showToast('Default type');

            const toast = toastContainer.querySelector('.toast.info');
            expect(toast).toBeTruthy();
        });

        it('should auto-remove toast after timeout', async () => {
            vi.useFakeTimers();

            showToast('Temporary message');
            expect(toastContainer.querySelector('.toast')).toBeTruthy();

            vi.advanceTimersByTime(3000);

            expect(toastContainer.querySelector('.toast')).toBeFalsy();

            vi.useRealTimers();
        });

        it('should handle missing container gracefully', () => {
            setToastContainer(null);
            document.body.innerHTML = '';

            expect(() => showToast('Test')).not.toThrow();
        });
    });

    describe('showLoading', () => {
        let overlay;

        beforeEach(() => {
            overlay = document.createElement('div');
            overlay.id = 'loading-overlay';
        });

        it('should add active class when show is true', () => {
            showLoading(overlay, true);
            expect(overlay.classList.contains('active')).toBe(true);
        });

        it('should remove active class when show is false', () => {
            overlay.classList.add('active');
            showLoading(overlay, false);
            expect(overlay.classList.contains('active')).toBe(false);
        });

        it('should handle toggling', () => {
            showLoading(overlay, true);
            expect(overlay.classList.contains('active')).toBe(true);

            showLoading(overlay, false);
            expect(overlay.classList.contains('active')).toBe(false);
        });
    });
});
