"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import StatCard from "@/components/StatCard";
import {
  Wrench, Target, Database, Zap,
  DollarSign, TrendingUp, Brain, Activity,
} from "lucide-react";

export default function OverviewPage() {
  const { data: status } = useSWR("/api/status", fetcher, { refreshInterval: 10000 });
  const { data: health } = useSWR("/api/health", fetcher, { refreshInterval: 30000 });
  const { data: trading } = useSWR("/api/trading/pnl", fetcher, { refreshInterval: 10000 });
  const { data: metrics } = useSWR("/api/system/metrics", fetcher, { refreshInterval: 5000 });

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Dashboard</h1>

      {/* Status Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard title="Tools" value={status?.tools ?? "—"} icon={Wrench} color="blue" />
        <StatCard title="Skills" value={status?.skills ?? "—"} icon={Target} color="purple" />
        <StatCard title="Cache Hits" value={status?.cache_entries ?? "—"} icon={Zap} color="yellow" />
        <StatCard title="API Cost" value={status?.total_cost ?? "—"} icon={DollarSign} color="green" />
      </div>

      {/* Trading + System Row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Trading PnL */}
        <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-5">
          <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
            <TrendingUp size={18} /> Trading
          </h2>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <div className="text-xs text-gray-500">Daily PnL</div>
              <div className={`text-xl font-bold ${(trading?.daily_pnl ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
                ${(trading?.daily_pnl ?? 0).toFixed(2)}
              </div>
            </div>
            <div>
              <div className="text-xs text-gray-500">Weekly PnL</div>
              <div className={`text-xl font-bold ${(trading?.weekly_pnl ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
                ${(trading?.weekly_pnl ?? 0).toFixed(2)}
              </div>
            </div>
            <div>
              <div className="text-xs text-gray-500">Trades Today</div>
              <div className="text-xl font-bold">{trading?.daily_trades ?? 0}</div>
            </div>
            <div>
              <div className="text-xs text-gray-500">Loss Streak</div>
              <div className="text-xl font-bold">{trading?.consecutive_losses ?? 0}</div>
            </div>
          </div>
        </div>

        {/* System Metrics */}
        <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-5">
          <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
            <Activity size={18} /> System
          </h2>
          <div className="space-y-3">
            {[
              { label: "CPU", value: metrics?.cpu_percent ?? 0 },
              { label: "RAM", value: metrics?.ram_percent ?? 0, detail: `${metrics?.ram_used_gb ?? 0}/${metrics?.ram_total_gb ?? 0} GB` },
              { label: "Disk", value: metrics?.disk_percent ?? 0 },
            ].map(({ label, value, detail }) => (
              <div key={label}>
                <div className="flex justify-between text-sm mb-1">
                  <span className="text-gray-400">{label}</span>
                  <span>{value}%{detail ? ` (${detail})` : ""}</span>
                </div>
                <div className="h-2 bg-[#1a1a2e] rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${
                      value > 80 ? "bg-red-400" : value > 60 ? "bg-yellow-400" : "bg-green-400"
                    }`}
                    style={{ width: `${value}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Health Checks */}
      <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-5">
        <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
          <Brain size={18} /> Health
        </h2>
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {(health?.checks ?? []).map((c: any) => (
            <div key={c.name} className="flex items-center gap-2 text-sm">
              <span className={`w-2 h-2 rounded-full ${
                c.status === "ok" || c.status === "pass" ? "bg-green-400" : "bg-red-400"
              }`} />
              <span className="text-gray-400">{c.name}</span>
              <span className="text-gray-600 text-xs ml-auto">{c.latency_ms}ms</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
