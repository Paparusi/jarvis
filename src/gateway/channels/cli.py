"""CLI Channel Adapter — Interactive REPL for JARVIS.

Chat with JARVIS directly in terminal. Useful for development and testing.
Supports all commands from Telegram + colored output.
"""

from __future__ import annotations

import sys

from src.brain.collector import DataCollector
from src.brain.processor import DataProcessor
from src.digital_twin.user_model import UserModel
from src.dreamtime.consolidator import MemoryConsolidator
from src.dreamtime.dreamer import Dreamer
from src.gateway.event_bus import EventType, get_event_bus
from src.gateway.models import AgentResponse, Channel, MessageEnvelope
from src.gateway.session import SessionManager
from src.intelligence.router import LLMRouter
from src.memory.manager import MemoryManager
from src.skills.evolver import SkillEvolver
from src.skills.loader import SkillLoader
from src.skills.registry import SkillRegistry
from src.skills.router import SkillRouter
from src.tools.base import ToolRegistry
from src.tools.registry_all import ALL_TOOLS
from src.utils.logging import get_logger

log = get_logger("cli")

# ANSI colors
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


class CLIAdapter:
    """Interactive CLI adapter for JARVIS."""

    def __init__(self) -> None:
        self._sessions = SessionManager()
        self._collector = DataCollector()
        self._processor = DataProcessor()
        self._memory = MemoryManager()
        self._user_model = UserModel()
        self._skill_loader = SkillLoader()
        self._skill_router = SkillRouter(self._skill_loader)
        self._bus = get_event_bus()

        # Load skills
        self._skill_loader.load_all()
        self._skill_registry = SkillRegistry(self._skill_loader)
        self._skill_registry._apply_metrics()

        # Register tools (centralized in registry_all.py)
        self._tool_registry = ToolRegistry()
        for tool in ALL_TOOLS:
            self._tool_registry.register(tool)

        # Init router
        skill_summary = self._skill_loader.get_metadata_summary()
        self._router = LLMRouter(
            skill_summary=skill_summary,
            tool_registry=self._tool_registry,
        )

        # Dreamtime components
        self._memory_consolidator = MemoryConsolidator(self._memory.semantic)
        self._dreamer = Dreamer(collector=self._collector, skill_registry=self._skill_registry)
        self._evolver = SkillEvolver(self._skill_registry, self._skill_loader)

        # CLI-specific
        self._user_id = "cli_user"
        self._session = self._sessions.get_or_create(
            Channel.CLI, self._user_id, "CLI User"
        )

    async def start(self) -> None:
        """Start the interactive REPL."""
        self._print_banner()

        while True:
            try:
                user_input = input(f"\n{_GREEN}Bạn >{_RESET} ").strip()
            except (EOFError, KeyboardInterrupt):
                print(f"\n{_DIM}Tạm biệt!{_RESET}")
                break

            if not user_input:
                continue

            # Handle commands
            if user_input.startswith("/"):
                should_exit = await self._handle_command(user_input)
                if should_exit:
                    break
                continue

            # Handle chat
            await self._handle_message(user_input)

    async def stop(self) -> None:
        log.info("cli_stopped")

    def _print_banner(self) -> None:
        skills_count = len(self._skill_loader.get_all_metadata())
        tools_count = len(self._tool_registry.get_all())
        print(f"""
{_BOLD}{_CYAN}╔══════════════════════════════════════╗
║     🤖 JARVIS v2 — CLI Mode         ║
╚══════════════════════════════════════╝{_RESET}

  {_DIM}Skills: {skills_count} | Tools: {tools_count} | Memory: ON{_RESET}
  {_DIM}Commands: /status /stats /profile /health /memory /skills /train /eval /digest /pentest /dreamtime /reset /quit{_RESET}
  {_DIM}Gõ tin nhắn rồi Enter để chat.{_RESET}
""")

    async def _handle_command(self, cmd: str) -> bool:
        """Handle slash commands. Returns True if should exit."""
        cmd = cmd.lower().strip()

        if cmd in ("/quit", "/exit", "/q"):
            print(f"{_DIM}Tạm biệt!{_RESET}")
            return True

        elif cmd == "/status":
            await self._cmd_status()

        elif cmd == "/memory":
            await self._cmd_memory()

        elif cmd.startswith("/remember "):
            text = cmd[10:].strip()
            await self._cmd_remember(text)

        elif cmd == "/skills":
            await self._cmd_skills()

        elif cmd.startswith("/train"):
            await self._cmd_train(cmd)

        elif cmd == "/stats":
            await self._cmd_stats()

        elif cmd == "/profile":
            await self._cmd_profile()

        elif cmd == "/health":
            await self._cmd_health()

        elif cmd == "/dreamtime":
            await self._cmd_dreamtime()

        elif cmd.startswith("/eval"):
            await self._cmd_eval()

        elif cmd.startswith("/digest"):
            await self._cmd_digest(cmd)

        elif cmd.startswith("/pentest"):
            await self._cmd_pentest(cmd)

        elif cmd == "/reset":
            self._session.messages.clear()
            print(f"{_YELLOW}🔄 Đã reset cuộc trò chuyện.{_RESET}")

        elif cmd == "/help":
            self._cmd_help()

        else:
            print(f"{_DIM}Lệnh không hợp lệ. Gõ /help để xem danh sách.{_RESET}")

        return False

    async def _handle_message(self, text: str) -> None:
        """Handle a chat message."""
        session_key = self._memory.session_key("cli", self._user_id)

        envelope = MessageEnvelope(
            channel=Channel.CLI,
            session_id=self._session.session_id,
            user_id=self._user_id,
            username="CLI User",
            content=text,
        )

        print(f"{_DIM}⏳ Đang xử lý...{_RESET}", end="", flush=True)

        try:
            # 1. Process through memory
            await self._memory.process_user_message(session_key, text)

            # 2. Update user model
            self._user_model.update_from_message(self._user_id, text)

            # 3. Get context
            memory_context = await self._memory.get_relevant_context(text, session_key)
            user_context = self._user_model.build_context(self._user_id)
            if user_context:
                memory_context = f"{user_context}\n\n{memory_context}" if memory_context else user_context

            # 4. Find skills
            matched_skills = []
            skill_context = ""
            try:
                matched_skills = await self._skill_router.find_skills(text)
                skill_context = self._skill_router.build_skill_context(matched_skills)
            except Exception as e:
                log.warning("skill_routing_error", error=str(e))

            # 5. Route through LLM
            response: AgentResponse = await self._router.route(
                self._session, text,
                memory_context=memory_context,
                skill_context=skill_context,
            )

            # 6. Update state
            self._session.add_user_message(text)
            self._session.add_assistant_message(response.content)
            await self._memory.process_assistant_response(session_key, response.content)
            for skill in matched_skills:
                self._skill_router.update_usage(skill.metadata.name)

            # 7. Log
            skills_used = [s.metadata.name for s in matched_skills]
            await self._collector.log_interaction(envelope, response, skills_used=skills_used)

            # 8. Display
            # Clear "processing" line
            print("\r" + " " * 40 + "\r", end="")
            self._print_response(response)

        except Exception as e:
            print(f"\r{_RED}❌ Lỗi: {e}{_RESET}")
            log.error("cli_message_error", error=str(e))

    def _print_response(self, response: AgentResponse) -> None:
        """Pretty-print the response."""
        # Model info
        model_short = response.model_used.split("/")[-1] if "/" in response.model_used else response.model_used
        latency = f"{response.latency_ms}ms" if response.latency_ms else "?"
        cost = f"${response.cost_usd:.4f}" if response.cost_usd else "free"

        print(f"{_CYAN}JARVIS >{_RESET} {response.content}")
        print(f"{_DIM}  [{model_short} | {latency} | {cost}]{_RESET}", end="")

        if response.reasoning_trace:
            print(f" {_DIM}[tools used]{_RESET}", end="")
        print()

    async def _cmd_status(self) -> None:
        brain_stats = self._collector.get_stats()
        mem_stats = self._memory.get_stats()
        router_stats = self._router.get_stats()
        cost = router_stats["cost"]
        tool_stats = router_stats.get("tools", {})

        local_pct = f"{cost.get('local_ratio', 0) * 100:.0f}%"
        print(f"""
{_BOLD}🤖 JARVIS Status{_RESET}

  🧠 Semantic memories: {mem_stats['semantic_memories']}
  📊 Training data: {brain_stats['total_records']} records
  🎯 Skills: {len(self._skill_loader.get_all_metadata())}
  🔧 Tools: {tool_stats.get('registered_tools', 0)}

{_BOLD}⚡ Router{_RESET}
  Local: {'ON' if router_stats['local_enabled'] else 'OFF'} ({router_stats['local_model']})
  Cloud: {router_stats['cloud_model']}
  Cache: {router_stats['cache']['cached_entries']} entries

{_BOLD}📈 Metrics{_RESET}
  Total: {cost['total_calls']} calls, ${cost['total_cost_usd']:.4f}
  Local/Cloud: {cost.get('local_calls', 0)}/{cost.get('cloud_calls', 0)} ({local_pct} local)
  Avg latency: {cost['avg_latency_ms']}ms
""")

    async def _cmd_memory(self) -> None:
        memories = self._memory.semantic.get_all(limit=10)
        if not memories:
            print(f"{_YELLOW}🧠 Bộ nhớ trống.{_RESET}")
            return
        print(f"\n{_BOLD}🧠 Bộ nhớ JARVIS:{_RESET}\n")
        for m in memories:
            print(f"  • {m['content']} {_DIM}[{m['category']}]{_RESET}")

    async def _cmd_remember(self, text: str) -> None:
        if not text:
            print(f"{_DIM}Dùng: /remember <thông tin>{_RESET}")
            return
        await self._memory.remember_fact(text, category="user_stated", importance=0.9)
        print(f"{_GREEN}✅ Đã ghi nhớ: {text}{_RESET}")

    async def _cmd_skills(self) -> None:
        skills = self._skill_loader.get_all_metadata()
        if not skills:
            print(f"{_YELLOW}Chưa có skill nào.{_RESET}")
            return
        print(f"\n{_BOLD}🎯 JARVIS Skills:{_RESET}\n")
        for s in sorted(skills, key=lambda x: -x.priority):
            emoji = s.emoji or "•"
            print(f"  {emoji} {_BOLD}{s.name}{_RESET} v{s.version} — {s.description[:60]}")
            print(f"    {_DIM}Priority: {s.priority} | Success: {s.success_rate:.0%} | Uses: {s.usage_count}{_RESET}")

    async def _cmd_train(self, cmd: str = "/train") -> None:
        """Process training data and optionally trigger training.

        /train            — Show training report
        /train now [14b]  — Force-trigger training (optional profile: 4b or 14b)
        /train check      — Check if auto-retrain threshold met
        """
        args = cmd.replace("/train", "", 1).strip().lower().split()
        subcmd = args[0] if args else ""
        profile = args[1] if len(args) > 1 else None

        if subcmd == "now":
            await self._cmd_train_now(profile)
            return
        if subcmd == "check":
            await self._cmd_train_check()
            return

        # Default: process data + show report
        print(f"{_DIM}⏳ Đang xử lý training data...{_RESET}")
        stats = self._processor.process_all()

        from src.brain.auto_trainer import AutoTrainer
        trainer = AutoTrainer()
        auto_stats = trainer.get_stats()

        print(f"""
{_BOLD}✅ Training Data Processed{_RESET}
  📥 Raw: {stats['raw']}
  🔍 Filtered: {stats.get('after_filter', 0)}
  🧹 Deduped: {stats.get('after_dedup', 0)}
  📝 SFT: {stats['sft']}
  ⚖️ DPO: {stats['dpo']}

{_BOLD}🤖 Auto-Trainer{_RESET}
  Ready: {'✅' if auto_stats['should_retrain'] else '❌'} {auto_stats['reason']}
  Training runs: {auto_stats['train_count']}
  Last train: {auto_stats['last_train_at'] or 'Never'}

  {_DIM}Commands: /train now [4b|14b] | /train check{_RESET}
""")

    async def _cmd_train_now(self, profile: str | None = None) -> None:
        """Force-trigger SFT training pipeline."""
        if profile and profile not in ("4b", "14b"):
            print(f"{_RED}❌ Unknown profile '{profile}'. Use: 4b or 14b{_RESET}")
            return

        profile_label = profile or "default"
        print(f"{_YELLOW}🚀 Starting training pipeline (profile={profile_label}, force=True)...{_RESET}")
        print(f"{_DIM}This may take 10-40 minutes depending on model size.{_RESET}")

        from src.brain.auto_trainer import AutoTrainer
        trainer = AutoTrainer(model_profile=profile)

        try:
            result = await trainer.run(force=True)
            status = result.get("status", "unknown")

            if status == "completed":
                sft = result.get("steps", {}).get("sft", {})
                loss = sft.get("metrics", {}).get("train_loss", "N/A")
                mode = sft.get("metrics", {}).get("mode", "N/A")
                export = result.get("steps", {}).get("export", {})
                eval_data = result.get("steps", {}).get("evaluation", {})

                print(f"""
{_BOLD}{_GREEN}✅ Training Complete!{_RESET}
  Profile: {profile_label}
  Mode: {mode}
  Loss: {loss}
  Export: {export.get('status', 'N/A')}
  Accuracy: {eval_data.get('accuracy', 'N/A')}
""")
            elif status == "sft_failed":
                print(f"{_RED}❌ SFT Training Failed: {result.get('error', 'Unknown')}{_RESET}")
            else:
                print(f"{_YELLOW}⚠️ Training status: {status} — {result.get('reason', '')}{_RESET}")
        except Exception as e:
            print(f"{_RED}❌ Training error: {e}{_RESET}")

    async def _cmd_train_check(self) -> None:
        """Check if auto-retrain threshold is met."""
        from src.brain.auto_trainer import AutoTrainer
        trainer = AutoTrainer()
        should, reason = trainer.should_retrain()
        stats = trainer.get_stats()

        print(f"""
{_BOLD}🔍 Training Check{_RESET}
  Ready: {'✅ Yes' if should else '❌ No'}
  {reason}
  Last SFT count: {stats['last_sft_count']}
  Training runs: {stats['train_count']}
""")
        history = stats.get("history", [])
        if history:
            print(f"{_BOLD}📜 History:{_RESET}")
            for h in history:
                print(f"  • {h['at'][:10]}: loss={h.get('loss', 'N/A')}, acc={h.get('accuracy', 'N/A')}")

    async def _cmd_eval(self) -> None:
        """Run model evaluation benchmark."""
        print(f"{_DIM}⏳ Running evaluation benchmark (this may take a while)...{_RESET}")
        try:
            from src.brain.evaluator import ModelEvaluator
            evaluator = ModelEvaluator()

            # Check history first
            trend = evaluator.compare_versions(limit=5)
            if trend["evaluations"] > 0:
                print(f"\n{_BOLD}📈 Previous Evaluations{_RESET}")
                print(f"  Trend: {trend['trend']}")
                print(f"  Latest accuracy: {trend['latest_accuracy']:.1%}")
                print(f"  Latest score: {trend['latest_score']:.1f}/10")

            # Run new benchmark
            report = await evaluator.run_benchmark(skip_cloud=True)

            print(f"\n{_BOLD}🎯 Evaluation Results{_RESET}")
            print(f"  Model: {report.model_name}")
            print(f"  Prompts tested: {report.total_prompts}")
            print(f"  Accuracy (≥7.0): {report.accuracy:.1%}")
            print(f"  Avg score: {report.overall_local_score:.1f}/10")
            print(f"  Duration: {report.duration_seconds:.1f}s")

            print(f"\n{_BOLD}📊 By Category{_RESET}")
            for cat, scores in report.category_scores.items():
                bar = "█" * int(scores["local_avg"]) + "░" * (10 - int(scores["local_avg"]))
                print(f"  {cat:20s} {bar} {scores['local_avg']:.1f}/10 ({scores['accurate']}/{scores['count']} accurate)")

            # Update metrics
            from src.monitoring.metrics import brain_eval_accuracy, brain_eval_score
            brain_eval_accuracy.set(report.accuracy)
            brain_eval_score.set(report.overall_local_score)

        except Exception as e:
            print(f"{_YELLOW}⚠️ Evaluation error: {e}{_RESET}")

    async def _cmd_digest(self, cmd: str) -> None:
        """Generate daily news digest."""
        args = cmd.replace("/digest", "").strip()
        custom_topics = [t.strip() for t in args.split(",") if t.strip()] if args else None

        print(f"{_DIM}📰 Đang tìm tin tức...{_RESET}")
        try:
            from src.intelligence.daily_digest import DigestGenerator
            gen = DigestGenerator(self._user_model)
            digest = await gen.generate(
                user_id=self._user_id,
                topics=custom_topics,
            )

            if not digest.items:
                print(f"{_YELLOW}Không tìm thấy tin tức nào.{_RESET}")
                return

            print(f"\n{_BOLD}📰 Daily Digest — {digest.generated_at.strftime('%d/%m/%Y')}{_RESET}\n")

            by_topic: dict[str, list] = {}
            for item in digest.items:
                by_topic.setdefault(item.topic, []).append(item)

            for topic, items in by_topic.items():
                print(f"{_BOLD}[{topic.upper()}]{_RESET}")
                for item in items:
                    print(f"  • {item.title[:80]}")
                    if item.snippet:
                        print(f"    {_DIM}{item.snippet[:120]}{_RESET}")
                    print(f"    {_DIM}{item.url}{_RESET}")
                print()

            print(f"{_DIM}⏱ {len(digest.items)} tin | {digest.search_time_ms}ms{_RESET}")

        except Exception as e:
            print(f"{_YELLOW}⚠️ Digest error: {e}{_RESET}")

    async def _cmd_stats(self) -> None:
        router_stats = self._router.get_stats()
        cost = router_stats["cost"]
        cache = router_stats["cache"]
        skill_stats = self._skill_registry.get_stats()
        tool_stats = router_stats.get("tools", {})

        model_lines = ""
        for m in cost.get("per_model", []):
            model_lines += f"\n  {m['model']}: {m['calls']} calls, ${m['cost']:.4f}"

        print(f"""
{_BOLD}📊 Detailed Stats{_RESET}

{_BOLD}⚡ Router{_RESET}
  Calls: {cost['total_calls']} | Latency: {cost['avg_latency_ms']}ms
  Cache: {cache['cached_entries']} entries, {cache['total_hits']} hits

{_BOLD}🤖 Models{_RESET}{model_lines if model_lines else chr(10) + '  No calls yet'}

{_BOLD}🎯 Skills{_RESET}
  Loaded: {skill_stats['total_skills']} | Uses: {skill_stats['total_usage']}
  Avg success: {skill_stats['avg_success_rate']:.0%}

{_BOLD}🔧 Tools{_RESET}: {tool_stats.get('registered_tools', 0)} registered

{_BOLD}💰 Cost{_RESET}: ${cost['total_cost_usd']:.4f}
""")

    async def _cmd_profile(self) -> None:
        import json
        model = self._user_model.get_or_create(self._user_id)
        topics = json.loads(model["topics"]) if isinstance(model["topics"], str) else model["topics"]

        topic_lines = ""
        if topics:
            for topic, count in sorted(topics.items(), key=lambda x: -x[1])[:5]:
                topic_lines += f"\n  {topic}: {count}"

        print(f"""
{_BOLD}👤 Digital Twin{_RESET}

  Messages: {model['total_messages']}
  Avg length: {model['avg_msg_length']:.0f} chars
  Language: {model['language']}

{_BOLD}🎯 Topics{_RESET}{topic_lines if topic_lines else chr(10) + '  Not enough data'}
""")

    async def _cmd_health(self) -> None:
        from src.metacognition.diagnostics import SelfDiagnostics
        print(f"{_DIM}🏥 Running health checks...{_RESET}")
        diag = SelfDiagnostics()
        checks = await diag.run_all()
        report = diag.format_report(checks)
        # Strip markdown bold for terminal
        report = report.replace("**", f"{_BOLD}").replace("**", f"{_RESET}")
        print(report)

    async def _cmd_dreamtime(self) -> None:
        """Run a full Dreamtime cycle."""
        print(f"{_DIM}💤 Running Dreamtime cycle...{_RESET}")
        results = {}

        # 1. Memory consolidation
        try:
            consolidation = await self._memory_consolidator.consolidate()
            results["consolidation"] = consolidation
            c = consolidation
            print(f"  🧹 Memory: dedup={c.get('deduplicated', 0)} strengthen={c.get('strengthened', 0)} decay={c.get('decayed', 0)}")
        except Exception as e:
            print(f"  {_RED}❌ Consolidation error: {e}{_RESET}")

        # 2. Dream analysis
        try:
            dream_report = await self._dreamer.dream()
            results["dream"] = dream_report
            sugg = dream_report.get("improvement_suggestions", [])
            cands = dream_report.get("new_skill_candidates", [])
            print(f"  🔮 Dream: {len(sugg)} suggestions, {len(cands)} skill candidates")
            for s in sugg[:3]:
                print(f"    → {s[:80]}")
        except Exception as e:
            print(f"  {_RED}❌ Dream error: {e}{_RESET}")

        # 3. Skill evolution
        try:
            evo_report = await self._evolver.run()
            evo = evo_report.to_dict()
            print(f"  🧬 Evolution: optimized={evo.get('optimized', 0)} merged={evo.get('merged', 0)} pruned={evo.get('pruned', 0)}")
        except Exception as e:
            print(f"  {_RED}❌ Evolution error: {e}{_RESET}")

        # 4. Skill auto-generation
        try:
            from src.skills.generator import SkillGenerator
            generator = SkillGenerator(min_occurrences=3)
            gen_stats = await generator.run()
            gen_count = gen_stats.get("skills_generated", 0)
            if gen_count > 0:
                gen_names = [g["topic"] if isinstance(g, dict) else g for g in gen_stats.get("generated", [])]
                print(f"  🆕 Auto-generated: {gen_count} skills ({', '.join(gen_names)})")
                self._skill_loader.load_all()
            else:
                print(f"  🆕 Auto-generation: no new patterns detected")
        except Exception as e:
            print(f"  {_RED}❌ Generation error: {e}{_RESET}")

        # 5. Training data
        try:
            proc_stats = self._processor.process_all()
            print(f"  📊 Training: {proc_stats['sft']} SFT + {proc_stats['dpo']} DPO pairs")
        except Exception as e:
            print(f"  {_RED}❌ Training data error: {e}{_RESET}")

        # 6. Auto-retrain
        try:
            from src.brain.auto_trainer import AutoTrainer
            trainer = AutoTrainer(min_new_sft=50)
            train_result = await trainer.run()
            status = train_result.get("status", "unknown")
            icon = "✅" if status == "completed" else "⏭️" if status == "skipped" else "❌"
            print(f"  🧠 Auto-retrain: {icon} {status} — {train_result.get('reason', '')}")
        except Exception as e:
            print(f"  {_RED}❌ Auto-retrain error: {e}{_RESET}")

        # 7. Red Team safety testing
        try:
            from src.adversarial.red_team import RedTeamAgent
            from src.adversarial.evaluator import SafetyEvaluator
            red_team = RedTeamAgent()
            evaluator = SafetyEvaluator(red_team=red_team)

            session = self._sessions.get_or_create("redteam", "system", "RedTeam")

            async def test_jarvis(prompt: str) -> str:
                resp = await self._router.route(session, prompt, use_tools=False)
                return resp.content

            report = await evaluator.run_evaluation(
                response_fn=test_jarvis,
                severity_min="medium",
                sample_size=10,
            )
            icon = "✅" if report.asr <= 0.05 else "⚠️"
            print(f"  🔴 Red Team: {icon} {report.safe_count}/{report.total_tests} safe, ASR={report.asr:.1%}")
            dpo_pairs = evaluator.extract_dpo_pairs(report)
            if dpo_pairs:
                print(f"    → Generated {len(dpo_pairs)} DPO pairs from failures")
        except Exception as e:
            print(f"  {_RED}❌ Red Team error: {e}{_RESET}")

        print(f"\n{_GREEN}✅ Dreamtime cycle complete!{_RESET}")

    async def _cmd_pentest(self, cmd: str) -> None:
        """Run autonomous pentest pipeline."""
        args = cmd.replace("/pentest", "").strip()

        if not args:
            print(f"""
{_BOLD}Usage:{_RESET} /pentest <target> [scope]

{_DIM}Scopes: full (default), quick, web_only, network_only, recon_only{_RESET}

Examples:
  /pentest example.com
  /pentest example.com quick
  /pentest https://example.com web_only
""")
            return

        parts = args.split()
        target = parts[0]
        scope = parts[1] if len(parts) > 1 else "full"

        valid_scopes = {"full", "quick", "web_only", "network_only", "recon_only"}
        if scope not in valid_scopes:
            print(f"{_RED}Scope không hợp lệ: {scope}{_RESET}")
            print(f"Chọn: {', '.join(sorted(valid_scopes))}")
            return

        print(f"\n{_CYAN}🔍 Pentest: {target} (scope: {scope}){_RESET}\n")

        from src.intelligence.pentest import PentestPipeline
        from src.intelligence.report_generator import ReportGenerator

        pipeline = PentestPipeline(self._tool_registry)

        def on_progress(phase: str, msg: str) -> None:
            print(f"  {_DIM}{msg}{_RESET}")

        pipeline.set_progress_callback(on_progress)

        try:
            report = await pipeline.run(target, scope=scope)

            # Print summary
            summary = ReportGenerator.generate_summary(report)
            score_colors = {"A": _GREEN, "B": _CYAN, "C": _YELLOW, "D": _YELLOW, "F": _RED}
            color = score_colors.get(report.score, _RESET)
            print(f"\n{color}{_BOLD}{'=' * 50}")
            print(summary)
            print(f"{'=' * 50}{_RESET}")

            # Save full report
            report_path = f"/tmp/jarvis_pentest_{target.replace('/', '_')}.md"
            full_report = ReportGenerator.generate_markdown(report)
            with open(report_path, "w") as f:
                f.write(full_report)

            print(f"\n{_DIM}📋 Full report saved: {report_path}{_RESET}")

        except Exception as e:
            print(f"{_RED}❌ Pentest error: {e}{_RESET}")

    def _cmd_help(self) -> None:
        print(f"""
{_BOLD}JARVIS CLI Commands:{_RESET}

  /status    — Trạng thái hệ thống
  /stats     — Thống kê chi tiết
  /profile   — Xem Digital Twin
  /health    — Kiểm tra sức khỏe
  /memory    — Xem bộ nhớ
  /remember  — Ghi nhớ: /remember <text>
  /skills    — Xem kỹ năng
  /train     — Training data (/train now [4b|14b])
  /eval      — Benchmark model quality
  /digest    — Daily news digest (topics từ sở thích)
  /pentest   — Pentest tự động: /pentest <target> [scope]
  /dreamtime — Chạy Dreamtime cycle
  /reset     — Reset trò chuyện
  /quit      — Thoát

  {_DIM}Hoặc gõ tin nhắn bất kỳ để chat.{_RESET}
""")
