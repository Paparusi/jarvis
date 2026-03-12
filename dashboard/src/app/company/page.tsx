"use client";

import { useWebSocketHub, WSEvent } from "@/hooks/useWebSocketHub";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { useRef, useEffect, useState } from "react";
import dynamic from "next/dynamic";

const PixelOffice = dynamic(
  () => import("@/components/company/PixelOffice"),
  { ssr: false }
);

const EVENT_COLORS: Record<string, string> = {
  ceo_route: "text-yellow-400",
  dept_assign: "text-blue-400",
  dept_direct: "text-blue-300",
  worker_start: "text-cyan-400",
  worker_done: "text-green-400",
  worker_fail: "text-red-400",
  ops_task: "text-purple-400",
  ops_done: "text-purple-300",
  ops_started: "text-purple-500",
  msg_sent: "text-gray-300",
  msg_escalation: "text-orange-400",
  msg_collab: "text-indigo-400",
  meeting_start: "text-teal-400",
  meeting_turn: "text-teal-300",
  meeting_done: "text-teal-500",
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
    case "ops_task":
      return `[OPS] ${d.routine_name} \u2192 ${d.department}`;
    case "ops_done":
      return `[OPS] routine completed`;
    case "ops_started":
      return `[OPS] Engine started (${d.routines} routines)`;
    case "msg_sent":
      return `${d.sender_id} \u2192 ${d.recipient_id}: "${(d.subject || d.content_preview || "").slice(0, 60)}"`;
    case "msg_escalation":
      return `[ESCALATION] ${d.sender_id}: "${(d.content_preview || d.reason || "").slice(0, 80)}"`;
    case "msg_collab":
      return `[COLLAB] ${d.from_dept} \u2192 ${d.to_dept}: "${(d.content_preview || "").slice(0, 80)}"`;
    case "meeting_start":
      return `[MEETING] "${d.topic}" (${d.participants?.length || 0} participants)`;
    case "meeting_turn":
      return `[MEETING] ${d.speaker_id}: "${(d.content_preview || "").slice(0, 80)}"`;
    case "meeting_done":
      return `[MEETING] concluded: "${(d.summary_preview || "").slice(0, 80)}"`;
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

interface ChatMessage {
  role: "user" | "assistant" | "error";
  text: string;
  meta?: { model?: string; cost?: string; latency_ms?: number; tools?: any[] };
}

// ── KPI Card ─────────────────────────────────────────────────────────
function KpiCard({ label, value, sub, color = "text-white" }: {
  label: string; value: string | number; sub?: string; color?: string;
}) {
  return (
    <div className="bg-[#0c0c16] border border-[#1e1e32] rounded-xl px-3 py-2.5 min-w-0">
      <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-0.5">{label}</div>
      <div className={`text-lg font-bold ${color} leading-tight`}>{value}</div>
      {sub && <div className="text-[10px] text-gray-600 mt-0.5">{sub}</div>}
    </div>
  );
}

// ── Schedule Row ─────────────────────────────────────────────────────
const STATUS_BADGE: Record<string, string> = {
  done: "bg-green-500/15 text-green-400",
  next: "bg-yellow-500/15 text-yellow-400",
  pending: "bg-gray-500/10 text-gray-500",
};

const DEPT_COLOR: Record<string, string> = {
  finance: "text-yellow-400",
  security: "text-red-400",
  engineering: "text-blue-400",
  research: "text-purple-400",
  operations: "text-cyan-400",
  sales: "text-green-400",
  marketing: "text-pink-400",
};

function ScheduleRow({ item }: { item: any }) {
  const time = `${String(item.hour).padStart(2, "0")}:${String(item.minute).padStart(2, "0")}`;
  return (
    <div className="flex items-center gap-2 py-1 text-xs">
      <span className="text-gray-500 font-mono w-10 shrink-0">{time}</span>
      <span className={`${DEPT_COLOR[item.department] || "text-gray-400"} w-20 shrink-0 truncate`}>
        {item.department}
      </span>
      <span className="text-gray-300 flex-1 truncate">{item.name}</span>
      <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${STATUS_BADGE[item.status] || STATUS_BADGE.pending}`}>
        {item.status}
      </span>
    </div>
  );
}

// ── Main Page ────────────────────────────────────────────────────────
export default function CompanyPage() {
  const { connected, events, send } = useWebSocketHub({
    channels: ["company", "system", "chat"],
  });
  const { data: status, mutate } = useSWR("/api/company/status", fetcher, {
    refreshInterval: 5000,
  });
  const { data: kpis } = useSWR("/api/company/kpis", fetcher, {
    refreshInterval: 15000,
  });
  const { data: scheduleData } = useSWR("/api/company/schedule", fetcher, {
    refreshInterval: 30000,
  });
  const { data: messagesData } = useSWR("/api/company/messages", fetcher, {
    refreshInterval: 10000,
  });

  const feedRef = useRef<HTMLDivElement>(null);
  const chatRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [waiting, setWaiting] = useState(false);
  const [showChat, setShowChat] = useState(false);
  const [activeTab, setActiveTab] = useState<"feed" | "schedule" | "reports" | "messages">("feed");

  // Listen for chat responses from WebSocket events
  useEffect(() => {
    const last = events[events.length - 1];
    if (!last || last.channel !== "chat") return;

    if (last.event === "response") {
      const d = last.data ?? last;
      const text = d.text || "";
      if (!text) return;
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          text,
          meta: {
            model: d.model,
            cost: d.cost,
            latency_ms: d.latency_ms,
            tools: d.tools_used,
          },
        },
      ]);
      setWaiting(false);
    } else if (last.event === "error") {
      const d = last.data ?? last;
      setMessages((prev) => [...prev, { role: "error", text: d.text || "Unknown error" }]);
      setWaiting(false);
    }
  }, [events]);

  useEffect(() => {
    if (feedRef.current) feedRef.current.scrollTop = feedRef.current.scrollHeight;
  }, [events]);

  useEffect(() => {
    if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight;
  }, [messages]);

  useEffect(() => {
    const last = events[events.length - 1];
    if (last && ["worker_start", "worker_done", "worker_fail"].includes(last.event)) {
      mutate();
    }
  }, [events, mutate]);

  const handleSend = () => {
    const text = input.trim();
    if (!text || waiting || !connected) return;
    setMessages((prev) => [...prev, { role: "user", text }]);
    send({ type: "message", text });
    setInput("");
    setWaiting(true);
    setShowChat(true);
  };

  const departments = status?.departments || {};
  const totalWorkers = status?.total_workers || 0;
  const busyCount = Object.values(departments).reduce(
    (acc: number, dept: any) =>
      acc + (dept.workers || []).filter((w: any) => w.status === "busy").length,
    0,
  );

  // KPI values
  const tradingPnl = kpis?.revenue?.trading_pnl ?? 0;
  const apiCost = kpis?.costs?.api_cost ?? 0;
  const budgetPct = kpis?.costs?.budget_used_pct ?? 0;
  const tasksCompleted = kpis?.productivity?.tasks_completed ?? 0;
  const successRate = kpis?.productivity?.success_rate ?? 0;
  const reportsToday = kpis?.productivity?.reports_today ?? 0;
  const schedDone = kpis?.schedule?.routines_done ?? 0;
  const schedTotal = kpis?.schedule?.routines_dispatched ?? 0;

  const schedule = scheduleData?.schedule || [];

  return (
    <div className="h-full flex flex-col gap-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Company HQ</h1>
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-4 text-xs text-gray-400">
            <span>
              Workers: <span className="text-white font-medium">{totalWorkers}</span>
            </span>
            <span>
              Active: <span className="text-cyan-400 font-medium">{busyCount}</span>
            </span>
          </div>
          <span
            className={`text-xs px-2.5 py-1 rounded-full font-medium ${
              connected
                ? "bg-green-500/15 text-green-400"
                : "bg-red-500/15 text-red-400"
            }`}
          >
            {connected ? "LIVE" : "OFFLINE"}
          </span>
        </div>
      </div>

      {/* KPI Cards Row */}
      <div className="grid grid-cols-6 gap-2">
        <KpiCard
          label="Trading P&L"
          value={`$${tradingPnl.toFixed(2)}`}
          color={tradingPnl >= 0 ? "text-green-400" : "text-red-400"}
        />
        <KpiCard
          label="API Cost"
          value={`$${apiCost.toFixed(2)}`}
          sub={`${budgetPct.toFixed(0)}% of $10 budget`}
          color="text-orange-400"
        />
        <KpiCard
          label="Tasks Done"
          value={tasksCompleted}
          sub={`${successRate}% success`}
          color="text-cyan-400"
        />
        <KpiCard
          label="Reports"
          value={reportsToday}
          sub="today"
          color="text-purple-400"
        />
        <KpiCard
          label="Schedule"
          value={`${schedDone}/${schedTotal}`}
          sub="routines done"
          color="text-yellow-400"
        />
        <KpiCard
          label="Departments"
          value={Object.keys(departments).length}
          sub={`${totalWorkers} workers`}
          color="text-blue-400"
        />
      </div>

      {/* Virtual Office Game */}
      <div className="flex-1 bg-[#0a0a14] border border-[#1e1e32] rounded-2xl overflow-hidden min-h-[320px]">
        <PixelOffice events={events} workerStatuses={buildWorkerStatuses(status)} companyStatus={status} />
      </div>

      {/* Bottom: Chat + Tabbed Panel */}
      <div className="h-[220px] shrink-0 flex gap-3">
        {/* CEO Chat */}
        <div className="flex-1 bg-[#0c0c16] border border-[#1e1e32] rounded-2xl p-3 flex flex-col">
          <h2 className="text-sm font-semibold text-gray-300 mb-2">Talk to CEO</h2>

          <div ref={chatRef} className="flex-1 overflow-y-auto space-y-2 text-xs mb-2">
            {messages.length === 0 && !showChat && (
              <div className="text-gray-600 text-center py-6 text-sm">
                Giao viec cho CEO o day
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} className={`${m.role === "user" ? "text-right" : ""}`}>
                {m.role === "user" ? (
                  <span className="inline-block bg-blue-600/20 text-blue-300 rounded-lg px-3 py-1.5 max-w-[85%] text-left">
                    {m.text}
                  </span>
                ) : m.role === "error" ? (
                  <span className="inline-block bg-red-600/15 text-red-400 rounded-lg px-3 py-1.5 max-w-[85%]">
                    {m.text}
                  </span>
                ) : (
                  <div className="space-y-1">
                    <div className="bg-[#14141f] border border-[#1e1e32] rounded-lg px-3 py-2 max-w-[95%] text-gray-300 whitespace-pre-wrap leading-relaxed max-h-[120px] overflow-y-auto">
                      {m.text}
                    </div>
                    {m.meta && (
                      <div className="text-[10px] text-gray-600">
                        {m.meta.model} · {m.meta.cost} · {((m.meta.latency_ms || 0) / 1000).toFixed(1)}s
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
            {waiting && (
              <div className="text-gray-500 text-xs animate-pulse">CEO is thinking...</div>
            )}
          </div>

          <div className="flex gap-2">
            <input
              ref={inputRef}
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSend()}
              placeholder={connected ? "Giao viec cho CEO..." : "Offline..."}
              disabled={!connected || waiting}
              className="flex-1 bg-[#0a0a14] border border-[#1e1e32] rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-600 outline-none focus:border-yellow-500/40 disabled:opacity-40"
            />
            <button
              onClick={handleSend}
              disabled={!connected || waiting || !input.trim()}
              className="px-4 py-2 bg-yellow-600/20 text-yellow-400 rounded-lg text-sm font-medium hover:bg-yellow-600/30 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              Send
            </button>
          </div>
        </div>

        {/* Tabbed Panel: Feed / Schedule / Reports / Messages */}
        <div className="flex-1 bg-[#0c0c16] border border-[#1e1e32] rounded-2xl p-3 flex flex-col">
          {/* Tab headers */}
          <div className="flex items-center gap-1 mb-2">
            {(["feed", "messages", "schedule", "reports"] as const).map((tab) => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
                  activeTab === tab
                    ? "bg-white/10 text-white"
                    : "text-gray-500 hover:text-gray-300"
                }`}
              >
                {tab === "feed" ? "Activity" : tab === "schedule" ? "Schedule" : tab === "messages" ? "Messages" : "Dept KPIs"}
              </button>
            ))}
            {activeTab === "feed" && (
              <span className="text-[10px] text-gray-600 ml-auto">{events.length} events</span>
            )}
          </div>

          {/* Tab content */}
          <div ref={feedRef} className="flex-1 overflow-y-auto">
            {activeTab === "feed" && (
              <div className="space-y-0.5 text-xs font-mono">
                {events.length === 0 && (
                  <div className="text-gray-600 text-center py-6 text-sm">Waiting for events...</div>
                )}
                {events.map((e, i) => (
                  <div key={i} className="flex gap-2 py-0.5 hover:bg-white/[0.02] rounded px-1 -mx-1">
                    <span className="text-gray-600 shrink-0">{e.ts?.slice(11, 19) || ""}</span>
                    <span className={EVENT_COLORS[e.event] || "text-gray-400"}>
                      {formatEvent(e)}
                    </span>
                  </div>
                ))}
              </div>
            )}

            {activeTab === "schedule" && (
              <div className="space-y-0.5">
                {schedule.length === 0 && (
                  <div className="text-gray-600 text-center py-6 text-sm">No schedule data</div>
                )}
                {schedule.map((item: any, i: number) => (
                  <ScheduleRow key={i} item={item} />
                ))}
              </div>
            )}

            {activeTab === "messages" && (
              <div className="space-y-1 text-xs">
                {(messagesData?.messages || []).length === 0 && (
                  <div className="text-gray-600 text-center py-6 text-sm">No internal messages yet</div>
                )}
                {(messagesData?.messages || []).slice(0, 30).map((msg: any, i: number) => {
                  const typeColor: Record<string, string> = {
                    delegation: "text-blue-400",
                    escalation: "text-orange-400",
                    collaboration_request: "text-indigo-400",
                    response: "text-green-400",
                    meeting_turn: "text-teal-400",
                  };
                  return (
                    <div key={i} className="flex gap-2 py-0.5">
                      <span className="text-gray-600 font-mono w-14 shrink-0">
                        {msg.created_at?.slice(11, 19) || ""}
                      </span>
                      <span className={`${typeColor[msg.message_type] || "text-gray-400"} shrink-0`}>
                        [{msg.message_type?.slice(0, 8)}]
                      </span>
                      <span className="text-gray-500 shrink-0">{msg.sender_id}</span>
                      <span className="text-gray-600">{"\u2192"}</span>
                      <span className="text-gray-500 shrink-0">{msg.recipient_id}</span>
                      <span className="text-gray-400 truncate flex-1">
                        {msg.content?.slice(0, 80)}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}

            {activeTab === "reports" && (
              <div className="space-y-1.5 text-xs">
                {Object.entries(kpis?.departments || {}).map(([dept, data]: [string, any]) => (
                  <div key={dept} className="flex items-center gap-3 py-1">
                    <span className={`${DEPT_COLOR[dept] || "text-gray-400"} w-24 shrink-0 font-medium capitalize`}>
                      {dept}
                    </span>
                    <span className="text-gray-400">
                      {data.tasks_completed} tasks
                    </span>
                    <span className="text-gray-500">|</span>
                    <span className="text-gray-400">
                      {data.reports_today} reports
                    </span>
                    <span className="text-gray-500">|</span>
                    <span className="text-orange-400/80">
                      ${data.cost?.toFixed(3) || "0.000"}
                    </span>
                    {data.tasks_failed > 0 && (
                      <>
                        <span className="text-gray-500">|</span>
                        <span className="text-red-400">{data.tasks_failed} failed</span>
                      </>
                    )}
                  </div>
                ))}
                {!kpis?.departments && (
                  <div className="text-gray-600 text-center py-6 text-sm">No KPI data</div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
