"use client";

import { useState, useRef, useEffect } from "react";
import { Send } from "lucide-react";
import { useWebSocket } from "@/hooks/useWebSocket";
import ChatMessage from "@/components/ChatMessage";

interface Message {
  role: "user" | "assistant";
  content: string;
  model?: string;
  latency_ms?: number;
  tools_used?: any[];
}

export default function ChatPage() {
  const [input, setInput] = useState("");
  const [chatMessages, setChatMessages] = useState<Message[]>([]);
  const [thinking, setThinking] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const { connected, messages, send } = useWebSocket("/ws/chat");

  useEffect(() => {
    if (messages.length === 0) return;
    const last = messages[messages.length - 1];

    if (last.type === "thinking") {
      setThinking(true);
    } else if (last.type === "response") {
      setThinking(false);
      setChatMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: last.text || "",
          model: last.model,
          latency_ms: last.latency_ms,
          tools_used: last.tools_used,
        },
      ]);
    } else if (last.type === "error") {
      setThinking(false);
      setChatMessages((prev) => [
        ...prev,
        { role: "assistant", content: `Error: ${last.text}` },
      ]);
    }
  }, [messages]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatMessages, thinking]);

  const handleSend = () => {
    if (!input.trim() || !connected) return;
    setChatMessages((prev) => [...prev, { role: "user", content: input }]);
    send({ type: "message", text: input });
    setInput("");
  };

  return (
    <div className="flex flex-col h-full -m-6">
      <div className="flex-1 overflow-y-auto p-6 space-y-4">
        {chatMessages.map((msg, i) => (
          <ChatMessage key={i} {...msg} />
        ))}
        {thinking && (
          <div className="flex items-center gap-2 text-gray-500 text-sm">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-purple-500 flex items-center justify-center text-sm">J</div>
            <span className="animate-pulse">Thinking...</span>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="border-t border-[#2a2a3e] p-4">
        <div className="flex gap-2 max-w-3xl mx-auto">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSend()}
            placeholder={connected ? "Nhập tin nhắn..." : "Đang kết nối..."}
            disabled={!connected}
            className="flex-1 bg-[#1a1a2e] border border-[#2a2a3e] rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-blue-500 disabled:opacity-50"
          />
          <button
            onClick={handleSend}
            disabled={!connected || !input.trim()}
            className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 rounded-lg px-4 py-2.5 transition-colors"
          >
            <Send size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}
