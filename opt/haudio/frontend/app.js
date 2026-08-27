'use strict';

const ui = {
  state: {},
  recordings: [],
  sounds: [],
  pendingVolumes: new Set(),
  volumeTimers: new Map(),
  volumeSequences: new Map(),
  deviceSignature: '',
  socket: null,
  reconnectDelay: 1000,
  fallbackTimer: null,
};

const byId = (id) => document.getElementById(id);

function showError(message) {
  const banner = byId('error-banner');
  banner.textContent = message;
  banner.hidden = false;
}

function clearError() {
  byId('error-banner').hidden = true;
}

async function request(path, options = {}) {
  const response = await fetch(path, options);
  let payload = null;
  try {
    payload = await response.json();
  } catch (_) {
    payload = null;
  }
  if (!response.ok) {
    const message = payload?.detail || `${response.status} ${response.statusText}`;
    showError(message);
    throw new Error(message);
  }
  clearError();
  return payload;
}

function post(path, body = {}) {
  return request(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body),
  });
}

function setConnection(connected, label = '') {
  const element = byId('connection-status');
  element.className = `connection ${connected ? 'online' : 'offline'}`;
  element.textContent = connected ? '● ONLINE' : `● ${label || 'OFFLINE'}`;
}

function setDeviceState(name, connected) {
  const element = byId(`${name}-state`);
  element.className = `device-state ${connected ? 'on' : 'off'}`;
  element.textContent = connected ? '● CONNECTED' : '● DISCONNECTED';
}

function setButton(button, active, activeText, inactiveText, activeClass = 'active') {
  button.classList.toggle(activeClass, active);
  button.textContent = active ? activeText : inactiveText;
  button.setAttribute('aria-pressed', String(active));
}

function meterPercent(decibels) {
  const value = Number.isFinite(decibels) ? decibels : -60;
  return Math.max(0, Math.min(100, ((value + 60) / 60) * 100));
}

function updateMeter(name, decibels) {
  const value = Number.isFinite(decibels) ? decibels : -60;
  byId(`${name}-meter`).style.width = `${meterPercent(value)}%`;
  byId(`${name}-level`).textContent = `${value.toFixed(1)} dB`;
}

function updateVolume(name, value) {
  byId(`${name}-volume-value`).textContent = `${value}%`;
  const slider = byId(`${name}-volume`);
  if (document.activeElement !== slider && !ui.pendingVolumes.has(name)) {
    slider.value = value;
  }
}

function updateDevices(devices) {
  if (!devices) return;
  const signature = JSON.stringify({cards: devices.cards, selected: devices.selected});
  if (signature === ui.deviceSignature) return;
  if (document.activeElement?.matches('select[data-role]')) return;
  ui.deviceSignature = signature;
  for (const role of ['pc1', 'pc2', 'headset']) {
    const select = byId(`${role}-device`);
    if (document.activeElement === select) continue;
    const fragment = document.createDocumentFragment();
    const unassigned = document.createElement('option');
    unassigned.value = '';
    unassigned.textContent = 'UNASSIGNED';
    fragment.appendChild(unassigned);
    for (const card of devices.cards || []) {
      const option = document.createElement('option');
      option.value = card.id;
      const capabilities = `${card.has_input ? 'input' : 'no input'}, ${card.has_output ? 'output' : 'no output'}`;
      option.textContent = `${card.product} · ${card.bus_path} (${capabilities})`;
      fragment.appendChild(option);
    }
    select.replaceChildren(fragment);
    select.value = devices.selected?.[role] || '';
  }
}

function formatUptime(seconds) {
  if (!Number.isFinite(seconds)) return '–';
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  return days ? `${days} d ${hours} h` : `${hours} h`;
}

