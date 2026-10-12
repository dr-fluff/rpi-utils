const customCommandList = document.querySelector('#custom-command-list');
const builtinCommandList = document.querySelector('#builtin-command-list');
const commandDialog = document.querySelector('#command-dialog');
const commandForm = document.querySelector('#command-form');
const commandFormError = document.querySelector('#command-form-error');
const commandDialogTitle = document.querySelector('#command-dialog-title');
const deleteCommandButton = document.querySelector('#delete-command-button');
const commandsNotice = document.querySelector('#commands-notice');
const scriptList = document.querySelector('#script-list');
const scriptDirectoryLabel = document.querySelector('#scripts-directory-label');
const commandScriptSelect = document.querySelector('#command-script');
const commandIconInput = document.querySelector('#command-icon');
const commandIconSearch = document.querySelector('#command-icon-search');
const commandIconGrid = document.querySelector('#command-icon-grid');
const selectedCommandIconLabel = document.querySelector('#selected-command-icon-label');
const commandLine = document.querySelector('#command-line');
const commandLineLabel = document.querySelector('#command-line-label');
const scriptArgumentsLabel = document.querySelector('#script-arguments-label');
const scriptDialog = document.querySelector('#script-dialog');
const scriptForm = document.querySelector('#script-form');
const scriptDialogTitle = document.querySelector('#script-dialog-title');
const scriptFormError = document.querySelector('#script-form-error');
const deleteScriptButton = document.querySelector('#delete-script-button');
const commandDefinitions = new Map();
let scriptDefinitions = [];
let defaultTimeoutSeconds = 60;
let commandIcons = [];
let commandIconsByName = new Map();

function iconButton(icon, label, className = 'button button-quiet icon-button') {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = className;
  button.title = label;
  button.setAttribute('aria-label', label);
  const glyph = document.createElement('span');
  glyph.className = 'fa-icon';
  glyph.setAttribute('aria-hidden', 'true');
  glyph.textContent = iconGlyph(icon);
  button.append(glyph);
  return button;
}

function iconGlyph(name) {
  const icon = commandIconsByName.get(name);
  return icon ? String.fromCodePoint(Number.parseInt(icon[2], 16)) : '';
}

async function loadCommandIcons() {
  const response = await fetch('/static/vendor/fa-solid-icons.json');
  if (!response.ok) throw new Error('Could not load the Font Awesome icon catalog.');
  commandIcons = await response.json();
  commandIconsByName = new Map(commandIcons.map(icon => [icon[0], icon]));
}

function showNotice(message, isError = false) {
  commandsNotice.textContent = message;
  commandsNotice.classList.toggle('login-error', isError);
  window.clearTimeout(showNotice.timer);
  showNotice.timer = window.setTimeout(() => {
    commandsNotice.textContent = '';
    commandsNotice.classList.remove('login-error');
  }, 6000);
}

async function api(url, options) {
  const response = await fetch(url, options);
  const result = await response.json();
  if (response.status === 401) {
    window.location.assign('/');
    throw new Error('Sign in to manage commands.');
  }
  if (!response.ok) throw new Error(result.detail || 'Request failed');
  return result;
}

async function ensureSignedIn() {
  const status = await api('/api/auth/status');
  if (!status.authenticated || status.must_change_password) {
    window.location.assign('/');
    return false;
  }
  return true;
}

async function loadCommands() {
  const { commands } = await api('/api/commands');
  commandDefinitions.clear();
  commands.forEach(command => commandDefinitions.set(command.name, command));
  renderCommands(commands);
}

async function loadSettings() {
  const settings = await api('/api/settings');
  defaultTimeoutSeconds = settings.default_timeout_seconds;
  scriptDirectoryLabel.textContent = `Stored outside Git at ${settings.scripts_directory}`;
}

async function loadScripts() {
  const { scripts, scripts_directory } = await api('/api/scripts');
  scriptDefinitions = scripts;
  commandScriptSelect.replaceChildren(new Option('Use a direct executable instead', ''));
  scripts.forEach(script => commandScriptSelect.add(new Option(script.name, script.name)));
  scriptDirectoryLabel.textContent = `Stored outside Git at ${scripts_directory}`;
  renderScripts();
}

