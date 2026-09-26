/* Laptop-local credentials. Never put keys in app state, storage, URLs or the bus. */
export function speechSettings({demo = false} = {}) {
  const section = document.createElement('section');
  section.className = 'speech-settings';
  section.innerHTML = `
    <h2 class="setting-label">Speak for me</h2>
    <form class="card speech-settings-form" autocomplete="off">
      <h3 class="card-title">ElevenLabs voice</h3>
      <p class="note">Add your voice details here. They stay in the laptop’s local .env file, which you can still edit directly.</p>
      <p class="note" data-status role="status">Loading speech settings…</p>
      <label for="elevenlabs-key">API key</label>
      <input id="elevenlabs-key" name="api_key" type="password" autocomplete="new-password" spellcheck="false" maxlength="4096" placeholder="Enter your API key" disabled>
      <p class="note">Leave blank to keep your current key.</p>
      <label for="elevenlabs-voice">Voice ID <span class="note">(optional)</span></label>
      <input id="elevenlabs-voice" name="voice_id" type="text" autocomplete="off" spellcheck="false" maxlength="256" placeholder="Default voice" disabled>
      <button class="primary full" type="submit" disabled>Save voice settings</button>
      <p class="note">Restart Attune after saving to use the new details.</p>
    </form>`;
  const form = section.querySelector('form');
  const key = form.elements.api_key;
  const voice = form.elements.voice_id;
  const save = form.querySelector('button');
  const status = form.querySelector('[data-status]');
  let ready = false;
  let busy = false;
  form.addEventListener('input', () => { form.dataset.editing = 'true'; });
  const endpoint = '/api/settings/elevenlabs';
  function configured(data) {
    key.placeholder = data.key_configured ? 'Key configured · enter to replace' : 'Enter your API key';
    voice.value = data.voice_id || '';
    voice.disabled = !!data.voice_from_environment;
    key.disabled = data.key_source === 'environment';
    status.textContent = data.key_source === 'environment'
      ? 'Using an environment key. Change it in the laptop environment to replace it.'
      : data.key_configured ? 'API key configured. Your saved key is never shown here.' : 'No API key configured yet.';
    if (data.voice_from_environment) status.textContent += ' Voice ID is also managed by the environment.';
    save.disabled = key.disabled && voice.disabled;
  }
  async function request(options) {
    const response = await fetch(endpoint, {cache: 'no-store', credentials: 'same-origin', ...options});
    if (!response.ok) {
      if (response.status === 403) throw new Error('Open Settings on the Attune laptop at localhost to manage voice details.');
      if (response.status === 400) throw new Error('Check the key and voice ID: use letters, numbers, underscores and hyphens.');
      throw new Error('Could not reach speech settings. Check that Attune is running and try again.');
    }
    return response.json();
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!ready || busy) return;
    busy = true;
    save.disabled = true;
    status.textContent = 'Saving…';
    const values = {};
    if (!key.disabled && key.value.trim()) values.api_key = key.value.trim();
    if (!voice.disabled) values.voice_id = voice.value.trim();
    key.value = '';
    try {
      const data = await request({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(values)});
      configured(data);
      delete form.dataset.editing;
      status.textContent = 'Saved on this laptop. Restart Attune to apply these settings.';
    } catch (error) {
      status.textContent = error.message;
      save.disabled = false;
    } finally {
      delete values.api_key;
      busy = false;
    }
  });
  if (demo) {
    status.textContent = 'Demo only. Open the live Attune Settings page on the laptop to add your voice details.';
  } else {
    request().then(data => { ready = true; configured(data); }).catch(error => { status.textContent = error.message; });
  }
  return section;
}
