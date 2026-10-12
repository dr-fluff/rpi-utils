const settingsForm = document.querySelector('#settings-form');
const telegramSettingsForm = document.querySelector('#telegram-settings-form');
const scriptsDirectoryInput = document.querySelector('#scripts-directory-input');
const defaultTimeoutInput = document.querySelector('#default-timeout-input');
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

async function loadDeviceSettings() {
  const settings = await api('/api/settings');
  scriptsDirectoryInput.value = settings.scripts_directory;
  defaultTimeoutInput.value = settings.default_timeout_seconds;
}

async function loadTelegramSettings() {
  const settings = await api('/api/telegram-settings');
  telegramChatIdsInput.value = settings.allowed_chat_ids;
  telegramStatus.textContent = settings.enabled ? 'BOT ENABLED' : 'BOT NOT CONFIGURED';
  telegramStatus.classList.toggle('telegram', settings.enabled);
  telegramTokenStatus.textContent = settings.bot_token_configured
    ? 'A token is saved. Leave this field blank to keep it, or enter a new token to replace it.'
    : 'No bot token is saved. Enter a token from BotFather to configure the bot.';
}

settingsForm.addEventListener('submit', async event => {
  event.preventDefault();
  const formData = new FormData(settingsForm);
  try {
    await api('/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        scripts_directory: formData.get('scripts_directory'),
        default_timeout_seconds: Number(formData.get('default_timeout_seconds')),
      }),
    });
    showNotice('Device settings saved.');
  } catch (error) {
    showNotice(error.message, true);
  }
});

telegramSettingsForm.addEventListener('submit', async event => {
  event.preventDefault();
  telegramFormError.hidden = true;
  const formData = new FormData(telegramSettingsForm);
  try {
    const settings = await api('/api/telegram-settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        bot_token: telegramBotTokenInput.value,
        allowed_chat_ids: formData.get('allowed_chat_ids'),
        clear_bot_token: clearTelegramTokenInput.checked,
      }),
    });
    telegramBotTokenInput.value = '';
    clearTelegramTokenInput.checked = false;
    telegramChatIdsInput.value = settings.allowed_chat_ids;
    telegramStatus.textContent = settings.enabled ? 'BOT ENABLED' : 'BOT NOT CONFIGURED';
    telegramStatus.classList.toggle('telegram', settings.enabled);
    telegramTokenStatus.textContent = settings.bot_token_configured
      ? 'A token is saved. Leave this field blank to keep it, or enter a new token to replace it.'
      : 'No bot token is saved. Enter a token from BotFather to configure the bot.';
    showNotice(settings.restarted
      ? 'Telegram settings saved and the bot was restarted.'
      : 'Telegram settings saved.');
  } catch (error) {
    telegramFormError.textContent = error.message;
    telegramFormError.hidden = false;
  }
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
    showNotice('Password changed.');
  } catch (error) {
    changePasswordError.textContent = error.message;
    changePasswordError.hidden = false;
  }
});

document.querySelector('#logout-button').addEventListener('click', async () => {
  try {
    await api('/api/auth/logout', { method: 'POST' });
    window.location.assign('/');
  } catch (error) {
    showNotice(error.message, true);
  }
});

clearTelegramTokenInput.addEventListener('change', () => {
  if (clearTelegramTokenInput.checked) telegramBotTokenInput.value = '';
  telegramBotTokenInput.disabled = clearTelegramTokenInput.checked;
});

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
}

initialize().catch(error => showNotice(error.message, true));
