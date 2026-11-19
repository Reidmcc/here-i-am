/**
 * Tests for logging in and out (issue #396): the login module, and the API
 * client's handling of a 401 from any request.
 *
 * The real ApiClient and the real module run; only fetch is mocked.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

import '../api.js';
import {
    setElements,
    setCallbacks,
    showLogin,
    isLoginShown,
    submitLogin,
    ensureLoggedIn,
    logOut,
    loginFailureMessage,
} from '../modules/auth.js';

function response(status, body = {}, headers = {}) {
    return {
        status,
        ok: status >= 200 && status < 300,
        headers: { get: (name) => headers[name] ?? null },
        json: () => Promise.resolve(body),
        blob: () => Promise.resolve(new Blob()),
    };
}

describe('Login', () => {
    let api;
    let elements;
    let reload;

    beforeEach(() => {
        api = window.api;
        api.setUnauthorizedHandler(null);
        global.fetch = vi.fn();

        const overlay = document.createElement('div');
        overlay.hidden = true;
        const submit = document.createElement('button');
        submit.textContent = 'Log in';
        elements = {
            overlay,
            message: document.createElement('p'),
            password: document.createElement('input'),
            error: document.createElement('p'),
            submit,
        };
        elements.error.hidden = true;
        setElements(elements);
        reload = vi.fn();
        setCallbacks({ reload, isRunning: () => false, onResumed: () => {} });
    });

    describe('a 401 from the backend', () => {
        it('calls the handler and throws an error marked unauthorized', async () => {
            const handler = vi.fn();
            api.setUnauthorizedHandler(handler);
            global.fetch.mockResolvedValue(response(401, { detail: 'Authentication required.' }));

            const failure = await api.listEntities().catch((e) => e);
            expect(failure.unauthorized).toBe(true);
            expect(failure.message).toBe('Authentication required.');
            expect(handler).toHaveBeenCalledWith('Authentication required.');
        });

        it('is caught on the streams and on text-to-speech too', async () => {
            const handler = vi.fn();
            api.setUnauthorizedHandler(handler);
            global.fetch.mockResolvedValue(response(401, { detail: 'no' }));

            const calls = [
                () => api.sendMessageStream({ message: 'hi' }),
                () => api.regenerateStream({ message_id: 'm' }),
                () => api.importExternalConversationsStream({}),
                () => api.textToSpeech('hello'),
            ];
            for (const call of calls) {
                const failure = await call().catch((e) => e);
                expect(failure.unauthorized).toBe(true);
            }
            expect(handler).toHaveBeenCalledTimes(calls.length);
        });

        it('other error statuses are not treated as logged out', async () => {
            const handler = vi.fn();
            api.setUnauthorizedHandler(handler);
            for (const status of [400, 403, 404, 500]) {
                global.fetch.mockResolvedValue(response(status, { detail: 'nope' }));
                const failure = await api.listEntities().catch((e) => e);
                expect(failure.unauthorized).toBeUndefined();
            }
            expect(handler).not.toHaveBeenCalled();
        });

        it("never goes to this browser's local voice servers", async () => {
            // Those are another origin and take no credentials; their 401
            // would mean nothing about this app's login
            const handler = vi.fn();
            api.setUnauthorizedHandler(handler);
            global.fetch.mockResolvedValue(response(401, { detail: 'no' }));
            await api.localVoiceHealth('http://localhost:8021').catch(() => {});
            expect(handler).not.toHaveBeenCalled();
        });
    });

    describe('api.login', () => {
        it('posts the password and nothing else', async () => {
            global.fetch.mockResolvedValue(response(200, { scope: 'researcher' }));
            await api.login('a long enough password');
            expect(global.fetch).toHaveBeenCalledWith('/api/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: '{"password":"a long enough password"}',
            });
        });

        it('a wrong password is an error with its status, not a logged-out event', async () => {
            const handler = vi.fn();
            api.setUnauthorizedHandler(handler);
            global.fetch.mockResolvedValue(response(401, { detail: 'Wrong password.' }));
            const failure = await api.login('nope').catch((e) => e);
            expect(failure.status).toBe(401);
            expect(failure.unauthorized).toBeUndefined();
            expect(handler).not.toHaveBeenCalled();
        });

        it('carries the wait when throttled', async () => {
            global.fetch.mockResolvedValue(
                response(429, { detail: 'Too many wrong passwords.' }, { 'Retry-After': '8' })
            );
            const failure = await api.login('nope').catch((e) => e);
            expect(failure.status).toBe(429);
            expect(failure.retryAfter).toBe(8);
        });
    });

    describe('showLogin', () => {
        it('shows the form with a message and an empty password', () => {
            elements.password.value = 'left over';
            showLogin('Your login has ended.');
            expect(isLoginShown()).toBe(true);
            expect(elements.message.textContent).toBe('Your login has ended.');
            expect(elements.password.value).toBe('');
            expect(elements.error.hidden).toBe(true);
        });

        it('does not reset a form that is already showing', () => {
            // A page that loads logged out gets a 401 from every request
            showLogin();
            elements.password.value = 'half typed';
            elements.error.textContent = 'Wrong password.';
            elements.error.hidden = false;
            showLogin('again');
            expect(elements.password.value).toBe('half typed');
            expect(elements.error.textContent).toBe('Wrong password.');
            expect(elements.message.textContent).toBe('Log in to continue.');
        });
    });

    describe('submitLogin', () => {
        beforeEach(() => showLogin());

        it('logs in and starts the page over', async () => {
            global.fetch.mockResolvedValue(response(200, { scope: 'researcher' }));
            elements.password.value = 'a long enough password';
            const event = { preventDefault: vi.fn() };
            await submitLogin(event);
            expect(event.preventDefault).toHaveBeenCalled();
            expect(reload).toHaveBeenCalledTimes(1);
            // The page doesn't keep the password
            expect(elements.password.value).toBe('');
        });

        it('on a page that was already running, lifts the form and reloads nothing', async () => {
            // The login ended while the page was in use: a reload would
            // throw away whatever is on it, a typed message included
            const onResumed = vi.fn();
            setCallbacks({ isRunning: () => true, onResumed });
            global.fetch.mockResolvedValue(response(200, { scope: 'researcher' }));
            elements.password.value = 'a long enough password';
            await submitLogin();
            expect(reload).not.toHaveBeenCalled();
            expect(isLoginShown()).toBe(false);
            expect(onResumed).toHaveBeenCalledTimes(1);
            expect(elements.submit.disabled).toBe(false);
            // And the form works again the next time the login ends
            showLogin('Your login has ended.');
            expect(isLoginShown()).toBe(true);
        });

        it('says so when the password was right but the browser kept no login', async () => {
            // A cookie the browser dropped: without this the page reloads
            // straight back to an empty form, forever, with no reason given
            global.fetch
                .mockResolvedValueOnce(response(200, { scope: 'researcher' }))
                .mockResolvedValueOnce(response(401, { detail: 'Authentication required.' }));
            elements.password.value = 'a long enough password';
            await submitLogin();
            expect(reload).not.toHaveBeenCalled();
            expect(isLoginShown()).toBe(true);
            expect(elements.error.textContent).toContain('did not keep the login');
            expect(elements.submit.disabled).toBe(false);
        });

        it('checks the login stuck by asking the backend who it is', async () => {
            global.fetch.mockResolvedValue(response(200, { scope: 'researcher' }));
            elements.password.value = 'a long enough password';
            await submitLogin();
            expect(global.fetch.mock.calls.map((call) => call[0])).toEqual([
                '/api/auth/login', '/api/auth/session',
            ]);
        });

        it('a wrong password says so, clears the field, and stays put', async () => {
            global.fetch.mockResolvedValue(response(401, { detail: 'Wrong password.' }));
            elements.password.value = 'not it';
            await submitLogin();
            expect(reload).not.toHaveBeenCalled();
            expect(isLoginShown()).toBe(true);
            expect(elements.error.textContent).toBe('Wrong password.');
            expect(elements.error.hidden).toBe(false);
            expect(elements.password.value).toBe('');
            expect(elements.submit.disabled).toBe(false);
        });

        it('an empty password is not sent', async () => {
            await submitLogin();
            expect(global.fetch).not.toHaveBeenCalled();
            expect(elements.error.textContent).toBe('Enter the password.');
        });

        it('a second submit while one is in flight is ignored', async () => {
            let finish;
            global.fetch.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
            elements.password.value = 'a long enough password';
            const first = submitLogin();
            expect(elements.submit.disabled).toBe(true);
            await submitLogin();
            expect(global.fetch).toHaveBeenCalledTimes(1);
            global.fetch.mockResolvedValue(response(200, {}));
            finish(response(200, {}));
            await first;
            expect(reload).toHaveBeenCalledTimes(1);
        });
    });

    describe('loginFailureMessage', () => {
        it('names each kind of failure', () => {
            expect(loginFailureMessage({ status: 401, message: 'Wrong password.' })).toBe('Wrong password.');
            expect(loginFailureMessage({ status: 429, retryAfter: 8 })).toBe(
                'Too many wrong passwords. Try again in 8 seconds.'
            );
            expect(loginFailureMessage({ status: 429, retryAfter: 1 })).toBe(
                'Too many wrong passwords. Try again in 1 second.'
            );
            expect(loginFailureMessage({ status: 429, retryAfter: null })).toBe(
                'Too many wrong passwords. Try again shortly.'
            );
            expect(loginFailureMessage({ status: 500, message: 'boom' })).toBe('Login failed (boom).');
            expect(loginFailureMessage(new TypeError('Failed to fetch'))).toBe(
                'Could not reach the server. Is it running?'
            );
        });

        it("passes on the server's own instructions when it is not set up", () => {
            const notSet = { status: 503, message: 'No password has been set. Run: python -m app.auth init' };
            expect(loginFailureMessage(notSet)).toBe(notSet.message);
            const proxy = { status: 403, message: 'Set AUTH_ALLOWED_ORIGINS=https://x' };
            expect(loginFailureMessage(proxy)).toBe(proxy.message);
        });
    });

    describe('ensureLoggedIn', () => {
        it('is true when the backend knows the session', async () => {
            global.fetch.mockResolvedValue(response(200, { scope: 'researcher' }));
            expect(await ensureLoggedIn()).toBe(true);
            expect(isLoginShown()).toBe(false);
            expect(global.fetch).toHaveBeenCalledWith('/api/auth/session', expect.anything());
        });

        it('shows the form and is false on a 401', async () => {
            global.fetch.mockResolvedValue(response(401, { detail: 'Authentication required.' }));
            expect(await ensureLoggedIn()).toBe(false);
            expect(isLoginShown()).toBe(true);
        });

        it('a backend that is down is neither logged in nor logged out', async () => {
            global.fetch.mockRejectedValue(new TypeError('Failed to fetch'));
            expect(await ensureLoggedIn()).toBe(null);
            expect(isLoginShown()).toBe(false);
        });
    });

    describe('logOut', () => {
        it('ends this login and starts the page over', async () => {
            global.fetch.mockResolvedValue(response(200, { signed_out: 1 }));
            await logOut();
            expect(global.fetch).toHaveBeenCalledWith(
                '/api/auth/logout', expect.objectContaining({ method: 'POST' })
            );
            expect(reload).toHaveBeenCalledTimes(1);
        });

        it('everywhere calls the other endpoint', async () => {
            global.fetch.mockResolvedValue(response(200, { signed_out: 3 }));
            await logOut(true);
            expect(global.fetch).toHaveBeenCalledWith(
                '/api/auth/logout-all', expect.objectContaining({ method: 'POST' })
            );
            expect(reload).toHaveBeenCalledTimes(1);
        });

        it('an already-ended login still starts the page over', async () => {
            global.fetch.mockResolvedValue(response(401, { detail: 'ended' }));
            await logOut();
            expect(reload).toHaveBeenCalledTimes(1);
        });

        it('a failure that is not about login is not swallowed', async () => {
            global.fetch.mockResolvedValue(response(500, { detail: 'boom' }));
            await expect(logOut()).rejects.toThrow('boom');
            expect(reload).not.toHaveBeenCalled();
        });
    });
});
