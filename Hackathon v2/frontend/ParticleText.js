/*!
 * ParticleText — vanilla-JS port of the React Bits "ParticleText" component.
 * Original: https://reactbits.dev (MIT). Ported to plain JS + CSS so AgriSense
 * stays dependency-free and works offline. API mirrors the React component:
 *
 *   const fx = new ParticleText(elementOrSelector, { text: 'Farm intelligence' });
 *   fx.setOptions({ text: 'New words' });  // re-samples with merged options
 *   fx.destroy();                          // stops the loop and removes nodes
 *
 * Elements marked with `data-particle-text` are initialised automatically from
 * `data-*` attributes, e.g. data-text, data-color, data-highlight-color.
 */
(() => {
  'use strict';

  const DEFAULTS = {
    text: 'React Bits',
    particleSize: 2,
    density: 4,
    color: '#ffffff',
    highlightColor: '#8b5cf6',
    scatter: 180,
    gatherDuration: 1600,
    stagger: 420,
    pointerRepel: 40,
    repelRadius: 120,
    idleDrift: 0.7,
    trigger: 'mount',
    fontSize: 'clamp(3rem, 12vw, 8rem)',
    fontWeight: 800,
    fontFamily: 'inherit',
    glow: true,
    className: ''
  };

  const hexToRgb = (hex) => {
    const clean = String(hex || '').replace('#', '').trim();
    if (!/^[0-9a-fA-F]{6}$/.test(clean)) return null;
    return {
      r: parseInt(clean.slice(0, 2), 16),
      g: parseInt(clean.slice(2, 4), 16),
      b: parseInt(clean.slice(4, 6), 16)
    };
  };

  const mixRgb = (from, to, amount) => ({
    r: Math.round(from.r + (to.r - from.r) * amount),
    g: Math.round(from.g + (to.g - from.g) * amount),
    b: Math.round(from.b + (to.b - from.b) * amount)
  });

  const rgbToCss = (rgb) => `rgb(${rgb.r}, ${rgb.g}, ${rgb.b})`;
  const clamp = (value, min, max) => Math.min(Math.max(value, min), max);
  const easeOutCubic = (t) => 1 - Math.pow(1 - t, 3);

  const resolveFontSize = (value, container, fontWeight, fontFamily) => {
    if (typeof value === 'number') return value;

    const probe = document.createElement('span');
    probe.textContent = 'M';
    probe.style.position = 'absolute';
    probe.style.visibility = 'hidden';
    probe.style.pointerEvents = 'none';
    probe.style.fontSize = value;
    probe.style.fontWeight = String(fontWeight);
    probe.style.fontFamily = fontFamily;
    container.appendChild(probe);
    const size = parseFloat(window.getComputedStyle(probe).fontSize) || 96;
    probe.remove();
    return size;
  };

  const waitForFonts = async (font) => {
    if (!('fonts' in document)) return;

    try {
      await document.fonts.load(font);
    } catch (error) {
      /* Font loading is best-effort; sampling continues with the fallback font. */
    }

    await document.fonts.ready;
  };

  class ParticleText {
    constructor(target, options) {
      this.target = typeof target === 'string' ? document.querySelector(target) : target;
      if (!this.target) return;

      const merged = Object.assign({}, DEFAULTS);
      if (options) {
        for (const key of Object.keys(options)) {
          if (options[key] !== undefined) merged[key] = options[key];
        }
      }
      this.options = merged;

      this.particles = [];
      this.animationFrame = null;
      this.resizeFrame = null;
      this.buildId = 0;
      this.gathering = false;
      this.gatherStart = 0;
      this.width = 0;
      this.height = 0;
      this.dpr = 1;
      this.reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
      this.pointer = { active: false, x: 0, y: 0, smoothX: 0, smoothY: 0 };

      this._buildDom();
      if (!this.ctx) return;
      this._bind();
      this._sample();
    }

    _buildDom() {
      const wrapper = document.createElement('div');
      wrapper.className = `particle-text ${this.options.className}`.trim();
      wrapper.setAttribute('aria-label', this.options.text);

      const canvas = document.createElement('canvas');
      canvas.className = 'particle-text__canvas';
      canvas.setAttribute('aria-hidden', 'true');

      const sr = document.createElement('span');
      sr.className = 'particle-text__sr';
      sr.textContent = this.options.text;

      wrapper.append(canvas, sr);
      this.target.appendChild(wrapper);

      this.wrapper = wrapper;
      this.canvas = canvas;
      this.ctx = canvas.getContext('2d');
    }

    _bind() {
      this._onPointerMove = (event) => {
        const rect = this.canvas.getBoundingClientRect();
        this.pointer.x = event.clientX - rect.left;
        this.pointer.y = event.clientY - rect.top;
        this.pointer.active = true;
      };
      this._onPointerLeave = () => {
        this.pointer.active = false;
      };
      this._onPointerEnter = (event) => {
        this._onPointerMove(event);
        if (this.options.trigger === 'hover') this._startGather(true);
      };
      this._onClick = () => {
        if (this.options.trigger === 'click') this._startGather(true);
      };
      this._onReduceMotionChange = (event) => {
        this.reducedMotion = event.matches;
        this._sample();
      };
      this._render = this._render.bind(this);
      this._sampleText = this._sampleText.bind(this);

      this.reduceMotionQuery = window.matchMedia?.('(prefers-reduced-motion: reduce)');
      this.reduceMotionQuery?.addEventListener('change', this._onReduceMotionChange);
      this.canvas.addEventListener('pointerenter', this._onPointerEnter);
      this.canvas.addEventListener('pointermove', this._onPointerMove);
      this.canvas.addEventListener('pointerleave', this._onPointerLeave);
      this.canvas.addEventListener('click', this._onClick);

      this.resizeObserver = new ResizeObserver(() => this._queueSample());
      this.resizeObserver.observe(this.target);

      // The render loop is expensive, so it only runs while the effect is on
      // screen and the tab is visible.
      this.visible = true;
      this._onVisibilityChange = () => {
        if (this._shouldAnimate()) this._ensureRenderLoop();
      };
      document.addEventListener('visibilitychange', this._onVisibilityChange);
      this.intersectionObserver = new IntersectionObserver((entries) => {
        this.visible = entries.some((entry) => entry.isIntersecting);
        if (this.visible) this._ensureRenderLoop();
      }, { threshold: 0 });
      this.intersectionObserver.observe(this.wrapper);
    }

    _shouldAnimate() {
      return this.visible && !document.hidden;
    }

    _startGather(fromScatter = true) {
      const particles = this.particles;
      if (!particles.length) return;

      const now = performance.now();
      const spread = this.reducedMotion ? 0 : this.options.scatter;

      particles.forEach((particle) => {
        if (fromScatter) {
          const angle = particle.seed * Math.PI * 2;
          const distance = spread * (0.35 + particle.depth * 0.75);
          particle.x = particle.targetX + Math.cos(angle) * distance + (particle.depth - 0.5) * spread * 0.55;
          particle.y = particle.targetY + Math.sin(angle) * distance + (particle.seed - 0.5) * spread * 0.55;
        }

        particle.startX = particle.x;
        particle.startY = particle.y;
        particle.delay = this.reducedMotion ? 0 : particle.seed * this.options.stagger;
      });

      this.gatherStart = now;
      this.gathering = true;
    }

    _drawParticle(particle) {
      const ctx = this.ctx;
      const size = particle.size;
      ctx.fillStyle = particle.color;

      if (size <= 3.2) {
        ctx.fillRect(particle.x - size / 2, particle.y - size / 2, size, size);
        return;
      }

      ctx.beginPath();
      ctx.arc(particle.x, particle.y, size / 2, 0, Math.PI * 2);
      ctx.fill();
    }

    _render(now) {
      const ctx = this.ctx;
      const options = this.options;
      const pointer = this.pointer;
      ctx.clearRect(0, 0, this.width, this.height);

      if (options.glow && !this.reducedMotion) {
        ctx.shadowBlur = options.particleSize * 3;
        ctx.shadowColor = options.highlightColor;
      } else {
        ctx.shadowBlur = 0;
      }

      pointer.smoothX += (pointer.x - pointer.smoothX) * 0.18;
      pointer.smoothY += (pointer.y - pointer.smoothY) * 0.18;

      let complete = true;

      this.particles.forEach((particle) => {
        let baseX = particle.targetX;
        let baseY = particle.targetY;
        let progress = 1;

        if (this.gathering) {
          const local = (now - this.gatherStart - particle.delay) / Math.max(1, this.reducedMotion ? 1 : options.gatherDuration);
          progress = clamp(local, 0, 1);
          const eased = easeOutCubic(progress);
          baseX = particle.startX + (particle.targetX - particle.startX) * eased;
          baseY = particle.startY + (particle.targetY - particle.startY) * eased;
          if (progress < 1) complete = false;
        } else if (!this.reducedMotion && options.idleDrift > 0) {
          const driftTime = now * 0.001;
          baseX += Math.sin(driftTime * 0.9 + particle.seed * 10) * options.idleDrift * particle.depth;
          baseY += Math.cos(driftTime * 0.75 + particle.depth * 10) * options.idleDrift * particle.depth;
        }

        if (pointer.active && !this.reducedMotion && options.pointerRepel > 0 && options.repelRadius > 0) {
          const dx = baseX - pointer.smoothX;
          const dy = baseY - pointer.smoothY;
          const distance = Math.hypot(dx, dy);
          if (distance > 0 && distance < options.repelRadius) {
            const force = Math.pow(1 - distance / options.repelRadius, 2) * options.pointerRepel;
            baseX += (dx / distance) * force;
            baseY += (dy / distance) * force;
          }
        }

        const follow = this.reducedMotion ? 1 : 0.22;
        particle.x += (baseX - particle.x) * follow;
        particle.y += (baseY - particle.y) * follow;

        ctx.globalAlpha = clamp(0.35 + progress * 0.65, 0, 1);
        this._drawParticle(particle);
      });

      ctx.globalAlpha = 1;
      ctx.shadowBlur = 0;

      if (this.gathering && complete) {
        this.gathering = false;
      }

      this.animationFrame = this._shouldAnimate() ? window.requestAnimationFrame(this._render) : null;
    }

    _ensureRenderLoop() {
      if (this.animationFrame === null && this._shouldAnimate()) {
        this.animationFrame = window.requestAnimationFrame(this._render);
      }
    }

    async _sampleText() {
      const currentBuild = ++this.buildId;
      const options = this.options;
      const ctx = this.ctx;
      const rect = this.target.getBoundingClientRect();
      this.width = Math.floor(rect.width);
      this.height = Math.floor(rect.height);

      if (this.width <= 0 || this.height <= 0) return;

      this.dpr = Math.min(window.devicePixelRatio || 1, 2);
      this.canvas.width = Math.max(1, Math.floor(this.width * this.dpr));
      this.canvas.height = Math.max(1, Math.floor(this.height * this.dpr));
      this.canvas.style.width = '100%';
      this.canvas.style.height = '100%';
      ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);

      const computed = window.getComputedStyle(this.target);
      const resolvedFamily = options.fontFamily === 'inherit' ? computed.fontFamily || 'sans-serif' : options.fontFamily;
      let resolvedSize = resolveFontSize(options.fontSize, this.target, options.fontWeight, resolvedFamily);
      let font = `${options.fontWeight} ${resolvedSize}px ${resolvedFamily}`;

      await waitForFonts(font);
      if (currentBuild !== this.buildId) return;

      const offscreen = document.createElement('canvas');
      const offCtx = offscreen.getContext('2d', { willReadFrequently: true });
      if (!offCtx) return;

      const content = String(options.text || ' ');
      const maxTextWidth = this.width * 0.92;
      offCtx.font = font;
      let metrics = offCtx.measureText(content);
      const measuredWidth = Math.max(1, metrics.width);
      if (measuredWidth > maxTextWidth) {
        resolvedSize = Math.max(18, resolvedSize * (maxTextWidth / measuredWidth));
        font = `${options.fontWeight} ${resolvedSize}px ${resolvedFamily}`;
        await waitForFonts(font);
        if (currentBuild !== this.buildId) return;
        offCtx.font = font;
        metrics = offCtx.measureText(content);
      }

      const left = Math.ceil(metrics.actualBoundingBoxLeft || 0);
      const right = Math.ceil(metrics.actualBoundingBoxRight || metrics.width);
      const ascent = Math.ceil(metrics.actualBoundingBoxAscent || resolvedSize * 0.78);
      const descent = Math.ceil(metrics.actualBoundingBoxDescent || resolvedSize * 0.22);
      const padding = Math.max(12, Math.ceil(resolvedSize * 0.08));
      const textWidth = Math.max(1, left + right);
      const textHeight = Math.max(1, ascent + descent);

      offscreen.width = textWidth + padding * 2;
      offscreen.height = textHeight + padding * 2;
      offCtx.clearRect(0, 0, offscreen.width, offscreen.height);
      offCtx.font = font;
      offCtx.textAlign = 'left';
      offCtx.textBaseline = 'alphabetic';
      offCtx.fillStyle = '#ffffff';
      offCtx.fillText(content, padding - left, padding + ascent);

      const imageData = offCtx.getImageData(0, 0, offscreen.width, offscreen.height);
      const targets = [];
      const step = Math.max(2, Math.floor(options.density));

      for (let y = 0; y < offscreen.height; y += step) {
        for (let x = 0; x < offscreen.width; x += step) {
          const alpha = imageData.data[(y * offscreen.width + x) * 4 + 3];
          if (alpha > 40) {
            targets.push({
              x: this.width / 2 - offscreen.width / 2 + x,
              y: this.height / 2 - offscreen.height / 2 + y,
              alpha: alpha / 255
            });
          }
        }
      }

      const maxParticles = Math.max(600, Math.min(2600, Math.floor((this.width * this.height) / 120)));
      const stride = Math.max(1, Math.ceil(targets.length / maxParticles));
      const baseRgb = hexToRgb(options.color);
      const highlightRgb = hexToRgb(options.highlightColor);
      const selected = targets.filter((_, index) => index % stride === 0);

      this.particles = selected.map((target, index) => {
        const seed = ((index * 9301 + 49297) % 233280) / 233280;
        const depth = 0.45 + (((index * 233 + 97) % 1000) / 1000) * 0.9;
        const blend = baseRgb && highlightRgb ? clamp(target.x / Math.max(1, this.width) + (seed - 0.5) * 0.35, 0, 1) : 0;
        const particleColor = baseRgb && highlightRgb ? rgbToCss(mixRgb(baseRgb, highlightRgb, blend)) : options.color;
        const angle = seed * Math.PI * 2;
        const distance = (this.reducedMotion ? 0 : options.scatter) * (0.35 + depth * 0.75);
        const startX = target.x + Math.cos(angle) * distance + (seed - 0.5) * options.scatter * 0.45;
        const startY = target.y + Math.sin(angle) * distance + (depth - 0.9) * options.scatter * 0.45;

        return {
          x: this.reducedMotion ? target.x : startX,
          y: this.reducedMotion ? target.y : startY,
          startX,
          startY,
          targetX: target.x,
          targetY: target.y,
          size: Math.max(0.6, options.particleSize * (0.75 + target.alpha * 0.45)),
          color: particleColor,
          seed,
          depth,
          delay: seed * options.stagger
        };
      });

      this.pointer.x = this.width / 2;
      this.pointer.y = this.height / 2;
      this.pointer.smoothX = this.pointer.x;
      this.pointer.smoothY = this.pointer.y;

      if (this.reducedMotion) {
        this.particles.forEach((particle) => {
          particle.x = particle.targetX;
          particle.y = particle.targetY;
          particle.startX = particle.targetX;
          particle.startY = particle.targetY;
          particle.delay = 0;
        });
        this.gathering = false;
      } else {
        this._startGather(false);
      }

      this._ensureRenderLoop();
    }

    _sample() {
      return this._sampleText();
    }

    _queueSample() {
      if (this.resizeFrame) window.cancelAnimationFrame(this.resizeFrame);
      this.resizeFrame = window.requestAnimationFrame(() => this._sampleText());
    }

    setOptions(options) {
      if (!options) return this;
      for (const key of Object.keys(options)) {
        if (options[key] !== undefined && key in DEFAULTS) this.options[key] = options[key];
      }
      if ('text' in options && options.text !== undefined) {
        this.wrapper.setAttribute('aria-label', options.text);
        this.wrapper.querySelector('.particle-text__sr').textContent = options.text;
      }
      this._sample();
      return this;
    }

    destroy() {
      this.buildId += 1;
      this.resizeObserver?.disconnect();
      this.intersectionObserver?.disconnect();
      document.removeEventListener('visibilitychange', this._onVisibilityChange);
      this.reduceMotionQuery?.removeEventListener('change', this._onReduceMotionChange);
      if (this.canvas) {
        this.canvas.removeEventListener('pointerenter', this._onPointerEnter);
        this.canvas.removeEventListener('pointermove', this._onPointerMove);
        this.canvas.removeEventListener('pointerleave', this._onPointerLeave);
        this.canvas.removeEventListener('click', this._onClick);
      }
      if (this.animationFrame !== null) window.cancelAnimationFrame(this.animationFrame);
      if (this.resizeFrame !== null) window.cancelAnimationFrame(this.resizeFrame);
      this.animationFrame = null;
      this.resizeFrame = null;
      this.wrapper?.remove();
    }
  }

  window.ParticleText = ParticleText;

  const readOptions = (dataset) => {
    const num = (key, fallback) => (dataset[key] !== undefined && dataset[key] !== '' ? Number(dataset[key]) : fallback);
    const bool = (key, fallback) => (dataset[key] === undefined ? fallback : dataset[key] !== 'false');
    return {
      text: dataset.text,
      particleSize: num('particleSize', undefined),
      density: num('density', undefined),
      color: dataset.color,
      highlightColor: dataset.highlightColor,
      scatter: num('scatter', undefined),
      gatherDuration: num('gatherDuration', undefined),
      stagger: num('stagger', undefined),
      pointerRepel: num('pointerRepel', undefined),
      repelRadius: num('repelRadius', undefined),
      idleDrift: num('idleDrift', undefined),
      trigger: dataset.trigger,
      fontSize: dataset.fontSize,
      fontWeight: num('fontWeight', undefined),
      fontFamily: dataset.fontFamily,
      glow: bool('glow', undefined),
      className: dataset.className
    };
  };

  const initAll = () => {
    document.querySelectorAll('[data-particle-text]').forEach((mount) => {
      if (mount.__particleText) return;
      mount.__particleText = new ParticleText(mount, readOptions(mount.dataset));
    });
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll, { once: true });
  } else {
    initAll();
  }
})();
