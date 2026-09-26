/*!
 * views.js — splits the dashboard's single long page into separate tabs.
 *
 * Every major block in index.html carries `data-view="a b"`, listing the views
 * it belongs to. Opening `#devices` (or clicking a nav link) hides every block
 * that does not belong to that view. Wrappers marked `data-view-group` are only
 * kept visible while at least one of their blocks is visible.
 *
 * The dedicated GPS map page (`?view=map`) is owned by app.js, so this script
 * stays out of the way in that mode.
 */
(() => {
  'use strict';

  if (new URLSearchParams(location.search).get('view') === 'map') return;

  const DEFAULT_VIEW = 'overview';
  const blocks = [...document.querySelectorAll('[data-view]')];
  const groups = [...document.querySelectorAll('[data-view-group]')];
  if (!blocks.length) return;

  const knownViews = new Set();
  blocks.forEach((block) => {
    block.getAttribute('data-view').split(/\s+/).filter(Boolean).forEach((view) => knownViews.add(view));
  });

  const viewLabel = (view) => document.querySelector(`.nav-link[href*="#${view}"] span:not(.nav-icon)`)?.textContent.trim()
    || document.querySelector(`.topnav-link[href*="#${view}"]`)?.textContent.trim()
    || 'Overview';

  const setActive = (view) => {
    document.querySelectorAll('.nav-link.active, .mobile-nav-link.active, .topnav-link.active').forEach((link) => {
      link.classList.remove('active');
      link.removeAttribute('aria-current');
    });
    document.querySelectorAll(`.nav-link[href*="#${view}"], .mobile-nav-link[href*="#${view}"], .topnav-link[href*="#${view}"]`)
      .forEach((link) => { link.classList.add('active'); link.setAttribute('aria-current', 'location'); });
    const breadcrumb = document.getElementById('breadcrumb-current');
    if (breadcrumb) breadcrumb.textContent = viewLabel(view);
  };

  const apply = (view) => {
    blocks.forEach((block) => {
      block.hidden = !block.getAttribute('data-view').split(/\s+/).includes(view);
    });
    groups.forEach((group) => {
      const children = [...group.querySelectorAll('[data-view]')];
      const visible = children.filter((block) => !block.hidden);
      group.hidden = visible.length === 0;
      group.classList.toggle('single-view', visible.length === 1 && children.length > 1);
    });
    document.body.dataset.view = view;
  };

  const resolveView = () => {
    const id = (location.hash || '').replace(/^#/, '');
    return id && knownViews.has(id) ? id : DEFAULT_VIEW;
  };

  const sync = () => {
    const view = resolveView();
    apply(view);
    setActive(view);
  };

  window.addEventListener('hashchange', () => {
    sync();
    window.scrollTo(0, 0);
  });

  document.querySelectorAll('.nav-link[href*="#"], .mobile-nav-link[href*="#"], .topnav-link[href*="#"]')
    .forEach((link) => {
      link.addEventListener('click', () => {
        if (link.target === '_blank') return;
        const id = link.getAttribute('href').split('#')[1];
        if (!knownViews.has(id)) return;     // dedicated pages such as ?view=map stay with the browser
        window.setTimeout(sync, 0);          // hashchange does not fire when the hash is unchanged
      });
    });

  sync();
})();
