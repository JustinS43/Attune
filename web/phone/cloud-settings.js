/*
 * Cloud captions (Google) in the phone's Settings (P-48).
 *
 * The on/off switch and the language send `cloud.set` {on, language?}; the laptop keeps the
 * choice. The Google API key is saved to the laptop's .env through the laptop-only
 * /api/settings/google (like the ElevenLabs key, P-41): it is never kept in app state, storage,
 * URLs or the bus, and is never shown again. The status line follows the `cloud` message.
 */

const LANGUAGE_LABEL = {'en-US': 'English (US)', 'es-US': 'Spanish (US) · preview'};
const DEFAULT_LANGUAGES = ['en-US', 'es-US'];
const REASON_TEXT = {
  'network': 'network',
  'quota': 'quota reached',
  'slow': 'slow',
  'error': 'Google error',
  'credentials missing': 'add a Google API key below',
  'credentials rejected': 'Google rejected the key',
  'library missing': 'Google speech library missing',
};

export function languageLabel(code) {
  return LANGUAGE_LABEL[code] || code;
}

/** The status line for a `cloud` message (null: no cloud captions on this engine yet). */
export function cloudStatusText(cloud, live = true) {
  if (!live) return 'Off · demo';
  if (!cloud) return 'Not available on this laptop yet';
  const reason = REASON_TEXT[cloud.reason] || cloud.reason || '';
  if (!cloud.enabled || cloud.state === 'off') return 'Off';
  switch (cloud.state) {
    case 'connecting': return 'Connecting…';
    case 'on': return Number.isFinite(cloud.latency_ms) ? `On · ${Math.round(cloud.latency_ms)} ms` : 'On';
    case 'fallback': return `Fell back to local${reason ? `: ${reason}` : ''}`;
    case 'paused': return 'Paused';
    case 'unavailable': return `Unavailable${reason ? `: ${reason}` : ''}`;
    default: return String(cloud.state);
  }
}

/** A calm "Cloud captions on" pill (null while they are off). */
export function cloudBadge(cloud) {
  if (!cloud?.enabled) return null;
  const pill = document.createElement('div');
  pill.className = 'demo-pill cloud-pill';
  pill.setAttribute('role', 'status');
  pill.append(cloudGlyph(), 'Cloud captions on');
  if (cloud.state === 'fallback' || cloud.state === 'unavailable') {
    const quiet = document.createElement('span');
    quiet.className = 'cloud-quiet';
    quiet.textContent = '· using local captions';
    pill.append(quiet);
  }
  return pill;
}

function cloudGlyph() {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'cloud-glyph');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('d', 'M7 18.5h10.2a3.8 3.8 0 0 0 .5-7.57A5.6 5.6 0 0 0 6.9 9.6 4.5 4.5 0 0 0 7 18.5z');
  svg.append(path);
  return svg;
}

/**
 * The Settings section. `getCloud()` returns the latest `cloud` message (or null), `send(on,
 * language)` sends cloud.set, `live` says whether the page is connected to an engine. Returns
 * {el, update()}: update() redraws the switch, language and status in place (no refetch).
 */
