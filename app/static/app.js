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
let availableProcesses = [];
const programRows = new Map();
let refreshInProgress = false;

async function request(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || 'Request failed');
  return data;
}

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
        text.append(name, command);
        main.append(indicator, text);
        const actions = document.createElement('div');
        actions.className = 'program-actions';
        const state = document.createElement('span');
        state.className = 'program-state';
        const button = document.createElement('button');
        button.className = 'small-button';
        button.addEventListener('click', () => control(program.id, button.dataset.running === 'true'));
        actions.append(state, button);
        row.append(main, actions);
        entry = { row, indicator, name, command, state, button };
        programRows.set(program.id, entry);
      }
      entry.name.textContent = program.name;
      entry.command.textContent = program.command.join(' ');
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
    await request(`/api/programs/${encodeURIComponent(id)}/${running ? 'stop' : 'start'}`, { method: 'POST' });
    await refresh();
  } catch (error) {
    showNotice(error.message);
  }
}

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
    const { ip } = await request('/api/ip');
    globalIp.textContent = ip;
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
refreshGlobalIp();
document.querySelector('#update-button').addEventListener('click', async event => {
  const button = event.currentTarget;
  if (!window.confirm('Install the latest published GitHub release and available system package upgrades?')) return;
  button.disabled = true;
  try {
    const result = await request('/api/update', { method: 'POST' });
    showNotice(result.restarting ? 'Update applied. The service is restarting.' : result.message);
  } catch (error) {
    showNotice(error.message);
  } finally {
    button.disabled = false;
  }
});
form.addEventListener('submit', async event => {
  event.preventDefault();
  const values = Object.fromEntries(new FormData(form));
  try {
    await request('/api/programs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: values.name, command: values.command, cwd: values.cwd || null }),
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
      body: JSON.stringify({ name: values.name, pid: Number(values.pid) }),
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
updateClock();
window.setInterval(updateClock, 1000);
refresh();
window.setInterval(refresh, 10000);