function applyStatus(status) {
  if (!status) return;
  ui.state = status;
  setConnection(true);

  for (const name of ['pc1', 'pc2', 'headset']) {
    const device = status[name] || {};
    setDeviceState(name, Boolean(device.connected));
    updateVolume(name, device.volume ?? 0);
  }
  const microphone = status.microphone || {};
  setDeviceState('microphone', Boolean(microphone.connected));
  updateVolume('mic', microphone.volume ?? 0);
  updateVolume('soundboard', status.soundboard?.volume ?? 100);

  setButton(byId('pc1-mute'), Boolean(status.pc1?.mute), 'MUTED', 'MUTE', 'danger');
  setButton(byId('pc2-mute'), Boolean(status.pc2?.mute), 'MUTED', 'MUTE', 'danger');
  setButton(byId('mic-mute'), Boolean(microphone.mute), 'MIC MUTED', 'MIC MUTE', 'danger');
  setButton(byId('mic-pc1'), Boolean(microphone.route_pc1), 'PC1 ACTIVE', 'PC1 OFF');
  setButton(byId('mic-pc2'), Boolean(microphone.route_pc2), 'PC2 ACTIVE', 'PC2 OFF');

  const levels = status.levels || {};
  updateMeter('pc1', levels.pc1);
  updateMeter('pc2', levels.pc2);
  updateMeter('headset', levels.headset);
  updateMeter('microphone', levels.microphone);

  const recording = Boolean(status.recording?.session);
  setButton(byId('recording-toggle'), recording, 'STOP RECORDING', 'START RECORDING', 'danger');
  const recordingIndicator = byId('recording-indicator');
  recordingIndicator.className = `recording-indicator ${recording ? 'active' : 'inactive'}`;
  recordingIndicator.textContent = recording ? '● RECORDING ACTIVE' : '○ NOT RECORDING';
  const muteAllActive = Boolean(status.presets?.mute_all_active);
  setButton(document.querySelector('[data-preset="mute-all"]'), muteAllActive, 'RESTORE AUDIO', 'MUTE ALL', 'danger');

  const soundboardActive = Boolean(status.soundboard?.active);
  const playing = soundboardActive ? status.soundboard.playing : '';
  const soundboardStop = byId('soundboard-stop');
  soundboardStop.classList.toggle('danger', soundboardActive);
  soundboardStop.setAttribute('aria-pressed', String(soundboardActive));
  byId('soundboard-status').textContent = playing ? `Playing: ${playing}` : '';
  updateDevices(status.devices);

  const system = status.system || {};
  byId('system-pipewire').textContent = system.pipewire ? '● RUNNING' : '● ERROR';
  byId('system-disk').textContent = Number.isFinite(system.disk_free_gb) ? `${system.disk_free_gb} GB` : '–';
  byId('system-load').textContent = Number.isFinite(system.cpu_load) ? system.cpu_load : '–';
  byId('system-ram').textContent = Number.isFinite(system.ram_used_percent) ? `${system.ram_used_percent}%` : '–';
  byId('system-temperature').textContent = Number.isFinite(system.temperature_c) ? `${system.temperature_c} °C` : '–';
  byId('system-uptime').textContent = formatUptime(system.uptime_seconds);
  byId('system-network').textContent = [system.connection_type, system.network_interface, system.primary_ip].filter(Boolean).join(' · ') || '–';
  byId('system-wifi').textContent = system.wlan_connected
    ? `${system.wlan_primary ? 'PRIMARY' : 'SECONDARY'} · ${system.wlan_signal_dbm ?? '–'} dBm`
    : 'DISCONNECTED';
  const graph = byId('graph-state');
  graph.className = `device-state ${system.graph_ready ? 'on' : 'off'}`;
  graph.textContent = system.graph_ready ? '● AUDIO GRAPH READY' : '● AUDIO GRAPH DEGRADED';
  const errors = byId('system-errors');
  errors.hidden = !(status.errors || []).length;
  errors.textContent = (status.errors || []).join(' · ');
}

function queueVolume(name, value) {
  byId(`${name}-volume-value`).textContent = `${value}%`;
  ui.pendingVolumes.add(name);
  clearTimeout(ui.volumeTimers.get(name));
  const sequence = (ui.volumeSequences.get(name) || 0) + 1;
  ui.volumeSequences.set(name, sequence);
  ui.volumeTimers.set(name, setTimeout(async () => {
    try {
      const status = await post(`/api/${name}/volume`, {value: Number(value)});
      if (ui.volumeSequences.get(name) === sequence) applyStatus(status);
    } catch (_) {
      await refreshStatus();
    } finally {
      if (ui.volumeSequences.get(name) === sequence) ui.pendingVolumes.delete(name);
    }
  }, 180));
}

