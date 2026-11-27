/* Unified Apple-grade motion choreographer: interruptible, GPU-only, reduced-motion aware. */
(() => {
  if (typeof window === 'undefined') return;
  const AT = (window.AT = window.AT || {});
  const query = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : { matches: false, addEventListener() {} };
  const activeGhosts = new Map();

  function reduced() {
    return Boolean(AT.state?.reduced || query.matches || document.documentElement.classList.contains('reduce-motion') || document.body?.classList.contains('reduce-motion'));
  }

  function syncReduced() {
    const on = Boolean(AT.state?.reduced);
    document.documentElement.classList.toggle('reduce-motion', on);
    if (document.body) document.body.classList.toggle('reduce-motion', on);
  }

  function scrollOptions(smooth = true) {
    return { behavior: !smooth || reduced() ? 'instant' : 'smooth' };
  }

  function cancelGhost(key) {
    const existing = activeGhosts.get(key);
    if (!existing) return;
    clearTimeout(existing.timer);
    existing.node.remove();
    activeGhosts.delete(key);
  }

  /**
   * Non-blocking exit animation: clones the visual shell, strips semantic roles/classes/data-*
   * so tests & DOM queries immediately see the real element gone, then animates out on GPU.
   */
  function exitGhost(source, variant = 'overlay', key = variant) {
    cancelGhost(key);
    if (!source || reduced() || !document.body) return;
    const rect = source.getBoundingClientRect();
    if (!rect.width && !rect.height) return;
    const ghost = source.cloneNode(true);
    const scrub = node => {
      if (node.nodeType !== 1) return;
      node.removeAttribute('id');
      node.removeAttribute('role');
      node.removeAttribute('aria-modal');
      node.removeAttribute('aria-labelledby');
      for (const attr of [...node.attributes]) {
        if (attr.name.startsWith('data-')) node.removeAttribute(attr.name);
      }
      node.classList.remove('overlay', 'modal', 'nav-flyout', 'rd-popover', 'page-ai-panel', 'milestone-drawer', 'day-drawer');
      for (const child of node.children) scrub(child);
    };
    scrub(ghost);
    ghost.setAttribute('aria-hidden', 'true');
    ghost.inert = true;
    ghost.classList.add('at-exit-ghost');
    if (variant === 'overlay' || variant === 'drawer') {
      ghost.classList.add('at-exit-overlay');
      if (variant === 'drawer') ghost.classList.add('at-exit-drawer');
    } else {
      ghost.classList.add(`at-exit-${variant}`);
      ghost.style.position = 'fixed';
      ghost.style.left = `${rect.left}px`;
      ghost.style.top = `${rect.top}px`;
      ghost.style.width = `${rect.width}px`;
      ghost.style.height = `${rect.height}px`;
      ghost.style.margin = '0';
      ghost.style.zIndex = variant === 'panel' ? '34' : '48';
      ghost.hidden = false;
    }
    document.body.append(ghost);
    const cleanup = () => cancelGhost(key);
    ghost.addEventListener('animationend', cleanup, { once: true });
    const timer = setTimeout(cleanup, 190);
    activeGhosts.set(key, { node: ghost, timer });
  }

  function restartClass(el, className, cleanupMs = 450) {
    if (!el || reduced()) return;
    if (el.classList.contains(className)) {
      el.classList.remove(className);
      void el.offsetWidth;
    }
    el.classList.add(className);
    clearTimeout(el._atAnimTimer);
    el._atAnimTimer = setTimeout(() => el.classList.remove(className), cleanupMs);
  }

  function enter(el, className = 'at-page-enter') {
    restartClass(el, className, 320);
  }

  function step(el, direction) {
    if (!el || !direction || reduced()) return;
    el.classList.remove('at-step-next', 'at-step-prev');
    restartClass(el, direction > 0 ? 'at-step-next' : 'at-step-prev', 220);
  }

  function flashValue(el, direction = 'neutral') {
    if (!el || reduced()) return;
    el.classList.remove('at-tick-up', 'at-tick-down', 'at-value-pulse');
    const cls = direction === 'up' ? 'at-tick-up' : direction === 'down' ? 'at-tick-down' : 'at-value-pulse';
    restartClass(el, cls, 560);
  }

  function flashSuccess(el) {
    restartClass(el, 'at-flash-success', 720);
  }

  function shake(el) {
    restartClass(el, 'at-shake', 300);
  }

  function patchProgress(bar, pct) {
    if (!bar) return;
    const clamped = Math.max(0, Math.min(100, Number(pct) || 0));
    bar.style.transform = `scaleX(${(clamped / 100).toFixed(4)})`;
  }

  /**
   * Tab switches rely on clean CSS color/border transitions rather than transforming
   * button elements horizontally (which stretched label text and flashed across tabs).
   */
  function captureTabs() {}

  function animateTabs() {}

  /**
   * Apple-grade horizontal momentum physics for the Milestone Pass Rail:
   * - Direct 1:1 pointer tracking with setPointerCapture (never drops drag outside strip)
   * - Smooth velocity coast with exponential damping (immediately interruptible)
   * - Smooth rAF wheel easing & keyboard stepping
   */
  function wireMomentumScroll(viewport, wheelHost, onPosition) {
    if (!viewport) return;
    let dragging = false, pointerId = null, startX = 0, startScroll = 0, moved = false;
    let lastX = 0, lastTime = 0, velocity = 0, coastFrame = 0, targetScroll = null, wheelFrame = 0;

    const maxScroll = () => Math.max(0, viewport.scrollWidth - viewport.clientWidth);
    const clamp = v => Math.max(0, Math.min(maxScroll(), v));
    const report = () => { if (onPosition) onPosition(viewport.scrollLeft); };

    const stopMotion = () => {
      if (coastFrame) { cancelAnimationFrame(coastFrame); coastFrame = 0; }
      if (wheelFrame) { cancelAnimationFrame(wheelFrame); wheelFrame = 0; targetScroll = null; }
    };

    const startCoast = () => {
      if (reduced() || Math.abs(velocity) < 0.08) { velocity = 0; report(); return; }
      let prevTime = performance.now();
      const tick = now => {
        const dt = Math.min(32, Math.max(4, now - prevTime));
        prevTime = now;
        const next = clamp(viewport.scrollLeft - velocity * dt);
        const hitEdge = next === viewport.scrollLeft;
        viewport.scrollLeft = next;
        report();
        velocity *= Math.pow(0.935, dt / 16.67);
        if (hitEdge || Math.abs(velocity) < 0.04) {
          coastFrame = 0;
          velocity = 0;
          return;
        }
        coastFrame = requestAnimationFrame(tick);
      };
      coastFrame = requestAnimationFrame(tick);
    };

    const glideTo = dest => {
      const clamped = clamp(dest);
      if (reduced()) {
        stopMotion();
        viewport.scrollLeft = clamped;
        report();
        return;
      }
      targetScroll = clamped;
      if (wheelFrame) return;
      const tick = () => {
        if (targetScroll === null) { wheelFrame = 0; return; }
        const diff = targetScroll - viewport.scrollLeft;
        if (Math.abs(diff) < 0.6) {
          viewport.scrollLeft = targetScroll;
          targetScroll = null;
          wheelFrame = 0;
          report();
          return;
        }
        viewport.scrollLeft += diff * 0.24;
        report();
        wheelFrame = requestAnimationFrame(tick);
      };
      wheelFrame = requestAnimationFrame(tick);
    };

    (wheelHost || viewport).addEventListener('wheel', e => {
      if (e.ctrlKey) return;
      e.preventDefault();
      e.stopPropagation();
      if (coastFrame) { cancelAnimationFrame(coastFrame); coastFrame = 0; velocity = 0; }
      const unit = e.deltaMode === 1 ? 18 : e.deltaMode === 2 ? viewport.clientWidth : 1;
      const delta = (Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY) * unit;
      const base = targetScroll !== null ? targetScroll : viewport.scrollLeft;
      glideTo(base + delta);
    }, { passive: false });

    viewport.addEventListener('pointerdown', e => {
      if (e.pointerType === 'touch' || e.button !== 0) return;
      stopMotion();
      dragging = true;
      pointerId = e.pointerId;
      startX = lastX = e.clientX;
      startScroll = viewport.scrollLeft;
      lastTime = performance.now();
      velocity = 0;
      moved = false;
    });

    viewport.addEventListener('pointermove', e => {
      if (!dragging) return;
      const dx = e.clientX - startX;
      if (!moved && Math.abs(dx) > 4) {
        moved = true;
        viewport.classList.add('dragging');
        try { viewport.setPointerCapture(pointerId); } catch {}
      }
      if (!moved) return;
      const now = performance.now();
      const dt = Math.max(4, now - lastTime);
      const instantVel = (e.clientX - lastX) / dt;
      velocity = velocity * 0.45 + instantVel * 0.55;
      lastX = e.clientX;
      lastTime = now;
      viewport.scrollLeft = clamp(startScroll - dx);
      report();
    });

    const finishDrag = () => {
      if (!dragging) return;
      dragging = false;
      viewport.classList.remove('dragging');
      if (pointerId !== null) {
        try { if (viewport.hasPointerCapture?.(pointerId)) viewport.releasePointerCapture(pointerId); } catch {}
        pointerId = null;
      }
      if (moved) {
        if (performance.now() - lastTime > 70) velocity = 0;
        startCoast();
      } else {
        report();
      }
    };

    viewport.addEventListener('pointerup', finishDrag);
    viewport.addEventListener('pointercancel', finishDrag);
    viewport.addEventListener('click', e => {
      if (moved) {
        e.preventDefault();
        e.stopPropagation();
        moved = false;
      }
    }, true);

    viewport.addEventListener('keydown', e => {
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        e.preventDefault();
        const base = targetScroll !== null ? targetScroll : viewport.scrollLeft;
        glideTo(base + (e.key === 'ArrowRight' ? 156 : -156));
      } else if (e.key === 'Home') {
        e.preventDefault();
        glideTo(0);
      } else if (e.key === 'End') {
        e.preventDefault();
        glideTo(maxScroll());
      }
    });
  }

  /** Lightweight FPS & jank frame-time profiler for verification. */
  function measureFrames(durationMs = 1000) {
    return new Promise(resolve => {
      const deltas = [];
      let last = performance.now();
      const end = last + durationMs;
      const stepFrame = now => {
        deltas.push(now - last);
        last = now;
        if (now < end) requestAnimationFrame(stepFrame);
        else {
          const valid = deltas.slice(1);
          const avg = valid.length ? valid.reduce((a, b) => a + b, 0) / valid.length : 16.67;
          const max = valid.length ? Math.max(...valid) : 16.67;
          const dropped = valid.filter(d => d > 25).length;
          resolve({ frames: valid.length, fps: +(1000 / avg).toFixed(1), avgMs: +avg.toFixed(2), maxMs: +max.toFixed(2), dropped });
        }
      };
      requestAnimationFrame(stepFrame);
    });
  }

  query.addEventListener?.('change', syncReduced);

  AT.motion = {
    reduced,
    syncReduced,
    scrollOptions,
    exitGhost,
    cancelGhost,
    enter,
    step,
    flashValue,
    flashSuccess,
    shake,
    patchProgress,
    captureTabs,
    animateTabs,
    wireMomentumScroll,
    measureFrames
  };
})();
