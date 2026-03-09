"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { Shield, Bell, Key } from "lucide-react";

export default function SettingsPage() {
  const { data: status } = useSWR("/api/status", fetcher);

  return (
    <div className="space-y-6 max-w-2xl">
      <h1 className="text-2xl font-bold">Settings</h1>

      <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-5">
        <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
          <Key size={18} /> System
        </h2>
        <div className="space-y-3 text-sm">
          <div className="flex justify-between py-2 border-b border-[#1a1a2e]">
            <span className="text-gray-400">Version</span>
            <span>{status?.version ?? "—"}</span>
          </div>
          <div className="flex justify-between py-2 border-b border-[#1a1a2e]">
            <span className="text-gray-400">LLM Model</span>
            <span>{status?.cloud_model ?? "—"}</span>
          </div>
          <div className="flex justify-between py-2 border-b border-[#1a1a2e]">
            <span className="text-gray-400">Tools</span>
            <span>{status?.tools ?? "—"}</span>
          </div>
          <div className="flex justify-between py-2">
            <span className="text-gray-400">Skills</span>
            <span>{status?.skills ?? "—"}</span>
          </div>
        </div>
      </div>

      <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-5">
        <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
          <Shield size={18} /> Risk Guard
        </h2>
        <div className="space-y-3 text-sm">
          {[
            { label: "Max Risk Per Trade", value: "1%" },
            { label: "Max Lot Size", value: "0.1" },
            { label: "Min R:R Ratio", value: "1.5" },
            { label: "Max Daily Loss", value: "3%" },
            { label: "Max Weekly Loss", value: "8%" },
            { label: "Max Open Positions", value: "3" },
            { label: "Max Daily Trades", value: "5" },
          ].map(({ label, value }) => (
            <div key={label} className="flex justify-between py-2 border-b border-[#1a1a2e] last:border-0">
              <span className="text-gray-400">{label}</span>
              <span className="font-mono">{value}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-5">
        <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
          <Bell size={18} /> Notifications
        </h2>
        <div className="space-y-3 text-sm">
          <div className="flex justify-between items-center py-2">
            <span className="text-gray-400">Telegram Alerts</span>
            <span className="text-green-400">Active</span>
          </div>
          <div className="flex justify-between items-center py-2">
            <span className="text-gray-400">Owner ID</span>
            <span className="font-mono text-gray-500">1991690969</span>
          </div>
        </div>
      </div>
    </div>
  );
}
