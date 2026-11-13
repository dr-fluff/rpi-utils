const settingsForm = document.querySelector('#settings-form');
const settingsBackButton = document.querySelector('#settings-back-button');
const settingsCancelButton = document.querySelector('#settings-cancel-button');
const settingsSaveButton = document.querySelector('#settings-save-button');
const scriptsDirectoryInput = document.querySelector('#scripts-directory-input');
const defaultTimeoutInput = document.querySelector('#default-timeout-input');
const scriptConfigInput = document.querySelector('#script-config-input');
const telegramBotTokenInput = document.querySelector('#telegram-bot-token');
const telegramChatIdsInput = document.querySelector('#telegram-chat-ids');
const clearTelegramTokenInput = document.querySelector('#clear-telegram-token');
const telegramStatus = document.querySelector('#telegram-status');
const telegramTokenStatus = document.querySelector('#telegram-token-status');
const telegramFormError = document.querySelector('#telegram-form-error');
const settingsNotice = document.querySelector('#settings-notice');
const changePasswordForm = document.querySelector('#change-password-form');
const changePasswordError = document.querySelector('#change-password-error');
const accountSettingsNotice = document.querySelector('#account-settings-notice');
const changePasswordSubmit = document.querySelector('#change-password-submit');
const newPasswordInput = document.querySelector('#settings-new-password');
const confirmPasswordInput = document.querySelector('#settings-confirm-password');
const scriptProfileList = document.querySelector('#script-profile-list');
const scriptProfileDialog = document.querySelector('#script-profile-dialog');
const scriptProfileForm = document.querySelector('#script-profile-form');
const scriptProfileNameInput = document.querySelector('#script-profile-name');
const scriptProfileConfigInput = document.querySelector('#script-profile-config');
const scriptProfilePrivateKeyInput = document.querySelector('#script-profile-private-key');
const scriptProfilePasswordInput = document.querySelector('#script-profile-password');
const scriptProfileKeyStatus = document.querySelector('#script-profile-key-status');
const scriptProfileFormError = document.querySelector('#script-profile-form-error');
const clearProfileKeyOption = document.querySelector('#clear-profile-key-option');
const clearProfilePasswordOption = document.querySelector('#clear-profile-password-option');
const clearProfileKeyInput = document.querySelector('#clear-profile-key');
const clearProfilePasswordInput = document.querySelector('#clear-profile-password');
const profileCredentialStatus = new Map();
let scriptProfiles = {};
let savedScriptProfiles = {};
let pendingProfileCredentials = {};
let profileEditorName = null;
let profileEditorDirty = false;
let savedDeviceSettings = null;
let savedTelegramChatIds = null;
let settingsLoaded = false;
let allowNavigation = false;

async function api(url, options) {
  const response = await fetch(url, options);
  const result = await response.json();
  if (response.status === 401) {
    window.location.assign('/');
    throw new Error('Sign in to manage settings.');
  }
  if (!response.ok) throw new Error(result.detail || 'Request failed');
  return result;
}

function showNotice(message, isError = false) {
  settingsNotice.textContent = message;
  settingsNotice.classList.toggle('login-error', isError);
}

