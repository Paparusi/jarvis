"use client";

import { useEffect, useRef } from "react";
import type { WSEvent } from "@/hooks/useWebSocketHub";

interface PixelOfficeProps {
  events: WSEvent[];
  workerStatuses: Record<string, string>;
}

export default function PixelOffice({ events, workerStatuses }: PixelOfficeProps) {
  const canvasRef = useRef<HTMLDivElement>(null);
  const appRef = useRef<any>(null);
  const workersRef = useRef<Map<string, any>>(new Map());
  const bubblesRef = useRef<Map<string, any>>(new Map());
  const initRef = useRef(false);

  // Initialize PixiJS
  useEffect(() => {
    if (!canvasRef.current || initRef.current) return;
    initRef.current = true;

    (async () => {
      // Dynamic import to avoid SSR issues
      const PIXI = await import("pixi.js");
      const { ROOMS, WORKER_POSITIONS, toIso } = await import("./office-map");
      const { createCharacter, createDesk, createRoomLabel } = await import("./sprites");

      const app = new PIXI.Application();
      await app.init({
        width: 1000,
        height: 450,
        backgroundColor: 0x0a0a14,
        antialias: true,
        resolution: window.devicePixelRatio || 1,
        autoDensity: true,
      });

      if (canvasRef.current) {
        canvasRef.current.appendChild(app.canvas as HTMLCanvasElement);
      }
      appRef.current = app;

      // Draw floor grid
      const floor = new PIXI.Graphics();
      for (let row = 0; row < 10; row++) {
        for (let col = 0; col < 16; col++) {
          const { x, y } = toIso(col, row);
          floor.moveTo(x, y);
          floor.lineTo(x + 32, y - 16);
          floor.lineTo(x + 64, y);
          floor.lineTo(x + 32, y + 16);
          floor.closePath();
          floor.fill((col + row) % 2 === 0 ? 0x111122 : 0x0e0e1c);
        }
      }
      app.stage.addChild(floor);

      // Draw rooms
      ROOMS.forEach((room) => {
        const g = new PIXI.Graphics();
        const tl = toIso(room.col, room.row);
        const tr = toIso(room.col + room.w, room.row);
        const br = toIso(room.col + room.w, room.row + room.h);
        const bl = toIso(room.col, room.row + room.h);

        g.moveTo(tl.x, tl.y);
        g.lineTo(tr.x, tr.y);
        g.lineTo(br.x, br.y);
        g.lineTo(bl.x, bl.y);
        g.closePath();
        g.fill({ color: 0x14142a, alpha: 0.6 });
        g.stroke({ width: 1.5, color: 0x2a2a4e });

        app.stage.addChild(g);

        // Room label
        const center = toIso(room.col + room.w / 2, room.row + 0.2);
        const label = createRoomLabel(room.label);
        label.anchor.set(0.5, 0.5);
        label.x = center.x;
        label.y = center.y;
        app.stage.addChild(label);
      });

      // Place workers with desks
      WORKER_POSITIONS.forEach((wp) => {
        const pos = toIso(wp.col, wp.row);

        // Desk
        const desk = createDesk();
        desk.x = pos.x - 16;
        desk.y = pos.y + 5;
        app.stage.addChild(desk);

        // Character
        const char = createCharacter(wp.room, wp.name);
        char.x = pos.x;
        char.y = pos.y;
        app.stage.addChild(char);
        workersRef.current.set(wp.workerId, char);
      });
    })();

    return () => {
      if (appRef.current) {
        appRef.current.destroy(true);
        appRef.current = null;
        initRef.current = false;
      }
    };
  }, []);

  // React to WS events
  useEffect(() => {
    if (!appRef.current || events.length === 0) return;
    const lastEvent = events[events.length - 1];
    const workerId = lastEvent.data?.worker_id;

    (async () => {
      const { createStatusIndicator, createSpeechBubble } = await import("./sprites");

      // Remove old bubble for this worker
      if (workerId && bubblesRef.current.has(workerId)) {
        const old = bubblesRef.current.get(workerId);
        old?.parent?.removeChild(old);
        bubblesRef.current.delete(workerId);
      }

      const charContainer = workerId
        ? workersRef.current.get(workerId)
        : null;

      if (charContainer && appRef.current) {
        let indicator: any = null;

        switch (lastEvent.event) {
          case "worker_start":
            indicator = createStatusIndicator("working");
            break;
          case "worker_done":
            indicator = createStatusIndicator("done");
            setTimeout(() => {
              indicator?.parent?.removeChild(indicator);
              if (workerId) bubblesRef.current.delete(workerId);
            }, 3000);
            break;
          case "worker_fail":
            indicator = createStatusIndicator("fail");
            setTimeout(() => {
              indicator?.parent?.removeChild(indicator);
              if (workerId) bubblesRef.current.delete(workerId);
            }, 5000);
            break;
        }

        if (indicator) {
          indicator.x = charContainer.x;
          indicator.y = charContainer.y;
          appRef.current.stage.addChild(indicator);
          bubblesRef.current.set(workerId, indicator);
        }
      }

      // CEO speech bubble
      if (lastEvent.event === "ceo_route") {
        const ceo = workersRef.current.get("ceo");
        if (ceo && appRef.current) {
          const msg = `\u2192 ${lastEvent.data.department}: "${(lastEvent.data.message || "").slice(0, 25)}"`;
          const bubble = createSpeechBubble(msg);
          bubble.x = ceo.x;
          bubble.y = ceo.y;
          appRef.current.stage.addChild(bubble);
          setTimeout(() => bubble?.parent?.removeChild(bubble), 4000);
        }
      }

      // Dept assign speech bubble
      if (lastEvent.event === "dept_assign") {
        const assignedWorkerId = lastEvent.data.worker_id;
        const worker = assignedWorkerId
          ? workersRef.current.get(assignedWorkerId)
          : null;
        if (worker && appRef.current) {
          const msg = `\uD83D\uDCCB ${(lastEvent.data.instruction || "").slice(0, 30)}`;
          const bubble = createSpeechBubble(msg);
          bubble.x = worker.x;
          bubble.y = worker.y;
          appRef.current.stage.addChild(bubble);
          setTimeout(() => bubble?.parent?.removeChild(bubble), 3000);
        }
      }
    })();
  }, [events]);

  // Update opacity for offline workers
  useEffect(() => {
    workersRef.current.forEach((container, wId) => {
      const st = workerStatuses[wId] || "idle";
      container.alpha = st === "offline" ? 0.3 : 1.0;
    });
  }, [workerStatuses]);

  return (
    <div
      ref={canvasRef}
      className="w-full h-full rounded-xl overflow-hidden bg-[#0a0a14]"
      style={{ minHeight: 400 }}
    />
  );
}
