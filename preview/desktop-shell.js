(() => {
  const desktop = window.atradeDesktop;
  const topbar = document.querySelector('.topbar');
  if (!desktop?.windowAction || !topbar) return;

  document.documentElement.classList.add('desktop-shell');
  const controls = document.createElement('div');
  controls.className = 'window-controls';
  controls.setAttribute('role', 'group');
  controls.setAttribute('aria-label', '窗口控制');
  const status = document.createElement('span');
  status.className = 'desktop-window-status';
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');

  const paths = {
    minimize: 'M4 10h12',
    maximize: 'M4.5 4.5h11v11h-11z',
    restore: 'M7 4.5h8.5V13 M4.5 7H13v8.5H4.5z',
    close: 'M5 5l10 10M15 5L5 15',
  };
  function setIcon(button, name) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 20 20');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    const path = document.createElementNS(svg.namespaceURI, 'path');
    path.setAttribute('d', paths[name]);
    svg.append(path);
    button.replaceChildren(svg);
  }
  function makeButton(action, label, icon) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'window-control';
    button.dataset.windowAction = action;
    button.setAttribute('aria-label', label);
    button.title = label;
    setIcon(button, icon);
    button.addEventListener('click', async () => {
      try {
        const result = await desktop.windowAction(action);
        if (!result?.ok) throw new Error('Window action failed');
        status.textContent = '';
      } catch {
        status.textContent = '窗口操作未完成，请重试。';
      }
    });
    controls.append(button);
    return button;
  }
  makeButton('minimize', '最小化', 'minimize');
  const maximize = makeButton('toggle-maximize', '最大化', 'maximize');
  makeButton('close', '关闭窗口', 'close');
  topbar.append(controls, status);

  let revision = -1;
  function updateState(state) {
    if (!state || state.revision < revision) return;
    revision = state.revision;
    const expanded = state.maximized || state.fullscreen;
    const label = expanded ? '还原窗口' : '最大化';
    maximize.setAttribute('aria-label', label);
    maximize.title = label;
    setIcon(maximize, expanded ? 'restore' : 'maximize');
    document.documentElement.classList.toggle('window-maximized', expanded);
  }
  const unsubscribe = desktop.onWindowState(updateState);
  desktop.getWindowState().then(updateState).catch(() => {});
  window.addEventListener('pagehide', unsubscribe, { once: true });

  // Include window controls at the boundary of the existing modal focus loop.
  // App content stays outside that loop, while the desktop can still be closed by keyboard.
  document.addEventListener('keydown', event => {
    if (event.key !== 'Tab') return;
    const modal = document.querySelector('.modal');
    if (!modal) return;
    const items = [...modal.querySelectorAll('button,input,select,textarea,a[href],[tabindex]')]
      .filter(element => !element.disabled && element.tabIndex >= 0 && element.getClientRects().length);
    const buttons = [...controls.querySelectorAll('button')];
    const active = document.activeElement;
    const index = buttons.indexOf(active);
    let next;
    if (index >= 0) {
      next = event.shiftKey ? buttons[index - 1] || items.at(-1) || buttons.at(-1)
        : buttons[index + 1] || items[0] || buttons[0];
    } else if (event.shiftKey && active === items[0]) next = buttons.at(-1);
    else if (!event.shiftKey && active === items.at(-1)) next = buttons[0];
    if (next) {
      event.preventDefault();
      event.stopImmediatePropagation();
      next.focus();
    }
  }, true);
})();
