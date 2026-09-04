/* PART 11: the particle field. Visual state only - no application data, no
 * API calls, no security decisions. FridayField exposes a tiny control
 * surface (form/disperse/ripple/pause/resume) and nothing else. */

(function () {
  "use strict";

  const BASE = "#0a0410";
  const NEBULA_HUES = [338, 348, 320, 190, 175, 300];
  const STAR_COUNT = 130;
  const PARTICLE_COUNT_DESKTOP = 440;
  const PARTICLE_COUNT_SMALL = 220;

  function rand(min, max) { return min + Math.random() * (max - min); }
  function clamp(v, min, max) { return Math.max(min, Math.min(max, v)); }
  function lerp(a, b, t) { return a + (b - a) * t; }

  function hueColor(h, s, l, a) {
    return `hsla(${h}, ${s}%, ${l}%, ${a})`;
  }

  class FridayField {
    constructor(canvas) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this.dpr = Math.min(2, window.devicePixelRatio || 1);
      this.w = 0;
      this.h = 0;
      this.cx = 0;
      this.cy = 0;

      this.reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      this.formFactor = this.reducedMotion ? 1 : 0; // 0 dispersed .. 1 formed
      this.targetForm = this.reducedMotion ? 1 : 0;
      this.breathe = 0;
      this.ripples = [];
      this.paused = false;
      this._raf = null;
      this._lastActivity = performance.now();
      this._lastTick = performance.now();

      this._buildNebula();
      this._buildStars();
      this.resize();
      window.addEventListener("resize", () => this.resize());
      document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "hidden") this.pause();
        else this.resume();
      });
    }

    resize() {
      const rect = this.canvas.getBoundingClientRect();
      this.w = rect.width;
      this.h = rect.height;
      this.cx = this.w / 2;
      this.cy = this.h / 2;
      this.canvas.width = Math.round(this.w * this.dpr);
      this.canvas.height = Math.round(this.h * this.dpr);
      this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      if (!this.particles) this._buildParticles();
    }

    _buildNebula() {
      this.nebula = NEBULA_HUES.map((hue) => ({
        hue,
        angle: rand(0, Math.PI * 2),
        dist: rand(0.05, 0.3),
        radius: rand(160, 350),
        opacity: rand(0.02, 0.038),
        speed: rand(0.7, 1.3) * 0.00028,
      }));
    }

    _buildStars() {
      this.stars = [];
      for (let i = 0; i < STAR_COUNT; i++) {
        const roll = Math.random();
        let radius, alpha, bright = false;
        if (roll > 1 - 0.035) {
          radius = 1.2; alpha = 0.82; bright = true;
        } else if (roll > 1 - 0.035 - 0.13) {
          radius = 0.7; alpha = 0.5;
        } else {
          radius = rand(0.18, 0.63); alpha = rand(0.1, 0.42);
        }
        this.stars.push({
          x: Math.random(), y: Math.random(),
          radius, alpha, bright,
          warm: Math.random() > 0.5,
          phase: rand(0, Math.PI * 2),
          twinkleSpeed: rand(0.0006, 0.0015),
        });
      }
    }

    _buildParticles() {
      const isSmall = Math.min(this.w, this.h) < 520;
      const count = isSmall ? PARTICLE_COUNT_SMALL : PARTICLE_COUNT_DESKTOP;
      this.particles = [];
      for (let i = 0; i < count; i++) {
        const roll = Math.random();
        let hue;
        if (roll < 0.38) hue = rand(340, 358);
        else if (roll < 0.64) hue = rand(318, 334);
        else if (roll < 0.86) hue = rand(186, 198);
        else hue = rand(28, 42);

        this.particles.push({
          hue,
          x: rand(0, this.w),
          y: rand(0, this.h),
          vx: rand(-0.15, 0.15),
          vy: rand(-0.15, 0.15),
          wanderAngle: rand(0, Math.PI * 2),
          orbitRadius: rand(32, 88),
          eccentricity: rand(0.72, 0.96),
          tilt: rand(-0.25, 0.25),
          orbitAngle: rand(0, Math.PI * 2),
          orbitSpeed: rand(0.0003, 0.0007) * (Math.random() < 0.5 ? -1 : 1),
          size: rand(0.6, 1.8),
          px: 0, py: 0,
        });
      }
    }

    form() {
      this.targetForm = 1;
      this._lastActivity = performance.now();
    }

    disperse() {
      this.targetForm = 0;
    }

    ripple() {
      this._lastActivity = performance.now();
      this.ripples.push({ radius: 0, age: 0 });
      if (this.ripples.length > 6) this.ripples.shift();
    }

    pause() {
      this.paused = true;
      if (this._raf) cancelAnimationFrame(this._raf);
      this._raf = null;
    }

    resume() {
      if (this.paused) {
        this.paused = false;
        this._lastTick = performance.now();
        this._raf = requestAnimationFrame((t) => this._tick(t));
      }
    }

    start() {
      if (this.reducedMotion) {
        this._renderStatic();
        return;
      }
      this._raf = requestAnimationFrame((t) => this._tick(t));
    }

    _renderStatic() {
      // Reduced motion: draw once in the formed state, no rAF loop.
      this.formFactor = 1;
      this._draw(0);
    }

    _tick(now) {
      const dt = Math.min(64, now - this._lastTick);
      this._lastTick = now;

      if (!this.reducedMotion && now - this._lastActivity > 8000) {
        this.targetForm = 0;
      }

      this.formFactor = lerp(this.formFactor, this.targetForm, 0.028);
      this.breathe += dt * 0.0012;

      this._draw(dt);

      if (!this.paused) {
        this._raf = requestAnimationFrame((t) => this._tick(t));
      }
    }

    _draw(dt) {
      const ctx = this.ctx;
      ctx.globalCompositeOperation = "source-over";
      ctx.fillStyle = BASE;
      ctx.fillRect(0, 0, this.w, this.h);

      ctx.globalCompositeOperation = "lighter";
      this._drawNebula(dt);
      this._drawStars(dt);
      this._drawRipples(dt);
      this._drawCoreGlow();
      this._drawParticles(dt);
      ctx.globalCompositeOperation = "source-over";
    }

    _drawNebula(dt) {
      const ctx = this.ctx;
      const minDim = Math.min(this.w, this.h);
      for (const n of this.nebula) {
        n.angle += n.speed * dt;
        const dist = n.dist * minDim;
        const x = this.cx + Math.cos(n.angle) * dist;
        const y = this.cy + Math.sin(n.angle) * dist * 0.7;
        const grad = ctx.createRadialGradient(x, y, 0, x, y, n.radius);
        grad.addColorStop(0, hueColor(n.hue, 70, 55, n.opacity));
        grad.addColorStop(1, hueColor(n.hue, 70, 55, 0));
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(x, y, n.radius, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    _drawStars(dt) {
      const ctx = this.ctx;
      for (const s of this.stars) {
        s.phase += s.twinkleSpeed * dt;
        const twinkle = 0.75 + Math.sin(s.phase) * 0.25;
        const alpha = s.alpha * twinkle;
        const x = s.x * this.w;
        const y = s.y * this.h;
        const tint = s.warm ? "255,244,230" : "225,235,255";
        ctx.fillStyle = `rgba(${tint},${alpha})`;
        ctx.beginPath();
        ctx.arc(x, y, s.radius, 0, Math.PI * 2);
        ctx.fill();

        if (s.bright) {
          ctx.strokeStyle = `rgba(255,235,245,${alpha * 0.24})`;
          ctx.lineWidth = 0.45;
          ctx.beginPath();
          ctx.moveTo(x - 6.5, y); ctx.lineTo(x + 6.5, y);
          ctx.moveTo(x, y - 6.5); ctx.lineTo(x, y + 6.5);
          ctx.stroke();
        }
      }
    }

    _drawRipples(dt) {
      const ctx = this.ctx;
      const next = [];
      for (const r of this.ripples) {
        r.age += dt;
        r.radius = r.age * 0.28;
        const life = 1 - r.radius / 260;
        if (life <= 0) continue;
        ctx.strokeStyle = `rgba(230,180,210,${0.16 * life})`;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(this.cx, this.cy, r.radius, 0, Math.PI * 2);
        ctx.stroke();
        next.push(r);
      }
      this.ripples = next;
    }

    _drawCoreGlow() {
      if (this.formFactor <= 0.15) return;
      const ctx = this.ctx;
      const strength = (this.formFactor - 0.15) / 0.85;
      const radius = 70 + Math.sin(this.breathe) * 6;
      const grad = ctx.createRadialGradient(this.cx, this.cy, 0, this.cx, this.cy, radius);
      grad.addColorStop(0, `rgba(255,235,220,${0.05 * strength})`);
      grad.addColorStop(1, "rgba(255,235,220,0)");
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(this.cx, this.cy, radius, 0, Math.PI * 2);
      ctx.fill();
    }

    _rippleBoost(x, y) {
      let boost = 0;
      for (const r of this.ripples) {
        const d = Math.hypot(x - this.cx, y - this.cy);
        const edge = Math.abs(d - r.radius);
        if (edge < 22) boost = Math.max(boost, (1 - edge / 22) * (1 - r.radius / 260));
      }
      return boost;
    }

    _drawParticles(dt) {
      const ctx = this.ctx;
      const f = this.formFactor;
      const breatheScale = 1 + Math.sin(this.breathe) * 0.05;

      for (const p of this.particles) {
        // Dispersed target: gentle independent wander, bounded to viewport.
        p.wanderAngle += rand(-0.02, 0.02);
        const wx = p.x + Math.cos(p.wanderAngle) * 0.3;
        const wy = p.y + Math.sin(p.wanderAngle) * 0.3;
        p.vx = lerp(p.vx, (wx - p.x) * 0.02, 0.1);
        p.vy = lerp(p.vy, (wy - p.y) * 0.02, 0.1);

        // Formed target: elliptical orbit around centre.
        p.orbitAngle += p.orbitSpeed * dt;
        const rx = p.orbitRadius * breatheScale;
        const ry = p.orbitRadius * p.eccentricity * breatheScale;
        const ox = Math.cos(p.orbitAngle) * rx;
        const oy = Math.sin(p.orbitAngle) * ry;
        const tiltedX = ox * Math.cos(p.tilt) - oy * Math.sin(p.tilt);
        const tiltedY = ox * Math.sin(p.tilt) + oy * Math.cos(p.tilt);
        const targetX = this.cx + tiltedX;
        const targetY = this.cy + tiltedY * 0.6;

        p.x += p.vx;
        p.y += p.vy;
        p.x = lerp(p.x, targetX, 0.038 * f);
        p.y = lerp(p.y, targetY, 0.038 * f);
        p.vx *= 0.9;
        p.vy *= 0.9;

        // Keep dispersed particles softly bounded.
        const margin = 20;
        if (p.x < -margin) p.x = this.w + margin;
        if (p.x > this.w + margin) p.x = -margin;
        if (p.y < -margin) p.y = this.h + margin;
        if (p.y > this.h + margin) p.y = -margin;

        const boost = this._rippleBoost(p.x, p.y);
        const baseAlpha = lerp(0.2, 0.66, f);
        const alpha = clamp(baseAlpha + boost * 0.5, 0, 1);

        ctx.fillStyle = hueColor(p.hue, 75, 68, alpha);
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
        ctx.fill();

        if (p.size > 1) {
          const grad = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.size * 3);
          grad.addColorStop(0, hueColor(p.hue, 75, 68, alpha * 0.18));
          grad.addColorStop(1, hueColor(p.hue, 75, 68, 0));
          ctx.fillStyle = grad;
          ctx.beginPath();
          ctx.arc(p.x, p.y, p.size * 3, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    }
  }

  window.FridayField = FridayField;
})();
