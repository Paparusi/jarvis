import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";

interface Props {
  role: "user" | "assistant";
  content: string;
  model?: string;
  latency_ms?: number;
  tools_used?: string[];
}

export default function ChatMessage({ role, content, model, latency_ms, tools_used }: Props) {
  return (
    <div className={`flex gap-3 ${role === "user" ? "flex-row-reverse" : ""}`}>
      <div className={`w-8 h-8 rounded-lg flex items-center justify-center text-sm shrink-0 ${
        role === "assistant"
          ? "bg-gradient-to-br from-blue-500 to-purple-500"
          : "bg-[#1a1a2e]"
      }`}>
        {role === "assistant" ? "J" : "B"}
      </div>
      <div className={`max-w-[80%] rounded-xl px-4 py-3 text-sm leading-relaxed ${
        role === "user"
          ? "bg-blue-600 text-white"
          : "bg-[#12121a] border border-[#2a2a3e]"
      }`}>
        {role === "assistant" ? (
          <ReactMarkdown rehypePlugins={[rehypeHighlight]} remarkPlugins={[remarkGfm]}>
            {content}
          </ReactMarkdown>
        ) : (
          <p>{content}</p>
        )}
        {role === "assistant" && (model || tools_used?.length) && (
          <div className="flex items-center gap-2 mt-2 text-xs text-gray-500">
            {model && <span>{model}</span>}
            {latency_ms && <span>{latency_ms}ms</span>}
            {tools_used?.map((t: any) => (
              <span key={typeof t === "string" ? t : t.name} className="px-1.5 py-0.5 bg-blue-500/10 text-blue-400 rounded text-[10px]">
                {typeof t === "string" ? t : t.name}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
