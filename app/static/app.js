const list = document.querySelector('#program-list');
const notice = document.querySelector('#notice');
const dialog = document.querySelector('#program-dialog');
const form = document.querySelector('#program-form');
const runningDialog = document.querySelector('#running-dialog');
const runningForm = document.querySelector('#running-form');
const runningProcessName = document.querySelector('#running-process-name');
const runningProcessSearch = document.querySelector('#running-process-search');
const runningProcessPid = document.querySelector('#running-process-pid');
const runningProcessList = document.querySelector('#running-process-list');
const runningProcessEmpty = document.querySelector('#running-process-empty');
const commandList = document.querySelector('#command-list');
const updateBanner = document.querySelector('#update-banner');
const updateBannerMessage = document.querySelector('#update-banner-message');
const updateBannerAction = document.querySelector('#update-banner-action');
const dashboardApp = document.querySelector('#dashboard-app');
const loginScreen = document.querySelector('#login-screen');
const loginForm = document.querySelector('#login-form');
const loginError = document.querySelector('#login-error');
const loginPassword = document.querySelector('#login-password');
const passwordScreen = document.querySelector('#password-screen');
const passwordForm = document.querySelector('#password-form');
const passwordHeading = document.querySelector('#password-heading');
const passwordIntro = document.querySelector('#password-intro');
const newPassword = document.querySelector('#new-password');
const confirmPassword = document.querySelector('#confirm-password');
const passwordError = document.querySelector('#password-error');
const terminalButton = document.querySelector('#terminal-button');
const terminalDialog = document.querySelector('#terminal-dialog');
const terminalElement = document.querySelector('#terminal');
const terminalStatus = document.querySelector('#terminal-status');
let availableProcesses = [];
let commandDefinitions = new Map();
let commandsLoaded = Promise.resolve();
const programRows = new Map();
let refreshInProgress = false;
let updateCheckInProgress = false;
let authEnabled = false;
let authActive = false;
let dashboardStarted = false;
let dashboardIntervals = [];
let terminalInstance;
let terminalSocket;

async function request(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (response.status === 401 && authActive && !url.startsWith('/api/auth/')) {
    showLogin('Your session expired. Sign in again.');
  }
  if (!response.ok) throw new Error(data.detail || 'Request failed');
  return data;
}

function showLogin(message = '') {
  authActive = false;
  dashboardIntervals.forEach(window.clearInterval);
  dashboardIntervals = [];
  dashboardApp.hidden = true;
  passwordScreen.hidden = true;
  loginScreen.hidden = false;
  if (terminalSocket) terminalSocket.close();
  if (terminalDialog.open) terminalDialog.close();
  terminalInstance?.dispose();
  terminalSocket = null;
  terminalInstance = null;
  loginError.textContent = message;
  loginError.hidden = !message;
  loginPassword.focus();
}

function showPasswordChange(required) {
  dashboardApp.hidden = true;
  loginScreen.hidden = true;
  passwordScreen.hidden = false;
  passwordHeading.textContent = required ? 'Set your password.' : 'Change password.';
  passwordIntro.textContent = required
    ? 'For security, replace the temporary install password before using the dashboard.'
    : 'Choose a new password with at least 12 characters.';
  document.querySelector('#cancel-password-change').hidden = required;
  passwordError.hidden = true;
  passwordForm.reset();
  newPassword.focus();
}

function showDashboard() {
  authActive = true;
  loginScreen.hidden = true;
  passwordScreen.hidden = true;
  dashboardApp.hidden = false;
  document.querySelector('#logout-button').hidden = !authEnabled;
  document.querySelector('#change-password-button').hidden = !authEnabled;
  initializeDashboard();
}

async function initializeAuth() {
  try {
    const status = await request('/api/auth/status');
    authEnabled = status.enabled;
    terminalButton.hidden = !status.terminal_available;
    if (status.authenticated && status.must_change_password) showPasswordChange(true);
    else if (status.authenticated) showDashboard();
    else showLogin();
  } catch (error) {
    showLogin(`Could not check sign-in status: ${error.message}`);
  }
}

loginForm.addEventListener('submit', async event => {
  event.preventDefault();
  loginError.hidden = true;
  try {
    await request('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: loginPassword.value }),
    });
    loginPassword.value = '';
    window.location.reload();
  } catch (error) {
    loginError.textContent = error.message;
    loginError.hidden = false;
  }
});

passwordForm.addEventListener('submit', async event => {
  event.preventDefault();
  passwordError.hidden = true;
  if (newPassword.value !== confirmPassword.value) {
    passwordError.textContent = 'The passwords do not match.';
    passwordError.hidden = false;
    confirmPassword.focus();
    return;
  }
  try {
    await request('/api/auth/password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: newPassword.value }),
    });
    passwordForm.reset();
    showNotice('Password changed.');
    showDashboard();
  } catch (error) {
    passwordError.textContent = error.message;
    passwordError.hidden = false;
  }
});

