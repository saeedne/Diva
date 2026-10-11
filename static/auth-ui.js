(() => {
  const nav = document.getElementById('account-nav');
  if (!nav) return;
  fetch('/auth/status', { credentials: 'same-origin' })
    .then(async response => response.ok ? response.json() : null)
    .then(account => {
      if (!account) return;
      const addLink = (label, href) => {
        const link = document.createElement('a');
        link.href = href;
        link.textContent = label;
        nav.append(link);
      };
      if (account.is_admin || account.pages.includes('search')) addLink('جست‌وجوی آگهی', '/');
      if (account.is_admin || account.pages.includes('estimate')) addLink('تخمین قیمت', '/estimate');
      if (account.is_admin) addLink('مدیریت کاربران', '/admin/users');
      for (const link of document.querySelectorAll('[data-page-link]')) {
        const page = link.dataset.pageLink;
        link.hidden = !(account.is_admin || account.pages.includes(page));
      }
      const name = document.createElement('span');
      name.className = 'account-name';
      name.textContent = 'حساب: ' + account.username;
      nav.append(name);
      const form = document.createElement('form');
      form.method = 'post';
      form.action = '/logout';
      const csrf = document.createElement('input');
      csrf.type = 'hidden';
      csrf.name = '_csrf';
      csrf.value = account.csrf;
      const button = document.createElement('button');
      button.type = 'submit';
      button.textContent = 'خروج';
      form.append(csrf, button);
      nav.append(form);
    })
    .catch(() => {});
})();
