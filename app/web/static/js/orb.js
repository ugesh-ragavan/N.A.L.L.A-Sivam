/**
 * app/web/static/js/orb.js
 *
 * NALLA SIVAM — Innovative 3D Living Particle Energy Core & Fluid Audio Visualizer.
 *
 * Combines the beloved organic 3D particle orb with an innovative fluid audio-reactive
 * energy field and gyroscopic gimbal rings:
 *   • IDLE: Organic slow-breathing core, depth-projected 3D particle swarm gently drifting.
 *   • LISTENING: Expanding sonar rings, fluid audio frequency ribbon reacting smoothly to mic level.
 *   • TRANSCRIBING / THINKING: Accelerated multi-axis 3D gyroscopic gimbal rings with orbital photon sparks.
 *   • SPEAKING: Harmonic resonant energy pulses radiating in cadence with voice playback.
 *   • ERROR: Crimson alert pulse with electric discharge.
 *
 * Performance & Smoothness:
 *   • True 60 FPS delta-time physics.
 *   • ZERO layout thrashing: Cached CSS accent colors (no getComputedStyle inside the render loop).
 *   • Smooth exponential moving average (EMA) audio smoothing (no jitter or sudden snapping).
 *   • Page Visibility API: Pauses animation when the tab is hidden.
 *   • Respects prefers-reduced-motion.
 */

