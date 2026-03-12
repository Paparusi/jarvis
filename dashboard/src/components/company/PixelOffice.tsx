"use client";

import { useEffect, useRef, useCallback } from "react";
import type { WSEvent } from "@/hooks/useWebSocketHub";
import {
  GW, GH, ROOMS, CORRIDOR_Y, CORRIDOR_H, DESKS, COFFEE_POS,
  PLANT_POSITIONS, BOARD_POSITIONS, FURNITURE, WATER_COOLER_POS,
  MEETING_SEATS, MEETING_TABLE_POS,
} from "./office-map";
import {
  CHAR_W, CHAR_H, DESK_W, DESK_H,
  SHELF_W, SHELF_H, PLANT_W, PLANT_H,
  FRIDGE_W, FRIDGE_H, COUNTER_W, COUNTER_H,
  COFFEE_W, COFFEE_H, SOFA_W, SOFA_H,
  MTABLE_W, MTABLE_H, TRASH_W, TRASH_H,
  CABINET_W, CABINET_H, COOLER_W, COOLER_H,
  CLOCK_W, CLOCK_H, PAINTING_W, PAINTING_H,
  BOARD_W, BOARD_H, CHAIR_W, CHAIR_H,
  preRenderChar, preRenderDesk, preRenderBookshelf,
  preRenderPlant, preRenderFridge, preRenderCounter,
  preRenderCoffee, preRenderSofa, preRenderMeetingTable,
  preRenderTrash, preRenderCabinet, preRenderCooler,
  preRenderClock, preRenderPainting, preRenderBoard,
  preRenderChair, preRenderWindowFrame,
  WIN_W, WIN_H,
  drawBubble, type CharSprites,
} from "./sprites";

/* ── Props ── */
interface PixelOfficeProps {
  events: WSEvent[];
  workerStatuses: Record<string, string>;
  companyStatus?: any;
}

/* ── Particle ── */
interface Particle {
  x: number; y: number;
  vx: number; vy: number;
  life: number; maxLife: number;
  color: string;
  size: number;
  type?: "normal" | "confetti";
  gravity?: number;
}

/* ── Character ── */
type CharState = "sitting" | "working" | "walking" | "standing" | "celebrating";

interface GameChar {
  id: string;
  name: string;
  dept: string;
  x: number; y: number;
  homeX: number; homeY: number;
  deskX: number; deskY: number;
  state: CharState;
  sprites: CharSprites;
  walkFrame: number;
  facingLeft: boolean;
  waypoints: { x: number; y: number }[];
  wpIndex: number;
  stateTimer: number;
  nextIdleAction: number;
  bubble?: { text: string; type: "info" | "done" | "fail"; expires: number };
  toolsCount: number;
  tasksCompleted: number;
  apiStatus: string;
  idlePhase: number;
  walkSpeed: number;
  lastThoughtTick: number;
  workProgress: number;
}

/* ── Idle Thoughts ── */
const IDLE_THOUGHTS: Record<string, string[]> = {
  ceo: ["Strategy...", "Revenue up!", "Team sync", "Q4 goals", "Board prep", "Vision 2026"],
  finance: ["Charts...", "Bull run?", "Risk check", "P/L +3.2%", "Hedge this", "Alpha found"],
  security: ["Scanning...", "All clear", "Patch it!", "0-day?!", "Audit done", "Firewall OK"],
  engineering: ["Coding...", "npm install", "Tests pass!", "Reviewing PR", "Ship it!", "Bug found"],
  research: ["Data...", "Insights!", "Hypothesis", "Patterns!", "More data?", "Correlation"],
  operations: ["SLA OK", "Scaling...", "99.9%!", "Logs clean", "Deploy OK", "Monitoring"],
  sales: ["New lead!", "Follow up", "Close deal", "Pipeline!", "Cold call", "Proposal"],
  marketing: ["Post time!", "Trending!", "Engagement", "Analytics", "Content!", "Viral? "],
};

/* ── Game Engine ── */
class OfficeGame {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private chars: Map<string, GameChar> = new Map();
  private particles: Particle[] = [];

  // Sprites
  private deskSprite!: HTMLCanvasElement;
  private bookshelfSprite!: HTMLCanvasElement;
  private plantSprite!: HTMLCanvasElement;
  private fridgeSprite!: HTMLCanvasElement;
  private counterSprite!: HTMLCanvasElement;
  private coffeeSprite!: HTMLCanvasElement;
  private sofaSprite!: HTMLCanvasElement;
  private meetingTableSprite!: HTMLCanvasElement;
  private trashSprite!: HTMLCanvasElement;
  private cabinetSprite!: HTMLCanvasElement;
  private coolerSprite!: HTMLCanvasElement;
  private clockSprite!: HTMLCanvasElement;
  private paintingSprite!: HTMLCanvasElement;
  private boardSprite!: HTMLCanvasElement;
  private chairSprite!: HTMLCanvasElement;
  private windowSprite!: HTMLCanvasElement;

  // State
  private meetingActive = false;
  private meetingTimer = 0;
  private meetingAttendees: string[] = [];
  private tick = 0;
  private animId = 0;
  private lastTime = 0;
  private accumulator = 0;
  private scale = 2;
  private canvasGW = GW;
  private canvasGH = GH;
  private offsetX = 0;
  private offsetY = 0;
  private mouseGX = -999;
  private mouseGY = -999;
  private hoveredChar: GameChar | null = null;

