const settingsForm = document.querySelector('#settings-form');
const settingsBackButton = document.querySelector('#settings-back-button');
const settingsCancelButton = document.querySelector('#settings-cancel-button');
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

function hasUnsavedChanges() {
  if (!settingsLoaded) return false;
  const current = currentDeviceSettings();
  return Object.keys(current).some(key => current[key] !== savedDeviceSettings[key])
    || telegramChatIdsInput.value !== savedTelegramChatIds
    || telegramBotTokenInput.value !== ''
    || clearTelegramTokenInput.checked
    || newPasswordInput.value !== ''
    || confirmPasswordInput.value !== '';
}

function updateUnsavedActions() {
  const unsaved = hasUnsavedChanges();
  settingsCancelButton.hidden = !unsaved;
}

function discardChanges() {
  scriptsDirectoryInput.value = savedDeviceSettings.scripts_directory;
  defaultTimeoutInput.value = savedDeviceSettings.default_timeout_seconds;
  scriptConfigInput.value = savedDeviceSettings.script_config;
  telegramChatIdsInput.value = savedTelegramChatIds;
  telegramBotTokenInput.value = '';
  telegramBotTokenInput.disabled = false;
  clearTelegramTokenInput.checked = false;
  changePasswordForm.reset();
  changePasswordError.hidden = true;
  telegramFormError.hidden = true;
  updateUnsavedActions();
  showNotice('Unsaved changes discarded.');
}

async function loadDeviceSettings() {
  const settings = await api('/api/settings');
  scriptsDirectoryInput.value = settings.scripts_directory;
  defaultTimeoutInput.value = settings.default_timeout_seconds;
  scriptConfigInput.value = JSON.stringify(settings.script_config, null, 2);
  savedDeviceSettings = currentDeviceSettings();
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
      }),
    });
    scriptsDirectoryInput.value = savedSettings.scripts_directory;
    defaultTimeoutInput.value = savedSettings.default_timeout_seconds;
    scriptConfigInput.value = JSON.stringify(savedSettings.script_config, null, 2);
    savedDeviceSettings = currentDeviceSettings();
  } catch (error) {
    showNotice(error.message, true);
    return;
  }

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
  await Promise.all([loadDeviceSettings(), loadTelegramSettings()]);
  settingsLoaded = true;
  updateUnsavedActions();
}

window.addEventListener('beforeunload', event => {
  if (!hasUnsavedChanges() || allowNavigation) return;
  event.preventDefault();
  event.returnValue = '';
});

initialize().catch(error => showNotice(error.message, true));
