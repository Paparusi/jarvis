"use client";

import { useState, useEffect } from "react";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { Play, Square, RefreshCw } from "lucide-react";
import TradingChart from "@/components/TradingChart";
import PositionsTable from "@/components/PositionsTable";
import ApprovalCard from "@/components/ApprovalCard";
import { useWebSocket } from "@/hooks/useWebSocket";

export default function TradingPage() {
  const { data: status } = useSWR("/api/trading/status", fetcher, { refreshInterval: 5000 });
  const { data: positions } = useSWR("/api/trading/positions", fetcher, { refreshInterval: 5000 });
  const { data: pnl } = useSWR("/api/trading/pnl", fetcher, { refreshInterval: 10000 });
  const { data: zones } = useSWR("/api/trading/zones", fetcher, { refreshInterval: 30000 });
  const { data: approvals, mutate: refreshApprovals } = useSWR("/api/trading/approvals", fetcher, { refreshInterval: 5000 });
  const { data: candleData } = useSWR("/api/trading/candles?symbol=XAUUSD&timeframe=H1&count=200", fetcher, { refreshInterval: 60000 });

  const { messages } = useWebSocket("/ws/trading");
  const [liveBid, setLiveBid] = useState<number | null>(null);

  // Handle WebSocket events
  useEffect(() => {
    if (messages.length === 0) return;
    const last = messages[messages.length - 1];
    if (last.type === "tick") {
      setLiveBid(last.bid);
    } else if (last.type === "approval" || last.type === "approval_update") {
      refreshApprovals();
    }
  }, [messages, refreshApprovals]);

  const handleControl = async (action: string) => {
    await fetch("/api/trading/control", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
  };

  const handleApprove = async (id: string) => {
    await fetch(`/api/trading/approve/${id}`, { method: "POST" });
    refreshApprovals();
  };

  const handleReject = async (id: string) => {
    await fetch(`/api/trading/reject/${id}`, { method: "POST" });
    refreshApprovals();
  };

  const running = status?.running ?? false;
  const pendingApprovals = approvals?.approvals ?? [];

  // Transform candle data for chart
  const chartData = (candleData?.candles ?? []).map((c: any) => ({
    time: c.time,
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
  }));

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <h1 className="text-2xl font-bold">Trading</h1>
          {liveBid && (
            <span className="text-lg font-mono text-yellow-400">{liveBid.toFixed(2)}</span>
          )}
        </div>
        <div className="flex gap-2">
          {!running ? (
            <button onClick={() => handleControl("start")}
              className="flex items-center gap-2 bg-green-600 hover:bg-green-700 px-4 py-2 rounded-lg text-sm">
              <Play size={14} /> Start Brain
            </button>
          ) : (
            <button onClick={() => handleControl("stop")}
              className="flex items-center gap-2 bg-red-600 hover:bg-red-700 px-4 py-2 rounded-lg text-sm">
              <Square size={14} /> Stop Brain
            </button>
          )}
          <button onClick={() => handleControl("plan")}
            className="flex items-center gap-2 bg-[#1a1a2e] border border-[#2a2a3e] hover:bg-[#2a2a3e] px-4 py-2 rounded-lg text-sm">
            <RefreshCw size={14} /> New Plan
          </button>
        </div>
      </div>

      {/* Pending Approvals */}
      {pendingApprovals.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-lg font-semibold text-yellow-400">
            Pending Approvals ({pendingApprovals.length})
          </h2>
          {pendingApprovals.map((a: any) => (
            <ApprovalCard key={a.id} approval={a} onApprove={handleApprove} onReject={handleReject} />
          ))}
        </div>
      )}

      {/* Brain Status */}
      <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-4">
        <div className="flex items-center gap-3 mb-3">
          <span className={`w-2.5 h-2.5 rounded-full ${running ? "bg-green-400 animate-pulse" : "bg-gray-600"}`} />
          <span className="font-medium">{running ? "Brain Active" : "Brain Stopped"}</span>
          {status?.session && <span className="text-gray-500 text-sm">Session: {status.session}</span>}
        </div>
        {status?.current_plan && (
          <div className="text-sm text-gray-400">
            <span className="text-gray-500">Bias:</span> {status.current_plan.bias} |
            <span className="text-gray-500 ml-2">Zones:</span> {status.current_plan.zones_count ?? 0} |
            <span className="text-gray-500 ml-2">Trades:</span> {status.current_plan.trades_taken ?? 0}/{status.current_plan.max_trades ?? 3}
          </div>
        )}
      </div>

      {/* PnL Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          { label: "Daily PnL", value: pnl?.daily_pnl ?? 0, pct: pnl?.daily_pnl_pct ?? 0 },
          { label: "Weekly PnL", value: pnl?.weekly_pnl ?? 0, pct: pnl?.weekly_pnl_pct ?? 0 },
          { label: "Trades Today", value: pnl?.daily_trades ?? 0 },
          { label: "Loss Streak", value: pnl?.consecutive_losses ?? 0 },
        ].map(({ label, value, pct }) => (
          <div key={label} className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-4">
            <div className="text-xs text-gray-500">{label}</div>
            <div className={`text-xl font-bold ${
              typeof pct === "number" ? (value >= 0 ? "text-green-400" : "text-red-400") : ""
            }`}>
              {typeof pct === "number" ? `$${Number(value).toFixed(2)}` : value}
            </div>
            {typeof pct === "number" && (
              <div className={`text-xs ${value >= 0 ? "text-green-600" : "text-red-600"}`}>
                {value >= 0 ? "+" : ""}{Number(pct).toFixed(2)}%
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Chart with real data */}
      <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-4">
        <h2 className="text-lg font-semibold mb-3">XAUUSD Chart</h2>
        <TradingChart data={chartData} zones={zones?.zones ?? []} />
      </div>

      {/* Positions */}
      <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-5">
        <h2 className="text-lg font-semibold mb-3">Open Positions</h2>
        <PositionsTable positions={positions?.positions ?? []} />
      </div>
    </div>
  );
}
