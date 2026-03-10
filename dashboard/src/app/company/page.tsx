"use client";

import { useWebSocketHub, WSEvent } from "@/hooks/useWebSocketHub";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { useRef, useEffect } from "react";
import dynamic from "next/dynamic";

const PixelOffice = dynamic(
  () => import("@/components/company/PixelOffice"),
  { ssr: false }
);

const STATUS_ICONS: Record<string, string> = {
  idle: "\uD83D\uDFE2",
  busy: "\uD83D\uDD35",
  error: "\uD83D\uDD34",
  offline: "\u26AB",
};

const EVENT_COLORS: Record<string, string> = {
  ceo_route: "text-yellow-400",
  dept_assign: "text-blue-400",
  dept_direct: "text-blue-300",
  worker_start: "text-cyan-400",
  worker_done: "text-green-400",
  worker_fail: "text-red-400",
};

function formatEvent(e: WSEvent): string {
  const d = e.data;
  switch (e.event) {
    case "ceo_route":
      return `CEO \u2192 ${d.department}: "${d.message || ""}"`;
    case "dept_assign":
      return `${d.department} \u2192 ${d.worker_name}: "${d.instruction || ""}"`;
    case "dept_direct":
      return `${d.department}: handling directly (${d.reason})`;
    case "worker_start":
      return `${d.worker_name} started working`;
    case "worker_done":
      return `${d.worker_name} \u2705 done (${d.duration_ms}ms)`;
    case "worker_fail":
      return `${d.worker_name} \u274C ${d.error || "failed"}`;
    default:
      return `${e.event}: ${JSON.stringify(d).slice(0, 80)}`;
  }
}

function buildWorkerStatuses(status: any): Record<string, string> {
  const result: Record<string, string> = {};
  if (!status?.departments) return result;
  Object.values(status.departments).forEach((dept: any) => {
    (dept.workers || []).forEach((w: any) => {
      result[w.worker_id] = w.status;
    });
  });
  return result;
}

export default function CompanyPage() {
  const { connected, events } = useWebSocketHub({
    channels: ["company", "system"],
  });
  const { data: status, mutate } = useSWR("/api/company/status", fetcher, {
    refreshInterval: 5000,
  });
  const feedRef = useRef<HTMLDivElement>(null);

  // Auto-scroll activity feed
  useEffect(() => {
    if (feedRef.current) {
      feedRef.current.scrollTop = feedRef.current.scrollHeight;
    }
  }, [events]);

  // Refresh org chart on worker status changes
  useEffect(() => {
    const last = events[events.length - 1];
    if (
      last &&
      ["worker_start", "worker_done", "worker_fail"].includes(last.event)
    ) {
      mutate();
    }
  }, [events, mutate]);

  const departments = status?.departments || {};
  const totalWorkers = status?.total_workers || 0;
  const busyCount = Object.values(departments).reduce(
    (acc: number, dept: any) => {
      return (
        acc +
        (dept.workers || []).filter((w: any) => w.status === "busy").length
      );
    },
    0,
  );

  return (
    <div className="h-full flex flex-col gap-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Company HQ</h1>
        <span
          className={`text-xs px-2 py-1 rounded-full ${
            connected
              ? "bg-green-500/20 text-green-400"
              : "bg-red-500/20 text-red-400"
          }`}
        >
          {connected ? "LIVE" : "OFFLINE"}
        </span>
      </div>

      {/* Pixel Office */}
      <div className="h-[420px] bg-[#12121a] border border-[#2a2a3e] rounded-xl overflow-hidden">
        <PixelOffice events={events} workerStatuses={buildWorkerStatuses(status)} />
      </div>

      {/* Main panels */}
      <div className="flex-1 grid grid-cols-1 lg:grid-cols-2 gap-4 min-h-0">
        {/* Left: Org Chart */}
        <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-5 overflow-y-auto">
          <h2 className="text-lg font-semibold mb-4">
            {"\uD83C\uDFE2"} Organization
          </h2>
          <div className="space-y-4">
            {/* CEO */}
            <div className="flex items-center gap-2 text-yellow-400 font-medium">
              <span>{"\uD83D\uDC54"}</span> CEO (JARVIS)
            </div>

            {/* Departments */}
            {Object.entries(departments).map(
              ([deptName, dept]: [string, any]) => (
                <div key={deptName} className="ml-4">
                  <div className="flex items-center gap-2 text-sm font-medium text-gray-300 mb-1">
                    <span className="text-gray-500">{"\u251C\u2500\u2500"}</span>
                    {deptName.charAt(0).toUpperCase() + deptName.slice(1)} Head
                  </div>
                  {(dept.workers || []).map((w: any) => (
                    <div
                      key={w.worker_id}
                      className="ml-6 flex items-center justify-between text-sm py-0.5"
                    >
                      <div className="flex items-center gap-2">
                        <span>{STATUS_ICONS[w.status] || "\u26AB"}</span>
                        <span className="text-gray-400">{w.name}</span>
                      </div>
                      <div className="flex items-center gap-3 text-xs text-gray-600">
                        <span>{w.tasks_completed || 0} tasks</span>
                        <span>${(w.total_cost || 0).toFixed(2)}</span>
                      </div>
                    </div>
                  ))}
                </div>
              ),
            )}

            {Object.keys(departments).length === 0 && (
              <div className="text-gray-600 text-sm">
                Company not initialized
              </div>
            )}
          </div>
        </div>

        {/* Right: Activity Feed */}
        <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-5 flex flex-col min-h-0">
          <h2 className="text-lg font-semibold mb-4">
            {"\uD83D\uDCE1"} Activity Feed
          </h2>
          <div
            ref={feedRef}
            className="flex-1 overflow-y-auto space-y-1 text-sm font-mono"
          >
            {events.length === 0 && (
              <div className="text-gray-600 text-center py-8">
                Waiting for events...
              </div>
            )}
            {events.map((e, i) => (
              <div key={i} className="flex gap-2">
                <span className="text-gray-600 shrink-0">
                  {e.ts?.slice(11, 19) || ""}
                </span>
                <span className={EVENT_COLORS[e.event] || "text-gray-400"}>
                  {formatEvent(e)}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Bottom Stats Bar */}
      <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl px-5 py-3 flex items-center gap-6 text-sm">
        <span className="text-gray-400">
          Workers: <span className="text-white">{totalWorkers}</span>
        </span>
        <span className="text-gray-400">
          Busy: <span className="text-cyan-400">{busyCount}</span>
        </span>
        <span className="text-gray-400">
          Cost:{" "}
          <span className="text-green-400">
            ${(status?.cost?.daily_spent || 0).toFixed(2)}
          </span>
          <span className="text-gray-600">/$10</span>
        </span>
        <span className="text-gray-400">
          Events: <span className="text-white">{events.length}</span>
        </span>
      </div>
    </div>
  );
}
