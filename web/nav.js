/* Shared navigation — injected into every page */
(function () {
  const LINKS = [
    { href: '/',             label: '📋 Inputs'   },
    { href: '/web/run.html', label: '▶  Run'      },
    { href: '/schedule',     label: '📅 Schedule' },
  ];
  const path = window.location.pathname;

  function isActive(href) {
    if (href === '/') return path === '/' || path === '/web/inputs.html';
    if (href === '/schedule') return path === '/schedule';
    return path.endsWith(href.split('/').pop());
  }

  function linkStyle(active) {
    return [
      'text-decoration:none',
      'padding:4px 12px',
      'border-radius:4px',
      'color:#fff',
      'font-size:12px',
      active ? 'font-weight:700' : 'font-weight:400',
      active ? 'background:rgba(255,203,5,.18)' : 'background:transparent',
      active ? 'border-bottom:2px solid #FFCB05' : 'border-bottom:2px solid transparent',
    ].join(';');
  }

  // If the page has the WIT global-header, add links to its right section
  const globalRight = document.querySelector('.global-right');
  if (globalRight) {
    const nav = document.createElement('nav');
    nav.style.cssText = 'display:flex;gap:4px;margin-right:8px';
    LINKS.forEach(({ href, label }) => {
      const a = document.createElement('a');
      a.href = href;
      a.style.cssText = linkStyle(isActive(href));
      a.textContent = label;
      nav.appendChild(a);
    });
    globalRight.before(nav);
    return;
  }

  // Otherwise inject a standalone top nav bar
  const nav = document.createElement('nav');
  nav.style.cssText = [
    'position:sticky', 'top:0', 'z-index:200',
    'display:flex', 'align-items:center', 'gap:6px',
    'padding:8px 20px',
    'background:#111827', 'color:#fff',
    'font-family:system-ui,sans-serif', 'font-size:13px',
    'box-shadow:0 1px 4px rgba(0,0,0,.35)',
  ].join(';');

  const logo = document.createElement('div');
  logo.style.cssText = 'width:28px;height:28px;border-radius:2px;background:#99181B;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:14px;flex-shrink:0;margin-right:8px';
  logo.textContent = 'W';
  nav.appendChild(logo);

  const title = document.createElement('span');
  title.style.cssText = 'font-weight:700;letter-spacing:.05em;text-transform:uppercase;font-size:13px;margin-right:10px';
  title.textContent = 'Class Scheduler';
  nav.appendChild(title);

  LINKS.forEach(({ href, label }) => {
    const a = document.createElement('a');
    a.href = href;
    a.style.cssText = linkStyle(isActive(href));
    a.textContent = label;
    nav.appendChild(a);
  });

  document.body.insertBefore(nav, document.body.firstChild);
})();
