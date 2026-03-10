"use client";

import { useEffect, useRef, useState, useCallback } from "react";

export interface WSEvent {
  channel: string;
  event: string;
  data: Record<string, any>;
  ts: string;
}

interface UseWebSocketHubOptions {
  channels: string[];
  maxEvents?: number;
}

export function useWebSocketHub({
  channels,
  maxEvents = 200,
}: UseWebSocketHubOptions) {
  const ws = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const [events, setEvents] = useState<WSEvent[]>([]);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const channelsRef = useRef(channels);
  channelsRef.current = channels;

  const connect = useCallback(() => {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${window.location.host}/ws`);

    socket.onopen = () => {
      setConnected(true);
      socket.send(
        JSON.stringify({
          action: "subscribe",
          channels: channelsRef.current,
        }),
      );
    };

    socket.onclose = () => {
      setConnected(false);
      reconnectTimer.current = setTimeout(connect, 3000);
    };

    socket.onmessage = (msg) => {
      try {
        const data = JSON.parse(msg.data);
        if (data.channel && data.event) {
          setEvents((prev) => {
            const next = [...prev, data as WSEvent];
            return next.length > maxEvents ? next.slice(-maxEvents) : next;
          });
        }
      } catch {
        // ignore malformed messages
      }
    };

    ws.current = socket;
  }, [maxEvents]);

  useEffect(() => {
    connect();
    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      ws.current?.close();
    };
  }, [connect]);

  const send = useCallback((data: any) => {
    if (ws.current?.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify(data));
    }
  }, []);

  const clearEvents = useCallback(() => setEvents([]), []);

  return { connected, events, send, clearEvents };
}
