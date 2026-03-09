"use client";

import { useState } from "react";
import useSWR, { mutate } from "swr";
import { fetcher } from "@/lib/api";
import { Search, Plus, Trash2 } from "lucide-react";

export default function MemoryPage() {
  const [query, setQuery] = useState("");
  const [newMemory, setNewMemory] = useState("");
  const url = query ? `/api/memory/search?q=${encodeURIComponent(query)}` : "/api/memory";
  const { data } = useSWR(url, fetcher);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    mutate(url);
  };

  const handleAdd = async () => {
    if (!newMemory.trim()) return;
    await fetch("/api/memory", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: newMemory }),
    });
    setNewMemory("");
    mutate(url);
  };

  const handleDelete = async (id: string) => {
    await fetch(`/api/memory/${id}`, { method: "DELETE" });
    mutate(url);
  };

  const memories = data?.memories ?? [];

  const categoryColors: Record<string, string> = {
    user_stated: "bg-blue-500/20 text-blue-400",
    preference: "bg-purple-500/20 text-purple-400",
    identity: "bg-green-500/20 text-green-400",
    user_saved: "bg-yellow-500/20 text-yellow-400",
  };

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Memory</h1>

      <form onSubmit={handleSearch} className="flex gap-2">
        <div className="relative flex-1">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search memories..."
            className="w-full bg-[#1a1a2e] border border-[#2a2a3e] rounded-lg pl-10 pr-4 py-2.5 text-sm focus:outline-none focus:border-blue-500"
          />
        </div>
      </form>

      <div className="flex gap-2">
        <input
          type="text"
          value={newMemory}
          onChange={(e) => setNewMemory(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleAdd()}
          placeholder="Add new memory..."
          className="flex-1 bg-[#1a1a2e] border border-[#2a2a3e] rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-blue-500"
        />
        <button onClick={handleAdd} className="bg-blue-600 hover:bg-blue-700 rounded-lg px-4 py-2.5">
          <Plus size={16} />
        </button>
      </div>

      <div className="space-y-2">
        {memories.length === 0 && <p className="text-gray-500 text-sm">No memories found</p>}
        {memories.map((m: any, i: number) => (
          <div key={i} className="bg-[#12121a] border border-[#2a2a3e] rounded-lg p-4 flex items-start gap-3 group">
            <div className="flex-1">
              <p className="text-sm">{m.content}</p>
              <div className="flex items-center gap-2 mt-2">
                <span className={`text-[10px] px-2 py-0.5 rounded-full ${categoryColors[m.category] ?? "bg-gray-500/20 text-gray-400"}`}>
                  {m.category}
                </span>
                {m.importance > 0 && <span className="text-[10px] text-gray-600">importance: {m.importance}</span>}
                {m.score > 0 && <span className="text-[10px] text-gray-600">score: {m.score.toFixed(2)}</span>}
              </div>
            </div>
            <button onClick={() => handleDelete(m.id)} className="opacity-0 group-hover:opacity-100 text-gray-600 hover:text-red-400 transition-all">
              <Trash2 size={14} />
            </button>
          </div>
        ))}
      </div>

      <div className="text-xs text-gray-600">Total: {memories.length} memories</div>
    </div>
  );
}