function currentDeviceSettings() {
  return {
    scripts_directory: scriptsDirectoryInput.value,
    default_timeout_seconds: defaultTimeoutInput.value,
    script_config: scriptConfigInput.value,
  };
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function hasUnsavedSettings() {
  const current = currentDeviceSettings();
  return Object.keys(current).some(key => current[key] !== savedDeviceSettings?.[key])
    || telegramChatIdsInput.value !== savedTelegramChatIds
    || telegramBotTokenInput.value !== ''
    || clearTelegramTokenInput.checked
    || JSON.stringify(scriptProfiles) !== JSON.stringify(savedScriptProfiles)
    || Object.keys(pendingProfileCredentials).length > 0;
}

function hasUnsavedChanges() {
  if (!settingsLoaded) return false;
  return hasUnsavedSettings()
    || profileEditorDirty
    || newPasswordInput.value !== ''
    || confirmPasswordInput.value !== '';
}

function updateUnsavedActions() {
  const unsaved = hasUnsavedChanges();
  settingsCancelButton.hidden = !unsaved;
  settingsSaveButton.disabled = !hasUnsavedSettings();
}

function discardChanges() {
  scriptsDirectoryInput.value = savedDeviceSettings.scripts_directory;
  defaultTimeoutInput.value = savedDeviceSettings.default_timeout_seconds;
  scriptConfigInput.value = savedDeviceSettings.script_config;
  scriptProfiles = clone(savedScriptProfiles);
  pendingProfileCredentials = {};
  telegramChatIdsInput.value = savedTelegramChatIds;
  telegramBotTokenInput.value = '';
  telegramBotTokenInput.disabled = false;
  clearTelegramTokenInput.checked = false;
  changePasswordForm.reset();
  changePasswordError.hidden = true;
  telegramFormError.hidden = true;
  scriptProfileDialog.close();
  profileEditorDirty = false;
  renderScriptProfiles();
  updateUnsavedActions();
  showNotice('Unsaved changes discarded.');
}

async function loadDeviceSettings() {
  const settings = await api('/api/settings');
  scriptsDirectoryInput.value = settings.scripts_directory;
  defaultTimeoutInput.value = settings.default_timeout_seconds;
  scriptConfigInput.value = JSON.stringify(settings.script_config, null, 2);
  scriptProfiles = clone(settings.script_profiles || {});
  savedScriptProfiles = clone(scriptProfiles);
  savedDeviceSettings = currentDeviceSettings();
}

async function loadScriptProfiles() {
  const { profiles } = await api('/api/script-profiles');
  profileCredentialStatus.clear();
  profiles.forEach(profile => profileCredentialStatus.set(profile.name, profile));
  renderScriptProfiles();
}

async function loadTelegramSettings() {
  const settings = await api('/api/telegram-settings');
  telegramChatIdsInput.value = settings.allowed_chat_ids;
  savedTelegramChatIds = settings.allowed_chat_ids;
  telegramStatus.textContent = settings.enabled ? 'BOT ENABLED' : 'BOT NOT CONFIGURED';
  telegramStatus.classList.toggle('telegram', settings.enabled);
  telegramTokenStatus.textContent = settings.bot_token_configured
    ? 'A token is saved. Leave this field blank to keep it, or enter a new token to replace it.'
    : 'No bot token is saved. Enter a token from BotFather to configure the bot.';
}

settingsForm.addEventListener('submit', async event => {
  event.preventDefault();
  const formData = new FormData(settingsForm);
  telegramFormError.hidden = true;
  let scriptConfig;
  try {
    scriptConfig = JSON.parse(scriptConfigInput.value);
    if (!scriptConfig || typeof scriptConfig !== 'object' || Array.isArray(scriptConfig)) {
      throw new Error('Enter a JSON object.');
    }
  } catch (error) {
    showNotice(`Script configuration must be valid JSON: ${error.message}`, true);
    return;
  }
  try {
    const savedSettings = await api('/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        scripts_directory: formData.get('scripts_directory'),
        default_timeout_seconds: Number(formData.get('default_timeout_seconds')),
        script_config: scriptConfig,
        script_profiles: scriptProfiles,
      }),
    });
    scriptsDirectoryInput.value = savedSettings.scripts_directory;
    defaultTimeoutInput.value = savedSettings.default_timeout_seconds;
    scriptConfigInput.value = JSON.stringify(savedSettings.script_config, null, 2);
    savedDeviceSettings = currentDeviceSettings();
    savedScriptProfiles = clone(savedSettings.script_profiles);
    pendingProfileCredentials = Object.fromEntries(
      Object.entries(pendingProfileCredentials).filter(([name]) => name in scriptProfiles),
    );
  } catch (error) {
    showNotice(error.message, true);
    return;
  }

  for (const [name, credentials] of Object.entries(pendingProfileCredentials)) {
    try {
      const savedCredentials = await api(`/api/script-profiles/${encodeURIComponent(name)}/credentials`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(credentials),
      });
      profileCredentialStatus.set(name, { ...profileCredentialStatus.get(name), ...savedCredentials });
      delete pendingProfileCredentials[name];
    } catch (error) {
      showNotice(`Settings saved, but credentials for ${name} were not saved: ${error.message}`, true);
      updateUnsavedActions();
      return;
    }
  }
  renderScriptProfiles();

  const telegramChanged = telegramChatIdsInput.value !== savedTelegramChatIds
    || telegramBotTokenInput.value !== ''
    || clearTelegramTokenInput.checked;
  let telegramRestarted = false;
  if (telegramChanged) {
    try {
      const settings = await api('/api/telegram-settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          bot_token: telegramBotTokenInput.value,
          allowed_chat_ids: telegramChatIdsInput.value,
          clear_bot_token: clearTelegramTokenInput.checked,
        }),
      });
      telegramBotTokenInput.value = '';
      clearTelegramTokenInput.checked = false;
      telegramBotTokenInput.disabled = false;
      telegramChatIdsInput.value = settings.allowed_chat_ids;
      telegramStatus.textContent = settings.enabled ? 'BOT ENABLED' : 'BOT NOT CONFIGURED';
      telegramStatus.classList.toggle('telegram', settings.enabled);
      telegramTokenStatus.textContent = settings.bot_token_configured
        ? 'A token is saved. Leave this field blank to keep it, or enter a new token to replace it.'
        : 'No bot token is saved. Enter a token from BotFather to configure the bot.';
      savedTelegramChatIds = settings.allowed_chat_ids;
      telegramRestarted = settings.restarted;
    } catch (error) {
      telegramFormError.textContent = error.message;
      telegramFormError.hidden = false;
      updateUnsavedActions();
      showNotice('Device settings were saved, but Telegram settings were not saved.', true);
      return;
    }
  }
  updateUnsavedActions();
  showNotice(telegramRestarted
    ? 'Settings saved and the Telegram bot was restarted.'
    : 'Settings saved.');
});

