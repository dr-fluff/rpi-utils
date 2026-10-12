(() => {
  let dialog;
  let title;
  let description;
  let status;
  let details;
  let message;

  function initialize() {
    dialog = document.createElement('dialog');
    dialog.className = 'command-output-dialog';

    const content = document.createElement('div');
    content.className = 'command-output-content';

    const heading = document.createElement('div');
    heading.className = 'dialog-heading';
    const headingText = document.createElement('div');
    const eyebrow = document.createElement('p');
    eyebrow.className = 'eyebrow';
    eyebrow.textContent = 'COMMAND OUTPUT';
    title = document.createElement('h2');
    headingText.append(eyebrow, title);
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'close-button icon-button';
    close.title = 'Close output';
    close.setAttribute('aria-label', 'Close output');
    const closeIcon = document.createElement('span');
    closeIcon.className = 'fa-icon fa-xmark';
    closeIcon.setAttribute('aria-hidden', 'true');
    close.append(closeIcon);
    close.addEventListener('click', () => dialog.close());
    heading.append(headingText, close);
    dialog.addEventListener('click', event => {
      if (event.target === dialog) dialog.close();
    });

    description = document.createElement('p');
    description.className = 'command-output-description';
    status = document.createElement('p');
    status.className = 'command-output-status';
    status.setAttribute('role', 'status');
    details = document.createElement('dl');
    details.className = 'command-output-details';
    message = document.createElement('div');
    message.className = 'command-output-message';
    message.setAttribute('aria-label', 'Command output');
    const actions = document.createElement('div');
    actions.className = 'dialog-actions';
    const done = document.createElement('button');
    done.type = 'button';
    done.className = 'button button-dark';
    done.textContent = 'Done';
    done.addEventListener('click', () => dialog.close());
    actions.append(done);

    content.append(heading, description, status, details, message, actions);
    dialog.append(content);
    document.body.append(dialog);
  }

  function displayValue(value) {
    if (typeof value === 'string') return value;
    if (value === null) return 'None';
    if (typeof value === 'object') return JSON.stringify(value, null, 2);
    return String(value);
  }

  function renderMessage(output) {
    message.replaceChildren();
    try {
      const formatted = document.createElement('pre');
      formatted.className = 'command-output-text';
      formatted.textContent = JSON.stringify(JSON.parse(output), null, 2);
      message.append(formatted);
      return;
    } catch {
      const lines = output.split(/\r?\n/).filter(line => line.trim());
      const values = lines.map(line => line.match(/^([A-Za-z][A-Za-z0-9 _./()-]{0,79})(?::\s+|=\s*)(.*)$/));
      if (lines.length && values.every(Boolean)) {
        const fields = document.createElement('dl');
        fields.className = 'command-output-details';
        values.forEach(([ , key, value ]) => {
          const field = document.createElement('div');
          const label = document.createElement('dt');
          label.textContent = key.trim();
          const data = document.createElement('dd');
          data.textContent = value;
          field.append(label, data);
          fields.append(field);
        });
        message.append(fields);
        return;
      }
    }
    const plainText = document.createElement('pre');
    plainText.className = 'command-output-text';
    plainText.textContent = output;
    message.append(plainText);
  }

  window.showCommandOutput = (name, commandDescription, result, error = null) => {
    if (!dialog) initialize();
    const succeeded = !error;
    title.textContent = `/${name}`;
    description.textContent = commandDescription || 'Command execution result';
    status.textContent = succeeded ? 'Completed successfully' : 'Command failed';
    status.classList.toggle('error', !succeeded);

    const outputFields = succeeded && result && typeof result === 'object'
      ? Object.entries(result).filter(([key, value]) => key !== 'message' && value !== undefined)
      : [];
    details.replaceChildren();
    outputFields.forEach(([key, value]) => {
      const field = document.createElement('div');
      const label = document.createElement('dt');
      label.textContent = key.replaceAll('_', ' ');
      const data = document.createElement('dd');
      data.textContent = displayValue(value);
      field.append(label, data);
      details.append(field);
    });
    details.hidden = outputFields.length === 0;

    const output = error
      ? String(error)
      : (typeof result?.message === 'string' ? result.message : 'Command completed successfully.');
    const showMessage = output && !(succeeded && ['ip', 'start', 'stop'].includes(name) && outputFields.length);
    if (showMessage) renderMessage(output);
    message.hidden = !showMessage;
    dialog.showModal();
  };
})();
