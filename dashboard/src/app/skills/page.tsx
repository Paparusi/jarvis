"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";

export default function SkillsPage() {
  const { data } = useSWR("/api/skills", fetcher);
  const skills = data?.skills ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Skills</h1>
        <span className="text-sm text-gray-500">{skills.length} skills loaded</span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {skills.map((s: any) => (
          <div key={s.name} className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-4 hover:border-blue-500/30 transition-colors">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-lg">{s.emoji || "🎯"}</span>
              <h3 className="font-semibold text-sm">{s.name}</h3>
              <span className="text-[10px] text-gray-600 ml-auto">v{s.version}</span>
            </div>
            <p className="text-xs text-gray-400 mb-3 line-clamp-2">{s.description}</p>
            <div className="flex items-center gap-3 text-[11px] text-gray-500">
              <span className={`px-1.5 py-0.5 rounded ${
                s.category === "core" ? "bg-blue-500/10 text-blue-400" :
                s.category === "security" ? "bg-red-500/10 text-red-400" :
                s.category === "auto" ? "bg-green-500/10 text-green-400" :
                "bg-gray-500/10 text-gray-400"
              }`}>{s.category}</span>
              <span>Priority: {s.priority}</span>
              <span>Uses: {s.usage_count}</span>
              <span className={s.success_rate >= 0.8 ? "text-green-400" : s.success_rate >= 0.5 ? "text-yellow-400" : "text-red-400"}>
                {(s.success_rate * 100).toFixed(0)}%
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
