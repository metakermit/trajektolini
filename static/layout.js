/**
 * Dubravko easter egg.
 * Triggered when "dubravko" appears in either search field.
 * Arrow keys to move, Space to poop. Refresh to reset.
 */
(function () {
  'use strict';

  // ── Config ────────────────────────────────────────────────────────────────────

  const DOG_SCALE  = 3;
  const DOG_PX     = 32;
  const DOG_SIZE   = DOG_PX * DOG_SCALE;   // 96 px on screen
  const SPEED_IN   = 2;                     // px/tick during walk-in
  const MOVE_SPEED = 3;                     // px/tick when player moves
  const GRAVITY    = 0.5;                   // px/tick² downward acceleration
  const JUMP_VEL   = -12;                   // px/tick initial upward velocity

  // ── Poop sprite (pixel art, 8 × 5 px) ────────────────────────────────────────

  const POOP_SCALE = 3;
  const POOP = [
    '..ddd...',
    '.dppd...',
    'dpppPd..',
    '.dPpd...',
    '..dd....',
  ];
  const POOP_PAL = { '.': null, 'd': '#3d1a06', 'p': '#7a3a14', 'P': '#5c2a0c' };

  function drawPoopSprite(ctx, x, y) {
    for (let r = 0; r < POOP.length; r++) {
      for (let c = 0; c < POOP[r].length; c++) {
        const color = POOP_PAL[POOP[r][c]];
        if (!color) continue;
        ctx.fillStyle = color;
        ctx.fillRect(x + c * POOP_SCALE, y + r * POOP_SCALE, POOP_SCALE, POOP_SCALE);
      }
    }
  }

  // ── Launch ────────────────────────────────────────────────────────────────────

  function launch() {
    if (document.getElementById('dubravko-canvas')) return;

    const img = new Image();
    img.src = '/static/icon.png';

    // Full-screen overlay canvas
    const canvas = document.createElement('canvas');
    canvas.id = 'dubravko-canvas';
    Object.assign(canvas.style, {
      position:                 'fixed',
      top:                      '0',
      left:                     '0',
      pointerEvents:            'auto',
      zIndex:                   '9999',
      imageRendering:           'pixelated',
      touchAction:              'none',          // prevent Safari hijacking gestures
      webkitTapHighlightColor:  'transparent',   // suppress iOS tap flash
      userSelect:               'none',
      webkitUserSelect:         'none',
    });

    let vpW = 0, vpH = 0;
    const ctx = canvas.getContext('2d');

    function onResize() {
      const vp  = window.visualViewport;
      const dpr = window.devicePixelRatio || 1;
      vpW = Math.round(vp ? vp.width  : window.innerWidth);
      vpH = Math.round(vp ? vp.height : window.innerHeight);
      // Size and position the canvas to match the visual viewport exactly,
      // keeping it clear of browser toolbars on mobile.
      canvas.style.width  = vpW + 'px';
      canvas.style.height = vpH + 'px';
      canvas.style.top    = Math.round(vp ? vp.offsetTop  : 0) + 'px';
      canvas.style.left   = Math.round(vp ? vp.offsetLeft : 0) + 'px';
      canvas.width  = vpW * dpr;
      canvas.height = vpH * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.imageSmoothingEnabled = false;
    }
    onResize();
    (window.visualViewport || window).addEventListener('resize', onResize);
    document.body.appendChild(canvas);

    // Remove focus from any form element so arrow keys don't navigate the UI
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();

    // ── State ──────────────────────────────────────────────────────────────────

    const ST = { WALK_IN: 0, IDLE: 1, SQUATTING: 2 };

    let state     = ST.WALK_IN;
    let tick      = 0;
    let dogX      = -DOG_SIZE;
    let dogY      = vpH - DOG_SIZE;
    let vy        = 0;                       // vertical velocity (px/tick)
    let facingLeft = false;                  // walking in from the left
    const poops   = [];                      // { x, y, age }

    const ground  = () => vpH - DOG_SIZE;
    const onGround = () => dogY >= ground() - 1;

    // Walk-in target: just inside the left edge
    const targetX = () => DOG_SIZE;

    // ── Keyboard ───────────────────────────────────────────────────────────────

    const keys = {};

    function onKeyDown(e) {
      if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', ' '].includes(e.key)) return;
      e.preventDefault();
      keys[e.key] = true;
      if (e.key === ' ' && state === ST.IDLE && onGround()) {
        state = ST.SQUATTING;
        tick  = 0;
      }
      if (e.key === 'ArrowUp' && state === ST.IDLE && onGround()) {
        vy = JUMP_VEL;
      }
    }
    function onKeyUp(e) { delete keys[e.key]; }
    document.addEventListener('keydown', onKeyDown, { capture: true });
    document.addEventListener('keyup',   onKeyUp,   { capture: true });

    // ── Touch controls ─────────────────────────────────────────────────────────
    // Zone per touch identifier: 'left' | 'right' | 'jump-left' | 'jump-right' | 'poop'
    const touchZones = new Map();

    function touchZone(tx, ty) {
      const onDog = tx >= dogX && tx <= dogX + DOG_SIZE &&
                    ty >= dogY && ty <= dogY + DOG_SIZE;
      if (onDog) return 'poop';
      if (ty < dogY + DOG_SIZE / 2) return tx < dogX + DOG_SIZE / 2 ? 'jump-left' : 'jump-right';
      return tx < dogX + DOG_SIZE / 2 ? 'left' : 'right';
    }

    function applyTouchStart(id, tx, ty) {
      const zone = touchZone(tx, ty);
      touchZones.set(id, zone);
      if (zone === 'poop' && state === ST.IDLE && onGround()) {
        state = ST.SQUATTING;
        tick  = 0;
      }
      if ((zone === 'jump-left' || zone === 'jump-right') && state === ST.IDLE && onGround()) {
        vy = JUMP_VEL;
      }
      if (zone === 'left'      || zone === 'jump-left')  { keys['ArrowLeft']  = true; facingLeft = true;  }
      if (zone === 'right'     || zone === 'jump-right') { keys['ArrowRight'] = true; facingLeft = false; }
    }

    function applyTouchEnd(id) {
      const zone = touchZones.get(id);
      touchZones.delete(id);
      const zones = [...touchZones.values()];
      // Only clear a direction key if no other touch is still holding it
      if ((zone === 'left'  || zone === 'jump-left')  && !zones.some(z => z === 'left'  || z === 'jump-left'))  delete keys['ArrowLeft'];
      if ((zone === 'right' || zone === 'jump-right') && !zones.some(z => z === 'right' || z === 'jump-right')) delete keys['ArrowRight'];
    }

    if (window.PointerEvent) {
      // Pointer Events API — works on iOS 13+, Android, desktop
      canvas.addEventListener('pointerdown', (e) => {
        e.preventDefault();
        canvas.setPointerCapture(e.pointerId);
        applyTouchStart(e.pointerId, e.clientX, e.clientY);
      });
      canvas.addEventListener('pointerup',     (e) => { e.preventDefault(); applyTouchEnd(e.pointerId); });
      canvas.addEventListener('pointercancel', (e) => { applyTouchEnd(e.pointerId); });
    } else {
      // Touch Events fallback
      canvas.addEventListener('touchstart', (e) => {
        e.preventDefault();
        for (const t of e.changedTouches) applyTouchStart(t.identifier, t.clientX, t.clientY);
      }, { passive: false });
      canvas.addEventListener('touchend', (e) => {
        e.preventDefault();
        for (const t of e.changedTouches) applyTouchEnd(t.identifier);
      }, { passive: false });
      canvas.addEventListener('touchcancel', (e) => {
        for (const t of e.changedTouches) applyTouchEnd(t.identifier);
      }, { passive: false });
    }

    // ── Simulation ─────────────────────────────────────────────────────────────

    function advance() {
      tick++;
      for (const p of poops) p.age++;

      switch (state) {

        case ST.WALK_IN:
          dogX += SPEED_IN;
          dogY  = ground();   // track real bottom while keyboard finishes closing
          if (dogX >= targetX()) {
            dogX = targetX();
            state = ST.IDLE;
            tick  = 0;
          }
          break;

        case ST.IDLE:
          if (keys['ArrowLeft'])  { dogX -= MOVE_SPEED; facingLeft = true;  }
          if (keys['ArrowRight']) { dogX += MOVE_SPEED; facingLeft = false; }
          vy   += GRAVITY;
          dogY += vy;
          if (dogY >= ground()) { dogY = ground(); vy = 0; }
          dogX = Math.max(0, Math.min(vpW - DOG_SIZE, dogX));
          break;

        case ST.SQUATTING:
          // Drop the poop at the mid-point of the animation
          if (tick === 35) {
            poops.push({
              x:   dogX + (facingLeft ? DOG_SIZE * 0.55 : DOG_SIZE * 0.1),
              y:   dogY + DOG_SIZE * 0.72,
              age: 0,
            });
          }
          if (tick > 70) { state = ST.IDLE; tick = 0; }
          break;
      }
    }

    // ── Rendering ──────────────────────────────────────────────────────────────

    function drawDog(x, y, flipX, squatting, moving) {
      ctx.save();
      if (squatting) {
        // Squash: anchor feet to ground, widen slightly
        ctx.translate(x + DOG_SIZE / 2, y + DOG_SIZE);
        if (flipX) ctx.scale(-1, 1);
        ctx.scale(1.15, 0.78);
        ctx.drawImage(img, -DOG_SIZE / 2, -DOG_SIZE, DOG_SIZE, DOG_SIZE);
      } else {
        const bob = moving ? Math.round(Math.sin(tick * 0.35) * 3) : 0;
        if (flipX) {
          ctx.translate(x + DOG_SIZE, 0);
          ctx.scale(-1, 1);
          ctx.drawImage(img, 0, y + bob, DOG_SIZE, DOG_SIZE);
        } else {
          ctx.drawImage(img, x, y + bob, DOG_SIZE, DOG_SIZE);
        }
      }
      ctx.restore();
    }

    function render() {
      ctx.clearRect(0, 0, vpW, vpH);

      // Semi-transparent dark overlay so the white dog stands out
      ctx.fillStyle = 'rgba(0, 0, 0, 0.45)';
      ctx.fillRect(0, 0, vpW, vpH);

      // Poops + fresh steam
      for (const p of poops) {
        drawPoopSprite(ctx, p.x, p.y);
        if (p.age < 80) {
          const fade = 1 - p.age / 80;
          ctx.fillStyle = '#b8b0a0';
          ctx.globalAlpha = fade * 0.55;
          const t = p.age * 0.18;
          for (let i = 0; i < 3; i++) {
            const rise   = (p.age * 1.2 + i * 9) % 22;
            const wobble = Math.round(Math.sin(t + i * 1.8) * 2);
            const sx = p.x + (1 + i * 2) * POOP_SCALE + wobble;
            const sy = p.y - rise - POOP_SCALE;
            if (sy > 0) ctx.fillRect(sx, sy, POOP_SCALE - 1, POOP_SCALE - 1);
          }
          ctx.globalAlpha = 1;
        }
      }

      // Dog — bob when walking in or moving horizontally
      const moving = state === ST.WALK_IN ||
        (state === ST.IDLE && (keys['ArrowLeft'] || keys['ArrowRight']));
      drawDog(dogX, dogY, facingLeft, state === ST.SQUATTING, moving);
    }

    (function loop() {
      advance();
      render();
      requestAnimationFrame(loop);
    }());
  }

  // ── Wire up ───────────────────────────────────────────────────────────────────

  function attach() {
    const form = document.getElementById('searchForm');
    if (!form) return;
    form.addEventListener('submit', function (e) {
      const hasDubravko = ['origin', 'destination'].some(id =>
        (document.getElementById(id)?.value ?? '').toLowerCase().includes('dubravko')
      );
      if (!hasDubravko) return;
      e.preventDefault();
      e.stopImmediatePropagation();

      // Dismiss the on-screen keyboard first, then wait for the viewport to
      // expand back before launching (so the dog lands at the real screen bottom).
      if (document.activeElement instanceof HTMLElement) document.activeElement.blur();

      const vp = window.visualViewport;
      const keyboardUp = vp && (window.innerHeight - vp.height) > 100;

      if (!keyboardUp) {
        launch();
        return;
      }

      // Keyboard is visible — wait for it to close (visualViewport resize)
      // or fall back after 700 ms if it never fires.
      function onKbGone() {
        if (window.innerHeight - vp.height <= 100) {
          vp.removeEventListener('resize', onKbGone);
          clearTimeout(fallback);
          launch();
        }
      }
      const fallback = setTimeout(() => {
        vp.removeEventListener('resize', onKbGone);
        launch();
      }, 700);
      vp.addEventListener('resize', onKbGone);
    }, { capture: true });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', attach);
  } else {
    attach();
  }
}());