changePasswordForm.addEventListener('submit', async event => {
  event.preventDefault();
  changePasswordError.hidden = true;
  if (newPasswordInput.value !== confirmPasswordInput.value) {
    changePasswordError.textContent = 'The passwords do not match.';
    changePasswordError.hidden = false;
    confirmPasswordInput.focus();
    return;
  }
  try {
    await api('/api/auth/password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: newPasswordInput.value }),
    });
    changePasswordForm.reset();
    updateUnsavedActions();
    showNotice('Password changed.');
  } catch (error) {
    changePasswordError.textContent = error.message;
    changePasswordError.hidden = false;
  }
});

document.querySelector('#logout-button').addEventListener('click', async () => {
  if (hasUnsavedChanges() && !window.confirm('You have unsaved changes. Sign out and discard them?')) return;
  try {
    await api('/api/auth/logout', { method: 'POST' });
    allowNavigation = true;
    window.location.assign('/');
  } catch (error) {
    showNotice(error.message, true);
  }
});

clearTelegramTokenInput.addEventListener('change', () => {
  if (clearTelegramTokenInput.checked) telegramBotTokenInput.value = '';
  telegramBotTokenInput.disabled = clearTelegramTokenInput.checked;
  updateUnsavedActions();
});

settingsForm.addEventListener('input', updateUnsavedActions);
settingsForm.addEventListener('change', updateUnsavedActions);
changePasswordForm.addEventListener('input', updateUnsavedActions);

settingsBackButton.addEventListener('click', () => {
  if (window.history.length > 1) window.history.back();
  else window.location.assign('/');
});

settingsCancelButton.addEventListener('click', discardChanges);

function renderScriptProfiles() {
  scriptProfileList.replaceChildren();
  const names = Object.keys(scriptProfiles).sort();
  if (!names.length) {
    const empty = document.createElement('p');
    empty.className = 'commands-empty';
    empty.textContent = 'No script profiles yet.';
    scriptProfileList.append(empty);
    return;
  }
  names.forEach(name => {
    const item = document.createElement('article');
    item.className = 'script-profile-item';
    const info = document.createElement('div');
    const title = document.createElement('strong');
    title.textContent = name;
    const details = document.createElement('p');
    details.className = 'field-hint';
    const status = profileCredentialStatus.get(name) || {};
    const pending = pendingProfileCredentials[name] || {};
    const hasKey = pending.clear_private_key ? false : Boolean(pending.private_key || status.private_key_configured);
    const hasPassword = pending.clear_password ? false : Boolean(pending.password || status.password_configured);
    details.textContent = [
      hasKey ? 'SSH key saved' : 'No SSH key',
      hasPassword ? 'Password saved' : 'No password',
    ].join(' · ');
    info.append(title, details);
    const actions = document.createElement('div');
    actions.className = 'program-actions';
    const edit = document.createElement('button');
    edit.type = 'button';
    edit.className = 'small-button';
    edit.textContent = 'Edit';
    edit.addEventListener('click', () => openScriptProfile(name));
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'small-button remove-button';
    remove.textContent = 'Remove';
    remove.addEventListener('click', () => removeScriptProfile(name));
    actions.append(edit, remove);
    item.append(info, actions);
    scriptProfileList.append(item);
  });
}

function openScriptProfile(name = null) {
  profileEditorName = name;
  profileEditorDirty = false;
  scriptProfileForm.reset();
  scriptProfileForm.elements.name.disabled = Boolean(name);
  document.querySelector('#script-profile-dialog-title').textContent = name ? 'Edit profile' : 'Add profile';
  document.querySelector('#delete-script-profile').hidden = !name;
  scriptProfileNameInput.value = name || '';
  scriptProfileConfigInput.value = name ? JSON.stringify(scriptProfiles[name], null, 2) : '{}';
  scriptProfilePrivateKeyInput.value = '';
  scriptProfilePasswordInput.value = '';
  const status = profileCredentialStatus.get(name) || {};
  clearProfileKeyOption.hidden = !status.private_key_configured;
  clearProfilePasswordOption.hidden = !status.password_configured;
  scriptProfileKeyStatus.textContent = status.private_key_configured
    ? 'A private key is saved. Select a file to replace it.'
    : 'No private key is saved. Do not select a .pub file.';
  scriptProfileFormError.hidden = true;
  scriptProfileDialog.showModal();
  updateUnsavedActions();
}

