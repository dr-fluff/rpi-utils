const list = document.querySelector('#program-list');
const notice = document.querySelector('#notice');
const dialog = document.querySelector('#program-dialog');
const form = document.querySelector('#program-form');

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
  try {
    const { programs } = await request('/api/status');
    document.querySelector('#program-count').textContent = programs.length;
    document.querySelector('#active-count').textContent = programs.filter(program => program.running).length;
    if (!programs.length) {
      list.innerHTML = '<div class="empty-state"><strong>No programs yet</strong>Add a program to start managing processes on this Pi.</div>';
      return;
    }
    list.replaceChildren(...programs.map(program => {
      const row = document.createElement('article');
      row.className = 'program-row';
      const main = document.createElement('div');
      main.className = 'program-main';
      const indicator = document.createElement('span');
      indicator.className = `program-indicator${program.running ? ' running' : ''}`;
      const text = document.createElement('div');
      const name = document.createElement('div');
      name.className = 'program-name';
      name.textContent = program.name;
      const command = document.createElement('div');
      command.className = 'program-command';
      command.textContent = program.command.join(' ');
      text.append(name, command);
      main.append(indicator, text);
      const actions = document.createElement('div');
      actions.className = 'program-actions';
      const state = document.createElement('span');
      state.className = `program-state${program.running ? ' running' : ''}`;
      state.textContent = program.running ? `RUNNING${program.pid ? ` · ${program.pid}` : ''}` : 'STOPPED';
      const button = document.createElement('button');
      button.className = `small-button${program.running ? ' stop' : ''}`;
      button.textContent = program.running ? 'Stop' : 'Start';
      button.addEventListener('click', () => control(program.id, program.running));
      actions.append(state, button);
      row.append(main, actions);
      return row;
    }));
  } catch (error) {
    showNotice(error.message);
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
document.querySelector('#ip-button').addEventListener('click', async () => {
  const button = document.querySelector('#ip-button');
  button.disabled = true;
  button.textContent = 'Checking…';
  try {
    const { ip } = await request('/api/ip');
    document.querySelector('#global-ip').textContent = ip;
    button.textContent = 'Refresh address ↗';
  } catch (error) {
    showNotice(error.message);
    button.textContent = 'Retry address ↗';
  } finally {
    button.disabled = false;
  }
});
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

function updateClock() {
  document.querySelector('#clock').textContent = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date());
}
updateClock();
window.setInterval(updateClock, 1000);
refresh();
window.setInterval(refresh, 10000);