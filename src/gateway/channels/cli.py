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
from src.app import JarvisApp
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

    def __init__(self, app: JarvisApp | None = None) -> None:
        if app is not None:
            self._app = app
            self._sessions = app.sessions
            self._collector = app.collector
            self._processor = app.processor
            self._memory = app.memory
            self._user_model = app.user_model
            self._skill_loader = app.skill_loader
            self._skill_router = app.skill_router
            self._bus = app.event_bus
            self._skill_registry = app.skill_registry
            self._tool_registry = app.tool_registry
            self._router = app.router
            self._memory_consolidator = app.memory_consolidator
            self._dreamer = app.dreamer
            self._evolver = app.evolver
            self._bounty_pipeline = app.bounty_pipeline
            self._hunter_pipeline = app.hunter_pipeline
            self._trading_brain = app.trading_brain
            self._ceo = app._ceo
        else:
            # Legacy path — backward compatible standalone init
            self._app = None
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
            self._bounty_pipeline = None
            self._hunter_pipeline = None
            self._trading_brain = None
            self._ceo = None

        # CLI-specific state (always initialized regardless of path)
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
  {_DIM}Commands: /company /status /stats /profile /health /memory /skills /train /eval /digest /mt5 /trade /pentest /bounty /hunt /dreamtime /reset /quit{_RESET}
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

        elif cmd.startswith("/bounty"):
            await self._cmd_bounty(cmd)

        elif cmd.startswith("/hunt"):
            await self._cmd_hunt(cmd)

        elif cmd.startswith("/trade"):
            await self._cmd_trade(cmd)

        elif cmd == "/mt5":
            await self._cmd_mt5()

        elif cmd == "/company":
            self._cmd_company()

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

    async def _cmd_bounty(self, cmd: str) -> None:
        """Bug Bounty Pipeline management."""
        if self._bounty_pipeline is None:
            print(f"{_RED}❌ Bug Bounty Pipeline chưa được khởi tạo.{_RESET}")
            return

        args = cmd.replace("/bounty", "").strip().split()
        subcmd = args[0] if args else "status"
        params = args[1:] if len(args) > 1 else []

        if subcmd == "status":
            stats = self._bounty_pipeline.stats()
            targets = stats["targets"]
            findings = stats["findings"]
            running = f"{_GREEN}Running{_RESET}" if stats["running"] else f"{_RED}Stopped{_RESET}"
            print(f"""
{_BOLD}🎯 Bug Bounty Pipeline{_RESET}

  Status: {running}
  Programs: {stats['programs']}
  Targets: {targets.get('queued', 0)} queued, {targets.get('scanning', 0)} scanning, {targets.get('scanned', 0)} scanned
  Findings: {findings['pending']} pending, {findings['total']} total
  Earnings: ${stats['earnings_usd']:.2f}

  {_DIM}Subcommands: status, start, stop, programs, findings, review <id>, approve <id>, reject <id>, earnings{_RESET}
""")

        elif subcmd == "start":
            if self._bounty_pipeline.is_running:
                print(f"{_YELLOW}⚠️ Pipeline đang chạy rồi.{_RESET}")
                return
            await self._bounty_pipeline.start(interval_hours=6)
            print(f"{_GREEN}🚀 Bug Bounty Pipeline đã bắt đầu! Scan mỗi 6 giờ.{_RESET}")

        elif subcmd == "stop":
            if not self._bounty_pipeline.is_running:
                print(f"{_YELLOW}⚠️ Pipeline chưa chạy.{_RESET}")
                return
            await self._bounty_pipeline.stop()
            print(f"{_GREEN}🛑 Bug Bounty Pipeline đã dừng.{_RESET}")

        elif subcmd == "programs":
            programs = self._bounty_pipeline.monitor.get_active_programs()
            if not programs:
                print(f"{_YELLOW}📭 Chưa có program nào.{_RESET}")
                return
            print(f"\n{_BOLD}🏢 Active Programs{_RESET}\n")
            for p in programs[:20]:
                bounty = f"${p.bounty_low}-${p.bounty_high}" if p.bounty_high else "N/A"
                print(f"  • {_BOLD}{p.name}{_RESET} ({p.platform})")
                print(f"    Bounty: {bounty} | Priority: {p.priority_score:.2f}")

        elif subcmd == "findings":
            findings = self._bounty_pipeline.get_pending_findings()
            if not findings:
                print(f"{_YELLOW}📭 Không có finding nào đang chờ.{_RESET}")
                return
            print(f"\n{_BOLD}🔍 Pending Findings{_RESET}\n")
            for f in findings[:20]:
                print(f"  {_BOLD}#{f.id}{_RESET} — {f.title}")
                print(f"    {f.severity} (CVSS {f.cvss}) | Confidence: {f.confidence:.0%} | {f.estimated_bounty_str}")

        elif subcmd == "review":
            if not params:
                print(f"{_DIM}Usage: /bounty review <id>{_RESET}")
                return
            try:
                finding_id = int(params[0])
            except ValueError:
                print(f"{_RED}❌ ID phải là số.{_RESET}")
                return
            row = self._bounty_pipeline.conn.execute(
                "SELECT * FROM bounty_findings WHERE id = ?", (finding_id,)
            ).fetchone()
            if not row:
                print(f"{_RED}❌ Finding #{finding_id} không tìm thấy.{_RESET}")
                return
            finding = self._bounty_pipeline._row_to_finding(row)
            t_row = self._bounty_pipeline.conn.execute(
                "SELECT domain FROM bounty_targets WHERE id = ?", (finding.target_id,)
            ).fetchone()
            domain = t_row["domain"] if t_row else "unknown"
            report = self._bounty_pipeline.reporter.generate_hackerone(finding, domain)
            print(f"\n{report}")

        elif subcmd == "approve":
            if not params:
                print(f"{_DIM}Usage: /bounty approve <id>{_RESET}")
                return
            try:
                finding_id = int(params[0])
            except ValueError:
                print(f"{_RED}❌ ID phải là số.{_RESET}")
                return
            self._bounty_pipeline.update_finding_status(finding_id, "approved")
            print(f"{_GREEN}✅ Finding #{finding_id} đã được approved.{_RESET}")

        elif subcmd == "reject":
            if not params:
                print(f"{_DIM}Usage: /bounty reject <id>{_RESET}")
                return
            try:
                finding_id = int(params[0])
            except ValueError:
                print(f"{_RED}❌ ID phải là số.{_RESET}")
                return
            self._bounty_pipeline.update_finding_status(finding_id, "rejected")
            print(f"{_RED}❌ Finding #{finding_id} đã bị rejected.{_RESET}")

        elif subcmd == "earnings":
            row = self._bounty_pipeline.conn.execute(
                "SELECT COALESCE(SUM(amount), 0) as total, COUNT(*) as cnt FROM bounty_earnings"
            ).fetchone()
            total = row["total"] if row else 0
            count = row["cnt"] if row else 0
            print(f"""
{_BOLD}💰 Earnings{_RESET}

  Total: ${total:.2f}
  Bounties paid: {count}
""")

        else:
            print(f"{_DIM}Usage: /bounty [status|start|stop|programs|findings|review|approve|reject|earnings]{_RESET}")

    async def _cmd_hunt(self, cmd: str) -> None:
        """AI Bug Hunter pipeline."""
        if self._hunter_pipeline is None:
            print(f"{_RED}❌ Hunter Pipeline chưa được khởi tạo.{_RESET}")
            return

        args = cmd.replace("/hunt", "").strip()

        if not args:
            print(f"""
{_BOLD}Usage:{_RESET} /hunt <domain> [mode]

{_DIM}Modes: full (default), quick, deep{_RESET}

Examples:
  /hunt target.com
  /hunt target.com quick
  /hunt target.com deep
""")
            return

        parts = args.split()
        target = parts[0]
        mode = parts[1] if len(parts) > 1 else "full"

        valid_modes = {"full", "quick", "deep"}
        if mode not in valid_modes:
            print(f"{_RED}Mode không hợp lệ: {mode}{_RESET}")
            print(f"Chọn: {', '.join(sorted(valid_modes))}")
            return

        print(f"\n{_CYAN}🎯 AI Hunt: {target} (mode: {mode}){_RESET}\n")

        def on_progress(agent: str, msg: str) -> None:
            print(f"  {_DIM}[{agent}] {msg}{_RESET}")

        self._hunter_pipeline._progress_fn = on_progress

        try:
            result = await self._hunter_pipeline.hunt(target, mode=mode)

            # Print summary
            summary = self._hunter_pipeline.format_summary(result)
            print(f"\n{_GREEN}{_BOLD}{'=' * 50}")
            print(summary)
            print(f"{'=' * 50}{_RESET}")

            # Print reports if any
            if result.reports:
                for i, report in enumerate(result.reports, 1):
                    print(f"\n{_BOLD}--- Report #{i} ---{_RESET}")
                    print(report[:2000])  # Truncate very long reports

        except Exception as e:
            print(f"{_RED}❌ Hunt error: {e}{_RESET}")

    async def _cmd_trade(self, cmd: str) -> None:
        """Handle /trade commands."""
        if self._trading_brain is None:
            print(f"{_YELLOW}Trading Brain chưa được khởi tạo.{_RESET}")
            return

        args = cmd.replace("/trade", "").strip().split()
        subcmd = args[0] if args else "status"

        if subcmd == "status":
            status = self._trading_brain.get_status()
            running = "ON" if status["running"] else "OFF"
            print(f"\n{_BOLD}Trading Brain{_RESET}")
            print(f"  Status: {running}")
            print(f"  Plan: {status['plan']}")
            print(f"  Active Zones: {status['active_zones']}")
            print(f"  Active Positions: {status['active_positions']}")
            print(f"  Pending Orders: {status.get('pending_orders', 0)}")
            print(f"  Trades Taken: {status['trades_taken']}")
            risk = status.get("risk", {})
            if risk:
                state = risk.get("state", {})
                print(f"  Daily P/L: {state.get('daily_pnl', 0):+.2f}")
                print(f"  Daily Trades: {state.get('daily_trades', 0)}")

        elif subcmd == "start":
            await self._trading_brain.start()
            print(f"{_GREEN}Trading Brain started.{_RESET}")

        elif subcmd == "stop":
            await self._trading_brain.stop()
            print(f"{_YELLOW}Trading Brain stopped.{_RESET}")

        elif subcmd == "plan":
            session = args[1] if len(args) > 1 else ""
            print(f"{_DIM}Analyzing market...{_RESET}")
            try:
                plan_text = await self._trading_brain.plan_now(session)
                print(plan_text)
            except Exception as e:
                print(f"{_RED}Plan error: {e}{_RESET}")

        elif subcmd == "config":
            rg = self._trading_brain._risk_guard
            if rg is None:
                print(f"{_RED}RiskGuard not available.{_RESET}")
                return
            if len(args) >= 3:
                param, value = args[1], args[2]
                try:
                    if value.lower() in ("true", "false"):
                        parsed = value.lower() == "true"
                    elif "." in value:
                        parsed = float(value)
                    else:
                        parsed = int(value)
                except ValueError:
                    parsed = value
                rg.update_config(**{param: parsed})
                print(f"{_GREEN}Updated: {param} = {parsed}{_RESET}")
            else:
                status = rg.get_status()
                config = status.get("config", {})
                print(f"\n{_BOLD}Risk Config{_RESET}")
                for k, v in config.items():
                    print(f"  {k}: {v}")

        elif subcmd == "pending":
            sub_action = args[1] if len(args) > 1 else "list"
            pm = self._trading_brain.pending_manager
            if sub_action == "cancel":
                count = await pm.cancel_all()
                print(f"{_GREEN}Đã hủy {count} pending order(s).{_RESET}")
            else:
                orders = pm.active_orders
                if not orders:
                    print(f"{_DIM}Không có pending orders.{_RESET}")
                else:
                    print(f"\n{_BOLD}Pending Orders ({len(orders)}){_RESET}")
                    for ticket, info in orders.items():
                        direction = info.get("direction", "?").upper()
                        order_type = info.get("order_type", "?")
                        price = info.get("price", 0)
                        sl = info.get("sl", 0)
                        tp1 = info.get("tp1", 0)
                        zone_id = info.get("zone_id", "?")
                        volume = info.get("volume", 0)
                        print(
                            f"  #{ticket} {direction} {order_type} @ {price:.2f} "
                            f"Vol: {volume} SL: {sl:.2f} TP: {tp1:.2f} Zone: {zone_id}"
                        )
                    print(f"\n{_DIM}Cancel all: /trade pending cancel{_RESET}")

        elif subcmd == "kill":
            result = await self._trading_brain.kill()
            print(f"{_RED}{result}{_RESET}")

        else:
            print(f"{_DIM}Usage: /trade [status|start|stop|plan|config|pending|kill]{_RESET}")

    async def _cmd_mt5(self) -> None:
        """Quick MT5 status: account + XAUUSD price + positions."""
        import asyncio

        print(f"{_DIM}📊 Đang kết nối MT5...{_RESET}")

        try:
            from src.trading.mt5_client import MT5Client

            client = MT5Client()
            try:
                available = await client.is_available()
                if not available:
                    print(f"{_RED}❌ MT5 Bridge offline.{_RESET}")
                    print(f"{_DIM}Kiểm tra Windows: python mt5_bridge.py{_RESET}")
                    return

                results = await asyncio.gather(
                    client.get_account(),
                    client.get_tick(__import__("os").environ.get("TRADING_SYMBOL", "XAUUSD")),
                    client.get_positions(),
                    return_exceptions=True,
                )
                account, tick, positions = results

                print(f"\n{_BOLD}📊 MT5 Status{_RESET}\n")

                if isinstance(account, dict):
                    print(f"  💰 Balance: ${account.get('balance', 0):,.2f}")
                    print(f"  📈 Equity: ${account.get('equity', 0):,.2f}")
                    print(f"  📊 Profit: ${account.get('profit', 0):+,.2f}")
                    print(f"  🔒 Margin: ${account.get('margin', 0):,.2f}")
                    print(f"  🆓 Free: ${account.get('margin_free', 0):,.2f}\n")

                if isinstance(tick, dict):
                    spread = tick.get('ask', 0) - tick.get('bid', 0)
                    print(f"  🥇 XAUUSD: {tick['bid']:.2f} / {tick['ask']:.2f} (spread: {spread:.2f})\n")

                if isinstance(positions, list) and positions:
                    total_pnl = sum(p.get("profit", 0) for p in positions)
                    print(f"  📋 Vị thế mở: {len(positions)} | P&L: ${total_pnl:+,.2f}")
                    for p in positions[:5]:
                        side = "BUY" if p.get("type", 0) == 0 else "SELL"
                        print(
                            f"    • {p.get('symbol', '?')} {side} {p.get('volume', 0)} lot | "
                            f"${p.get('profit', 0):+,.2f}"
                        )
                elif isinstance(positions, list):
                    print("  📋 Không có vị thế mở")

                print()
            finally:
                await client.close()

        except Exception as e:
            print(f"{_RED}❌ MT5 error: {e}{_RESET}")

    def _cmd_company(self) -> None:
        """Show company structure and department status."""
        if not self._ceo:
            print(f"{_DIM}Company structure not initialized.{_RESET}")
            return

        status = self._ceo.get_status()
        print(f"\n{_BOLD}🏢 JARVIS Company{_RESET}\n")

        dept_emojis = {
            "finance": "💰",
            "security": "🛡️",
            "engineering": "⚙️",
            "research": "🔬",
            "operations": "📋",
        }

        for dept_name, info in status["departments"].items():
            emoji = dept_emojis.get(dept_name, "📋")
            print(f"  {emoji} {_BOLD}{info['name']}{_RESET} — {info['tools']} tools")

        print(f"\n  📊 Total: {status['total_departments']} departments")

    def _cmd_help(self) -> None:
        print(f"""
{_BOLD}JARVIS CLI Commands:{_RESET}

  /company   — Company structure & departments
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
  /mt5       — MT5 status: account + XAUUSD price + positions
  /trade     — Trading Brain: /trade [status|start|stop|plan|config|pending|kill]
  /pentest   — Pentest tự động: /pentest <target> [scope]
  /bounty    — Bug Bounty Pipeline: /bounty [status|start|stop|programs|findings|review|approve|reject|earnings]
  /hunt      — AI Bug Hunter: /hunt <domain> [full|quick|deep]
  /dreamtime — Chạy Dreamtime cycle
  /reset     — Reset trò chuyện
  /quit      — Thoát

  {_DIM}Hoặc gõ tin nhắn bất kỳ để chat.{_RESET}
""")