document.querySelector('#change-password-button').addEventListener('click', () => showPasswordChange(false));
document.querySelector('#cancel-password-change').addEventListener('click', showDashboard);

document.querySelector('#logout-button').addEventListener('click', async () => {
  try {
    await request('/api/auth/logout', { method: 'POST' });
    showLogin();
  } catch (error) {
    showNotice(error.message);
  }
});

function showNotice(message) {
  notice.textContent = message;
  window.clearTimeout(showNotice.timer);
  showNotice.timer = window.setTimeout(() => { notice.textContent = ''; }, 5000);
}

async function refresh() {
  if (refreshInProgress) return;
  refreshInProgress = true;
  try {
    const { programs } = await request('/api/status');
    document.querySelector('#program-count').textContent = programs.length;
    document.querySelector('#active-count').textContent = programs.filter(program => program.running).length;
    if (!programs.length) {
      for (const entry of programRows.values()) entry.row.remove();
      programRows.clear();
      if (!list.querySelector('.empty-state')) {
        list.replaceChildren();
        const empty = document.createElement('div');
        empty.className = 'empty-state';
        const title = document.createElement('strong');
        title.textContent = 'No programs yet';
        empty.append(title, document.createTextNode('Add a program to start managing processes on this Pi.'));
        list.append(empty);
      }
      return;
    }
    list.querySelector('.empty-state')?.remove();
    const activeIds = new Set(programs.map(program => program.id));
    for (const [id, entry] of programRows) {
      if (!activeIds.has(id)) {
        entry.row.remove();
        programRows.delete(id);
      }
    }
    programs.forEach((program, index) => {
      let entry = programRows.get(program.id);
      if (!entry) {
        const row = document.createElement('article');
        row.className = 'program-row';
        const main = document.createElement('div');
        main.className = 'program-main';
        const indicator = document.createElement('span');
        indicator.className = 'program-indicator';
        const text = document.createElement('div');
        const name = document.createElement('div');
        name.className = 'program-name';
        const command = document.createElement('div');
        command.className = 'program-command';
        const serviceLink = document.createElement('a');
        serviceLink.className = 'program-link';
        serviceLink.target = '_blank';
        serviceLink.rel = 'noopener noreferrer';
        text.append(name, command);
        text.append(serviceLink);
        main.append(indicator, text);
        const actions = document.createElement('div');
        actions.className = 'program-actions';
        const state = document.createElement('span');
        state.className = 'program-state';
        const button = document.createElement('button');
        button.className = 'small-button';
        button.addEventListener('click', () => control(program.id, button.dataset.running === 'true'));
        const removeButton = document.createElement('button');
        removeButton.className = 'small-button remove-button';
        removeButton.textContent = 'Remove';
        removeButton.addEventListener('click', () => removeProgram(program.id, program.name));
        actions.append(state, button, removeButton);
        row.append(main, actions);
        entry = { row, indicator, name, command, serviceLink, state, button, removeButton };
        programRows.set(program.id, entry);
      }
      entry.name.textContent = program.name;
      entry.command.textContent = program.command.join(' ');
      entry.serviceLink.hidden = !program.url;
      if (program.url) {
        entry.serviceLink.href = program.url;
        entry.serviceLink.textContent = `Open web interface ↗`;
      } else {
        entry.serviceLink.removeAttribute('href');
        entry.serviceLink.textContent = '';
      }
      entry.indicator.classList.toggle('running', program.running);
      entry.state.classList.toggle('running', program.running);
      entry.state.textContent = program.running ? `RUNNING${program.pid ? ` · ${program.pid}` : ''}` : 'STOPPED';
      entry.button.classList.toggle('stop', program.running);
      entry.button.textContent = program.running ? 'Stop' : 'Start';
      entry.button.dataset.running = String(program.running);
      const currentRow = list.children[index];
      if (currentRow !== entry.row) list.insertBefore(entry.row, currentRow || null);
    });
  } catch (error) {
    showNotice(error.message);
  } finally {
    refreshInProgress = false;
  }
}

async function control(id, running) {
  try {
    await runCommand(running ? 'stop' : 'start', [id]);
    await refresh();
  } catch (error) {
    showNotice(error.message);
  }
}

async function removeProgram(id, name) {
  if (!window.confirm(`Remove "${name}" from the program list? If it is running, it will keep running.`)) return;
  try {
    const result = await request(`/api/programs/${encodeURIComponent(id)}`, { method: 'DELETE' });
    showNotice(result.message);
    await refresh();
  } catch (error) {
    showNotice(error.message);
  }
}