(function () {
  "use strict";

  let canvas = null;
  let ctx = null;
  let animId = null;
  let width = 340;
  let height = 340;
  let currentState = "IDLE";
  let rawAudioLevel = 0.0;
  let smoothedAudioLevel = 0.0;
  let tick = 0;
  let lastFrameTime = performance.now();
  let isTabVisible = true;
  let prefersReducedMotion = false;

  // Cached theme color to prevent layout thrashing
  let cachedAccentRgb = "0, 240, 255"; // Default Cyan
  let lastColorCacheTime = 0;

  function updateCachedAccentColor(force = false) {
    const now = performance.now();
    if (force || now - lastColorCacheTime > 2000) {
      try {
        const rgb = getComputedStyle(document.body).getPropertyValue("--accent-rgb").trim();
        if (rgb) cachedAccentRgb = rgb;
      } catch (_) {}
      lastColorCacheTime = now;
    }
  }

  function getAccentColor(alpha = 1.0) {
    return `rgba(${cachedAccentRgb}, ${alpha})`;
  }

  // -------------------------------------------------------------------------
  // 3D Spherical Particle System
  // -------------------------------------------------------------------------
  const PARTICLE_COUNT = 80;
  const particles = [];
  const FOCAL_LENGTH = 220;

  class Particle3D {
    constructor() {
      this.init();
    }

    init() {
      // Spherical coordinates for true 3D orbital motion
      this.radius = 35 + Math.random() * 85;
      this.theta = Math.random() * Math.PI * 2; // Azimuth
      this.phi = (Math.random() - 0.5) * Math.PI; // Elevation
      this.speedTheta = (0.006 + Math.random() * 0.012) * (Math.random() > 0.5 ? 1 : -1);
      this.speedPhi = (0.003 + Math.random() * 0.006) * (Math.random() > 0.5 ? 1 : -1);
      this.size = 1.2 + Math.random() * 2.2;
      this.baseAlpha = 0.35 + Math.random() * 0.6;
    }

    update(speedMultiplier, dtFactor) {
      this.theta += this.speedTheta * speedMultiplier * dtFactor;
      this.phi += this.speedPhi * speedMultiplier * dtFactor;

      // Wrap angles
      if (this.theta > Math.PI * 2) this.theta -= Math.PI * 2;
      if (this.theta < 0) this.theta += Math.PI * 2;

      // Keep phi within spherical bounds
      if (Math.abs(this.phi) > Math.PI * 0.48) {
        this.speedPhi = -this.speedPhi;
      }
    }

    getProjected(cx, cy, currentCoreR) {
      // Compute 3D Cartesian coordinates
      const r = this.radius + currentCoreR * 0.25;
      const x3d = r * Math.cos(this.phi) * Math.cos(this.theta);
      const y3d = r * Math.sin(this.phi);
      const z3d = r * Math.cos(this.phi) * Math.sin(this.theta);

      // Perspective projection
      const scale = FOCAL_LENGTH / (FOCAL_LENGTH + z3d + 120);
      const x2d = cx + x3d * scale;
      const y2d = cy + y3d * scale;
      const radius2d = Math.max(0.6, this.size * scale);

      // Depth fade: particles behind the core fade smoothly
      const depthAlpha = Math.max(0.12, Math.min(1.0, (z3d + 100) / 200)) * this.baseAlpha;

      return { x: x2d, y: y2d, r: radius2d, alpha: depthAlpha, z: z3d };
    }
  }

  // -------------------------------------------------------------------------
  // Initialization & Canvas Sizing
  // -------------------------------------------------------------------------
  function initOrb(canvasElement) {
    if (!canvasElement) return;
    canvas = canvasElement;
    ctx = canvas.getContext("2d");

    prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    updateCachedAccentColor(true);

    resize();
    window.addEventListener("resize", resize);

    // Initialize particle swarm
    particles.length = 0;
    for (let i = 0; i < PARTICLE_COUNT; i++) {
      particles.push(new Particle3D());
    }

    // Tab visibility handling (pause loop when tab is hidden to save CPU & battery)
    document.addEventListener("visibilitychange", () => {
      isTabVisible = !document.hidden;
      if (isTabVisible && !animId) {
        lastFrameTime = performance.now();
        animId = requestAnimationFrame(renderLoop);
      } else if (!isTabVisible && animId) {
        cancelAnimationFrame(animId);
        animId = null;
      }
    });

    if (animId) cancelAnimationFrame(animId);
    lastFrameTime = performance.now();
    animId = requestAnimationFrame(renderLoop);
  }

  function resize() {
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const size = Math.min(rect.width || 340, 380);
    width = size;
    height = size;
    const dpr = Math.min(window.devicePixelRatio || 1, 2); // Cap at 2x for smooth 60fps
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    ctx.resetTransform();
    ctx.scale(dpr, dpr);
  }

  function setVoiceState(state, level = 0.0) {
    currentState = (state || "IDLE").toUpperCase();
    rawAudioLevel = Math.max(0.0, Math.min(1.0, level));
  }

  // -------------------------------------------------------------------------
  // Main Render Loop (Butter-Smooth 60 FPS Delta-Time)
  // -------------------------------------------------------------------------
  function renderLoop(currentTime) {
    if (!isTabVisible) {
      animId = null;
      return;
    }

    animId = requestAnimationFrame(renderLoop);

    // Delta-time normalization
    const deltaMs = Math.min(currentTime - lastFrameTime, 50); // Clamp spikes
    lastFrameTime = currentTime;
    const dtFactor = deltaMs / 16.667; // 1.0 at 60 FPS

    updateCachedAccentColor(false);

    // Smooth audio reactivity with exponential moving average
    smoothedAudioLevel += (rawAudioLevel - smoothedAudioLevel) * (0.22 * dtFactor);

    drawFrame(dtFactor);
  }

  function drawFrame(dtFactor) {
    if (!ctx) return;
    ctx.clearRect(0, 0, width, height);

    const cx = width / 2;
    const cy = height / 2;

    const isError = currentState === "ERROR";
    const isListening = currentState === "LISTENING";
    const isThinking = currentState === "THINKING" || currentState === "TRANSCRIBING";
    const isSpeaking = currentState === "SPEAKING";

    // Advance animation tick
    if (!prefersReducedMotion) {
      let tickSpeed = 0.022;
      if (isThinking) tickSpeed = 0.065;
      else if (isSpeaking) tickSpeed = 0.045;
      else if (isListening) tickSpeed = 0.032;
      tick += tickSpeed * dtFactor;
    }

    // Determine core dynamics based on voice state
    let speedMult = 1.0;
    let coreRadius = 46;
    let glowRadius = 72;

    if (currentState === "IDLE") {
      speedMult = 1.0;
      const breath = Math.sin(tick * 1.6) * 3.5;
      coreRadius = 46 + breath;
      glowRadius = 74 + breath * 2.2;
    } else if (isListening) {
      speedMult = 1.6;
      const micPulse = smoothedAudioLevel * 22;
      coreRadius = 48 + micPulse + Math.sin(tick * 4) * 2;
      glowRadius = 88 + micPulse * 2.5;
    } else if (isThinking) {
      speedMult = 3.6;
      coreRadius = 48 + Math.sin(tick * 5) * 4;
      glowRadius = 94;
    } else if (isSpeaking) {
      speedMult = 2.2;
      const speechWave = Math.abs(Math.sin(tick * 7)) * 14 + smoothedAudioLevel * 10;
      coreRadius = 50 + speechWave;
      glowRadius = 104 + speechWave * 1.8;
    } else if (isError) {
      speedMult = 0.6;
      coreRadius = 44 + Math.sin(tick * 8) * 2;
      glowRadius = 76;
    }

    // 1. Soft Outer Atmospheric Aura Glow
    drawAtmosphericAura(cx, cy, glowRadius, isError, isSpeaking);

    // 2. Concentric Sonar Ripples & Mic Reactive Spikes (LISTENING)
    if (isListening) {
      drawSonarRipples(cx, cy, tick, smoothedAudioLevel);
      drawMicSensorSpikes(cx, cy, coreRadius, smoothedAudioLevel);
    }

    // 3. Radial Voice Waveform & Harmonic Ribbon (SPEAKING)
    if (isSpeaking) {
      drawSpeakingVoiceWaveform(cx, cy, coreRadius, smoothedAudioLevel);
    }

    // 4. 3D Particle Field (Sorted by Z for correct depth rendering)
    drawParticleField(cx, cy, coreRadius, speedMult, dtFactor, isError);

    // 5. Gyroscopic Gimbal Rings (THINKING / TRANSCRIBING)
    if (isThinking) {
      drawGyroscopicGimbal(cx, cy, tick);
    }

    // 6. Central Fusion Core (White-hot nucleus with soft theme glow)
    drawFusionCore(cx, cy, coreRadius, isError, isSpeaking, isListening);

    // 7. Subtle Tactical Focus Ring & Reticle (Minimal & Smooth)
    drawFocusReticle(cx, cy, coreRadius, isError);
  }

  // -------------------------------------------------------------------------
  // 1. Atmospheric Aura
  // -------------------------------------------------------------------------
  function drawAtmosphericAura(cx, cy, radius, isError, isSpeaking) {
    const grad = ctx.createRadialGradient(cx, cy, radius * 0.2, cx, cy, radius);
    if (isError) {
      grad.addColorStop(0, "rgba(239, 68, 68, 0.45)");
      grad.addColorStop(0.6, "rgba(239, 68, 68, 0.12)");
      grad.addColorStop(1, "transparent");
    } else if (isSpeaking) {
      grad.addColorStop(0, "rgba(251, 191, 36, 0.35)"); // Warm golden speech aura
      grad.addColorStop(0.45, getAccentColor(0.22));
      grad.addColorStop(1, "transparent");
    } else {
      grad.addColorStop(0, getAccentColor(0.38));
      grad.addColorStop(0.55, getAccentColor(0.12));
      grad.addColorStop(1, "transparent");
    }
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.fill();
  }

  // -------------------------------------------------------------------------
  // 2. Sonar Ripples & Mic Sensor Spikes (LISTENING)
  // -------------------------------------------------------------------------
  function drawSonarRipples(cx, cy, t, audioLevel) {
    ctx.lineWidth = 1.4;
    const count = 4;
    for (let i = 0; i < count; i++) {
      const progress = (t * 0.8 + i / count) % 1;
      const r = 50 + progress * (76 + audioLevel * 35);
      const alpha = (1 - progress) * 0.6;
      ctx.strokeStyle = getAccentColor(alpha);
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.stroke();
    }
  }

  function drawMicSensorSpikes(cx, cy, baseR, audioLevel) {
    const barCount = 32;
    ctx.save();
    ctx.translate(cx, cy);

    for (let i = 0; i < barCount; i++) {
      const angle = (i / barCount) * Math.PI * 2;
      const wave = Math.sin(angle * 4 + tick * 5) * 0.3 + 0.7;
      const spikeLen = 6 + (audioLevel * 32 + wave * 6);
      const innerR = baseR + 5;
      const outerR = innerR + spikeLen;

      const x1 = Math.cos(angle) * innerR;
      const y1 = Math.sin(angle) * innerR;
      const x2 = Math.cos(angle) * outerR;
      const y2 = Math.sin(angle) * outerR;

      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.lineWidth = 1.8;
      ctx.strokeStyle = getAccentColor(Math.min(1.0, 0.4 + audioLevel * 0.6));
      ctx.stroke();

      // White energy pip at outer tip
      if (audioLevel > 0.08) {
        ctx.beginPath();
        ctx.arc(x2, y2, 1.2, 0, Math.PI * 2);
        ctx.fillStyle = "#ffffff";
        ctx.fill();
      }
    }
    ctx.restore();
  }

  // -------------------------------------------------------------------------
  // 3. Speaking Radial Voice Waveform & Ribbon (SPEAKING)
  // -------------------------------------------------------------------------
  function drawSpeakingVoiceWaveform(cx, cy, baseR, audioLevel) {
    const barCount = 40;
    ctx.save();
    ctx.translate(cx, cy);

    const ribbonPoints = [];

    // Radial voice waveform bars
    for (let i = 0; i < barCount; i++) {
      const theta = (i / barCount) * Math.PI * 2;
      const harmonic1 = Math.sin(theta * 6 + tick * 8);
      const harmonic2 = Math.cos(theta * 10 - tick * 4) * 0.6;
      const cadence = Math.abs(harmonic1 + harmonic2);
      const barHeight = 8 + cadence * 12 + audioLevel * 30;

      const r1 = baseR + 6;
      const r2 = r1 + barHeight;

      const x1 = Math.cos(theta) * r1;
      const y1 = Math.sin(theta) * r1;
      const x2 = Math.cos(theta) * r2;
      const y2 = Math.sin(theta) * r2;

      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.lineWidth = 2.0;

      // Alternating gold and theme accent
      if (i % 2 === 0) {
        ctx.strokeStyle = `rgba(251, 191, 36, ${Math.min(0.95, 0.5 + audioLevel * 0.45)})`;
      } else {
        ctx.strokeStyle = getAccentColor(Math.min(0.95, 0.5 + audioLevel * 0.45));
      }
      ctx.stroke();

      ribbonPoints.push({ x: x2, y: y2 });
    }

    // Connect peak points with fluid ribbon
    if (ribbonPoints.length > 2) {
      ctx.beginPath();
      ctx.moveTo(ribbonPoints[0].x, ribbonPoints[0].y);
      for (let i = 1; i < ribbonPoints.length; i++) {
        ctx.lineTo(ribbonPoints[i].x, ribbonPoints[i].y);
      }
      ctx.closePath();
      ctx.strokeStyle = "rgba(251, 191, 36, 0.75)";
      ctx.lineWidth = 1.2;
      ctx.stroke();
    }

    ctx.restore();
  }

  // -------------------------------------------------------------------------
  // 4. 3D Particle Field
  // -------------------------------------------------------------------------
  function drawParticleField(cx, cy, currentCoreR, speedMult, dtFactor, isError) {
    const projected = [];

    for (let i = 0; i < particles.length; i++) {
      const p = particles[i];
      p.update(speedMult, dtFactor);
      projected.push(p.getProjected(cx, cy, currentCoreR));
    }

    // Sort by Z so background particles render behind the core
    projected.sort((a, b) => a.z - b.z);

    for (const p of projected) {
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = isError ? `rgba(239, 68, 68, ${p.alpha})` : getAccentColor(p.alpha);
      ctx.fill();
    }
  }

  // -------------------------------------------------------------------------
  // 5. Gyroscopic Gimbal Rings (THINKING / TRANSCRIBING)
  // -------------------------------------------------------------------------
  function drawGyroscopicGimbal(cx, cy, t) {
    ctx.save();
    ctx.translate(cx, cy);

    // Primary Gimbal Ring (Tilted, rotating clockwise)
    ctx.save();
    ctx.rotate(t * 1.8);
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = getAccentColor(0.65);
    ctx.beginPath();
    ctx.ellipse(0, 0, 80, 28, 0, 0, Math.PI * 2);
    ctx.stroke();

    // Orbital photon spark traveling on the primary ring
    const sparkAngle = t * 3.2;
    const sx = Math.cos(sparkAngle) * 80;
    const sy = Math.sin(sparkAngle) * 28;
    ctx.beginPath();
    ctx.arc(sx, sy, 2.8, 0, Math.PI * 2);
    ctx.fillStyle = "#ffffff";
    ctx.fill();
    ctx.restore();

    // Secondary Gimbal Ring (Counter-rotating at 60-degree tilt)
    ctx.save();
    ctx.rotate(-t * 1.4 + Math.PI / 3);
    ctx.lineWidth = 1.2;
    ctx.strokeStyle = getAccentColor(0.45);
    ctx.beginPath();
    ctx.ellipse(0, 0, 92, 32, 0, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();

    ctx.restore();
  }

  // -------------------------------------------------------------------------
  // 6. Fusion Energy Core
  // -------------------------------------------------------------------------
  function drawFusionCore(cx, cy, r, isError, isSpeaking, isListening) {
    const grad = ctx.createRadialGradient(cx - r * 0.22, cy - r * 0.22, 2, cx, cy, r);
    if (isError) {
      grad.addColorStop(0, "#ffffff");
      grad.addColorStop(0.32, "#ef4444");
      grad.addColorStop(0.75, "rgba(239, 68, 68, 0.4)");
      grad.addColorStop(1, "transparent");
    } else if (isSpeaking) {
      grad.addColorStop(0, "#ffffff"); // White-hot center
      grad.addColorStop(0.22, "rgba(251, 191, 36, 0.95)"); // Golden voice energy
      grad.addColorStop(0.65, getAccentColor(0.5)); // Theme corona
      grad.addColorStop(1, "transparent");
    } else if (isListening) {
      grad.addColorStop(0, "#ffffff"); // Concentrated focus singularity
      grad.addColorStop(0.2, getAccentColor(1.0)); // High-intensity reception
      grad.addColorStop(0.68, getAccentColor(0.42));
      grad.addColorStop(1, "transparent");
    } else {
      grad.addColorStop(0, "#ffffff"); // White-hot singularity
      grad.addColorStop(0.28, getAccentColor(0.95)); // Saturated theme tone
      grad.addColorStop(0.72, getAccentColor(0.35)); // Soft corona
      grad.addColorStop(1, "transparent");
    }

    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fill();
  }

  // -------------------------------------------------------------------------
  // 7. Tactical Reticle & Focus Crosshair (Clean & Minimal)
  // -------------------------------------------------------------------------
  function drawFocusReticle(cx, cy, coreR, isError) {
    ctx.strokeStyle = isError ? "rgba(239, 68, 68, 0.3)" : getAccentColor(0.28);
    ctx.lineWidth = 1.0;

    const gap = coreR + 6;
    const length = 10;

    ctx.beginPath();
    // Left
    ctx.moveTo(cx - gap - length, cy);
    ctx.lineTo(cx - gap, cy);
    // Right
    ctx.moveTo(cx + gap, cy);
    ctx.lineTo(cx + gap + length, cy);
    // Top
    ctx.moveTo(cx, cy - gap - length);
    ctx.lineTo(cx, cy - gap);
    // Bottom
    ctx.moveTo(cx, cy + gap);
    ctx.lineTo(cx, cy + gap + length);
    ctx.stroke();
  }

  // Public API
  window.NallaOrb = {
    init: initOrb,
    setState: setVoiceState,
    resize: resize,
    updateTheme: () => updateCachedAccentColor(true),
  };
})();
