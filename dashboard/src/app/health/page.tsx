"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { RefreshCw } from "lucide-react";

export default function HealthPage() {
  const { data: health, mutate: refreshHealth } = useSWR("/api/health", fetcher);
  const { data: metrics } = useSWR("/api/system/metrics", fetcher, { refreshInterval: 5000 });
  const { data: dreamtime } = useSWR("/api/dreamtime/status", fetcher, { refreshInterval: 30000 });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Health Monitor</h1>
        <button onClick={() => refreshHealth()} className="flex items-center gap-2 bg-[#1a1a2e] border border-[#2a2a3e] px-4 py-2 rounded-lg text-sm hover:bg-[#2a2a3e]">
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-5">
        <h2 className="text-lg font-semibold mb-4">Health Checks</h2>
        <div className="space-y-3">
          {(health?.checks ?? []).map((c: any) => (
            <div key={c.name} className="flex items-center justify-between py-2 border-b border-[#1a1a2e] last:border-0">
              <div className="flex items-center gap-3">
                <span className={`w-3 h-3 rounded-full ${
                  c.status === "ok" || c.status === "pass" ? "bg-green-400" : "bg-red-400"
                }`} />
                <span className="font-medium text-sm">{c.name}</span>
              </div>
              <div className="flex items-center gap-4 text-sm">
                <span className="text-gray-400">{c.message}</span>
                <span className="text-gray-600 text-xs">{c.latency_ms}ms</span>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-5">
        <h2 className="text-lg font-semibold mb-4">System Resources</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {[
            { label: "CPU", value: metrics?.cpu_percent ?? 0 },
            { label: "RAM", value: metrics?.ram_percent ?? 0, detail: `${metrics?.ram_used_gb ?? 0}/${metrics?.ram_total_gb ?? 0} GB` },
            { label: "Disk", value: metrics?.disk_percent ?? 0, detail: `${metrics?.disk_used_gb ?? 0} GB used` },
          ].map(({ label, value, detail }) => (
            <div key={label} className="text-center">
              <div className="relative w-24 h-24 mx-auto mb-2">
                <svg className="w-24 h-24 -rotate-90" viewBox="0 0 100 100">
                  <circle cx="50" cy="50" r="40" fill="none" stroke="#1a1a2e" strokeWidth="8" />
                  <circle cx="50" cy="50" r="40" fill="none"
                    stroke={value > 80 ? "#f87171" : value > 60 ? "#fbbf24" : "#4ade80"}
                    strokeWidth="8" strokeLinecap="round"
                    strokeDasharray={`${value * 2.51} 251`} />
                </svg>
                <div className="absolute inset-0 flex items-center justify-center text-lg font-bold">
                  {value}%
                </div>
              </div>
              <div className="text-sm font-medium">{label}</div>
              {detail && <div className="text-xs text-gray-500">{detail}</div>}
            </div>
          ))}
        </div>
      </div>

      <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-5">
        <h2 className="text-lg font-semibold mb-4">Dreamtime</h2>
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <span className="text-gray-500">Status:</span>
            <span className="ml-2">{dreamtime?.enabled ? "Enabled" : "Disabled"}</span>
          </div>
          <div>
            <span className="text-gray-500">Idle trigger:</span>
            <span className="ml-2">{dreamtime?.idle_minutes ?? 30} min</span>
          </div>
          <div>
            <span className="text-gray-500">Last run:</span>
            <span className="ml-2">{dreamtime?.last_run ?? "Never"}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