async function runCommand(name, args = []) {
  await commandsLoaded;
  const command = commandDefinitions.get(name);
  if (!command) throw new Error(`Command /${name} is not available`);
  if (command.requires_confirmation && !window.confirm(`Run /${name}? ${command.description}`)) return null;
  return request(`/api/commands/${encodeURIComponent(name)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ args }),
  });
}

async function loadCommands() {
  try {
    const { commands } = await request('/api/commands');
    commandDefinitions = new Map(commands.map(command => [command.name, command]));
    commandList.replaceChildren(...commands.filter(command => !command.program_action).map(command => {
      const button = document.createElement('button');
      button.className = 'button button-quiet command-button';
      button.type = 'button';
      button.textContent = `/${command.name}`;
      button.title = command.description;
      button.addEventListener('click', async () => {
        try {
          const args = [];
          if (command.usage) {
            const argument = window.prompt(`Argument for /${command.name}: ${command.usage}`);
            if (argument === null) return;
            args.push(argument);
          }
          const result = await runCommand(command.name, args);
          if (!result) return;
          if (command.name === 'ip') {
            document.querySelector('#global-ip').textContent = result.global_ip;
          }
          showNotice(result.message);
          if (command.name === 'status') await refresh();
        } catch (error) {
          showNotice(error.message);
        }
      });
      return button;
    }));
  } catch (error) {
    showNotice(`Could not load commands: ${error.message}`);
  }
}

async function checkForUpdates() {
  if (updateCheckInProgress) return;
  updateCheckInProgress = true;
  try {
    const result = await request('/api/update-check');
    if (!result.update_available) {
      updateBanner.hidden = true;
      return;
    }
    updateBanner.classList.remove('error');
    updateBannerMessage.textContent = `Release ${result.latest_version} is available (installed ${result.current_version}).`;
    updateBannerAction.textContent = 'Update now';
    updateBannerAction.dataset.action = 'update';
    updateBanner.hidden = false;
  } catch (error) {
    updateBanner.classList.add('error');
    updateBannerMessage.textContent = `Could not check for updates: ${error.message}`;
    updateBannerAction.textContent = 'Retry';
    updateBannerAction.dataset.action = 'retry';
    updateBanner.hidden = false;
  } finally {
    updateCheckInProgress = false;
  }
}

updateBannerAction.addEventListener('click', async () => {
  if (updateBannerAction.dataset.action === 'retry') {
    await checkForUpdates();
    return;
  }
  try {
    const result = await runCommand('update');
    if (!result) return;
    updateBanner.hidden = true;
    showNotice(result.message);
    if (result.restarting) window.setTimeout(() => window.location.reload(), 5000);
    else await checkForUpdates();
  } catch (error) {
    showNotice(error.message);
  }
});

terminalButton.addEventListener('click', () => {
  if (typeof Terminal !== 'function' || typeof FitAddon === 'undefined') {
    showNotice('The terminal interface could not be loaded.');
    return;
  }
  terminalDialog.showModal();
  terminalStatus.textContent = 'Connecting…';
  terminalInstance = new Terminal({
    cursorBlink: true,
    fontFamily: 'SFMono-Regular, Menlo, monospace',
    fontSize: 13,
    theme: { background: '#111915', foreground: '#f8faf5', cursor: '#c7f36b' },
  });
  const terminal = terminalInstance;
  const fitAddon = new FitAddon.FitAddon();
  terminal.loadAddon(fitAddon);
  terminal.open(terminalElement);
  fitAddon.fit();
  const socketUrl = new URL('/api/terminal', window.location.href);
  socketUrl.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  terminalSocket = new WebSocket(socketUrl);
  const sendResize = () => {
    if (terminalSocket.readyState === WebSocket.OPEN) {
      terminalSocket.send(JSON.stringify({
        type: 'resize',
        cols: terminal.cols,
        rows: terminal.rows,
      }));
    }
  };
  terminalSocket.addEventListener('open', () => {
    terminalStatus.textContent = 'Connected';
    sendResize();
    terminal.focus();
  });
  terminalSocket.addEventListener('message', event => terminal.write(event.data));
  terminalSocket.addEventListener('close', event => {
    terminalStatus.textContent = event.code === 4403 ? 'Sign-in required' : 'Disconnected';
    if (event.code !== 1000 && terminalDialog.open) terminal.write('\r\n[Terminal disconnected]\r\n');
  });
  terminal.onData(data => {
    if (terminalSocket.readyState === WebSocket.OPEN) {
      terminalSocket.send(JSON.stringify({ type: 'input', data }));
    }
  });
  const fitTerminal = () => {
    fitAddon.fit();
    sendResize();
  };
  window.addEventListener('resize', fitTerminal);
  terminalDialog.addEventListener('close', () => window.removeEventListener('resize', fitTerminal), { once: true });
});

document.querySelector('#close-terminal').addEventListener('click', () => {
  terminalDialog.close();
});
terminalDialog.addEventListener('close', () => {
  terminalSocket?.close(1000);
  terminalSocket = null;
  terminalInstance?.dispose();
  terminalInstance = null;
});

document.querySelector('#refresh-button').addEventListener('click', refresh);
document.querySelector('#add-button').addEventListener('click', () => dialog.showModal());
document.querySelector('#close-dialog').addEventListener('click', () => dialog.close());
document.querySelector('#cancel-button').addEventListener('click', () => dialog.close());
document.querySelector('#add-running-button').addEventListener('click', async () => {
  try {
    const { processes } = await request('/api/processes');
    availableProcesses = processes;
    runningProcessSearch.value = '';
    runningProcessPid.value = '';
    runningProcessName.value = '';
    renderRunningProcesses();
    runningDialog.showModal();
  } catch (error) {
    showNotice(error.message);
  }
});
document.querySelector('#close-running-dialog').addEventListener('click', () => runningDialog.close());
document.querySelector('#cancel-running-button').addEventListener('click', () => runningDialog.close());
runningProcessSearch.addEventListener('input', renderRunningProcesses);
function renderRunningProcesses() {
  const query = runningProcessSearch.value.trim().toLowerCase();
  const filteredProcesses = availableProcesses.filter(process =>
    `${process.pid} ${process.command.join(' ')} ${process.cwd || ''}`.toLowerCase().includes(query));
  runningProcessList.replaceChildren(...filteredProcesses.map(process => {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = `process-picker-item${String(process.pid) === runningProcessPid.value ? ' selected' : ''}`;
    item.setAttribute('role', 'option');
    item.setAttribute('aria-selected', String(String(process.pid) === runningProcessPid.value));
    const command = document.createElement('strong');
    command.textContent = process.command.join(' ');
    const details = document.createElement('span');
    details.textContent = `PID ${process.pid}${process.cwd ? ` · ${process.cwd}` : ''}`;
    item.append(command, details);
    item.addEventListener('click', () => {
      runningProcessPid.value = process.pid;
      runningProcessName.value = process.command[0].split('/').pop();
      renderRunningProcesses();
    });
    return item;
  }));
  runningProcessEmpty.hidden = filteredProcesses.length > 0;
  runningProcessEmpty.textContent = availableProcesses.length
    ? 'No processes match that search.'
    : 'No unregistered running processes were found.';
}
async function refreshGlobalIp(retryCount = 0) {
  const button = document.querySelector('#ip-button');
  const globalIp = document.querySelector('#global-ip');
  button.disabled = true;
  button.textContent = 'Checking…';
  try {
    const result = await runCommand('ip');
    globalIp.textContent = result.global_ip;
    button.textContent = 'Force refresh ↗';
  } catch (error) {
    globalIp.textContent = 'Unavailable';
    button.textContent = 'Retry address ↗';
    if (retryCount < 2) {
      window.setTimeout(() => refreshGlobalIp(retryCount + 1), 1500);
    } else {
      showNotice(`Could not check global IP: ${error.message}`);
    }
  } finally {
    button.disabled = false;
  }
}
document.querySelector('#ip-button').addEventListener('click', refreshGlobalIp);
form.addEventListener('submit', async event => {
  event.preventDefault();
  const values = Object.fromEntries(new FormData(form));
  try {
    await request('/api/programs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: values.name,
        command: values.command,
        cwd: values.cwd || null,
        url: values.url || null,
      }),
    });
    form.reset();
    dialog.close();
    await refresh();
  } catch (error) {
    showNotice(error.message);
  }
});
runningForm.addEventListener('submit', async event => {
  event.preventDefault();
  const values = Object.fromEntries(new FormData(runningForm));
  try {
    await request('/api/programs/running', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: values.name, pid: Number(values.pid), url: values.url || null }),
    });
    runningDialog.close();
    await refresh();
  } catch (error) {
    showNotice(error.message);
  }
});

function updateClock() {
  document.querySelector('#clock').textContent = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date());
}
function initializeDashboard() {
  if (dashboardStarted) return;
  dashboardStarted = true;
  updateClock();
  commandsLoaded = loadCommands();
  commandsLoaded.then(refreshGlobalIp);
  checkForUpdates();
  dashboardIntervals.push(window.setInterval(updateClock, 1000));
  refresh();
  dashboardIntervals.push(window.setInterval(refresh, 10000));
  dashboardIntervals.push(window.setInterval(checkForUpdates, 5 * 60 * 1000));
}
initializeAuth();