  static TICK_MS = 1000 / 15;

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d")!;
    this.computeScale();
    this.init();
  }

  private computeScale() {
    const parent = this.canvas.parentElement;
    if (!parent) return;
    const rect = parent.getBoundingClientRect();

    this.scale = 3;
    this.canvasGW = GW;
    this.canvasGH = GH;
    
    this.canvas.width = this.canvasGW * this.scale;
    this.canvas.height = this.canvasGH * this.scale;
    // Let CSS handle the display size (fills container via max-width/height)
    this.canvas.style.width = "100%";
    this.canvas.style.maxWidth = `${this.canvasGW * this.scale}px`;
    this.canvas.style.height = "auto";

    this.offsetX = 0;
    this.offsetY = 0;

    this.ctx.imageSmoothingEnabled = false;
  }

  private init() {
    // Pre-render all furniture sprites
    this.deskSprite = preRenderDesk();
    this.bookshelfSprite = preRenderBookshelf();
    this.plantSprite = preRenderPlant();
    this.fridgeSprite = preRenderFridge();
    this.counterSprite = preRenderCounter();
    this.coffeeSprite = preRenderCoffee();
    this.sofaSprite = preRenderSofa();
    this.meetingTableSprite = preRenderMeetingTable();
    this.trashSprite = preRenderTrash();
    this.cabinetSprite = preRenderCabinet();
    this.coolerSprite = preRenderCooler();
    this.clockSprite = preRenderClock();
    this.paintingSprite = preRenderPainting();
    this.boardSprite = preRenderBoard();
    this.chairSprite = preRenderChair();
    this.windowSprite = preRenderWindowFrame();

    // Create characters
    let idx = 0;
    DESKS.forEach((d) => {
      this.chars.set(d.workerId, {
        id: d.workerId, name: d.name, dept: d.dept,
        x: d.charX, y: d.charY,
        homeX: d.charX, homeY: d.charY,
        deskX: d.deskX, deskY: d.deskY,
        state: "sitting",
        sprites: preRenderChar(d.workerId, d.dept),
        walkFrame: 0, facingLeft: false,
        waypoints: [], wpIndex: 0,
        stateTimer: 0,
        nextIdleAction: 60 + Math.floor(Math.random() * 150),
        toolsCount: 0, tasksCompleted: 0, apiStatus: "idle",
        idlePhase: idx * 2.3,
        walkSpeed: 0.8 + Math.random() * 0.4,
        lastThoughtTick: 0,
        workProgress: 0,
      });
      idx++;
    });
  }

  start() {
    this.lastTime = performance.now();
    const loop = (now: number) => {
      this.animId = requestAnimationFrame(loop);
      const dt = now - this.lastTime;
      this.lastTime = now;
      this.accumulator += dt;
      while (this.accumulator >= OfficeGame.TICK_MS) {
        this.update();
        this.accumulator -= OfficeGame.TICK_MS;
        this.tick++;
      }
      this.render();
    };
    this.animId = requestAnimationFrame(loop);
  }

  stop() { cancelAnimationFrame(this.animId); }

  /* ── Mouse ── */
  setMousePos(canvasX: number, canvasY: number) {
    this.mouseGX = canvasX / this.scale - this.offsetX;
    this.mouseGY = canvasY / this.scale - this.offsetY;
    this.findHoveredChar();
  }

  clearMouse() {
    this.mouseGX = -999;
    this.mouseGY = -999;
    this.hoveredChar = null;
  }

  get isHovering(): boolean { return this.hoveredChar !== null; }

  private findHoveredChar() {
    this.hoveredChar = null;
    let closest = 16;
    this.chars.forEach(ch => {
      const dx = this.mouseGX - ch.x;
      const dy = this.mouseGY - (ch.y + CHAR_H / 2);
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < closest) {
        closest = dist;
        this.hoveredChar = ch;
      }
    });
  }

  /* ── Sync from API ── */
  updateFromAPI(status: any) {
    if (!status?.departments) return;
    Object.values(status.departments).forEach((dept: any) => {
      (dept.workers || []).forEach((w: any) => {
        const ch = this.chars.get(w.worker_id);
        if (!ch) return;
        ch.name = w.name || ch.name;
        ch.toolsCount = w.tools_count || 0;
        ch.tasksCompleted = w.tasks_completed || 0;
        ch.apiStatus = w.status || "idle";

        if (w.status === "busy" && ch.state !== "working" && ch.state !== "celebrating") {
          ch.waypoints = [];
          ch.wpIndex = 0;
          ch.x = ch.homeX;
          ch.y = ch.homeY;
          ch.state = "working";
          ch.stateTimer = 0;
          ch.workProgress = Math.max(ch.workProgress, 0.01);
        } else if (w.status === "idle" && ch.state === "working") {
          ch.state = "sitting";
          ch.stateTimer = 0;
        }
      });
    });
  }

  /* ── WS Event ── */
  handleEvent(e: WSEvent) {
    const wId = e.data?.worker_id;
    switch (e.event) {
      case "ceo_route": {
        const ceo = this.chars.get("ceo");
        if (ceo) {
          ceo.bubble = { text: `-> ${e.data.department}`, type: "info", expires: this.tick + 60 };
          this.startCeoVisit(ceo, e.data.department);
        }
        break;
      }
      case "worker_start": {
        const ch = wId ? this.chars.get(wId) : null;
        if (ch) {
          ch.waypoints = [];
          ch.wpIndex = 0;
          ch.x = ch.homeX;
          ch.y = ch.homeY;
          ch.state = "working";
          ch.stateTimer = 0;
          ch.workProgress = 0;
          ch.bubble = { text: "On it!", type: "info", expires: this.tick + 50 };
        }
        break;
      }
      case "dept_assign": {
        const ch = wId ? this.chars.get(wId) : null;
        if (ch) {
          ch.bubble = { text: (e.data.instruction || "").slice(0, 24) || "Task assigned", type: "info", expires: this.tick + 60 };
        }
        break;
      }
      case "worker_done": {
        const ch = wId ? this.chars.get(wId) : null;
        if (ch) {
          ch.state = "celebrating"; ch.stateTimer = 0;
          ch.bubble = { text: `Done! ${e.data.duration_ms || 0}ms`, type: "done", expires: this.tick + 60 };
          ch.tasksCompleted++;
          ch.workProgress = 0;
          this.spawnConfetti(ch.x, ch.y);
        }
        break;
      }
      case "worker_fail": {
        const ch = wId ? this.chars.get(wId) : null;
        if (ch) {
          ch.state = "sitting";
          ch.workProgress = 0;
          ch.bubble = { text: (e.data.error || "Failed").slice(0, 20), type: "fail", expires: this.tick + 75 };
        }
        break;
      }
    }
  }

  /* ── Particles ── */
  private spawnConfetti(cx: number, cy: number) {
    const colors = ["#FFD700", "#FF6B6B", "#4ECDC4", "#A78BFA", "#F0C050", "#FF9FF3", "#54A0FF", "#5F27CD"];
    for (let i = 0; i < 16; i++) {
      const angle = (Math.PI * 2 / 16) * i + Math.random() * 0.3;
      const speed = 0.5 + Math.random() * 0.8;
      this.particles.push({
        x: cx + (Math.random() - 0.5) * 6,
        y: cy + CHAR_H * 0.3,
        vx: Math.cos(angle) * speed,
        vy: Math.sin(angle) * speed - 0.7,
        life: 0, maxLife: 30 + Math.random() * 25 | 0,
        color: colors[i % colors.length],
        size: 2,
        type: "confetti",
        gravity: 0.025,
      });
    }
  }

  private updateParticles() {
    for (let i = this.particles.length - 1; i >= 0; i--) {
      const p = this.particles[i];
      p.x += p.vx;
      p.y += p.vy;
      if (p.gravity) p.vy += p.gravity;
      p.life++;
      if (p.life >= p.maxLife) this.particles.splice(i, 1);
    }
  }

  /* ── CEO Visit ── */
  private startCeoVisit(ceo: GameChar, targetDept: string) {
    const targetRoom = ROOMS.find(r => r.id === targetDept);
    if (!targetRoom || ceo.state === "walking") return;

    const ceoRoom = ROOMS.find(r => r.id === "ceo")!;
    const corY = CORRIDOR_Y + CORRIDOR_H / 2;
    const waypoints: { x: number; y: number }[] = [
      { x: ceoRoom.doorX, y: ceo.homeY },
      { x: ceoRoom.doorX, y: corY },
      { x: targetRoom.doorX, y: corY },
    ];
    const wait = Array(12).fill({ x: targetRoom.doorX, y: corY });
    const back: { x: number; y: number }[] = [
      { x: ceoRoom.doorX, y: corY },
      { x: ceoRoom.doorX, y: ceo.homeY },
      { x: ceo.homeX, y: ceo.homeY },
    ];

    ceo.waypoints = [...waypoints, ...wait, ...back];
    ceo.wpIndex = 0; ceo.state = "walking"; ceo.stateTimer = 0;
  }

  /* ── CEO Patrol ── */
  private startCeoPatrol(ceo: GameChar) {
    const ceoRoom = ROOMS.find(r => r.id === "ceo")!;
    const corY = CORRIDOR_Y + CORRIDOR_H / 2;
    const patrolX = 100 + Math.random() * (GW - 200);
    const waypoints: { x: number; y: number }[] = [
      { x: ceoRoom.doorX, y: ceo.homeY },
      { x: ceoRoom.doorX, y: corY },
      { x: patrolX, y: corY },
    ];
    const wait = Array(10).fill({ x: patrolX, y: corY });
    const back: { x: number; y: number }[] = [
      { x: ceoRoom.doorX, y: corY },
      { x: ceoRoom.doorX, y: ceo.homeY },
      { x: ceo.homeX, y: ceo.homeY },
    ];
    ceo.waypoints = [...waypoints, ...wait, ...back];
    ceo.wpIndex = 0; ceo.state = "walking"; ceo.stateTimer = 0;
    ceo.bubble = { text: "Checking in...", type: "info", expires: this.tick + 40 };
  }

  /* ── Coworker Visit ── */
  private startCoworkerVisit(ch: GameChar) {
    const peers = DESKS.filter(d => d.dept === ch.dept && d.workerId !== ch.id);
    if (peers.length === 0) return;
    const target = peers[Math.floor(Math.random() * peers.length)];

    const waypoints: { x: number; y: number }[] = [
      { x: target.charX + 12, y: target.charY },
    ];
    const wait = Array(20).fill({ x: target.charX + 12, y: target.charY });
    const back: { x: number; y: number }[] = [
      { x: ch.homeX, y: ch.homeY },
    ];

    ch.waypoints = [...waypoints, ...wait, ...back];
    ch.wpIndex = 0; ch.state = "walking"; ch.stateTimer = 0;
    ch.nextIdleAction = this.tick + 200 + Math.random() * 300 | 0;
    const greetings = ["Hey!", "Hi!", "Quick chat?", "Got a sec?", "Look at this"];
    ch.bubble = { text: greetings[Math.floor(Math.random() * greetings.length)], type: "info", expires: this.tick + 30 };
  }

  /* ── Coffee Break ── */
  private startCoffeeBreak(ch: GameChar) {
    const room = ROOMS.find(r => r.id === ch.dept);
    if (!room) return;
    const corY = CORRIDOR_Y + CORRIDOR_H / 2;
    const to: { x: number; y: number }[] = [
      { x: ch.homeX, y: corY },
      { x: room.doorX, y: corY },
      { x: COFFEE_POS.x + 6, y: corY },
      { x: COFFEE_POS.x + 6, y: COFFEE_POS.y + 20 },
    ];
    const wait = Array(12).fill({ x: COFFEE_POS.x + 6, y: COFFEE_POS.y + 20 });
    const back: { x: number; y: number }[] = [
      { x: COFFEE_POS.x + 6, y: corY },
      { x: room.doorX, y: corY },
      { x: ch.homeX, y: corY },
      { x: ch.homeX, y: ch.homeY },
    ];
    ch.waypoints = [...to, ...wait, ...back];
    ch.wpIndex = 0; ch.state = "walking"; ch.stateTimer = 0;
    ch.nextIdleAction = this.tick + 250 + Math.random() * 350 | 0;
  }

  /* ── Water Break ── */
  private startWaterBreak(ch: GameChar) {
    const room = ROOMS.find(r => r.id === ch.dept);
    if (!room) return;
    const corY = CORRIDOR_Y + CORRIDOR_H / 2;
    const wc = WATER_COOLER_POS;
    const to: { x: number; y: number }[] = [
      { x: ch.homeX, y: corY },
      { x: room.doorX, y: corY },
      { x: wc.x + 6, y: corY },
      { x: wc.x + 6, y: wc.y + 28 },
    ];
    const wait = Array(10).fill({ x: wc.x + 6, y: wc.y + 28 });
    const back: { x: number; y: number }[] = [
      { x: wc.x + 6, y: corY },
      { x: room.doorX, y: corY },
      { x: ch.homeX, y: corY },
      { x: ch.homeX, y: ch.homeY },
    ];
    ch.waypoints = [...to, ...wait, ...back];
    ch.wpIndex = 0; ch.state = "walking"; ch.stateTimer = 0;
    ch.nextIdleAction = this.tick + 220 + Math.random() * 350 | 0;
  }

  /* ── Team Meeting ── */
  private startTeamMeeting() {
    if (this.meetingActive) return;

    const available: GameChar[] = [];
    this.chars.forEach(ch => {
      if (ch.state === "sitting" && ch.id !== "ceo") available.push(ch);
    });
    if (available.length < 2) return;

    const shuffled = available.sort(() => Math.random() - 0.5);
    const count = Math.min(3 + Math.floor(Math.random() * 3), shuffled.length, MEETING_SEATS.length);
    const attendees = shuffled.slice(0, count);

    const ceo = this.chars.get("ceo");
    const ceoJoins = ceo && ceo.state === "sitting" && Math.random() < 0.3;
    if (ceoJoins && ceo) attendees.unshift(ceo);

    const meetRoom = ROOMS.find(r => r.id === "meeting")!;
    const corY = CORRIDOR_Y + CORRIDOR_H / 2;

    this.meetingActive = true;
    this.meetingTimer = this.tick;
    this.meetingAttendees = attendees.map(a => a.id);

    attendees.forEach((ch, i) => {
      const seatIdx = Math.min(i, MEETING_SEATS.length - 1);
      const seat = MEETING_SEATS[seatIdx];
      const srcRoom = ROOMS.find(r => r.id === ch.dept);
      const srcDoorX = srcRoom ? srcRoom.doorX : ch.homeX;

      const to: { x: number; y: number }[] = ch.id === "ceo"
        ? [
            { x: meetRoom.doorX, y: ch.homeY },
            { x: meetRoom.doorX, y: corY },
            { x: meetRoom.doorX, y: seat.y + 12 },
            { x: seat.x, y: seat.y + 12 },
          ]
        : [
            { x: ch.homeX, y: corY },
            { x: srcDoorX, y: corY },
            { x: meetRoom.doorX, y: corY },
            { x: meetRoom.doorX, y: seat.y + 12 },
            { x: seat.x, y: seat.y + 12 },
          ];

      const meetDuration = 60 + Math.floor(Math.random() * 30);
      const wait = Array(meetDuration).fill({ x: seat.x, y: seat.y + 12 });

      const back: { x: number; y: number }[] = ch.id === "ceo"
        ? [
            { x: meetRoom.doorX, y: seat.y + 12 },
            { x: meetRoom.doorX, y: corY },
            { x: meetRoom.doorX, y: ch.homeY },
            { x: ch.homeX, y: ch.homeY },
          ]
        : [
            { x: meetRoom.doorX, y: seat.y + 12 },
            { x: meetRoom.doorX, y: corY },
            { x: srcDoorX, y: corY },
            { x: ch.homeX, y: corY },
            { x: ch.homeX, y: ch.homeY },
          ];

      ch.waypoints = [...to, ...wait, ...back];
      ch.wpIndex = 0; ch.state = "walking"; ch.stateTimer = 0;
      ch.nextIdleAction = this.tick + 500 + Math.random() * 400 | 0;

      if (i === 0) {
        const topics = ["Sync meeting", "Sprint review", "Planning", "Standup", "Brainstorm"];
        ch.bubble = { text: topics[Math.floor(Math.random() * topics.length)], type: "info", expires: this.tick + 45 };
      }
    });
  }

  private updateMeeting() {
    if (!this.meetingActive) return;
    const allDone = this.meetingAttendees.every(id => {
      const ch = this.chars.get(id);
      return !ch || ch.state === "sitting";
    });
    if (allDone || this.tick - this.meetingTimer > 700) {
      this.meetingActive = false;
      this.meetingAttendees = [];
    }
  }

  /* ── Idle Thoughts ── */
  private tryIdleThought(ch: GameChar) {
    if (ch.bubble) return;
    if (this.tick - ch.lastThoughtTick < 150) return;
    if (Math.random() > 0.015) return;

    const thoughts = IDLE_THOUGHTS[ch.dept] || IDLE_THOUGHTS.operations;
    const text = thoughts[Math.floor(Math.random() * thoughts.length)];
    ch.bubble = { text, type: "info", expires: this.tick + 40 };
    ch.lastThoughtTick = this.tick;
  }

  /* ── Update ── */
  private update() {
    this.updateParticles();

    this.chars.forEach((ch) => {
      ch.stateTimer++;
      if (ch.bubble && this.tick > ch.bubble.expires) ch.bubble = undefined;

      if (ch.state === "working") {
        ch.workProgress = Math.min(1, ch.workProgress + 0.004 + Math.random() * 0.003);
      }

      switch (ch.state) {
        case "sitting":
          this.updateSitting(ch);
          this.tryIdleThought(ch);
          break;
        case "walking": this.updateWalking(ch); break;
        case "celebrating":
          if (ch.stateTimer > 50) { ch.state = "sitting"; ch.stateTimer = 0; }
          if (ch.stateTimer % 8 === 0) {
            this.particles.push({
              x: ch.x + (Math.random() - 0.5) * 12,
              y: ch.y + Math.random() * CHAR_H,
              vx: (Math.random() - 0.5) * 0.4,
              vy: -0.3 - Math.random() * 0.2,
              life: 0, maxLife: 12,
              color: ["#FFD700", "#4ECDC4", "#FF6B6B"][Math.floor(Math.random() * 3)],
              size: 2,
            });
          }
          break;
        case "standing":
          if (ch.stateTimer > 30) { ch.state = "sitting"; ch.stateTimer = 0; }
          break;
      }
    });

    const ceo = this.chars.get("ceo");
    if (ceo && ceo.state === "sitting" && this.tick % 400 === 0 && Math.random() < 0.2) {
      this.startCeoPatrol(ceo);
    }

    if (this.tick % 600 === 0 && Math.random() < 0.15) {
      this.startTeamMeeting();
    }
    this.updateMeeting();
  }

  private updateSitting(ch: GameChar) {
    if (this.tick > ch.nextIdleAction && ch.id !== "ceo") {
      const r = Math.random();
      if (r < 0.15) this.startCoffeeBreak(ch);
      else if (r < 0.25) this.startWaterBreak(ch);
      else if (r < 0.40) this.startCoworkerVisit(ch);
      else if (r < 0.50) {
        ch.state = "standing"; ch.stateTimer = 0;
        ch.nextIdleAction = this.tick + 140 + Math.random() * 250 | 0;
      }
      else ch.nextIdleAction = this.tick + 100 + Math.random() * 200 | 0;
    }
  }

  private updateWalking(ch: GameChar) {
    if (ch.wpIndex >= ch.waypoints.length) {
      ch.state = "sitting"; ch.stateTimer = 0;
      ch.x = ch.homeX; ch.y = ch.homeY;
      return;
    }
    const t = ch.waypoints[ch.wpIndex];
    const dx = t.x - ch.x, dy = t.y - ch.y;
    const dist = Math.sqrt(dx * dx + dy * dy);
    if (dist < ch.walkSpeed) {
      ch.x = t.x; ch.y = t.y; ch.wpIndex++;
    } else {
      ch.x += (dx / dist) * ch.walkSpeed;
      ch.y += (dy / dist) * ch.walkSpeed;
      ch.facingLeft = dx < -0.2;
    }
    ch.walkFrame = this.tick % 8 < 4 ? 0 : 1;
  }

  /* ── Render ── */
  private render() {
    const ctx = this.ctx;
    const s = this.scale;

    ctx.setTransform(s, 0, 0, s, 0, 0);

    // Dark navy background (outside rooms)
    ctx.fillStyle = "#1A1A30";
    ctx.fillRect(0, 0, this.canvasGW, this.canvasGH);

    ctx.translate(this.offsetX, this.offsetY);

    this.drawFloors();
    this.drawWalls();
    // Re-draw corridor floor ON TOP of walls (walls may overlap it)
    this.drawCorridorFloor();
    this.drawFurniture();
    this.drawEntities();
    this.drawParticles();
    this.drawLabels();
    this.drawProgressBars();
    this.drawBubbles();
    if (this.hoveredChar) this.drawTooltip(this.hoveredChar);
  }

  /* ── Tile-based Floors ── */
  private drawFloors() {
    const ctx = this.ctx;
    const TILE = 16;

    ROOMS.forEach((room) => {
      const { x, y, w, h, floor, id } = room;

      // Base floor color
      ctx.fillStyle = floor;
      ctx.fillRect(x, y, w, h);

      // Floor pattern based on room type
      if (id === "office" || id === "finance" || id === "security" || id === "engineering" ||
          id === "research" || id === "operations" || id === "sales" || id === "marketing") {
        // Hardwood planks with realistic variation and grain
        const plankW = TILE * 2; // wider planks look more realistic
        const plankH = TILE;
        for (let fy = y; fy < y + h; fy += plankH) {
          const rowOffset = ((fy / plankH | 0) & 1) ? plankW / 2 : 0; // stagger planks
          for (let fx = x - plankW; fx < x + w + plankW; fx += plankW) {
            const px = fx + rowOffset;
            if (px + plankW < x || px > x + w) continue;
            // Each plank slightly different shade
            const shade = ((px * 7 + fy * 3) % 5);
            const colors = ["#B8864E", "#B48448", "#C09050", "#AC7C44", "#BA884C"];
            ctx.fillStyle = colors[shade];
            ctx.fillRect(Math.max(px, x), fy, Math.min(plankW, x + w - px), plankH);
            // Plank bottom edge (darker)
            ctx.fillStyle = "#9A6C38";
            ctx.fillRect(Math.max(px, x), fy + plankH - 1, Math.min(plankW, x + w - px), 1);
            // Grain line (subtle)
            ctx.fillStyle = "#A87840";
            ctx.fillRect(Math.max(px, x), fy + plankH/2, Math.min(plankW, x + w - px), 1);
            // Vertical joint
            if (px >= x && px < x + w) {
              ctx.fillStyle = "#9A6C38";
              ctx.fillRect(px, fy, 1, plankH);
            }
          }
        }
      } else if (id === "kitchen") {
        // Checkered linoleum tiles
        for (let fy = y; fy < y + h; fy += TILE) {
          for (let fx = x; fx < x + w; fx += TILE) {
            const alt = ((fx / TILE | 0) + (fy / TILE | 0)) & 1;
            ctx.fillStyle = alt ? "#E0D4C0" : "#E8DCC8";
            ctx.fillRect(fx, fy, TILE, TILE);
            ctx.fillStyle = "#D0C4B0";
            ctx.fillRect(fx, fy + TILE - 1, TILE, 1);
            ctx.fillRect(fx + TILE - 1, fy, 1, TILE);
          }
        }
      } else if (id === "ceo") {
        // Rich carpet with woven pattern
        for (let fy = y; fy < y + h; fy += 2) {
          for (let fx = x; fx < x + w; fx += 2) {
            const noise = ((fx * 3 + fy * 7) % 5);
            const colors = ["#344E62", "#3A5268", "#2E4858", "#3C5470", "#344E62"];
            ctx.fillStyle = colors[noise];
            ctx.fillRect(fx, fy, 2, 2);
          }
        }
        // Ornate carpet border (double line)
        ctx.strokeStyle = "#4A7090";
        ctx.lineWidth = 1;
        ctx.strokeRect(x + 6, y + 6, w - 12, h - 12);
        ctx.strokeStyle = "#3A6080";
        ctx.strokeRect(x + 8, y + 8, w - 16, h - 16);
      } else if (id === "meeting") {
        // Solid gray-blue carpet (subtle)
        ctx.fillStyle = "#748090";
        ctx.fillRect(x, y, w, h);
        // Very subtle noise (not checkerboard!)
        for (let fy = y; fy < y + h; fy += 3) {
          for (let fx = x; fx < x + w; fx += 5) {
            ctx.fillStyle = "#707888";
            ctx.fillRect(fx, fy, 2, 1);
          }
        }
      }
    });

    // Corridor floor drawn in drawCorridorFloor() after walls
  }

  /* ── Walls with Height (RPG Maker style) ── */
  private drawWalls() {
    const ctx = this.ctx;
    const WALL_HEIGHT = 32;

    ROOMS.forEach((room) => {
      const { x, y, w, h, accent, doorX, id } = room;

      // Skip virtual department zones
      if (id !== "office" && id !== "kitchen" && id !== "ceo" && id !== "meeting") return;

      // North wall with gradient depth (tallest, most visible)
      // Layer 1: darkest base
      ctx.fillStyle = "#1E1E28";
      ctx.fillRect(x, y - WALL_HEIGHT, w, WALL_HEIGHT);
      // Layer 2: mid tone
      ctx.fillStyle = "#2A2A38";
      ctx.fillRect(x + 1, y - WALL_HEIGHT + 1, w - 2, WALL_HEIGHT - 4);
      // Layer 3: lighter top band (shows wall thickness from above)
      ctx.fillStyle = "#5A5060";
      ctx.fillRect(x + 2, y - WALL_HEIGHT + 2, w - 4, 6);
      // Layer 4: subtle highlight strip at very top
      ctx.fillStyle = "#6A6070";
      ctx.fillRect(x + 3, y - WALL_HEIGHT + 3, w - 6, 2);

      // Baseboard (warm brown strip at wall-floor junction)
      ctx.fillStyle = "#5C3A1E";
      ctx.fillRect(x, y - 2, w, 2);

      // South wall with depth
      ctx.fillStyle = "#2A2A38";
      ctx.fillRect(x, y + h, w, 10);
      ctx.fillStyle = "#1E1E28";
      ctx.fillRect(x, y + h, w, 2); // dark edge
      ctx.fillStyle = "#3A3A48";
      ctx.fillRect(x + 1, y + h + 3, w - 2, 5); // lighter middle

      // West wall
      ctx.fillStyle = "#2A2A38";
      ctx.fillRect(x - 10, y - WALL_HEIGHT, 10, h + WALL_HEIGHT + 10);
      ctx.fillStyle = "#3A3A48";
      ctx.fillRect(x - 8, y - WALL_HEIGHT + 2, 6, h + WALL_HEIGHT + 6);

      // East wall
      ctx.fillStyle = "#2A2A38";
      ctx.fillRect(x + w, y - WALL_HEIGHT, 10, h + WALL_HEIGHT + 10);
      ctx.fillStyle = "#3A3A48";
      ctx.fillRect(x + w + 2, y - WALL_HEIGHT + 2, 6, h + WALL_HEIGHT + 6);

      // Inner shadow at wall-floor edges (vignette)
      ctx.fillStyle = "rgba(0,0,0,0.08)";
      ctx.fillRect(x, y, w, 6); // top edge shadow
      ctx.fillRect(x, y + h - 4, w, 4); // bottom edge shadow
      ctx.fillRect(x, y, 4, h); // left edge shadow
      ctx.fillRect(x + w - 4, y, 4, h); // right edge shadow

      // Door opening (gap in wall)
      const doorW = 36;
      if (y < CORRIDOR_Y) {
        ctx.fillStyle = room.floor || "#B8864E";
        ctx.fillRect(doorX - doorW/2, y + h, doorW, 10);
        // Door frame highlights
        ctx.fillStyle = "#5C3A1E";
        ctx.fillRect(doorX - doorW/2 - 2, y + h, 2, 10);
        ctx.fillRect(doorX + doorW/2, y + h, 2, 10);
      } else {
        ctx.fillStyle = room.floor || "#B8864E";
        ctx.fillRect(doorX - doorW/2, y - WALL_HEIGHT, doorW, WALL_HEIGHT);
        ctx.fillStyle = "#5C3A1E";
        ctx.fillRect(doorX - doorW/2 - 2, y - WALL_HEIGHT, 2, WALL_HEIGHT);
        ctx.fillRect(doorX + doorW/2, y - WALL_HEIGHT, 2, WALL_HEIGHT);
      }

      // Room label on a "plate" sign
      const labelW = ctx.measureText(room.label).width || 60;
      ctx.fillStyle = "#3A3A48";
      ctx.fillRect(x + w/2 - labelW/2 - 8, y - WALL_HEIGHT + 8, labelW + 16, 14);
      ctx.fillStyle = "#4A4A58";
      ctx.fillRect(x + w/2 - labelW/2 - 7, y - WALL_HEIGHT + 9, labelW + 14, 12);
      ctx.fillStyle = "#E8E0D8";
      ctx.font = "bold 8px monospace";
      ctx.textAlign = "center";
      ctx.fillText(room.label, x + w / 2, y - WALL_HEIGHT + 18);
    });

    // Draw windows on north walls AFTER wall is drawn
    FURNITURE.filter(f => f.type === "window").forEach(f => {
      // Windows go on the north wall face
      // Find which room this window belongs to
      const room = ROOMS.find(r => 
        (r.id === "office" || r.id === "kitchen" || r.id === "ceo" || r.id === "meeting") &&
        f.x >= r.x && f.x < r.x + r.w
      );
      if (room && this.windowSprite) {
        const wy = room.y - WALL_HEIGHT + 6; // position on wall face
        ctx.drawImage(this.windowSprite, f.x, wy);
      }
    });
    
    // Corridor walls
    ctx.fillStyle = "#2A2A38";
    ctx.fillRect(0, CORRIDOR_Y - 10, GW, 10);
    ctx.fillStyle = "#5C3A1E"; // baseboard
    ctx.fillRect(0, CORRIDOR_Y - 2, GW, 2);
    ctx.fillStyle = "#2A2A38";
    ctx.fillRect(0, CORRIDOR_Y + CORRIDOR_H, GW, 10);
    ctx.fillStyle = "#1E1E28";
    ctx.fillRect(0, CORRIDOR_Y + CORRIDOR_H, GW, 2);
  }

  /* ── Corridor Floor (drawn after walls to prevent overlap) ── */
  private drawCorridorFloor() {
    const ctx = this.ctx;
    const TILE = 16;
    // Polished stone/tile corridor (distinct from wood floors)
    const corrColors = ["#A0A8B0", "#98A0A8", "#A8B0B8", "#9CA4AC"];
    for (let fy = CORRIDOR_Y; fy < CORRIDOR_Y + CORRIDOR_H; fy += TILE) {
      for (let fx = 0; fx < GW; fx += TILE) {
        const ci = ((fx / TILE | 0) + (fy / TILE | 0)) % corrColors.length;
        ctx.fillStyle = corrColors[ci];
        ctx.fillRect(fx, fy, TILE, TILE);
        ctx.fillStyle = "#889098";
        ctx.fillRect(fx, fy + TILE - 1, TILE, 1);
        ctx.fillRect(fx + TILE - 1, fy, 1, TILE);
      }
    }
    // Decorative stripe
    ctx.fillStyle = "#808890";
    ctx.fillRect(0, CORRIDOR_Y + CORRIDOR_H / 2 - 1, GW, 1);
    // Top/bottom edge shadows
    ctx.fillStyle = "rgba(0,0,0,0.1)";
    ctx.fillRect(0, CORRIDOR_Y, GW, 3);
    ctx.fillRect(0, CORRIDOR_Y + CORRIDOR_H - 3, GW, 3);
  }

  /* ── Furniture ── */
  private drawFurniture() {
    const ctx = this.ctx;

    FURNITURE.forEach(f => {
      // Skip windows (rendered on walls)
      if (f.type === "window") return;
      
      // Drop shadow under furniture
      ctx.save();
      ctx.globalAlpha = 0.12;
      ctx.fillStyle = "#000000";
      const sw = f.type === "bookshelf" ? SHELF_W : f.type === "sofa" ? SOFA_W : f.type === "meeting_table" ? MTABLE_W : 12;
      const sh = f.type === "bookshelf" ? SHELF_H : f.type === "sofa" ? SOFA_H : f.type === "meeting_table" ? MTABLE_H : 12;
      ctx.fillRect(f.x + 1, f.y + sh - 1, sw, 2);
      ctx.restore();
      
      switch (f.type) {
        case "bookshelf":
          ctx.drawImage(this.bookshelfSprite, f.x, f.y);
          break;
        case "plant":
          // Subtle sway
          ctx.save();
          const sway = Math.sin(this.tick * 0.05 + f.x * 0.2) * 0.015;
          ctx.translate(f.x + PLANT_W / 2, f.y + PLANT_H);
          ctx.rotate(sway);
          ctx.drawImage(this.plantSprite, -PLANT_W / 2, -PLANT_H);
          ctx.restore();
          break;
        case "fridge":
          ctx.drawImage(this.fridgeSprite, f.x, f.y);
          break;
        case "counter":
          ctx.drawImage(this.counterSprite, f.x, f.y);
          break;
        case "coffee":
          ctx.drawImage(this.coffeeSprite, f.x, f.y);
          // LED indicator
          const ledOn = this.tick % 40 < 20;
          ctx.fillStyle = ledOn ? "#5A8A3A" : "#A03030";
          ctx.fillRect(f.x + 9, f.y + 2, 2, 2);
          break;
        case "sofa":
          ctx.drawImage(this.sofaSprite, f.x, f.y);
          break;
        case "meeting_table":
          ctx.drawImage(this.meetingTableSprite, f.x, f.y);
          break;
        case "trash":
          ctx.drawImage(this.trashSprite, f.x, f.y);
          break;
        case "filing_cabinet":
          ctx.drawImage(this.cabinetSprite, f.x, f.y);
          break;
        case "water_cooler":
          ctx.drawImage(this.coolerSprite, f.x, f.y);
          break;
        case "clock":
          ctx.drawImage(this.clockSprite, f.x, f.y);
          break;
        case "painting":
          ctx.drawImage(this.paintingSprite, f.x, f.y);
          break;
        case "whiteboard":
          ctx.drawImage(this.boardSprite, f.x, f.y);
          break;
        case "chair":
          ctx.drawImage(this.chairSprite, f.x, f.y);
          break;
        case "window":
          if (this.windowSprite) ctx.drawImage(this.windowSprite, f.x, f.y);
          break;
      }
    });
  }

  /* ── Entities (Desks + Characters) ── */
  private drawEntities() {
    const entities: { y: number; draw: () => void }[] = [];

    DESKS.forEach(d => {
      entities.push({
        y: d.deskY + DESK_H,
        draw: () => {
          // Desk shadow
          this.ctx.save();
          this.ctx.globalAlpha = 0.2;
          this.ctx.fillStyle = "#000000";
          this.ctx.fillRect(d.deskX + 2, d.deskY + DESK_H, DESK_W - 4, 2);
          this.ctx.restore();

          this.ctx.drawImage(this.deskSprite, d.deskX, d.deskY);
          
          // Draw chair behind desk
          this.ctx.drawImage(this.chairSprite, d.charX - 2, d.charY + CHAR_H - 4);

          // Monitor glow (baked in, subtle)
          const ch = this.chars.get(d.workerId);
          if (ch && (ch.state === "working" || ch.state === "sitting")) {
            this.ctx.save();
            this.ctx.globalAlpha = ch.state === "working" ? 0.15 : 0.08;
            this.ctx.fillStyle = "#8ABCE0";
            this.ctx.beginPath();
            this.ctx.ellipse(d.deskX + DESK_W / 2, d.deskY + 10, 10, 6, 0, 0, Math.PI * 2);
            this.ctx.fill();
            this.ctx.restore();
          }
        }
      });
    });

    this.chars.forEach(ch => {
      entities.push({ y: ch.y + CHAR_H, draw: () => this.drawChar(ch) });
    });

    entities.sort((a, b) => a.y - b.y);
    entities.forEach(e => e.draw());
  }

  private drawChar(ch: GameChar) {
    const ctx = this.ctx;
    let sprite: HTMLCanvasElement;
    const cx = Math.round(ch.x - CHAR_W / 2);
    const cy = Math.round(ch.y);

    switch (ch.state) {
      case "sitting": sprite = ch.sprites.sit; break;
      case "working": sprite = this.tick % 12 < 6 ? ch.sprites.work1 : ch.sprites.work2; break;
      case "walking":
        sprite = ch.facingLeft
          ? (ch.walkFrame === 0 ? ch.sprites.walk1L : ch.sprites.walk2L)
          : (ch.walkFrame === 0 ? ch.sprites.walk1 : ch.sprites.walk2);
        break;
      case "celebrating": {
        const jmp = ch.stateTimer % 10 < 5 ? -4 : 0;
        ctx.drawImage(ch.sprites.stand, cx, cy + jmp);
        this.drawCharShadow(ch);
        return;
      }
      default: sprite = ch.sprites.stand;
    }

    this.drawCharShadow(ch);
    ctx.drawImage(sprite, cx, cy);

    if (this.hoveredChar === ch) {
      ctx.save();
      ctx.globalAlpha = 0.3;
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 1;
      ctx.strokeRect(cx - 2, cy - 2, CHAR_W + 4, CHAR_H + 4);
      ctx.restore();
    }
  }

  private drawCharShadow(ch: GameChar) {
    this.ctx.save();
    this.ctx.globalAlpha = 0.25;
    this.ctx.fillStyle = "#000000";
    this.ctx.beginPath();
    this.ctx.ellipse(ch.x, ch.y + CHAR_H - 2, 7, 3, 0, 0, Math.PI * 2);
    this.ctx.fill();
    this.ctx.restore();
  }

  private drawParticles() {
    const ctx = this.ctx;
    ctx.save();
    this.particles.forEach(p => {
      const alpha = 1 - p.life / p.maxLife;
      ctx.globalAlpha = alpha * (p.type === "confetti" ? 0.8 : 0.6);
      ctx.fillStyle = p.color;
      ctx.fillRect(Math.round(p.x), Math.round(p.y), p.size, p.size);
    });
    ctx.restore();
  }

  private drawLabels() {
    const ctx = this.ctx;
    ctx.textAlign = "center";

    this.chars.forEach(ch => {
      if (ch.state === "walking") return;
      if (this.hoveredChar === ch) return;
      const nx = Math.round(ch.x);
      const ny = Math.round(ch.y) + CHAR_H + 4;

      // Truncate long names
      let name = ch.name;
      if (name.length > 14) name = name.slice(0, 12) + "..";
      ctx.font = "bold 5px monospace";
      const tw = ctx.measureText(name).width;
      ctx.fillStyle = "#1A1A30DD";
      ctx.fillRect(nx - tw / 2 - 2, ny - 1, tw + 4, 8);

      ctx.fillStyle = "#E8E0D0";
      ctx.fillText(name, nx, ny + 4);

      if (ch.toolsCount > 0 || ch.tasksCompleted > 0) {
        const info = `${ch.toolsCount}T ${ch.tasksCompleted}D`;
        ctx.fillStyle = "#A8A890";
        ctx.font = "5px monospace";
        ctx.fillText(info, nx, ny + 12);
      }
    });

    // Status dots
    this.chars.forEach(ch => {
      const dotY = Math.round(ch.y) - 4;
      const dotX = Math.round(ch.x);
      const color =
        ch.state === "working" ? "#4A7ACA" :
        ch.state === "celebrating" ? "#5A8A3A" :
        ch.state === "walking" ? "#C87040" :
        ch.apiStatus === "error" ? "#A03030" :
        ch.apiStatus === "offline" ? "#6A6A7A" :
        "#5A8A3A";

      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(dotX, dotY, 2, 0, Math.PI * 2);
      ctx.fill();
    });
  }

  private drawProgressBars() {
    const ctx = this.ctx;
    this.chars.forEach(ch => {
      if (ch.state !== "working") return;
      const accent = ROOMS.find(r => r.id === ch.dept)?.accent || "#4A7ACA";
      const bw = 18;
      const bx = Math.round(ch.x - bw / 2);
      const by = Math.round(ch.y) - 8;

      ctx.fillStyle = "#1A1A30AA";
      ctx.fillRect(bx, by, bw, 3);
      ctx.fillStyle = accent;
      ctx.fillRect(bx, by, Math.round(bw * ch.workProgress), 3);
    });
  }

  private drawBubbles() {
    this.chars.forEach(ch => {
      if (ch.bubble) {
        drawBubble(this.ctx, Math.round(ch.x), Math.round(ch.y) - 8, ch.bubble.text, ch.bubble.type);
      }
    });
  }

  private drawTooltip(ch: GameChar) {
    const ctx = this.ctx;
    ctx.save();

    const room = ROOMS.find(r => r.id === ch.dept);
    const accent = room?.accent || "#6A6A7A";
    const deptLabel = ch.dept.charAt(0).toUpperCase() + ch.dept.slice(1);

    const statusText =
      ch.state === "working" ? "Working" :
      ch.state === "walking" ? "Away" :
      ch.state === "celebrating" ? "Done!" :
      ch.state === "standing" ? "Stretching" :
      ch.apiStatus === "offline" ? "Offline" :
      "Idle";

    const lines = [
      ch.name,
      deptLabel + " Dept",
      `Status: ${statusText}`,
      `Tools: ${ch.toolsCount} | Done: ${ch.tasksCompleted}`,
    ];

    ctx.font = "bold 7px monospace";
    let maxW = 0;
    lines.forEach(l => { maxW = Math.max(maxW, ctx.measureText(l).width); });

    const pw = maxW + 16;
    const lineH = 10;
    const ph = lines.length * lineH + 10;
    let bx = Math.round(ch.x - pw / 2);
    let by = Math.round(ch.y - 20 - ph);
    bx = Math.max(4, Math.min(GW - pw - 4, bx));
    by = Math.max(4, by);

    ctx.fillStyle = "#1A1A30F0";
    ctx.beginPath();
    ctx.roundRect(bx, by, pw, ph, 4);
    ctx.fill();

    ctx.strokeStyle = accent + "AA";
    ctx.lineWidth = 1;
    ctx.stroke();

    ctx.fillStyle = accent + "66";
    ctx.fillRect(bx + 2, by + 2, pw - 4, 2);

    ctx.textAlign = "left";
    lines.forEach((line, i) => {
      if (i === 0) {
        ctx.fillStyle = "#ffffff";
        ctx.font = "bold 7px monospace";
      } else if (i === 1) {
        ctx.fillStyle = accent;
        ctx.font = "6px monospace";
      } else {
        ctx.fillStyle = "#C8C8B0";
        ctx.font = "6px monospace";
      }
      ctx.fillText(line, bx + 8, by + 8 + i * lineH);
    });

    ctx.restore();
  }
}