function renderCommands(commands) {
  customCommandList.replaceChildren();
  builtinCommandList.replaceChildren();
  const custom = commands.filter(command => command.is_custom);
  const builtins = commands.filter(command => !command.is_custom);
  if (!custom.length) {
    const empty = document.createElement('p');
    empty.className = 'commands-empty';
    empty.textContent = 'No custom commands yet. Add one to run a local script.';
    customCommandList.append(empty);
  }
  custom.forEach(command => customCommandList.append(renderCommand(command, true)));
  builtins.forEach(command => builtinCommandList.append(renderCommand(command, false)));
}

function renderCommand(command, editable) {
  const row = document.createElement('article');
  row.className = 'managed-command';
  const info = document.createElement('div');
  info.className = 'managed-command-info';
  const heading = document.createElement('div');
  heading.className = 'managed-command-title';
  const name = document.createElement('span');
  name.textContent = `/${command.name}`;
  const icon = editable ? command.icon || 'bolt' : ({
    help: 'circle-question',
    ip: 'globe',
    status: 'chart-simple',
    start: 'play',
    stop: 'stop',
    restart: 'arrows-rotate',
    update: 'download',
  }[command.name] || 'bolt');
  const commandGlyph = document.createElement('span');
  commandGlyph.className = 'fa-icon command-icon';
  commandGlyph.setAttribute('aria-hidden', 'true');
  commandGlyph.textContent = iconGlyph(icon);
  heading.append(commandGlyph, name);
  if (editable) {
    const badge = document.createElement('span');
    badge.className = `command-badge${command.telegram_enabled ? ' telegram' : ''}`;
    badge.textContent = command.telegram_enabled ? 'TELEGRAM ENABLED' : 'DASHBOARD ONLY';
    heading.append(badge);
  }
  const description = document.createElement('p');
  description.className = 'managed-command-description';
  description.textContent = command.description;
  info.append(heading, description);
  if (editable) {
    const executable = document.createElement('p');
    executable.className = 'managed-command-exec';
    executable.textContent = command.script_name
      ? `${command.script_name}${command.arguments ? ` ${command.arguments}` : ''}`
      : command.command;
    info.append(executable);
  } else if (command.usage) {
    const usage = document.createElement('p');
    usage.className = 'managed-command-exec';
    usage.textContent = `Usage: /${command.name} ${command.usage}`;
    info.append(usage);
  }

  const actions = document.createElement('div');
  actions.className = 'managed-command-actions';
  const run = iconButton(icon, `Run /${command.name}`, 'small-button icon-button');
  run.addEventListener('click', () => void executeCommand(command.name));
  actions.append(run);
  if (editable) {
    const edit = iconButton('pen-to-square', `Edit /${command.name}`, 'small-button icon-button');
    edit.addEventListener('click', () => openCommandDialog(command));
    actions.append(edit);
  }
  row.append(info, actions);
  return row;
}

function renderScripts() {
  scriptList.replaceChildren();
  if (!scriptDefinitions.length) {
    const empty = document.createElement('p');
    empty.className = 'commands-empty';
    empty.textContent = 'No scripts yet. Add a .sh, .bash, or .py script here, or place one in this directory using SSH.';
    scriptList.append(empty);
    return;
  }
  scriptDefinitions.forEach(script => {
    const row = document.createElement('article');
    row.className = 'managed-command';
    const info = document.createElement('div');
    info.className = 'managed-command-info';
    const filename = document.createElement('div');
    filename.className = 'managed-command-title';
    filename.textContent = script.name;
    const note = document.createElement('p');
    note.className = 'managed-command-description';
    note.textContent = 'Private script file · executable by the dashboard service user';
    info.append(filename, note);
    const actions = document.createElement('div');
    actions.className = 'managed-command-actions';
    const addCommand = iconButton('plus', `Add a command using ${script.name}`, 'small-button icon-button');
    addCommand.addEventListener('click', () => openCommandDialogForScript(script));
    const edit = iconButton('pen-to-square', `Edit ${script.name}`, 'small-button icon-button');
    edit.addEventListener('click', () => void openScriptDialog(script.name));
    actions.append(addCommand, edit);
    row.append(info, actions);
    scriptList.append(row);
  });
}