async function applyScriptProfile(event) {
  event.preventDefault();
  scriptProfileFormError.hidden = true;
  const name = scriptProfileNameInput.value.trim();
  if (!/^[a-z][a-z0-9_-]{0,31}$/.test(name)) {
    scriptProfileFormError.textContent = 'Use a lowercase letter followed by lowercase letters, numbers, underscores, or hyphens.';
    scriptProfileFormError.hidden = false;
    return;
  }
  if (!profileEditorName && name in scriptProfiles) {
    scriptProfileFormError.textContent = 'A profile with this name already exists.';
    scriptProfileFormError.hidden = false;
    return;
  }
  let config;
  try {
    config = JSON.parse(scriptProfileConfigInput.value);
    if (!config || typeof config !== 'object' || Array.isArray(config)) throw new Error('Enter a JSON object.');
  } catch (error) {
    scriptProfileFormError.textContent = `Profile settings must be valid JSON: ${error.message}`;
    scriptProfileFormError.hidden = false;
    return;
  }
  const credentialChanges = { ...(pendingProfileCredentials[name] || {}) };
  const keyFile = scriptProfilePrivateKeyInput.files[0];
  if (keyFile) {
    const privateKey = await keyFile.text();
    const firstLine = privateKey.trim().split(/\r?\n/, 1)[0];
    if (!firstLine.startsWith('-----BEGIN ') || !firstLine.includes('PRIVATE KEY-----')) {
      scriptProfileFormError.textContent = 'Select an SSH private key file, not a .pub public key.';
      scriptProfileFormError.hidden = false;
      return;
    }
    credentialChanges.private_key = privateKey;
    delete credentialChanges.clear_private_key;
  }
  if (scriptProfilePasswordInput.value) {
    credentialChanges.password = scriptProfilePasswordInput.value;
    delete credentialChanges.clear_password;
  }
  if (clearProfileKeyInput.checked) {
    if (!keyFile) credentialChanges.clear_private_key = true;
  }
  if (clearProfilePasswordInput.checked) {
    if (!scriptProfilePasswordInput.value) credentialChanges.clear_password = true;
  }
  scriptProfiles[name] = config;
  if (Object.keys(credentialChanges).length) pendingProfileCredentials[name] = credentialChanges;
  scriptProfileDialog.close();
  profileEditorDirty = false;
  renderScriptProfiles();
  updateUnsavedActions();
}

function removeScriptProfile(name) {
  if (!window.confirm(`Remove the ${name} profile and its saved credentials?`)) return false;
  delete scriptProfiles[name];
  pendingProfileCredentials[name] = { clear_private_key: true, clear_password: true };
  renderScriptProfiles();
  updateUnsavedActions();
  return true;
}

document.querySelector('#add-script-profile').addEventListener('click', () => openScriptProfile());
document.querySelector('#close-script-profile-dialog').addEventListener('click', () => {
  scriptProfileDialog.close();
  profileEditorDirty = false;
  updateUnsavedActions();
});
document.querySelector('#cancel-script-profile').addEventListener('click', () => {
  scriptProfileDialog.close();
  profileEditorDirty = false;
  updateUnsavedActions();
});
document.querySelector('#delete-script-profile').addEventListener('click', () => {
  if (!profileEditorName || !removeScriptProfile(profileEditorName)) return;
  scriptProfileDialog.close();
  profileEditorDirty = false;
  updateUnsavedActions();
});
scriptProfileForm.addEventListener('input', () => {
  profileEditorDirty = true;
  updateUnsavedActions();
});
scriptProfileForm.addEventListener('change', () => {
  profileEditorDirty = true;
  updateUnsavedActions();
});
scriptProfileForm.addEventListener('submit', applyScriptProfile);

async function initialize() {
  const authStatus = await api('/api/auth/status');
  if (!authStatus.authenticated || authStatus.must_change_password) {
    window.location.assign('/');
    return;
  }
  if (!authStatus.enabled) {
    accountSettingsNotice.textContent = 'Dashboard password protection is not configured, so password changes are unavailable.';
    accountSettingsNotice.hidden = false;
    newPasswordInput.disabled = true;
    confirmPasswordInput.disabled = true;
    changePasswordSubmit.disabled = true;
  }
  await Promise.all([loadDeviceSettings(), loadTelegramSettings(), loadScriptProfiles()]);
  settingsLoaded = true;
  renderScriptProfiles();
  updateUnsavedActions();
}

window.addEventListener('beforeunload', event => {
  if (!hasUnsavedChanges() || allowNavigation) return;
  event.preventDefault();
  event.returnValue = '';
});

initialize().catch(error => showNotice(error.message, true));
