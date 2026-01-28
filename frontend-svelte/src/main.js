// Debug helper - logs to browser console
function debug(msg) {
  console.log('[main.js]', msg);
}

debug('main.js starting...');

let app;

try {
  debug('Importing svelte...');
  const { mount } = await import('svelte');
  debug('Svelte imported');

  debug('Importing CSS...');
  await import('./app.css');
  debug('CSS imported');

  debug('Importing App component...');
  const { default: App } = await import('./App.svelte');
  debug('App component imported');

  const target = document.getElementById('app');
  debug('Target element: ' + (target ? 'found' : 'NOT FOUND'));

  if (!target) {
    throw new Error('#app element not found');
  }

  debug('Mounting app...');
  app = mount(App, { target });
  debug('App mounted successfully - now calling onMount...');
} catch (error) {
  debug('FATAL ERROR: ' + error.message);
  document.body.innerHTML = `
    <div style="padding: 2rem; font-family: sans-serif; color: red;">
      <h1>Failed to start application</h1>
      <pre>${error.message}\n${error.stack}</pre>
    </div>
  `;
}

export default app;