async function waitForPendingVolumes() {
  const deadline = Date.now() + 3000;
  while (ui.pendingVolumes.size && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}

async function refreshStatus() {
  try {
    applyStatus(await request('/api/status'));
  } catch (_) {
    setConnection(false);
  }
}

async function assignDevice(role, cardId) {
  const select = byId(`${role}-device`);
  select.disabled = true;
  try {
    await post('/api/devices/assign', {role, card_id: cardId});
    ui.deviceSignature = '';
    await refreshStatus();
  } finally {
    select.disabled = false;
  }
}

function fileSize(bytes) {
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

function iconButton(symbol, label, className = '') {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = `icon-button ${className}`.trim();
  button.textContent = symbol;
  button.title = label;
  button.setAttribute('aria-label', label);
  return button;
}

function downloadLink(url, label) {
  const link = document.createElement('a');
  link.className = 'icon-button';
  link.href = url;
  link.download = '';
  link.textContent = '⇩';
  link.title = `Download ${label}`;
  link.setAttribute('aria-label', `Download ${label}`);
  return link;
}

function fileRow(name, size) {
  const row = document.createElement('div');
  row.className = 'file-row';
  const details = document.createElement('div');
  details.className = 'file-details';
  const title = document.createElement('strong');
  title.textContent = name;
  const metadata = document.createElement('small');
  metadata.textContent = fileSize(size);
  details.append(title, metadata);
  const actions = document.createElement('div');
  actions.className = 'file-actions';
  row.append(details, actions);
  return {row, actions};
}

function recordingUrl(path) {
  return path.split('/').map(encodeURIComponent).join('/');
}

function renderRecordings() {
  const list = byId('recording-list');
  list.replaceChildren();
  if (!ui.recordings.length) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.textContent = 'No recordings available.';
    list.appendChild(empty);
    return;
  }
  for (const file of ui.recordings) {
    const {row, actions} = fileRow(file.name, file.size);
    if (file.active) row.classList.add('active-file');
    actions.appendChild(downloadLink(`/api/recordings/${recordingUrl(file.path)}`, file.name));
    const rename = iconButton('✎', `Rename ${file.name}`);
    rename.disabled = Boolean(file.active);
    rename.addEventListener('click', () => renameRecording(file));
    const remove = iconButton('🗑', `Delete ${file.name}`, 'danger');
    remove.disabled = Boolean(file.active);
    remove.addEventListener('click', () => deleteRecording(file));
    actions.append(rename, remove);
    list.appendChild(row);
  }
}

function renderSounds() {
  const list = byId('soundboard-list');
  list.replaceChildren();
  if (!ui.sounds.length) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.textContent = 'No MP3 files uploaded.';
    list.appendChild(empty);
    return;
  }
  for (const file of ui.sounds) {
    const {row, actions} = fileRow(file.name, file.size);
    const play = iconButton('▶', `Play ${file.name}`, 'play');
    play.addEventListener('click', () => playSound(file.name, play));
    actions.appendChild(play);
    actions.appendChild(downloadLink(`/api/soundboard/${encodeURIComponent(file.name)}`, file.name));
    const rename = iconButton('✎', `Rename ${file.name}`);
    rename.addEventListener('click', () => renameSound(file.name));
    const remove = iconButton('🗑', `Delete ${file.name}`, 'danger');
    remove.addEventListener('click', () => deleteSound(file.name));
    actions.append(rename, remove);
    list.appendChild(row);
  }
}

async function loadFiles() {
  try {
    const [recordings, soundboard] = await Promise.all([
      request('/api/recordings'),
      request('/api/soundboard'),
    ]);
    ui.recordings = recordings || [];
    ui.sounds = soundboard.files || [];
    renderRecordings();
    renderSounds();
  } catch (_) {
    // request() already reports a useful message.
  }
}

async function playSound(name, button) {
  button.disabled = true;
  byId('soundboard-status').textContent = `Starting ${name}…`;
  try {
    await post(`/api/soundboard/${encodeURIComponent(name)}/play`);
    await refreshStatus();
  } finally {
    button.disabled = false;
  }
}

async function renameSound(name) {
  const newName = window.prompt('New MP3 filename:', name);
  if (!newName || newName === name) return;
  await post(`/api/soundboard/${encodeURIComponent(name)}/rename`, {name: newName});
  await loadFiles();
}

