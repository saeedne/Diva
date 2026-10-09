(() => {
  if (!('serviceWorker' in navigator)) return;
  navigator.serviceWorker.register('/service-worker.js').catch(() => {});

  const standalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
  if (standalone || sessionStorage.getItem('divar-install-dismissed') === '1') return;

  let installPrompt = null;
  let isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent) && !window.MSStream;
  const panel = document.createElement('aside');
  panel.className = 'pwa-install';
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', 'نصب وب‌اپ');
  panel.innerHTML = '<div class="pwa-install__copy"><strong>نصب وب‌اپ دیوار</strong><span class="pwa-install__message"></span></div><div class="pwa-install__actions"><button class="pwa-install__button" type="button"></button><button class="pwa-install__close" type="button" aria-label="بستن پیام نصب">×</button></div>';
  document.body.append(panel);
  const message = panel.querySelector('.pwa-install__message');
  const installButton = panel.querySelector('.pwa-install__button');
  const closeButton = panel.querySelector('.pwa-install__close');
  const show = () => requestAnimationFrame(() => panel.classList.add('is-visible'));
  const dismiss = () => {
    panel.classList.remove('is-visible');
    sessionStorage.setItem('divar-install-dismissed', '1');
  };

  const setManualInstructions = () => {
    installButton.textContent = isIOS ? 'راهنمای نصب' : 'راهنمای نصب';
    message.textContent = isIOS
      ? 'از دکمهٔ اشتراک‌گذاری Safari، «افزودن به صفحهٔ اصلی» را انتخاب کن.'
      : 'از منوی مرورگر گزینهٔ «افزودن به صفحهٔ اصلی» یا «نصب برنامه» را بزن.';
    installButton.onclick = () => {
      message.textContent = isIOS
        ? 'در Safari روی اشتراک‌گذاری بزن، سپس «افزودن به صفحهٔ اصلی» را انتخاب کن.'
        : 'منوی مرورگر را باز کن و «نصب برنامه» یا «افزودن به صفحهٔ اصلی» را انتخاب کن.';
      installButton.hidden = true;
    };
  };

  setManualInstructions();
  window.addEventListener('beforeinstallprompt', event => {
    event.preventDefault();
    installPrompt = event;
    installButton.textContent = 'نصب برنامه';
    installButton.hidden = false;
    message.textContent = 'برای دسترسی سریع، برنامه را روی گوشی نصب کن.';
    installButton.onclick = async () => {
      if (!installPrompt) return;
      installPrompt.prompt();
      await installPrompt.userChoice;
      installPrompt = null;
      dismiss();
    };
  });
  closeButton.addEventListener('click', dismiss);
  window.addEventListener('appinstalled', () => panel.remove());
  if (window.matchMedia('(max-width: 760px)').matches) setTimeout(show, 900);
})();