/* ── React Component ── */
export default function PixelOffice({ events, workerStatuses, companyStatus }: PixelOfficeProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const gameRef = useRef<OfficeGame | null>(null);
  const processedRef = useRef(0);

  useEffect(() => {
    if (!canvasRef.current || gameRef.current) return;
    const game = new OfficeGame(canvasRef.current);
    gameRef.current = game;
    game.start();
    return () => { game.stop(); gameRef.current = null; };
  }, []);

  useEffect(() => {
    gameRef.current?.updateFromAPI(companyStatus);
  }, [companyStatus]);

  useEffect(() => {
    if (!gameRef.current || events.length <= processedRef.current) return;
    const fresh = events.slice(processedRef.current);
    processedRef.current = events.length;
    fresh.forEach(e => gameRef.current!.handleEvent(e));
  }, [events]);

  const handleMouseMove = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const game = gameRef.current;
    if (!game) return;
    const rect = e.currentTarget.getBoundingClientRect();
    game.setMousePos(e.clientX - rect.left, e.clientY - rect.top);
    e.currentTarget.style.cursor = game.isHovering ? "pointer" : "default";
  }, []);

  const handleMouseLeave = useCallback(() => {
    gameRef.current?.clearMouse();
  }, []);

  return (
    <div ref={containerRef} className="w-full h-full flex items-center justify-center overflow-hidden bg-[#1A1A30]">
      <canvas
        ref={canvasRef}
        className="rounded-lg"
        style={{ imageRendering: "pixelated" }}
        onMouseMove={handleMouseMove}
        onMouseLeave={handleMouseLeave}
      />
    </div>
  );
}