async function deleteSound(name) {
  if (!window.confirm(`Delete ${name}?`)) return;
  await request(`/api/soundboard/${encodeURIComponent(name)}`, {method: 'DELETE'});
  await loadFiles();
}

async function renameRecording(file) {
  const newName = window.prompt('New Opus filename:', file.name);
  if (!newName || newName === file.name) return;
  await post(`/api/recordings/${recordingUrl(file.path)}/rename`, {name: newName});
  await loadFiles();
}

async function deleteRecording(file) {
  if (!window.confirm(`Delete ${file.name}?`)) return;
  await request(`/api/recordings/${recordingUrl(file.path)}`, {method: 'DELETE'});
  await loadFiles();
}

function connectWebSocket() {
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
  const socket = new WebSocket(`${protocol}://${location.host}/ws`);
  ui.socket = socket;
  socket.addEventListener('open', () => {
    ui.reconnectDelay = 1000;
    setConnection(true);
    clearInterval(ui.fallbackTimer);
    ui.fallbackTimer = null;
  });
  socket.addEventListener('message', (event) => {
    try {
      applyStatus(JSON.parse(event.data));
    } catch (error) {
      showError(`Invalid live status: ${error.message}`);
    }
  });
  socket.addEventListener('close', () => {
    setConnection(false, 'RECONNECTING');
    if (!ui.fallbackTimer) ui.fallbackTimer = setInterval(refreshStatus, 5000);
    setTimeout(connectWebSocket, ui.reconnectDelay);
    ui.reconnectDelay = Math.min(ui.reconnectDelay * 2, 15000);
  });
  socket.addEventListener('error', () => socket.close());
}

function bindControls() {
  for (const button of document.querySelectorAll('[data-preset]')) {
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        applyStatus(await post(`/api/preset/${button.dataset.preset}`));
      } finally {
        button.disabled = false;
      }
    });
  }
  byId('preset-save').addEventListener('click', async () => {
    const button = byId('preset-save');
    const name = byId('preset-save-target').value;
    button.disabled = true;
    byId('preset-status').textContent = 'Saving…';
    try {
      await waitForPendingVolumes();
      await post(`/api/presets/${name}/save`);
      byId('preset-status').textContent = 'Saved.';
    } finally {
      button.disabled = false;
    }
  });
  for (const name of ['pc1', 'pc2', 'headset', 'mic', 'soundboard']) {
    byId(`${name}-volume`).addEventListener('input', (event) => queueVolume(name, event.target.value));
  }
  byId('pc1-mute').addEventListener('click', async () => applyStatus(await post('/api/pc1/mute', {value: !ui.state.pc1?.mute})));
  byId('pc2-mute').addEventListener('click', async () => applyStatus(await post('/api/pc2/mute', {value: !ui.state.pc2?.mute})));
  byId('mic-mute').addEventListener('click', async () => applyStatus(await post('/api/mic/mute', {value: !ui.state.microphone?.mute})));
  byId('mic-pc1').addEventListener('click', async () => applyStatus(await post('/api/mic/route/pc1', {value: !ui.state.microphone?.route_pc1})));
  byId('mic-pc2').addEventListener('click', async () => applyStatus(await post('/api/mic/route/pc2', {value: !ui.state.microphone?.route_pc2})));
  for (const select of document.querySelectorAll('select[data-role]')) {
    select.addEventListener('change', () => assignDevice(select.dataset.role, select.value));
  }
  byId('recording-toggle').addEventListener('click', async () => {
    const button = byId('recording-toggle');
    button.disabled = true;
    try {
      applyStatus(await post('/api/recording/toggle'));
      await loadFiles();
    } finally {
      button.disabled = false;
    }
  });
  byId('soundboard-stop').addEventListener('click', async () => {
    await post('/api/soundboard/stop');
    await refreshStatus();
  });
  byId('sound-upload').addEventListener('change', async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    byId('soundboard-status').textContent = `Uploading ${file.name}…`;
    try {
      await request('/api/soundboard/upload', {method: 'POST', body: form});
      event.target.value = '';
      await loadFiles();
      byId('soundboard-status').textContent = 'Upload complete.';
    } catch (_) {
      byId('soundboard-status').textContent = 'Upload failed.';
    }
  });
}

bindControls();
refreshStatus();
loadFiles();
connectWebSocket();
setInterval(loadFiles, 30000);
