console.log('[main.js] Script starting...');

let app;

try {
  console.log('[main.js] Importing svelte...');
  const { mount } = await import('svelte');
  console.log('[main.js] Svelte imported');

  console.log('[main.js] Importing CSS...');
  await import('./app.css');
  console.log('[main.js] CSS imported');

  console.log('[main.js] Importing App component...');
  const { default: App } = await import('./App.svelte');
  console.log('[main.js] App component imported');

  const target = document.getElementById('app');
  console.log('[main.js] Target element:', target);

  if (!target) {
    throw new Error('#app element not found');
  }

  console.log('[main.js] Mounting app...');
  app = mount(App, { target });
  console.log('[main.js] App mounted successfully');
} catch (error) {
  console.error('[main.js] FATAL ERROR:', error);
  document.body.innerHTML = `
    <div style="padding: 2rem; font-family: sans-serif; color: red;">
      <h1>Failed to start application</h1>
      <pre>${error.message}\n${error.stack}</pre>
    </div>
  `;
}

export default app;