async function executeCommand(name) {
  const command = commandDefinitions.get(name);
  if (!command) return;
  if (command.requires_confirmation && !window.confirm(`Run /${name}? ${command.description}`)) return;
  const args = [];
  if (command.usage) {
    const value = window.prompt(`Argument for /${name}: ${command.usage}`);
    if (value === null) return;
    args.push(value);
  }
  try {
    const result = await api(`/api/commands/${encodeURIComponent(name)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ args }),
    });
    showNotice(result.message);
  } catch (error) {
    showNotice(error.message, true);
  }
}

function openCommandDialog(command = null) {
  commandForm.reset();
  commandForm.dataset.originalName = command?.name || '';
  commandDialogTitle.textContent = command ? 'Edit command' : 'Add a command';
  deleteCommandButton.hidden = !command;
  commandForm.elements.name.value = command?.name || '';
  commandForm.elements.description.value = command?.description || '';
  selectCommandIcon(command?.icon || 'bolt');
  commandScriptSelect.value = command?.script_name || '';
  commandLine.value = command && !command.script_name ? command.command : '';
  commandForm.elements.arguments.value = command?.arguments || '';
  commandForm.elements.telegram_enabled.checked = command?.telegram_enabled || false;
  commandForm.elements.requires_confirmation.checked = command?.requires_confirmation || false;
  commandForm.elements.timeout_seconds.value = command?.timeout_seconds || defaultTimeoutSeconds;
  updateCommandScriptFields();
  commandFormError.hidden = true;
  commandDialog.showModal();
  commandForm.elements.name.focus();
}

function renderCommandIcons() {
  const query = commandIconSearch.value.trim().toLowerCase();
  commandIconGrid.replaceChildren();
  const matches = commandIcons.filter(([name, label, , terms]) =>
    `${name} ${label} ${terms.join(' ')}`.toLowerCase().includes(query));
  matches.forEach(([name, label, unicode]) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `command-icon-option${commandIconInput.value === name ? ' selected' : ''}`;
    button.setAttribute('aria-label', label);
    button.setAttribute('aria-pressed', String(commandIconInput.value === name));
    button.title = label;
    const glyph = document.createElement('span');
    glyph.className = 'fa-icon';
    glyph.setAttribute('aria-hidden', 'true');
    glyph.textContent = String.fromCodePoint(Number.parseInt(unicode, 16));
    const caption = document.createElement('span');
    caption.textContent = label;
    button.append(glyph, caption);
    button.addEventListener('click', () => selectCommandIcon(name));
    commandIconGrid.append(button);
  });
  if (!matches.length) {
    const empty = document.createElement('p');
    empty.className = 'commands-empty';
    empty.textContent = 'No matching icons.';
    commandIconGrid.append(empty);
  }
}

function selectCommandIcon(name) {
  const icon = commandIconsByName.get(name);
  if (!icon) return;
  commandIconInput.value = icon[0];
  selectedCommandIconLabel.textContent = icon[1];
  renderCommandIcons();
}

function openCommandDialogForScript(script) {
  const existing = [...commandDefinitions.values()].find(command =>
    command.is_custom && command.script_name === script.name);
  openCommandDialog(existing || null);
  if (existing) return;

  const baseName = script.name
    .replace(/\.(sh|bash|py)$/i, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 32) || 'run_script';
  let name = /^[a-z]/.test(baseName) ? baseName : `script_${baseName}`.slice(0, 32);
  const rootName = name;
  let suffix = 2;
  while (commandDefinitions.has(name)) {
    const ending = `_${suffix}`;
    name = `${rootName.slice(0, 32 - ending.length)}${ending}`;
    suffix += 1;
  }

  commandForm.elements.name.value = name;
  commandForm.elements.description.value = script.name
    .replace(/\.(sh|bash|py)$/i, '')
    .replace(/[-_]+/g, ' ')
    .replace(/\b\w/g, character => character.toUpperCase());
  commandScriptSelect.value = script.name;
  updateCommandScriptFields();
}

function updateCommandScriptFields() {
  const hasScript = Boolean(commandScriptSelect.value);
  commandLineLabel.hidden = hasScript;
  commandLine.required = !hasScript;
  scriptArgumentsLabel.hidden = !hasScript;
}

async function deleteCommand() {
  const name = commandForm.dataset.originalName;
  if (!name || !window.confirm(`Delete /${name}?`)) return;
  try {
    await api(`/api/custom-commands/${encodeURIComponent(name)}`, { method: 'DELETE' });
    commandDialog.close();
    showNotice(`Removed /${name}.`);
    await loadCommands();
  } catch (error) {
    commandFormError.textContent = error.message;
    commandFormError.hidden = false;
  }
}

document.querySelector('#add-command-button').addEventListener('click', () => openCommandDialog());
commandIconSearch.addEventListener('input', renderCommandIcons);
document.querySelector('#close-command-dialog').addEventListener('click', () => commandDialog.close());
document.querySelector('#cancel-command-button').addEventListener('click', () => commandDialog.close());
deleteCommandButton.addEventListener('click', () => void deleteCommand());
commandForm.addEventListener('submit', async event => {
  event.preventDefault();
  commandFormError.hidden = true;
  const originalName = commandForm.dataset.originalName;
  const formData = new FormData(commandForm);
  const payload = {
    name: formData.get('name'),
    description: formData.get('description'),
    command: commandScriptSelect.value ? '' : formData.get('command'),
    script_name: commandScriptSelect.value || null,
    arguments: commandScriptSelect.value ? formData.get('arguments') : '',
    telegram_enabled: commandForm.elements.telegram_enabled.checked,
    requires_confirmation: commandForm.elements.requires_confirmation.checked,
    timeout_seconds: Number(formData.get('timeout_seconds')),
    icon: commandIconInput.value,
  };
  try {
    await api(originalName
      ? `/api/custom-commands/${encodeURIComponent(originalName)}`
      : '/api/custom-commands', {
      method: originalName ? 'PUT' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    commandDialog.close();
    showNotice(originalName ? `Saved /${payload.name}.` : `Added /${payload.name}.`);
    await loadCommands();
  } catch (error) {
    commandFormError.textContent = error.message;
    commandFormError.hidden = false;
  }
});

async function openScriptDialog(name = '') {
  scriptForm.reset();
  scriptForm.dataset.originalName = name;
  scriptForm.elements.name.readOnly = Boolean(name);
  scriptDialogTitle.textContent = name ? 'Edit script' : 'Add a script';
  deleteScriptButton.hidden = !name;
  scriptFormError.hidden = true;
  if (name) {
    try {
      const script = await api(`/api/scripts/${encodeURIComponent(name)}`);
      scriptForm.elements.name.value = script.name;
      scriptForm.elements.content.value = script.content;
    } catch (error) {
      showNotice(error.message, true);
      return;
    }
  } else {
    scriptForm.elements.name.value = 'new-script.sh';
    scriptForm.elements.content.value = '#!/bin/sh\n\n';
  }
  scriptDialog.showModal();
  scriptForm.elements.name.focus();
}

async function saveScript(event) {
  event.preventDefault();
  scriptFormError.hidden = true;
  const name = scriptForm.elements.name.value.trim();
  try {
    await api(`/api/scripts/${encodeURIComponent(name)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: scriptForm.elements.content.value }),
    });
    scriptDialog.close();
    showNotice(`Saved ${name}.`);
    await Promise.all([loadScripts(), loadCommands()]);
  } catch (error) {
    scriptFormError.textContent = error.message;
    scriptFormError.hidden = false;
  }
}

async function deleteScript() {
  const name = scriptForm.dataset.originalName;
  if (!name || !window.confirm(`Delete ${name}?`)) return;
  try {
    await api(`/api/scripts/${encodeURIComponent(name)}`, { method: 'DELETE' });
    scriptDialog.close();
    showNotice(`Deleted ${name}.`);
    await Promise.all([loadScripts(), loadCommands()]);
  } catch (error) {
    scriptFormError.textContent = error.message;
    scriptFormError.hidden = false;
  }
}

document.querySelector('#add-script-button').addEventListener('click', () => void openScriptDialog());
document.querySelector('#close-script-dialog').addEventListener('click', () => scriptDialog.close());
document.querySelector('#cancel-script-button').addEventListener('click', () => scriptDialog.close());
deleteScriptButton.addEventListener('click', () => void deleteScript());
scriptForm.addEventListener('submit', saveScript);
commandScriptSelect.addEventListener('change', updateCommandScriptFields);

ensureSignedIn()
  .then(async signedIn => {
    if (!signedIn) return;
    await loadCommandIcons();
    await Promise.all([loadCommands(), loadSettings(), loadScripts()]);
  })
  .catch(error => showNotice(error.message, true));