export function cloudSettings({demo = false, live = false, getCloud, send, toast}) {
  const section = document.createElement('section');
  section.className = 'cloud-settings';
  section.innerHTML = `
    <h2 class="setting-label">Cloud captions (Google)</h2>
    <div class="card cloud-settings-card">
      <button class="setting-row cloud-switch" type="button" role="switch" aria-checked="false" aria-label="Cloud captions" data-cloud-switch>
        <span class="icon-bubble cloud-bubble"></span>
        <span class="grow">
          <span class="card-title">Cloud captions</span>
          <span class="card-sub" data-cloud-status role="status">Off</span>
        </span>
        <span class="switch"></span>
      </button>
      <p class="note cloud-privacy">While on, the microphone audio is sent to Google Speech-to-Text to tell speakers apart. Off by default.</p>
      <label for="cloud-language">Language</label>
      <div class="select-wrap"><select id="cloud-language" data-cloud-language></select></div>
      <form class="cloud-settings-form" autocomplete="off">
        <label for="google-key">Google API key</label>
        <p class="note" data-key-status role="status">Checking for a key…</p>
        <input id="google-key" name="api_key" type="password" autocomplete="new-password" spellcheck="false" maxlength="4096" placeholder="Paste your Google API key" disabled>
        <p class="note">Leave blank to keep the saved key. It stays in the laptop’s .env file and applies without a restart. A service-account file named in .env also works; the key is tried first.</p>
        <button class="primary full" type="submit" disabled>Save key</button>
      </form>
    </div>`;
  section.querySelector('.cloud-bubble').append(cloudGlyph());
  const row = section.querySelector('[data-cloud-switch]');
  const knob = row.querySelector('.switch');
  const statusLine = section.querySelector('[data-cloud-status]');
  const select = section.querySelector('[data-cloud-language]');
  const form = section.querySelector('form');
  const key = form.elements.api_key;
  const save = form.querySelector('button');
  const keyStatus = form.querySelector('[data-key-status]');
  const endpoint = '/api/settings/google';
  let ready = false;
  let busy = false;
  let languagesShown = '';

  function update() {
    const cloud = getCloud();
    const on = !!cloud?.enabled;
    row.setAttribute('aria-checked', String(on));
    row.disabled = live && !cloud;
    knob.classList.toggle('on', on);
    statusLine.textContent = cloudStatusText(cloud, live);
    statusLine.classList.toggle('cloud-attention', live && (cloud?.state === 'fallback' || cloud?.state === 'unavailable') && on);
    const languages = Array.isArray(cloud?.languages) && cloud.languages.length ? cloud.languages : DEFAULT_LANGUAGES;
    const sig = languages.join('|');
    if (sig !== languagesShown) {
      languagesShown = sig;
      select.replaceChildren(...languages.map(code => {
        const option = document.createElement('option');
        option.value = code;
        option.textContent = languageLabel(code);
        return option;
      }));
    }
    if (document.activeElement !== select) select.value = cloud?.language && languages.includes(cloud.language) ? cloud.language : languages[0];
    select.disabled = live && !cloud;
  }

  row.addEventListener('click', event => {
    event.stopPropagation();
    if (!live) { toast?.('Demo only. Turn cloud captions on from the live Attune page.'); return; }
    const cloud = getCloud();
    if (!cloud) return;
    const on = !cloud.enabled;
    send(on);
    statusLine.textContent = on ? 'Turning on…' : 'Turning off…';
  });
  select.addEventListener('change', () => {
    if (!live) { toast?.('Demo only. Change the language from the live Attune page.'); return; }
    send(!!getCloud()?.enabled, select.value);
    select.blur();
  });

  form.addEventListener('input', () => { form.dataset.editing = 'true'; });
  function configured(data) {
    key.disabled = data.key_source === 'environment';
    key.placeholder = data.key_configured ? 'Key saved · paste to replace' : 'Paste your Google API key';
    keyStatus.textContent = data.key_source === 'environment'
      ? 'Using a key from the laptop environment'
      : data.key_configured ? 'Key saved ••••' : 'No key yet';
    if (!data.key_configured && data.service_account) keyStatus.textContent += ' · a service-account file is set';
    save.disabled = key.disabled;
  }
  async function request(options) {
    const response = await fetch(endpoint, {cache: 'no-store', credentials: 'same-origin', ...options});
    if (!response.ok) {
      if (response.status === 403) throw new Error('Open Settings on the Attune laptop at localhost to add the Google key.');
      if (response.status === 400) throw new Error('Check the key: use letters, numbers, underscores and hyphens.');
      if (response.status === 404 || response.status === 405) throw new Error('This preview has no engine. Open Settings from the live Attune page on your laptop.');
      throw new Error('Could not reach the key settings. Check that Attune is running and try again.');
    }
    return response.json();
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!ready || busy || key.disabled) return;
    busy = true;
    save.disabled = true;
    keyStatus.textContent = 'Saving…';
    const values = {};
    if (key.value.trim()) values.api_key = key.value.trim();
    key.value = '';
    try {
      const data = await request({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(values)});
      configured(data);
      delete form.dataset.editing;
      if (values.api_key) keyStatus.textContent = 'Key saved •••• · applies without a restart';
    } catch (error) {
      keyStatus.textContent = error.message;
      save.disabled = false;
    } finally {
      delete values.api_key;
      busy = false;
    }
  });

  update();
  if (demo) {
    keyStatus.textContent = 'Demo only. Open the live Attune Settings page on the laptop to add a Google key.';
  } else {
    request().then(data => { ready = true; configured(data); }).catch(error => { keyStatus.textContent = error.message; });
  }
  return {el: section, update};
}
