/**
 * Login Module (issue #396)
 *
 * The backend answers only to a credential. In the browser that is a
 * session cookie the page's scripts can't read (HttpOnly), bought with the
 * researcher's password. This module is the form that buys it, and the two
 * buttons that end it.
 *
 * The page never holds the password past the request that sends it, and
 * never sees the session at all: logged in or not is learned by asking the
 * backend, and a 401 from any request (api.js routes them here) is the
 * answer "not".
 */

const api = window.api;

let elements = {};
let callbacks = {
    // Reload the page once logged in or out; injected so tests can watch
    reload: () => window.location.reload(),
    // Whether the app behind the form had already started (the login ended
    // while the page was in use). Then logging in just lifts the form: a
    // reload would throw away whatever is on the page, including a message
    // typed and not yet sent.
    isRunning: () => false,
    // Called when the form is lifted without a reload
    onResumed: () => {},
};

let submitting = false;

export function setElements(els) {
    elements = els;
    // A new form has nothing in flight
    submitting = false;
}

export function setCallbacks(cbs) {
    callbacks = { ...callbacks, ...cbs };
}

export function isLoginShown() {
    return Boolean(elements.overlay) && !elements.overlay.hidden;
}

/**
 * Show the login form over the app. Safe to call many times: a page that
 * loads logged out gets a 401 from every request it makes, and the form
 * (and whatever is typed in it) must not be reset by each of them.
 * @param {string} [message] - Why the form is showing
 */
export function showLogin(message) {
    if (!elements.overlay) return;
    if (isLoginShown()) return;
    if (elements.message) {
        elements.message.textContent = message || 'Log in to continue.';
    }
    setError('');
    elements.overlay.hidden = false;
    if (elements.password) {
        elements.password.value = '';
        elements.password.focus();
    }
}

function setError(text) {
    if (!elements.error) return;
    elements.error.textContent = text;
    elements.error.hidden = !text;
}

function setBusy(busy) {
    submitting = busy;
    if (elements.submit) {
        elements.submit.disabled = busy;
        elements.submit.textContent = busy ? 'Logging in…' : 'Log in';
    }
}

/**
 * What to tell the person for a failed login. The backend's own sentence
 * is used where it explains something they can act on.
 */
export function loginFailureMessage(error) {
    if (error && error.status === 401) {
        return 'Wrong password.';
    }
    if (error && error.status === 429) {
        const wait = error.retryAfter;
        return wait
            ? `Too many wrong passwords. Try again in ${wait} second${wait === 1 ? '' : 's'}.`
            : 'Too many wrong passwords. Try again shortly.';
    }
    if (error && (error.status === 503 || error.status === 403)) {
        // No password set on the server yet, or a proxy the server doesn't
        // know as itself: both say what to do about it
        return error.message;
    }
    if (error && error.status) {
        return `Login failed (${error.message}).`;
    }
    return 'Could not reach the server. Is it running?';
}

/**
 * Submit the login form.
 * @param {Event} [event]
 */
export async function submitLogin(event) {
    if (event) event.preventDefault();
    if (submitting || !elements.password) return;
    const password = elements.password.value;
    if (!password) {
        setError('Enter the password.');
        return;
    }
    setBusy(true);
    setError('');
    try {
        await api.login(password);
    } catch (error) {
        elements.password.value = '';
        setError(loginFailureMessage(error));
        setBusy(false);
        elements.password.focus();
        return;
    }
    elements.password.value = '';

    // The password was right. Did the browser keep the login? If it
    // dropped the cookie (a Secure cookie over plain http, or cookies
    // blocked for this address), a reload would come straight back to an
    // empty form with no word of why.
    try {
        await api.getAuthSession();
    } catch (error) {
        if (error && error.unauthorized) {
            setError(
                'The password was right, but this browser did not keep the login. '
                + 'Check that cookies are allowed for this address; over plain http, '
                + 'the server must not be set to AUTH_COOKIE_SECURE=always.'
            );
            setBusy(false);
            return;
        }
        // The backend went away between the two requests: carry on, and
        // the page will say so in its own way
    }

    if (callbacks.isRunning()) {
        // The app was already up when the login ended. Lift the form and
        // leave the page as it is.
        elements.overlay.hidden = true;
        setBusy(false);
        callbacks.onResumed();
        return;
    }
    // The page loaded logged out, so most of what it tried to load was
    // refused. Starting over is simpler and surer than replaying it.
    callbacks.reload();
}

/**
 * Ask the backend whether this page is logged in. A 401 shows the form
 * (through api.js); any other failure is the backend being down, which
 * the rest of startup reports in its own way.
 * @returns {Promise<boolean|null>} true when logged in, false when not
 *   (the form is showing), null when the backend didn't say
 */
export async function ensureLoggedIn() {
    try {
        await api.getAuthSession();
        return true;
    } catch (error) {
        if (error && error.unauthorized) {
            showLogin();
            return false;
        }
        return null;
    }
}

/**
 * End this browser's login, or every browser's.
 * @param {boolean} everywhere
 */
export async function logOut(everywhere = false) {
    try {
        if (everywhere) {
            await api.logoutEverywhere();
        } else {
            await api.logout();
        }
    } catch (error) {
        if (!(error && error.unauthorized)) {
            throw error;
        }
        // Already logged out: the page just needs to catch up
    }
    callbacks.reload();
}
