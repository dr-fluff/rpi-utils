const list = document.querySelector('#program-list');
const notice = document.querySelector('#notice');
const dialog = document.querySelector('#program-dialog');
const form = document.querySelector('#program-form');
const editDialog = document.querySelector('#edit-program-dialog');
const editForm = document.querySelector('#edit-program-form');
const editProgramName = document.querySelector('#edit-program-name');
const editProgramUrl = document.querySelector('#edit-program-url');
const editProgramError = document.querySelector('#edit-program-error');
const editRemoveButton = document.querySelector('#edit-remove-button');
const runningDialog = document.querySelector('#running-dialog');
const runningForm = document.querySelector('#running-form');
const runningProcessName = document.querySelector('#running-process-name');
const runningProcessSearch = document.querySelector('#running-process-search');
const runningProcessPid = document.querySelector('#running-process-pid');
const runningProcessList = document.querySelector('#running-process-list');
const runningProcessEmpty = document.querySelector('#running-process-empty');
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
let authActive = false;
let dashboardStarted = false;
let updateCheckInProgress = false;
let dashboardIntervals = [];
let terminalInstance;
let terminalSocket;

function setIconButton(button, icon, label) {
  button.classList.add('icon-button');
  button.title = label;
  button.setAttribute('aria-label', label);
  const iconElement = document.createElement('span');
  iconElement.className = `fa-icon fa-${icon}`;
  iconElement.setAttribute('aria-hidden', 'true');
  button.replaceChildren(iconElement);
}

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
  terminalSocket?.close();
  terminalInstance?.dispose();
  terminalSocket = null;
  terminalInstance = null;
  loginError.textContent = message;
  loginError.hidden = !message;
  loginPassword.focus();
}

function showPasswordChange(required) {
  authActive = true;
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
  initializeDashboard();
}

async function initializeAuth() {
  try {
    const status = await request('/api/auth/status');
    terminalButton.hidden = !status.terminal_available;
    if (status.authenticated && status.must_change_password) {
      showPasswordChange(true);
    } else if (status.authenticated) {
      showDashboard();
    } else {
      showLogin();
    }
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
    showDashboard();
    showNotice('Password changed.');
  } catch (error) {
    passwordError.textContent = error.message;
    passwordError.hidden = false;
  }
});

document.querySelector('#cancel-password-change').addEventListener('click', showDashboard);

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
        const editButton = document.createElement('button');
        editButton.className = 'small-button';
        setIconButton(editButton, 'pen-to-square', 'Edit');
        editButton.addEventListener('click', () => openEditDialog(entry.program));
        actions.append(state, button, editButton);
        row.append(main, actions);
        entry = { row, indicator, name, command, serviceLink, state, button, editButton, program };
        programRows.set(program.id, entry);
      }
      entry.program = program;
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
      setIconButton(entry.button, program.running ? 'stop' : 'play', program.running ? 'Stop' : 'Start');
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
    editDialog.close();
    showNotice(result.message);
    await refresh();
  } catch (error) {
    showNotice(error.message);
  }
}

function openEditDialog(program) {
  editForm.dataset.programId = program.id;
  editForm.dataset.programName = program.name;
  editProgramName.value = program.name;
  editProgramUrl.value = program.url || '';
  editProgramError.hidden = true;
  editDialog.showModal();
  editProgramName.focus();
}

editForm.addEventListener('submit', async event => {
  event.preventDefault();
  editProgramError.hidden = true;
  const programId = editForm.dataset.programId;
  if (!programId) return;
  const values = Object.fromEntries(new FormData(editForm));
  try {
    await request(`/api/programs/${encodeURIComponent(programId)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: values.name, url: values.url || null }),
    });
    editDialog.close();
    showNotice('Program settings saved.');
    await refresh();
  } catch (error) {
    editProgramError.textContent = error.message;
    editProgramError.hidden = false;
  }
});

editRemoveButton.addEventListener('click', () => {
  const programId = editForm.dataset.programId;
  const programName = editForm.dataset.programName;
  if (programId && programName) void removeProgram(programId, programName);
});

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
  } catch (error) {
    showNotice(`Could not load commands: ${error.message}`);
  }
}

document.querySelector('#refresh-button').addEventListener('click', refresh);
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
    setIconButton(updateBannerAction, 'download', 'Update now');
    updateBannerAction.dataset.action = 'update';
    updateBanner.hidden = false;
  } catch (error) {
    updateBanner.classList.add('error');
    updateBannerMessage.textContent = `Could not check for updates: ${error.message}`;
    setIconButton(updateBannerAction, 'arrows-rotate', 'Retry update check');
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

document.querySelector('#add-button').addEventListener('click', () => dialog.showModal());
document.querySelector('#close-dialog').addEventListener('click', () => dialog.close());
document.querySelector('#cancel-button').addEventListener('click', () => dialog.close());
document.querySelector('#close-edit-dialog').addEventListener('click', () => editDialog.close());
document.querySelector('#cancel-edit-button').addEventListener('click', () => editDialog.close());
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
    item.title = `Select process ${process.command.join(' ')} (PID ${process.pid})`;
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
  setIconButton(button, 'arrows-rotate', 'Refreshing global IP…');
  try {
    const result = await runCommand('ip');
    globalIp.textContent = result.global_ip;
    setIconButton(button, 'arrows-rotate', 'Force refresh global IP');
  } catch (error) {
    globalIp.textContent = 'Unavailable';
    setIconButton(button, 'arrows-rotate', 'Retry global IP lookup');
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
    if (terminalSocket?.readyState === WebSocket.OPEN) {
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
    if (terminalSocket?.readyState === WebSocket.OPEN) {
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

document.querySelector('#close-terminal').addEventListener('click', () => terminalDialog.close());
terminalDialog.addEventListener('close', () => {
  terminalSocket?.close(1000);
  terminalSocket = null;
  terminalInstance?.dispose();
  terminalInstance = null;
});

function initializeDashboard() {
  if (dashboardStarted) return;
  dashboardStarted = true;
  commandsLoaded = loadCommands();
  commandsLoaded.then(refreshGlobalIp);
  checkForUpdates();
  refresh();
  dashboardIntervals.push(window.setInterval(refresh, 10000));
  dashboardIntervals.push(window.setInterval(checkForUpdates, 5 * 60 * 1000));
}
initializeAuth();