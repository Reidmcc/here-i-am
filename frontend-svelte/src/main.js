// Debug helper - writes to a visible div since console isn't working
function debug(msg) {
  const el = document.getElementById('debug-log');
  if (el) el.innerHTML += msg + '<br>';
}

// Create debug div
const debugDiv = document.createElement('div');
debugDiv.id = 'debug-log';
debugDiv.style.cssText = 'position:fixed;top:0;left:0;right:0;background:#000;color:#0f0;padding:10px;font-family:monospace;font-size:12px;z-index:99999;max-height:200px;overflow:auto;';
document.body.appendChild(debugDiv);

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
