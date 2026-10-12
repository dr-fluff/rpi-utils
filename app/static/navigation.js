const navigationRoot = document.querySelector('#site-navigation');

async function loadNavigation() {
  try {
    const response = await fetch('/static/navigation.html');
    if (!response.ok) throw new Error(`Navigation request failed with status ${response.status}`);
    navigationRoot.innerHTML = await response.text();

    const page = navigationRoot.dataset.page;
    const labels = {
      dashboard: 'LOCAL DEVICE',
      commands: 'DEVICE COMMANDS',
      settings: 'DEVICE SETTINGS',
    };
    document.querySelector('#navigation-page-label').textContent = labels[page] || 'DEVICE';
    const activeLink = document.querySelector(`[data-page-link="${page}"]`);
    activeLink?.setAttribute('aria-current', 'page');

    const clockContainer = document.querySelector('#navigation-clock');
    if (navigationRoot.dataset.showClock === 'true') {
      clockContainer.hidden = false;
      const updateClock = () => {
        document.querySelector('#clock').textContent = new Intl.DateTimeFormat(undefined, {
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
          hour12: false,
        }).format(new Date());
      };
      updateClock();
      window.setInterval(updateClock, 1000);
    }
  } catch (error) {
    console.error('Could not load shared navigation.', error);
    navigationRoot.textContent = 'Navigation is unavailable. Reload this page to try again.';
    navigationRoot.classList.add('navigation-error');
  }
}

loadNavigation();
