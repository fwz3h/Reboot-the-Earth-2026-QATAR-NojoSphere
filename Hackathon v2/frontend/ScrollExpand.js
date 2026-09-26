/*!
 * ScrollExpand — vanilla-JS port of the React Bits "ScrollExpand" component.
 * Original: https://reactbits.dev (MIT). Ported to plain JS + CSS so AgriSense
 * stays dependency-free and works offline. API mirrors the React component:
 *
 *   const fx = new ScrollExpand(elementOrSelector, { src: 'field.svg', title: 'Rooted in data' });
 *   fx.destroy();
 *
 * Elements marked with `data-scroll-expand` are initialised automatically from
 * `data-*` attributes. Any existing markup inside the mount becomes the overlay
 * content that fades in once the frame reaches full bleed (React `children`).
 */
(() => {
  'use strict';

  const DEFAULTS = {
    src: '',
    mediaType: 'image',
    poster: '',
    alt: '',
    title: '',
    scrollHint: '',
    startWidth: 42,
    startHeight: 58,
    startRadius: 24,
    endRadius: 0,
    mediaZoom: 1.35,
    scrollDistance: 1.2,
    holdDistance: 0.35,
    smoothing: 0.1,
    overlayScrim: 0.45,
    useWindowScroll: false,
    enabled: true,
    className: ''
  };

  const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);

  const smoothstep = (edge0, edge1, x) => {
    const t = clamp((x - edge0) / (edge1 - edge0 || 1e-6), 0, 1);
    return t * t * (3 - 2 * t);
  };

  const el = (tag, className) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    return node;
  };

  class ScrollExpand {
    constructor(target, options) {
      this.root = typeof target === 'string' ? document.querySelector(target) : target;
      if (!this.root) return;

      const merged = Object.assign({}, DEFAULTS);
      if (options) {
        for (const key of Object.keys(options)) {
          if (options[key] !== undefined) merged[key] = options[key];
        }
      }
      this.options = merged;

      this.raf = 0;
      this.current = 0;
      this.target = 0;
      this.stageH = 0;
      this.running = false;

      this._build();
      this._bind();
      this._measure();
      this.target = this._readProgress();
      this.current = this.target;
      this._applyProgress(this.current);
    }

    _build() {
      const root = this.root;
      const overlayHTML = root.innerHTML.trim();
      const options = this.options;

      root.textContent = '';
      root.classList.add('scroll-expand');
      if (!options.useWindowScroll) root.classList.add('scroll-expand--scroller');
      if (options.className) {
        options.className.split(/\s+/).filter(Boolean).forEach((name) => root.classList.add(name));
      }

      const track = el('div', 'scroll-expand__track');
      const stage = el('div', 'scroll-expand__stage');
      const frame = el('div', 'scroll-expand__frame');

      let media;
      if (options.mediaType === 'video') {
        media = document.createElement('video');
        media.className = 'scroll-expand__media';
        media.src = options.src;
        if (options.poster) media.poster = options.poster;
        media.autoplay = true;
        media.muted = true;
        media.loop = true;
        media.playsInline = true;
        media.setAttribute('muted', '');
        media.setAttribute('playsinline', '');
        if (options.alt) media.setAttribute('aria-label', options.alt);
      } else {
        media = document.createElement('img');
        media.className = 'scroll-expand__media';
        media.src = options.src;
        media.alt = options.alt || '';
        media.draggable = false;
      }

      const scrim = el('div', 'scroll-expand__scrim');
      frame.append(media, scrim);

      let overlay = null;
      if (overlayHTML) {
        overlay = el('div', 'scroll-expand__overlay');
        overlay.innerHTML = overlayHTML;
        frame.append(overlay);
      }

      stage.append(frame);

      let title = null;
      if (options.title) {
        title = el('div', 'scroll-expand__title');
        title.textContent = options.title;
        stage.append(title);
      }

      let hint = null;
      if (options.scrollHint) {
        hint = el('div', 'scroll-expand__hint');
        hint.textContent = options.scrollHint;
        stage.append(hint);
      }

      track.append(stage);
      root.append(track);

      this.track = track;
      this.stage = stage;
      this.frame = frame;
      this.media = media;
      this.scrim = scrim;
      this.overlay = overlay;
      this.title = title;
      this.hint = hint;
    }

    _bind() {
      this.reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;

      this._onScroll = () => {
        this.target = this._readProgress();
        if (this.options.smoothing <= 0 || this.reduceMotion) {
          this.current = this.target;
          this._applyProgress(this.current);
          return;
        }
        this._kick();
      };
      this._onResize = () => {
        this._measure();
        this.target = this._readProgress();
        this.current = this.target;
        this._applyProgress(this.current);
      };
      this._tick = this._tick.bind(this);

      this.scroller = this.options.useWindowScroll ? window : this.root;
      this.scroller.addEventListener('scroll', this._onScroll, { passive: true });
      window.addEventListener('resize', this._onResize);
      this.resizeObserver = new ResizeObserver(this._onResize);
      this.resizeObserver.observe(this.root);
    }

    _measure() {
      const options = this.options;
      this.stageH = options.useWindowScroll ? window.innerHeight : this.root.clientHeight;
      if (this.stageH <= 0) return;

      this.stage.style.height = `${this.stageH}px`;
      this.track.style.height = `${this.stageH * (1 + Math.max(0, options.scrollDistance) + Math.max(0, options.holdDistance))}px`;

      const w = this.root.clientWidth || this.stageH;
      this.stage.style.setProperty('--se-title-size', `${clamp(w * 0.075, 20, 84)}px`);
    }

    _readProgress() {
      const options = this.options;
      if (!options.enabled) return 1;
      const span = this.stageH * Math.max(0.01, options.scrollDistance);
      if (options.useWindowScroll) {
        const top = this.track.getBoundingClientRect().top;
        return clamp(-top / span, 0, 1);
      }
      return clamp(this.root.scrollTop / span, 0, 1);
    }

    _tick() {
      const options = this.options;
      const k = options.smoothing <= 0 ? 1 : 1 - Math.exp(-1 / (60 * options.smoothing));
      this.current += (this.target - this.current) * k;
      if (Math.abs(this.target - this.current) < 0.0004) {
        this.current = this.target;
        this.running = false;
      }
      this._applyProgress(this.current);
      this.raf = this.running ? window.requestAnimationFrame(this._tick) : 0;
    }

    _kick() {
      if (this.running) return;
      this.running = true;
      if (!this.raf) this.raf = window.requestAnimationFrame(this._tick);
    }

    _applyProgress(p) {
      const options = this.options;
      const frame = this.frame;
      const media = this.media;
      if (!frame || !media) return;

      const e = smoothstep(0, 1, p);

      const w = options.startWidth + (100 - options.startWidth) * e;
      const h = options.startHeight + (100 - options.startHeight) * e;
      const ix = Math.max(0, (100 - w) / 2);
      const iy = Math.max(0, (100 - h) / 2);
      const r = options.startRadius + (options.endRadius - options.startRadius) * e;
      frame.style.clipPath = `inset(${iy}% ${ix}% ${iy}% ${ix}% round ${r}px)`;

      media.style.transform = `scale(${options.mediaZoom + (1 - options.mediaZoom) * e})`;

      if (this.scrim) this.scrim.style.opacity = `${options.overlayScrim * e}`;

      if (this.title) {
        const out = smoothstep(0.4, 0.88, p);
        this.title.style.opacity = `${1 - out}`;
        this.title.style.transform = `translate3d(0, ${-28 * out}px, 0) scale(${1 + 0.06 * out})`;
      }

      if (this.hint) {
        const gone = smoothstep(0, 0.12, p);
        this.hint.style.opacity = `${1 - gone}`;
        this.hint.style.transform = `translate3d(0, ${8 * gone}px, 0)`;
      }

      if (this.overlay) {
        const inn = smoothstep(0.68, 1, p);
        this.overlay.style.opacity = `${inn}`;
        this.overlay.style.transform = `translate3d(0, ${18 * (1 - inn)}px, 0)`;
      }
    }

    setOptions(options) {
      if (!options) return this;
      const structural = ['src', 'mediaType', 'poster', 'alt', 'title', 'scrollHint', 'className', 'useWindowScroll'];
      let rebuild = false;
      for (const key of Object.keys(options)) {
        if (options[key] === undefined || !(key in DEFAULTS)) continue;
        if (structural.includes(key) && this.options[key] !== options[key]) rebuild = true;
        this.options[key] = options[key];
      }
      if (rebuild) {
        this._teardown();
        this._build();
        this._bind();
      }
      this._measure();
      this.target = this._readProgress();
      this.current = this.target;
      this._applyProgress(this.current);
      return this;
    }

    _teardown() {
      if (this.raf) window.cancelAnimationFrame(this.raf);
      this.raf = 0;
      this.running = false;
      this.scroller?.removeEventListener('scroll', this._onScroll);
      window.removeEventListener('resize', this._onResize);
      this.resizeObserver?.disconnect();
    }

    destroy() {
      this._teardown();
    }
  }

  window.ScrollExpand = ScrollExpand;

  const readOptions = (dataset) => {
    const num = (key) => (dataset[key] !== undefined && dataset[key] !== '' ? Number(dataset[key]) : undefined);
    const bool = (key) => (dataset[key] === undefined ? undefined : dataset[key] !== 'false');
    return {
      src: dataset.src,
      mediaType: dataset.mediaType,
      poster: dataset.poster,
      alt: dataset.alt,
      title: dataset.title,
      scrollHint: dataset.scrollHint,
      startWidth: num('startWidth'),
      startHeight: num('startHeight'),
      startRadius: num('startRadius'),
      endRadius: num('endRadius'),
      mediaZoom: num('mediaZoom'),
      scrollDistance: num('scrollDistance'),
      holdDistance: num('holdDistance'),
      smoothing: num('smoothing'),
      overlayScrim: num('overlayScrim'),
      useWindowScroll: bool('useWindowScroll'),
      enabled: bool('enabled'),
      className: dataset.className
    };
  };

  const initAll = () => {
    document.querySelectorAll('[data-scroll-expand]').forEach((mount) => {
      if (mount.__scrollExpand) return;
      mount.__scrollExpand = new ScrollExpand(mount, readOptions(mount.dataset));
    });
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll, { once: true });
  } else {
    initAll();
  }
})();
