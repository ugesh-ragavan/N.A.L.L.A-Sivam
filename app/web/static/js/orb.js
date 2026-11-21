/**
 * app/web/static/js/orb.js
 *
 * Futuristic Interactive 3D Particle Orb Centerpiece.
 * Reacts dynamically to VoiceState:
 *   • IDLE: Slow breathing glow, gentle particle drift.
 *   • LISTENING: Concentric expanding sonar ripples and reactive energy spikes.
 *   • TRANSCRIBING / THINKING: Gyroscopic cyber-rings spinning on 3 axes with particle flares.
 *   • SPEAKING: Dynamic audio waveform pulses radiating outward.
 *   • ERROR: Red alert glitch flicker.
 */

(function () {
  "use strict";

  let canvas = null;
  let ctx = null;
  let animId = null;
  let width = 320;
  let height = 320;
  let currentState = "IDLE";
  let audioLevel = 0.0;
  let tick = 0;

  // Particle swarm
  const PARTICLE_COUNT = 70;
  const particles = [];

  class OrbParticle {
    constructor() {
      this.reset();
    }

    reset() {
      const angle = Math.random() * Math.PI * 2;
      const dist = 30 + Math.random() * 80;
      this.x = Math.cos(angle) * dist;
      this.y = Math.sin(angle) * dist;
      this.z = (Math.random() - 0.5) * 80;
      this.radius = 1 + Math.random() * 2;
      this.speed = 0.005 + Math.random() * 0.015;
      this.angle = angle;
      this.dist = dist;
      this.alpha = 0.3 + Math.random() * 0.7;
    }

    update(speedMultiplier) {
      this.angle += this.speed * speedMultiplier;
      this.x = Math.cos(this.angle) * this.dist;
      this.y = Math.sin(this.angle) * this.dist;
    }
  }

  function initOrb(canvasElement) {
    if (!canvasElement) return;
    canvas = canvasElement;
    ctx = canvas.getContext("2d");

    resize();
    window.addEventListener("resize", resize);

    // Initialize particles
    particles.length = 0;
    for (let i = 0; i < PARTICLE_COUNT; i++) {
      particles.push(new OrbParticle());
    }

    // Start animation loop
    if (animId) cancelAnimationFrame(animId);
    loop();
  }

  function resize() {
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    width = rect.width || 320;
    height = rect.height || 320;
    canvas.width = width * window.devicePixelRatio;
    canvas.height = height * window.devicePixelRatio;
    ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
  }

  function setVoiceState(state, level = 0.0) {
    currentState = (state || "IDLE").toUpperCase();
    audioLevel = Math.max(0.0, Math.min(1.0, level));
  }

  function getAccentColor(alpha = 1.0) {
    // Read CSS variable --accent-rgb
    const rgb = getComputedStyle(document.body).getPropertyValue("--accent-rgb").trim() || "0, 240, 255";
    return `rgba(${rgb}, ${alpha})`;
  }

  function loop() {
    tick += 0.02;
    ctx.clearRect(0, 0, width, height);

    const cx = width / 2;
    const cy = height / 2;
    const baseColor = getAccentColor(1.0);

    // Determine speed and visuals based on state
    let speedMult = 1.0;
    let coreRadius = 45;
    let glowSize = 60;
    let coreColor = baseColor;

    if (currentState === "IDLE") {
      speedMult = 1.0;
      coreRadius = 45 + Math.sin(tick * 1.5) * 4;
      glowSize = 70 + Math.sin(tick * 1.5) * 10;
    } else if (currentState === "LISTENING") {
      speedMult = 1.4;
      const boost = audioLevel * 25;
      coreRadius = 50 + boost + Math.sin(tick * 4) * 3;
      glowSize = 90 + boost * 2;
      drawRipples(cx, cy, tick);
    } else if (currentState === "TRANSCRIBING" || currentState === "THINKING") {
      speedMult = 3.5;
      coreRadius = 48 + Math.sin(tick * 5) * 5;
      glowSize = 95;
      drawGyroRings(cx, cy, tick * 2);
    } else if (currentState === "SPEAKING") {
      speedMult = 2.2;
      const wave = Math.abs(Math.sin(tick * 8)) * 20;
      coreRadius = 52 + wave;
      glowSize = 110 + wave;
      drawWaveformPulses(cx, cy, tick);
    } else if (currentState === "ERROR") {
      speedMult = 0.5;
      coreColor = Math.sin(tick * 10) > 0 ? "rgba(239, 68, 68, 1)" : "rgba(239, 68, 68, 0.4)";
      glowSize = 80;
    }

    // 1. Outer ambient glow
    const gradGlow = ctx.createRadialGradient(cx, cy, coreRadius * 0.2, cx, cy, glowSize);
    gradGlow.addColorStop(0, currentState === "ERROR" ? "rgba(239,68,68,0.5)" : getAccentColor(0.45));
    gradGlow.addColorStop(0.5, currentState === "ERROR" ? "rgba(239,68,68,0.15)" : getAccentColor(0.12));
    gradGlow.addColorStop(1, "transparent");
    ctx.fillStyle = gradGlow;
    ctx.beginPath();
    ctx.arc(cx, cy, glowSize, 0, Math.PI * 2);
    ctx.fill();

    // 2. Render particle field
    ctx.fillStyle = currentState === "ERROR" ? "#ef4444" : getAccentColor(0.85);
    for (const p of particles) {
      p.update(speedMult);
      ctx.beginPath();
      ctx.arc(cx + p.x, cy + p.y, p.radius, 0, Math.PI * 2);
      ctx.fill();
    }

    // 3. Central Energy Core
    const gradCore = ctx.createRadialGradient(cx - coreRadius * 0.2, cy - coreRadius * 0.2, 2, cx, cy, coreRadius);
    gradCore.addColorStop(0, "#ffffff");
    gradCore.addColorStop(0.35, currentState === "ERROR" ? "#ef4444" : baseColor);
    gradCore.addColorStop(0.85, currentState === "ERROR" ? "#7f1d1d" : getAccentColor(0.3));
    gradCore.addColorStop(1, "transparent");

    ctx.fillStyle = gradCore;
    ctx.beginPath();
    ctx.arc(cx, cy, coreRadius, 0, Math.PI * 2);
    ctx.fill();

    // 4. Subtle inner HUD crosshair
    ctx.strokeStyle = getAccentColor(0.25);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(cx - coreRadius - 12, cy);
    ctx.lineTo(cx - coreRadius - 2, cy);
    ctx.moveTo(cx + coreRadius + 2, cy);
    ctx.lineTo(cx + coreRadius + 12, cy);
    ctx.moveTo(cx, cy - coreRadius - 12);
    ctx.lineTo(cx, cy - coreRadius - 2);
    ctx.moveTo(cx, cy + coreRadius + 2);
    ctx.lineTo(cx, cy + coreRadius + 12);
    ctx.stroke();

    animId = requestAnimationFrame(loop);
  }

  // Effect: Sonar ripples for LISTENING
  function drawRipples(cx, cy, t) {
    ctx.lineWidth = 1.5;
    for (let i = 0; i < 3; i++) {
      const prog = (t * 0.8 + i * 0.33) % 1;
      const r = 50 + prog * 70;
      const alpha = (1 - prog) * 0.6;
      ctx.strokeStyle = getAccentColor(alpha);
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.stroke();
    }
  }

  // Effect: Gyro rings for THINKING / TRANSCRIBING
  function drawGyroRings(cx, cy, angle) {
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = getAccentColor(0.7);

    // Ring 1: tilted ellipse
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(angle);
    ctx.beginPath();
    ctx.ellipse(0, 0, 75, 26, 0, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();

    // Ring 2: counter-rotating ellipse
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(-angle * 0.85 + Math.PI / 3);
    ctx.strokeStyle = getAccentColor(0.5);
    ctx.beginPath();
    ctx.ellipse(0, 0, 85, 30, 0, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();
  }

  // Effect: Radiating waveform pulses for SPEAKING
  function drawWaveformPulses(cx, cy, t) {
    ctx.lineWidth = 2;
    ctx.strokeStyle = getAccentColor(0.65);
    const bars = 36;
    for (let i = 0; i < bars; i++) {
      const theta = (i / bars) * Math.PI * 2;
      const len = 6 + Math.abs(Math.sin(i * 0.8 + t * 5)) * 18;
      const x1 = cx + Math.cos(theta) * 62;
      const y1 = cy + Math.sin(theta) * 62;
      const x2 = cx + Math.cos(theta) * (62 + len);
      const y2 = cy + Math.sin(theta) * (62 + len);
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.stroke();
    }
  }

  // Expose API
  window.NallaOrb = {
    init: initOrb,
    setState: setVoiceState,
  };
})();
